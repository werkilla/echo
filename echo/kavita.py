"""Kavita API client.

Contract verified against live Kavita 0.9.0.2 (spikes/03-kavita-contract).
Echo is a pure API consumer: auth via API key → JWT, refresh on 401.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

log = logging.getLogger(__name__)


@dataclass
class Progress:
    """Mirror of Kavita's ProgressDto (the fields Echo uses)."""

    volume_id: int
    chapter_id: int
    page_num: int
    series_id: int
    library_id: int
    book_scroll_id: str | None
    last_modified_utc: str | None = None

    @classmethod
    def from_dto(cls, d: dict[str, Any]) -> "Progress":
        return cls(
            volume_id=d["volumeId"],
            chapter_id=d["chapterId"],
            page_num=d.get("pageNum", 0),
            series_id=d["seriesId"],
            library_id=d["libraryId"],
            book_scroll_id=d.get("bookScrollId"),
            last_modified_utc=d.get("lastModifiedUtc"),
        )

    def to_dto(self) -> dict[str, Any]:
        return {
            "volumeId": self.volume_id,
            "chapterId": self.chapter_id,
            "pageNum": self.page_num,
            "seriesId": self.series_id,
            "libraryId": self.library_id,
            "bookScrollId": self.book_scroll_id,
        }


class KavitaError(RuntimeError):
    pass


class KavitaClient:
    def __init__(self, base_url: str, api_key: str, timeout: int = 30):
        self.base = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout = timeout
        self._session = requests.Session()
        self._token: str | None = None

    # -- auth ---------------------------------------------------------------

    def _authenticate(self) -> None:
        try:
            r = self._session.post(
                f"{self.base}/api/Plugin/authenticate",
                params={"apiKey": self._api_key, "pluginName": "Echo"},
                timeout=self.timeout,
            )
        except requests.RequestException as e:
            # never propagate the original exception — its URL contains the API key
            raise KavitaError(
                f"Kavita unreachable at {self.base}: {type(e.__cause__ or e).__name__}"
            ) from None
        if not r.ok:
            raise KavitaError(f"Kavita auth failed: HTTP {r.status_code}")
        self._token = r.json()["token"]
        self._session.headers["Authorization"] = f"Bearer {self._token}"
        log.info("kavita auth ok")

    def _request(self, method: str, path: str, retry: bool = True, **kw) -> requests.Response:
        if self._token is None:
            self._authenticate()
        kw.setdefault("timeout", self.timeout)
        r = self._session.request(method, f"{self.base}{path}", **kw)
        if r.status_code == 401 and retry:
            log.info("kavita 401 — refreshing JWT")
            self._token = None
            return self._request(method, path, retry=False, **kw)
        return r

    def _json(self, method: str, path: str, **kw) -> Any:
        r = self._request(method, path, **kw)
        if not r.ok:
            raise KavitaError(f"{method} {path} → HTTP {r.status_code}: {r.text[:200]}")
        return r.json() if r.text else None

    # -- library ------------------------------------------------------------

    def on_deck(self) -> list[dict]:
        """Series with recent reading activity (active-book detection)."""
        try:
            return self._json("POST", "/api/Series/on-deck",
                              params={"libraryId": 0}, json={}) or []
        except KavitaError:
            # older shape
            return self._json("GET", "/api/Series/on-deck") or []

    def series(self, series_id: int) -> dict:
        return self._json("GET", f"/api/Series/{series_id}")

    def volumes(self, series_id: int) -> list[dict]:
        return self._json("GET", "/api/Series/volumes",
                          params={"seriesId": series_id}) or []

    def chapters(self, series_id: int) -> list[dict]:
        """Flattened chapters across volumes, in reading order."""
        return [c for v in self.volumes(series_id) for c in v.get("chapters", [])]

    # -- progress -----------------------------------------------------------

    def get_progress(self, chapter_id: int) -> Progress:
        return Progress.from_dto(self._json(
            "GET", "/api/Reader/get-progress", params={"chapterId": chapter_id}))

    def set_progress(self, progress: Progress) -> None:
        r = self._request("POST", "/api/Reader/progress", json=progress.to_dto())
        if not r.ok:
            raise KavitaError(f"progress write failed: HTTP {r.status_code}: {r.text[:200]}")

    # -- files --------------------------------------------------------------

    def download_epub(self, series_id: int, chapter_id: int) -> bytes:
        """Kavita returns the underlying EPUB for the chapter's volume."""
        r = self._request("GET", "/api/Download/chapter",
                          params={"chapterId": chapter_id, "seriesId": series_id},
                          stream=True)
        if not r.ok:
            raise KavitaError(f"epub download failed: HTTP {r.status_code}")
        return r.content

    def healthy(self) -> bool:
        try:
            self._json("GET", "/api/Server/server-info-slim")
            return True
        except Exception:
            return False


# -- write-back safety (§7.3) ----------------------------------------------

READER_WINS_SECONDS = 120
SKIP_BACK_MIN_LISTEN = 60


def may_write_back(
    current: Progress,
    proposed: Progress,
    now_utc: float,
    kavita_last_modified_utc: float,
    listened_since_skip_back: float | None = None,
) -> tuple[bool, str]:
    """Apply §7.3 write-back safety rules. Returns (allowed, reason)."""
    # Rule: reader wins — if Kavita progress changed recently from another source
    if now_utc - kavita_last_modified_utc < READER_WINS_SECONDS:
        return False, "reader-active: Kavita progress changed in the last 2 min"
    # Rule: never move backwards unless an explicit skip-back was listened through
    if proposed.chapter_id == current.chapter_id and proposed.page_num < current.page_num:
        if listened_since_skip_back is None or listened_since_skip_back < SKIP_BACK_MIN_LISTEN:
            return False, "would move progress backwards without sustained listen"
    return True, "ok"
