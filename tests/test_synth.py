"""Synthesis engine + store tests with a fake TTS (fixture MP3, no Kokoro)."""

import os

import pytest

from echo.epub import parse_epub
from echo.store import Store
from echo.synth import PRIORITY_LIVE, SynthEngine
from echo.tts import mp3_duration

FIXTURE_MP3 = os.path.join(os.path.dirname(__file__), "fixtures", "silence.mp3")


class FakeTTS:
    def __init__(self):
        with open(FIXTURE_MP3, "rb") as f:
            self.audio = f.read()
        self.calls = 0

    def synthesize(self, text: str) -> bytes:
        self.calls += 1
        return self.audio

    def healthy(self):
        return True


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path))


@pytest.fixture
def engine(store):
    return SynthEngine(store, FakeTTS(), voice="af_heart", workers=1)


def _drain(engine):
    """Process the queue synchronously (no worker threads in tests)."""
    while not engine._q.empty():
        _, _, (sid, spine, seg) = engine._q.get()
        engine._synthesize_one(sid, spine, seg)
        engine._queued.discard((sid, spine, seg))


def test_fixture_mp3_duration():
    with open(FIXTURE_MP3, "rb") as f:
        assert 0.3 < mp3_duration(f.read()) < 1.0


def test_plan_persists_segments(store, engine, fixture_epub):
    ch1 = parse_epub(fixture_epub)[0]
    n = engine.plan_chapter(610, ch1)
    assert n > 0
    assert len(store.segments(610, 0)) == n
    # idempotent
    assert engine.plan_chapter(610, ch1) == n


def test_evict_derived_and_audio(store, engine, fixture_epub):
    """store.delete_derived / delete_audio wipe a book's cache (the refetch path)."""
    ch1 = parse_epub(fixture_epub)[0]
    engine.enqueue_chapter(610, ch1, PRIORITY_LIVE)
    _drain(engine)
    assert store.segments(610, 0)                       # segments present
    audio_dir = os.path.join(store.dir, "audio", "610")
    assert os.path.isdir(audio_dir)                     # audio written

    store.delete_derived(610)
    assert store.segments(610, 0) == []                 # derived rows gone
    assert store.chapter_status(610, 0) == "pending"     # chapter row gone → default

    store.delete_audio(610)
    assert not os.path.exists(audio_dir)                # audio gone
    store.delete_audio(610)                             # idempotent — no crash on missing


def test_full_chapter_synthesis_and_assembly(store, engine, fixture_epub):
    ch1 = parse_epub(fixture_epub)[0]
    engine.enqueue_chapter(610, ch1, PRIORITY_LIVE)
    _drain(engine)

    assert store.chapter_status(610, 0) == "done"
    path = store.chapter_audio_path(610, "af_heart", 0)
    assert os.path.exists(path)

    segs = store.segments(610, 0)
    seg_dur = segs[0].duration_s
    # offsets cumulative and monotonic; byte offsets too
    seg_bytes = len(engine.tts.audio)
    for i, s in enumerate(segs):
        assert s.duration_s == pytest.approx(seg_dur, rel=0.01)
        assert s.offset_s == pytest.approx(i * seg_dur, rel=0.01)
        assert s.byte_offset == i * seg_bytes
    assert segs[-1].byte_offset < os.path.getsize(path)
    # chapter file = concat of N fixture files
    assert os.path.getsize(path) == len(segs) * len(engine.tts.audio)
    # seg files cleaned up
    assert not os.path.exists(store.seg_audio_path(610, "af_heart", 0, 0))


def test_resume_partial_synthesis(store, engine, fixture_epub):
    ch1 = parse_epub(fixture_epub)[0]
    engine.plan_chapter(610, ch1)
    n = len(store.segments(610, 0))
    # synthesize only seg 0, then enqueue the chapter — only n-1 tasks remain
    engine.enqueue_segment(610, 0, 0, PRIORITY_LIVE)
    _drain(engine)
    engine.enqueue_chapter(610, ch1, PRIORITY_LIVE)
    remaining = engine._q.qsize()
    assert remaining == n - 1
    _drain(engine)
    assert store.chapter_status(610, 0) == "done"


def test_priority_ordering(store, engine, fixture_epub):
    chapters = parse_epub(fixture_epub)
    engine.enqueue_chapter(610, chapters[1], priority=10)   # background
    engine.enqueue_chapter(610, chapters[0], PRIORITY_LIVE)  # live preempts
    pri, _, (sid, spine, seg) = engine._q.get()
    assert pri == PRIORITY_LIVE and spine == 0


def test_empty_chapter_marked(store, engine):
    from echo.epub import Chapter
    engine.plan_chapter(610, Chapter(spine_index=5, href="x.xhtml", title=None))
    assert store.chapter_status(610, 5) == "empty"


def test_progress_log_undo(store):
    store.log_progress_write(610, {"pageNum": 58}, {"pageNum": 59})
    store.log_progress_write(610, {"pageNum": 59}, {"pageNum": 60})
    assert store.last_progress_write(610) == {"pageNum": 59}
