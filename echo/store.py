"""SQLite index for the audio store (§9.2).

All of this is rebuildable bookkeeping — reading progress lives in Kavita.
Layout on disk:
    {config}/echo.db
    {config}/epubs/{series_id}.epub
    {config}/audio/{series_id}/{voice}/{spine:04d}.mp3          (assembled chapters)
    {config}/audio/{series_id}/{voice}/segs/{spine}/{seg:05d}.mp3 (pre-assembly)
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
  series_id INTEGER PRIMARY KEY,
  title TEXT,
  kavita_volume_id INTEGER,
  kavita_chapter_id INTEGER,
  kavita_library_id INTEGER,
  pages INTEGER,               -- Kavita totalPages == spine count (R6 tripwire)
  spine_count INTEGER,
  epub_mtime REAL,
  updated_at REAL,
  archived INTEGER DEFAULT 0   -- hidden from the library + skipped by the scheduler
);
CREATE TABLE IF NOT EXISTS chapters (
  series_id INTEGER,
  spine_index INTEGER,
  status TEXT DEFAULT 'pending',   -- pending | synthesizing | done | empty
  seg_count INTEGER DEFAULT 0,
  duration_s REAL DEFAULT 0,
  PRIMARY KEY (series_id, spine_index)
);
CREATE TABLE IF NOT EXISTS segments (
  series_id INTEGER,
  spine_index INTEGER,
  seg_index INTEGER,
  block_index INTEGER,
  text TEXT,
  duration_s REAL,             -- NULL until synthesized
  offset_s REAL,               -- NULL until chapter assembled
  byte_offset INTEGER,         -- NULL until chapter assembled
  PRIMARY KEY (series_id, spine_index, seg_index)
);
CREATE TABLE IF NOT EXISTS progress_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL,
  series_id INTEGER,
  before_json TEXT,
  after_json TEXT
);
"""


@dataclass
class SegRow:
    seg_index: int
    block_index: int
    text: str
    duration_s: float | None
    offset_s: float | None
    byte_offset: int | None


class Store:
    def __init__(self, config_dir: str):
        self.dir = config_dir
        os.makedirs(config_dir, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            os.path.join(config_dir, "echo.db"), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._migrate()
            self._db.commit()

    def _migrate(self) -> None:
        """Add columns to pre-existing DBs (CREATE TABLE IF NOT EXISTS won't)."""
        cols = {r["name"] for r in self._db.execute("PRAGMA table_info(books)")}
        if "archived" not in cols:
            self._db.execute("ALTER TABLE books ADD COLUMN archived INTEGER DEFAULT 0")

    # -- paths ---------------------------------------------------------------

    def epub_path(self, series_id: int) -> str:
        p = os.path.join(self.dir, "epubs")
        os.makedirs(p, exist_ok=True)
        return os.path.join(p, f"{series_id}.epub")

    def chapter_audio_path(self, series_id: int, voice: str, spine: int) -> str:
        p = os.path.join(self.dir, "audio", str(series_id), voice)
        os.makedirs(p, exist_ok=True)
        return os.path.join(p, f"{spine:04d}.mp3")

    def seg_audio_path(self, series_id: int, voice: str, spine: int, seg: int) -> str:
        p = os.path.join(self.dir, "audio", str(series_id), voice, "segs", str(spine))
        os.makedirs(p, exist_ok=True)
        return os.path.join(p, f"{seg:05d}.mp3")

    # -- books ----------------------------------------------------------------

    def upsert_book(self, series_id: int, title: str, volume_id: int,
                    chapter_id: int, library_id: int, pages: int,
                    spine_count: int, epub_mtime: float) -> None:
        with self._lock:
            # archived is intentionally omitted — a Kavita refresh must not
            # resurrect a book the user archived (it stays 0 on first insert).
            self._db.execute(
                """INSERT INTO books (series_id, title, kavita_volume_id,
                     kavita_chapter_id, kavita_library_id, pages, spine_count,
                     epub_mtime, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(series_id) DO UPDATE SET title=excluded.title,
                     kavita_volume_id=excluded.kavita_volume_id,
                     kavita_chapter_id=excluded.kavita_chapter_id,
                     kavita_library_id=excluded.kavita_library_id,
                     pages=excluded.pages, spine_count=excluded.spine_count,
                     epub_mtime=excluded.epub_mtime, updated_at=excluded.updated_at""",
                (series_id, title, volume_id, chapter_id, library_id,
                 pages, spine_count, epub_mtime, time.time()))
            self._db.commit()

    def set_archived(self, series_id: int, archived: bool) -> None:
        with self._lock:
            self._db.execute("UPDATE books SET archived=? WHERE series_id=?",
                             (1 if archived else 0, series_id))
            self._db.commit()

    def is_archived(self, series_id: int) -> bool:
        b = self.book(series_id)
        return bool(b and b.get("archived"))

    def book(self, series_id: int) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM books WHERE series_id=?",
                                 (series_id,)).fetchone()
        return dict(r) if r else None

    def books(self, archived: bool | None = False) -> list[dict]:
        """archived=False → active library (default); True → archive; None → all."""
        sql = "SELECT * FROM books"
        params: tuple = ()
        if archived is not None:
            sql += " WHERE archived=?"
            params = (1 if archived else 0,)
        sql += " ORDER BY updated_at DESC"
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, params)]

    # -- chapters / segments ---------------------------------------------------

    def chapter_status(self, series_id: int, spine: int) -> str:
        with self._lock:
            r = self._db.execute(
                "SELECT status FROM chapters WHERE series_id=? AND spine_index=?",
                (series_id, spine)).fetchone()
        return r["status"] if r else "pending"

    def set_chapter(self, series_id: int, spine: int, status: str,
                    seg_count: int | None = None, duration_s: float | None = None):
        with self._lock:
            self._db.execute(
                """INSERT INTO chapters (series_id, spine_index, status)
                   VALUES (?,?,?)
                   ON CONFLICT(series_id, spine_index) DO UPDATE SET status=excluded.status""",
                (series_id, spine, status))
            if seg_count is not None:
                self._db.execute(
                    "UPDATE chapters SET seg_count=? WHERE series_id=? AND spine_index=?",
                    (seg_count, series_id, spine))
            if duration_s is not None:
                self._db.execute(
                    "UPDATE chapters SET duration_s=? WHERE series_id=? AND spine_index=?",
                    (duration_s, series_id, spine))
            self._db.commit()

    def chapters_done(self, series_id: int) -> int:
        with self._lock:
            r = self._db.execute(
                "SELECT COUNT(*) n FROM chapters WHERE series_id=? AND status IN ('done','empty')",
                (series_id,)).fetchone()
        return r["n"]

    def ensure_segments(self, series_id: int, spine: int,
                        segs: list[tuple[int, int, str]]) -> None:
        """Persist the segment plan (seg_index, block_index, text) once."""
        with self._lock:
            self._db.executemany(
                """INSERT OR IGNORE INTO segments
                   (series_id, spine_index, seg_index, block_index, text)
                   VALUES (?,?,?,?,?)""",
                [(series_id, spine, s, b, t) for s, b, t in segs])
            self._db.commit()

    def segments(self, series_id: int, spine: int) -> list[SegRow]:
        with self._lock:
            rows = self._db.execute(
                """SELECT seg_index, block_index, text, duration_s, offset_s, byte_offset
                   FROM segments WHERE series_id=? AND spine_index=? ORDER BY seg_index""",
                (series_id, spine)).fetchall()
        return [SegRow(**dict(r)) for r in rows]

    def set_seg_duration(self, series_id: int, spine: int, seg: int, duration_s: float):
        with self._lock:
            self._db.execute(
                """UPDATE segments SET duration_s=? WHERE series_id=? AND spine_index=?
                   AND seg_index=?""", (duration_s, series_id, spine, seg))
            self._db.commit()

    def finalize_offsets(self, series_id: int, spine: int,
                         offsets: list[tuple[int, float, int]]) -> None:
        """(seg_index, offset_s, byte_offset) after assembly."""
        with self._lock:
            self._db.executemany(
                """UPDATE segments SET offset_s=?, byte_offset=?
                   WHERE series_id=? AND spine_index=? AND seg_index=?""",
                [(off, boff, series_id, spine, seg) for seg, off, boff in offsets])
            self._db.commit()

    # -- progress log (R5: undo last sync) --------------------------------------

    def log_progress_write(self, series_id: int, before: dict, after: dict) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO progress_log (ts, series_id, before_json, after_json) VALUES (?,?,?,?)",
                (time.time(), series_id, json.dumps(before), json.dumps(after)))
            self._db.commit()

    def last_progress_write(self, series_id: int) -> dict | None:
        with self._lock:
            r = self._db.execute(
                """SELECT before_json FROM progress_log WHERE series_id=?
                   ORDER BY id DESC LIMIT 1""", (series_id,)).fetchone()
        return json.loads(r["before_json"]) if r else None

    def audio_bytes_total(self) -> int:
        total = 0
        audio_root = os.path.join(self.dir, "audio")
        for root, _, files in os.walk(audio_root):
            total += sum(os.path.getsize(os.path.join(root, f)) for f in files)
        return total
