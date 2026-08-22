"""Background scheduler (§9.3): poll Kavita, full-book synthesis for active books."""

from __future__ import annotations

import logging
import threading

from .library import Library
from .synth import PRIORITY_BACKGROUND, SynthEngine

log = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, library: Library, engine: SynthEngine, poll_minutes: int):
        self.library = library
        self.engine = engine
        self.poll_s = poll_minutes * 60
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.wait(self.poll_s if self._first_done else 5):
            self.tick()
    _first_done = False

    def tick(self):
        try:
            books = self.library.refresh()
        except Exception:
            log.exception("scheduler: kavita refresh failed")
            return
        finally:
            self._first_done = True
        for book in books:
            sid = book["series_id"]
            try:
                if self.library.is_active(sid):
                    self.prepare_book(sid)
            except Exception:
                log.exception("scheduler: prepare failed for %s", sid)

    def prepare_book(self, series_id: int, priority: int = PRIORITY_BACKGROUND):
        """Full-book synthesis (D12): current position → end, then backfill."""
        chapters = self.library.chapters(series_id)
        try:
            pos = self.library.position(series_id)
            start = pos.spine_index
        except Exception:
            start = 0
        order = list(range(start, len(chapters))) + list(range(0, start))
        for offset, spine in enumerate(order):
            # keep reading order roughly prioritized within background band
            self.engine.enqueue_chapter(series_id, chapters[spine],
                                        priority=priority + min(offset, 89))
        log.info("prepare queued for %s (%d chapters, from spine %d)",
                 series_id, len(chapters), start)
