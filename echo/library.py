"""Library manager: Kavita on-deck ⇄ local books, EPUB cache, position resolution."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .epub import Chapter, parse_epub
from .kavita import KavitaClient, Progress
from .mapper import resolve_scroll_id
from .store import Store

log = logging.getLogger(__name__)

ACTIVE_WINDOW_S = 48 * 3600


@dataclass
class Position:
    spine_index: int
    block_index: int
    seg_index: int | None
    exact: bool


def _parse_kavita_utc(ts: str | None) -> float:
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return 0.0


class Library:
    def __init__(self, store: Store, kavita: KavitaClient):
        self.store = store
        self.kavita = kavita
        self._parsed: dict[int, list[Chapter]] = {}
        self._lock = threading.Lock()

    # -- sync -----------------------------------------------------------------

    def refresh(self) -> list[dict]:
        """Sync on-deck series into the store. Returns book rows."""
        for series in self.kavita.on_deck():
            try:
                self._sync_book(series)
            except Exception:
                log.exception("sync failed for series %s", series.get("id"))
        return self.store.books()

    def _sync_book(self, series: dict) -> None:
        sid = series["id"]
        chs = self.kavita.chapters(sid)
        if not chs:
            return
        vols = self.kavita.volumes(sid)
        ch_meta = chs[0]
        epub_path = self.store.epub_path(sid)
        if not os.path.exists(epub_path):
            log.info("downloading EPUB for %s (%s)", series.get("name"), sid)
            data = self.kavita.download_epub(sid, ch_meta["id"])
            with open(epub_path, "wb") as f:
                f.write(data)
        chapters = self.chapters(sid)
        pages = ch_meta.get("pages") or 0
        if pages and pages != len(chapters):
            log.error("R6 TRIPWIRE %s: Kavita pages=%s but spine=%s — "
                      "write-back will be blocked", sid, pages, len(chapters))
        self.store.upsert_book(
            series_id=sid, title=series.get("name", f"series {sid}"),
            volume_id=vols[0]["id"], chapter_id=ch_meta["id"],
            library_id=series.get("libraryId", 0), pages=pages,
            spine_count=len(chapters), epub_mtime=os.path.getmtime(epub_path))

    # -- parsed chapters --------------------------------------------------------

    def chapters(self, series_id: int) -> list[Chapter]:
        with self._lock:
            if series_id in self._parsed:
                return self._parsed[series_id]
        path = self.store.epub_path(series_id)
        with open(path, "rb") as f:
            chapters = parse_epub(f.read())
        with self._lock:
            self._parsed[series_id] = chapters
        return chapters

    # -- position ----------------------------------------------------------------

    def progress(self, series_id: int) -> Progress:
        book = self.store.book(series_id)
        if not book:
            raise KeyError(f"unknown book {series_id}")
        return self.kavita.get_progress(book["kavita_chapter_id"])

    def position(self, series_id: int) -> Position:
        """Kavita progress → (spine, block, seg)."""
        prog = self.progress(series_id)
        chapters = self.chapters(series_id)
        spine = min(max(prog.page_num, 0), len(chapters) - 1)
        ch = chapters[spine]
        if not ch.blocks:
            return Position(spine, 0, None, exact=False)
        pos = resolve_scroll_id(prog.book_scroll_id, ch.blocks, ch.ids, ch.id_xpaths)
        seg = None
        segs = self.store.segments(series_id, spine)
        if segs:
            seg = next((s.seg_index for s in segs
                        if s.block_index >= pos.block_index), segs[-1].seg_index)
        return Position(spine, pos.block_index, seg, exact=pos.exact)

    def is_active(self, series_id: int) -> bool:
        """Progress changed within the activity window (§9.3)."""
        try:
            prog = self.progress(series_id)
        except Exception:
            return False
        return time.time() - _parse_kavita_utc(prog.last_modified_utc) < ACTIVE_WINDOW_S
