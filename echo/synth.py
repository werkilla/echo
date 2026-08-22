"""Synthesis engine (§9): priority queue, bounded workers, chapter assembly.

Priorities: PRIORITY_LIVE (playback path) preempts PRIORITY_BACKGROUND
(full-book prepare). Task unit = one segment, so preemption is natural.
Per-segment MP3s are byte-concatenated into the chapter file on completion
(MP3 frames are self-contained — proven in spike 1), then seg files removed.
"""

from __future__ import annotations

import itertools
import logging
import os
import queue
import threading

from .epub import Chapter, segment_chapter
from .store import Store
from .tts import KokoroClient, mp3_duration

log = logging.getLogger(__name__)

PRIORITY_LIVE = 0
PRIORITY_BACKGROUND = 10


class SynthEngine:
    def __init__(self, store: Store, tts: KokoroClient, voice: str, workers: int = 2):
        self.store = store
        self.tts = tts
        self.voice = voice
        self.workers = workers
        self._q: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._queued: set[tuple[int, int, int]] = set()   # (series, spine, seg)
        self._lock = threading.Lock()
        self._seg_done: dict[tuple[int, int, int], threading.Event] = {}
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()

    # -- lifecycle -------------------------------------------------------------

    def start(self):
        for i in range(self.workers):
            t = threading.Thread(target=self._worker, name=f"synth-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self._stop.set()

    # -- planning ---------------------------------------------------------------

    def plan_chapter(self, series_id: int, chapter: Chapter) -> int:
        """Persist the segment plan for a spine item. Returns seg count."""
        if not chapter.blocks:
            self.store.set_chapter(series_id, chapter.spine_index, "empty", seg_count=0)
            return 0
        segs = self.store.segments(series_id, chapter.spine_index)
        if segs:
            return len(segs)
        plan = [(s.seg_index, s.block_index, s.text)
                for s in segment_chapter(chapter.blocks)]
        self.store.ensure_segments(series_id, chapter.spine_index, plan)
        self.store.set_chapter(series_id, chapter.spine_index, "pending",
                               seg_count=len(plan))
        return len(plan)

    def enqueue_chapter(self, series_id: int, chapter: Chapter,
                        priority: int = PRIORITY_BACKGROUND,
                        from_seg: int = 0) -> None:
        if self.store.chapter_status(series_id, chapter.spine_index) in ("done", "empty"):
            return
        self.plan_chapter(series_id, chapter)
        for seg in self.store.segments(series_id, chapter.spine_index):
            if seg.seg_index >= from_seg and seg.duration_s is None:
                self.enqueue_segment(series_id, chapter.spine_index,
                                     seg.seg_index, priority)
        self.store.set_chapter(series_id, chapter.spine_index, "synthesizing")
        self._maybe_assemble(series_id, chapter.spine_index)

    def enqueue_segment(self, series_id: int, spine: int, seg: int, priority: int):
        key = (series_id, spine, seg)
        with self._lock:
            if key in self._queued:
                return
            self._queued.add(key)
            self._seg_done.setdefault(key, threading.Event())
        self._q.put((priority, next(self._seq), key))

    # -- playback-path helpers ----------------------------------------------------

    def seg_file_ready(self, series_id: int, spine: int, seg: int) -> str | None:
        path = self.store.seg_audio_path(series_id, self.voice, spine, seg)
        return path if os.path.exists(path) and os.path.getsize(path) > 0 else None

    def wait_for_segment(self, series_id: int, spine: int, seg: int,
                         timeout: float = 30.0) -> str | None:
        """Block until a segment's MP3 exists (playback path). Returns path."""
        if p := self.seg_file_ready(series_id, spine, seg):
            return p
        key = (series_id, spine, seg)
        with self._lock:
            ev = self._seg_done.setdefault(key, threading.Event())
        self.enqueue_segment(series_id, spine, seg, PRIORITY_LIVE)
        ev.wait(timeout)
        return self.seg_file_ready(series_id, spine, seg)

    # -- worker ---------------------------------------------------------------------

    def _worker(self):
        while not self._stop.is_set():
            try:
                _, _, key = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            series_id, spine, seg = key
            try:
                self._synthesize_one(series_id, spine, seg)
            except Exception:
                log.exception("segment synth failed: %s", key)
            finally:
                with self._lock:
                    self._queued.discard(key)
                    if ev := self._seg_done.get(key):
                        ev.set()
                self._q.task_done()

    def _synthesize_one(self, series_id: int, spine: int, seg: int):
        path = self.store.seg_audio_path(series_id, self.voice, spine, seg)
        if os.path.exists(path) and os.path.getsize(path) > 0:
            return
        row = next((s for s in self.store.segments(series_id, spine)
                    if s.seg_index == seg), None)
        if row is None:
            log.warning("no segment row for %s/%s/%s", series_id, spine, seg)
            return
        audio = self.tts.synthesize(row.text)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(audio)
        os.replace(tmp, path)
        self.store.set_seg_duration(series_id, spine, seg, mp3_duration(audio))
        self._maybe_assemble(series_id, spine)

    # -- assembly -----------------------------------------------------------------

    def _maybe_assemble(self, series_id: int, spine: int):
        segs = self.store.segments(series_id, spine)
        if not segs or any(s.duration_s is None for s in segs):
            return
        if self.store.chapter_status(series_id, spine) == "done":
            return
        chapter_path = self.store.chapter_audio_path(series_id, self.voice, spine)
        offsets: list[tuple[int, float, int]] = []
        t, b = 0.0, 0
        tmp = chapter_path + ".tmp"
        with open(tmp, "wb") as out:
            for s in segs:
                seg_path = self.store.seg_audio_path(series_id, self.voice,
                                                     spine, s.seg_index)
                with open(seg_path, "rb") as f:
                    data = f.read()
                offsets.append((s.seg_index, t, b))
                out.write(data)
                t += s.duration_s
                b += len(data)
        os.replace(tmp, chapter_path)
        self.store.finalize_offsets(series_id, spine, offsets)
        self.store.set_chapter(series_id, spine, "done", duration_s=t)
        # seg files no longer needed
        for s in segs:
            try:
                os.remove(self.store.seg_audio_path(series_id, self.voice,
                                                    spine, s.seg_index))
            except OSError:
                pass
        log.info("assembled %s spine %s: %.1f min", series_id, spine, t / 60)
