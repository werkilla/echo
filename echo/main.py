"""Echo FastAPI app (§10): player API + PWA static."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import requests
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, config
from .kavita import KavitaClient, Progress, may_write_back
from .library import Library, _parse_kavita_utc
from .mapper import scroll_id_for_block
from .scheduler import Scheduler
from .store import Store
from .synth import PRIORITY_LIVE, SynthEngine
from .tts import KokoroClient

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("echo")

cfg = config.load()
store = Store(cfg.config_dir)
kavita = KavitaClient(cfg.kavita_url, cfg.kavita_api_key)
tts = KokoroClient(cfg.kokoro_url, cfg.default_voice)
engine = SynthEngine(store, tts, cfg.default_voice, workers=2)
library = Library(store, kavita)
scheduler = Scheduler(library, engine, cfg.poll_minutes)

app = FastAPI(title="Echo", version=__version__)


@app.on_event("startup")
def startup():
    engine.start()
    scheduler.start()


def auth(request: Request):
    """Bearer header, or ?token= for <audio>/<img> tags that can't send headers.
    (LAN/Tailscale only — seatbelt, not a vault, per design §10.)"""
    tok = request.headers.get("Authorization", "").removeprefix("Bearer ").strip() \
        or request.query_params.get("token", "")
    if tok != cfg.echo_token:
        raise HTTPException(401, "bad token")


# -- health (unauthenticated) ---------------------------------------------------

@app.get("/api/health")
def health():
    return {"echo": __version__, "kavita": kavita.healthy(), "kokoro": tts.healthy(),
            "audio_gb": round(store.audio_bytes_total() / 1e9, 2)}


# -- books ------------------------------------------------------------------------

def _book_summary(b: dict) -> dict:
    sid = b["series_id"]
    return {
        "series_id": sid, "title": b["title"],
        "spine_count": b["spine_count"],
        "chapters_done": store.chapters_done(sid),
        "archived": bool(b.get("archived")),
    }


@app.get("/api/books", dependencies=[Depends(auth)])
def books(archived: bool = False):
    """Active library by default; ?archived=true for the archive."""
    return [_book_summary(b) for b in store.books(archived=archived)]


@app.post("/api/books/refresh", dependencies=[Depends(auth)])
def refresh():
    library.refresh()
    return {"books": len(store.books())}


@app.post("/api/books/{sid}/prepare", dependencies=[Depends(auth)])
def prepare(sid: int):
    if not store.book(sid):
        library.refresh()
        if not store.book(sid):
            raise HTTPException(404, "unknown book")
    scheduler.prepare_book(sid)
    return {"status": "queued"}


@app.post("/api/books/{sid}/archive", dependencies=[Depends(auth)])
def archive(sid: int):
    if not store.book(sid):
        raise HTTPException(404, "unknown book")
    store.set_archived(sid, True)
    return {"series_id": sid, "archived": True}


@app.post("/api/books/{sid}/unarchive", dependencies=[Depends(auth)])
def unarchive(sid: int):
    if not store.book(sid):
        raise HTTPException(404, "unknown book")
    store.set_archived(sid, False)
    return {"series_id": sid, "archived": False}


@app.get("/api/books/{sid}/position", dependencies=[Depends(auth)])
def position(sid: int):
    if not store.book(sid):
        raise HTTPException(404, "unknown book")
    pos = library.position(sid)
    segs = store.segments(sid, pos.spine_index)
    offset_s = None
    if pos.seg_index is not None:
        row = next((s for s in segs if s.seg_index == pos.seg_index), None)
        offset_s = row.offset_s if row else None
    chapters = library.chapters(sid)
    return {
        "spine_index": pos.spine_index, "block_index": pos.block_index,
        "seg_index": pos.seg_index, "offset_s": offset_s, "exact": pos.exact,
        "chapter_title": chapters[pos.spine_index].title,
        "chapter_status": store.chapter_status(sid, pos.spine_index),
    }


@app.get("/api/books/{sid}/chapters/{spine}/index", dependencies=[Depends(auth)])
def chapter_index(sid: int, spine: int):
    chapters = library.chapters(sid)
    if spine >= len(chapters):
        raise HTTPException(404)
    engine.plan_chapter(sid, chapters[spine])
    segs = store.segments(sid, spine)
    return {
        "spine_index": spine, "title": chapters[spine].title,
        "status": store.chapter_status(sid, spine),
        "segments": [{
            "seg": s.seg_index, "block": s.block_index,
            "offset_s": s.offset_s, "duration_s": s.duration_s,
            "text": s.text[:120],
        } for s in segs],
        # full read-along text with inline emphasis preserved (§11 reader view)
        "blocks": [{
            "index": b.index, "tag": b.tag, "html": b.html,
        } for b in chapters[spine].blocks],
    }


# -- audio ---------------------------------------------------------------------------

@app.get("/api/audio/{sid}/{spine}.mp3", dependencies=[Depends(auth)])
def chapter_audio(sid: int, spine: int):
    """Assembled chapter file (iOS Range requests handled by FileResponse)."""
    path = store.chapter_audio_path(sid, cfg.default_voice, spine)
    if store.chapter_status(sid, spine) == "done" and os.path.exists(path):
        return FileResponse(path, media_type="audio/mpeg")
    raise HTTPException(404, "chapter not assembled — use /stream for cold start")


@app.get("/api/books/{sid}/stream", dependencies=[Depends(auth)])
def stream(sid: int, spine: int, from_seg: int = 0):
    """Cold path (§9.3): progressive MP3 from a segment onward, synthesizing live."""
    chapters = library.chapters(sid)
    if spine >= len(chapters):
        raise HTTPException(404)
    engine.plan_chapter(sid, chapters[spine])
    segs = store.segments(sid, spine)
    if not segs:
        raise HTTPException(404, "empty chapter")
    # ensure everything from here to chapter end is queued hot
    engine.enqueue_chapter(sid, chapters[spine], PRIORITY_LIVE, from_seg=from_seg)

    chapter_path = store.chapter_audio_path(sid, cfg.default_voice, spine)

    def gen():
        # assembled already? serve the byte range from the requested segment
        if store.chapter_status(sid, spine) == "done" and os.path.exists(chapter_path):
            start = next((s.byte_offset for s in store.segments(sid, spine)
                          if s.seg_index == from_seg), 0) or 0
            with open(chapter_path, "rb") as f:
                f.seek(start)
                while chunk := f.read(64 * 1024):
                    yield chunk
            return
        for s in segs:
            if s.seg_index < from_seg:
                continue
            path = engine.wait_for_segment(sid, spine, s.seg_index, timeout=60)
            if path is None:
                log.error("stream: segment %s/%s/%s never arrived", sid, spine, s.seg_index)
                return
            with open(path, "rb") as f:
                yield f.read()

    return StreamingResponse(gen(), media_type="audio/mpeg")


# -- progress write-back (§7.3) ---------------------------------------------------------

class PlayheadReport(BaseModel):
    spine_index: int
    block_index: int
    listened_since_skip_back: float | None = None


@app.post("/api/books/{sid}/progress", dependencies=[Depends(auth)])
def report_progress(sid: int, report: PlayheadReport):
    book = store.book(sid)
    if not book:
        raise HTTPException(404)
    # R6 tripwire: never write if pagination model doesn't hold for this book
    if book["pages"] != book["spine_count"]:
        raise HTTPException(409, "pages/spine mismatch — write-back disabled for this book")
    chapters = library.chapters(sid)
    ch = chapters[report.spine_index]
    if not ch.blocks:
        raise HTTPException(400, "no blocks in spine item")
    block = ch.blocks[min(report.block_index, len(ch.blocks) - 1)]

    current = kavita.get_progress(book["kavita_chapter_id"])
    proposed = Progress(
        volume_id=book["kavita_volume_id"], chapter_id=book["kavita_chapter_id"],
        page_num=report.spine_index, series_id=sid,
        library_id=book["kavita_library_id"],
        book_scroll_id=scroll_id_for_block(block))
    allowed, reason = may_write_back(
        current, proposed, now_utc=time.time(),
        kavita_last_modified_utc=_parse_kavita_utc(current.last_modified_utc),
        listened_since_skip_back=report.listened_since_skip_back)
    if not allowed:
        return {"written": False, "reason": reason}
    kavita.set_progress(proposed)
    store.log_progress_write(sid, current.to_dto(), proposed.to_dto())
    log.info("progress write %s: p%s %r → p%s %r", sid, current.page_num,
             current.book_scroll_id, proposed.page_num, proposed.book_scroll_id)
    return {"written": True, "reason": reason}


@app.post("/api/books/{sid}/undo-sync", dependencies=[Depends(auth)])
def undo_sync(sid: int):
    """R5: restore Kavita progress to the value before Echo's last write."""
    before = store.last_progress_write(sid)
    if not before:
        raise HTTPException(404, "no writes logged")
    kavita.set_progress(Progress.from_dto(before))
    return {"restored": before}


@app.delete("/api/books/{sid}/audio", dependencies=[Depends(auth)])
def delete_audio(sid: int):
    import shutil
    path = os.path.join(store.dir, "audio", str(sid))
    if os.path.exists(path):
        shutil.rmtree(path)
    return {"deleted": sid}


# -- PWA (must be last: catch-all static mount) -------------------------------------------

pwa_dir = os.path.join(os.path.dirname(__file__), "..", "pwa")
if os.path.isdir(pwa_dir):
    app.mount("/", StaticFiles(directory=pwa_dir, html=True), name="pwa")
