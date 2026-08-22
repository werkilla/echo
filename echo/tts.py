"""Kokoro TTS client (OpenAI-compatible) + MP3 duration."""

from __future__ import annotations

import io
import logging

import requests
from mutagen.mp3 import MP3

log = logging.getLogger(__name__)


class TTSError(RuntimeError):
    pass


class KokoroClient:
    def __init__(self, base_url: str, voice: str, timeout: int = 120):
        self.base = base_url.rstrip("/")
        self.voice = voice
        self.timeout = timeout

    def synthesize(self, text: str) -> bytes:
        r = requests.post(
            f"{self.base}/v1/audio/speech",
            json={"model": "kokoro", "input": text, "voice": self.voice,
                  "response_format": "mp3", "speed": 1.0},
            timeout=self.timeout)
        if not r.ok:
            raise TTSError(f"kokoro HTTP {r.status_code}: {r.text[:200]}")
        if not r.content:
            raise TTSError("kokoro returned empty audio")
        return r.content

    def healthy(self) -> bool:
        try:
            return requests.get(f"{self.base}/health", timeout=5).ok
        except requests.RequestException:
            return False


def mp3_duration(data: bytes) -> float:
    """Accurate duration of an MP3 payload (mutagen frame scan)."""
    return MP3(io.BytesIO(data)).info.length
