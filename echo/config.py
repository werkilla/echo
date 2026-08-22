"""Configuration from environment (.env is loaded by docker compose / Makefile)."""

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


@dataclass(frozen=True)
class Config:
    kavita_url: str = field(default_factory=lambda: _env("KAVITA_URL").rstrip("/"))
    kavita_api_key: str = field(default_factory=lambda: _env("KAVITA_API_KEY"))
    echo_token: str = field(default_factory=lambda: _env("ECHO_TOKEN", "dev-token"))
    kokoro_url: str = field(default_factory=lambda: _env("KOKORO_URL", "http://kokoro:8880").rstrip("/"))
    audio_max_gb: float = field(default_factory=lambda: float(_env("AUDIO_MAX_GB", "50")))
    poll_minutes: int = field(default_factory=lambda: int(_env("POLL_MINUTES", "5")))
    default_voice: str = field(default_factory=lambda: _env("DEFAULT_VOICE", "af_heart"))
    config_dir: str = field(default_factory=lambda: _env("CONFIG_DIR", "/config"))


def load() -> Config:
    return Config()
