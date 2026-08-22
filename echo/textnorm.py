"""TTS text normalization (§8.4) — the layer iOS's screen reader lacks.

Rules: em/en dashes → comma-pause phrasing; smart quotes → plain; ellipses
preserved; soft hyphens and mid-word line breaks stripped; inline footnote
markers removed.
"""

from __future__ import annotations

import re

_SMART = {
    "‘": "'", "’": "'",          # single smart quotes
    "“": '"', "”": '"',          # double smart quotes
    "«": '"', "»": '"',          # guillemets
    "‹": "'", "›": "'",
}

_SOFT_HYPHEN = "­"
_NBSP = " "

# " — ", "—", " – " used parenthetically → comma pause
_DASH_MID = re.compile(r"\s*[—–]\s*")
# dash at clause end (before closing quote/end) — keep as period-ish pause
_DASH_TRAIL = re.compile(r"[—–]\s*$")

# [1], [23] style footnote markers
_FOOTNOTE_BRACKET = re.compile(r"\[\d{1,3}\]")
# mid-word line break with optional hyphen: "won-\nder" / "won\nder"
_MIDWORD_BREAK = re.compile(r"(\w)-?\n(\w)")

_ELLIPSIS = re.compile(r"\.{3,}")
_WS = re.compile(r"[ \t\r\f\v]+")


def normalize(text: str) -> str:
    """Normalize one block's text for synthesis. Idempotent."""
    t = text.replace(_SOFT_HYPHEN, "").replace(_NBSP, " ")
    for smart, plain in _SMART.items():
        t = t.replace(smart, plain)
    t = t.replace("…", "...")
    t = _MIDWORD_BREAK.sub(r"\1\2", t)
    t = t.replace("\n", " ")
    t = _DASH_TRAIL.sub(".", t)
    t = _DASH_MID.sub(", ", t)
    t = _FOOTNOTE_BRACKET.sub("", t)
    t = _ELLIPSIS.sub("...", t)
    t = _WS.sub(" ", t).strip()
    # collapse artifacts like ", ," from dash next to existing comma
    t = re.sub(r",\s*,", ",", t)
    t = re.sub(r"\s+([,.;:!?])", r"\1", t)
    return t
