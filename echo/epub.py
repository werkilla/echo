"""EPUB text pipeline (§8): parse spine → ordered blocks → sentences.

The Block table (index, text, xpath, char offsets) is the shared coordinate
system between position mapping (§7.3) and the audio pipeline (§9).
"""

from __future__ import annotations

import io
import logging
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from html import escape as _esc
from urllib.parse import unquote

log = logging.getLogger(__name__)

from lxml import etree, html as lhtml

from .textnorm import normalize

# Block-level elements we read, in document order (§8.4).
BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "div"}
# Subtrees we skip entirely
SKIP_TAGS = {"table", "figure", "img", "svg", "pre", "code", "aside", "script", "style", "nav"}
# Inline elements stripped but text kept — except sup/sub footnote markers, dropped
DROP_INLINE = {"sup", "sub"}
# Inline emphasis kept in the read-along display HTML (mapped to em/strong so the
# PWA only styles two tags). Everything else is unwrapped, text preserved.
_EMPHASIS = {"em": "em", "i": "em", "strong": "strong", "b": "strong"}

_XHTML_NS = "{http://www.w3.org/1999/xhtml}"


@dataclass
class Block:
    index: int                 # 0-based paragraph index within the chapter
    text: str                  # normalized, TTS-ready
    raw_text: str              # pre-normalization (for debugging/metrics)
    xpath: str                 # Kavita-style descoped XPath, e.g. //body/p[5]
    char_start: int            # cumulative char offset of block start (raw chapter)
    char_end: int
    tag: str = "p"
    html: str = ""             # read-along display HTML: em/strong kept, text escaped


@dataclass
class Chapter:
    spine_index: int
    href: str                  # spine item href inside the EPUB
    title: str | None
    blocks: list[Block] = field(default_factory=list)
    ids: dict[str, int] = field(default_factory=dict)  # element id → block index
    id_xpaths: dict[str, str] = field(default_factory=dict)  # element id → its descoped XPath

    @property
    def total_chars(self) -> int:
        return self.blocks[-1].char_end if self.blocks else 0


@dataclass
class Sentence:
    """Synthesis/cache unit: one sentence merged to a 15–280 char window."""
    block_index: int
    seg_index: int             # index within the chapter's sentence list
    text: str


# -- EPUB parsing -------------------------------------------------------------


def _local(tag) -> str:
    """lxml tag → local name, namespace stripped."""
    if not isinstance(tag, str):
        return ""  # comments / PIs
    return tag.rsplit("}", 1)[-1].lower()


class Epub:
    """Minimal, robust EPUB reader: container.xml → OPF → spine order."""

    def __init__(self, data: bytes):
        self.zf = zipfile.ZipFile(io.BytesIO(data))
        opf_path = self._zip_name(self._opf_path())
        self.opf_dir = opf_path.rsplit("/", 1)[0] + "/" if "/" in opf_path else ""
        opf = etree.fromstring(self.zf.read(opf_path))
        ns = {"o": "http://www.idpf.org/2007/opf"}
        manifest = {
            item.get("id"): item.get("href")
            for item in opf.findall(".//o:manifest/o:item", ns)
        }
        media = {
            item.get("id"): item.get("media-type", "")
            for item in opf.findall(".//o:manifest/o:item", ns)
        }
        self.spine_hrefs: list[str] = [
            manifest[ref.get("idref")]
            for ref in opf.findall(".//o:spine/o:itemref", ns)
            if ref.get("idref") in manifest
            and "html" in media.get(ref.get("idref"), "")
        ]

    def _opf_path(self) -> str:
        container = etree.fromstring(self.zf.read("META-INF/container.xml"))
        ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
        rootfile = container.find(".//c:rootfile", ns)
        full_path = rootfile.get("full-path") if rootfile is not None else None
        if not full_path:
            raise ValueError("EPUB container.xml has no rootfile full-path")
        return full_path

    def chapter_html(self, href: str) -> bytes:
        return self.zf.read(self._zip_name(self.opf_dir + href))

    def _zip_name(self, name: str) -> str:
        """Resolve an OPF/container path to an actual zip entry name.

        Real-world EPUBs diverge from a naive `zf.read(dir + href)` two ways:
        - hrefs are URI-encoded per the spec (spaces as %20, "'" as %27) but zip
          entries store the literal decoded filename — an encoded lookup KeyErrors
          on any book whose internal files contain spaces or punctuation;
        - hrefs may be relative with "../" (OPF in a subdir), which zipfile does
          not normalize, so the raw joined path is absent from the archive.
        Try the plausible spellings in order and return the first that exists;
        fall back to the original so zipfile raises its own clear KeyError."""
        candidates = [name, posixpath.normpath(name)]
        candidates += [unquote(c) for c in list(candidates)]
        names = set(self.zf.namelist())
        for cand in candidates:
            if cand in names:
                return cand
        return name


# -- Block extraction ----------------------------------------------------------


def _element_text(el, drop_inline=DROP_INLINE) -> str:
    """Text content, dropping footnote-marker inline elements (sup/sub)."""
    parts: list[str] = []

    def walk(node):
        tag = _local(node.tag)
        if tag in drop_inline or tag in SKIP_TAGS:
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(el)
    return "".join(parts)


def _element_html(el) -> str:
    """Read-along display HTML for one block: keep em/strong emphasis, escape all
    text, drop footnote markers and skipped subtrees. Original typography (smart
    quotes, em dashes) is preserved — this is for the eye, not the TTS engine.
    """
    parts: list[str] = []

    def walk(node):
        tag = _local(node.tag)
        if tag in DROP_INLINE or tag in SKIP_TAGS:
            return
        emph = _EMPHASIS.get(tag) if node is not el else None
        if emph:
            parts.append(f"<{emph}>")
        if node.text:
            parts.append(_esc(node.text, quote=False))
        for child in node:
            walk(child)
            if child.tail:
                parts.append(_esc(child.tail, quote=False))
        if emph:
            parts.append(f"</{emph}>")

    walk(el)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _kavita_xpath(el, body) -> str:
    """Kavita-style descoped XPath: //body/div[1]/p[5].

    Each step is tag[i] with i = 1-based index among same-tag siblings,
    from (but not including) <body> down to the element.
    """
    steps: list[str] = []
    node = el
    while node is not None and node is not body:
        parent = node.getparent()
        if parent is None:
            break
        tag = _local(node.tag)
        same = [s for s in parent if _local(s.tag) == tag]
        steps.append(f"{tag}[{same.index(node) + 1}]")
        node = parent
    return "//body/" + "/".join(reversed(steps))


def _parse_chapter_html(data: bytes):
    """Encoding-aware parse: honor the XML declaration, default UTF-8.

    (Bug found live: praise.xhtml parsed as Latin-1 → mojibake smart quotes.)
    """
    m = re.search(rb'encoding=["\']([A-Za-z0-9_.-]+)["\']', data[:200])
    enc = m.group(1).decode("ascii") if m else "utf-8"
    parser = lhtml.HTMLParser(encoding=enc)
    # document_fromstring: always returns a full <html> tree (fromstring may
    # guess "fragment" on minimal docs and return a bare element)
    return lhtml.document_fromstring(data, parser=parser)


def extract_blocks(chapter_html: bytes) -> list[Block]:
    """Ordered Block list for one spine item (§8.3)."""
    return extract_blocks_and_ids(chapter_html)[0]


def extract_blocks_and_ids(
    chapter_html: bytes,
) -> tuple[list[Block], dict[str, int], dict[str, str]]:
    """Blocks + element-id → block-index map + element-id → its own descoped XPath.

    Kavita anchors by id() when the top-visible element has an id (found live on
    ACOMAF chapter headings). It also emits a *compound* form, id("X")/rel/path,
    when the id sits on a wrapper element (seen live on 0.9.1 for books whose
    paragraphs are nested in id'd divs) — the id_xpaths map lets the mapper expand
    that back to an absolute XPath. See mapper.resolve_scroll_id."""
    if not chapter_html or not chapter_html.strip():
        return [], {}, {}  # empty spine file (seen live): no blocks, not a crash
    root = _parse_chapter_html(chapter_html)
    body = root.find("body")
    if body is None:  # some EPUBs are XHTML-namespaced; lhtml usually strips, but be safe
        for el in root.iter():
            if _local(el.tag) == "body":
                body = el
                break
    if body is None:
        return [], {}, {}

    blocks: list[Block] = []
    ids: dict[str, int] = {}
    id_xpaths: dict[str, str] = {}
    last_block_el = None
    offset = 0
    skip_depth_elems: set = set()

    for el in body.iter():
        tag = _local(el.tag)
        el_id = el.get("id") if isinstance(el.tag, str) else None
        if el_id and el_id not in ids:
            # id on/inside the last appended block → that block; otherwise the
            # next block to be appended (headings, wrapper divs, the block itself)
            inside_last = last_block_el is not None and (
                el is last_block_el or last_block_el in set(el.iterancestors()))
            ids[el_id] = len(blocks) - 1 if inside_last else len(blocks)
            id_xpaths[el_id] = _kavita_xpath(el, body)
        if tag in SKIP_TAGS:
            skip_depth_elems.update(el.iterdescendants())
            skip_depth_elems.add(el)
            continue
        if el in skip_depth_elems or tag not in BLOCK_TAGS:
            continue
        # nested blocks (p inside blockquote/li): only take the innermost —
        # skip if this element contains another block element
        if any(_local(d.tag) in BLOCK_TAGS for d in el.iterdescendants()):
            continue
        raw = _element_text(el)
        raw_stripped = re.sub(r"\s+", " ", raw).strip()
        if not raw_stripped:
            continue
        text = normalize(raw)
        if not text:
            continue
        blocks.append(Block(
            index=len(blocks),
            text=text,
            raw_text=raw_stripped,
            xpath=_kavita_xpath(el, body),
            char_start=offset,
            char_end=offset + len(raw_stripped),
            tag=tag,
            html=_element_html(el),
        ))
        last_block_el = el
        offset += len(raw_stripped) + 1

    if blocks:  # clamp trailing ids (id after the last block)
        ids = {k: min(v, len(blocks) - 1) for k, v in ids.items()}
    else:
        ids, id_xpaths = {}, {}
    return blocks, ids, id_xpaths


def parse_epub(data: bytes) -> list[Chapter]:
    """Full pipeline: EPUB bytes → chapters (spine order) with blocks."""
    epub = Epub(data)
    chapters: list[Chapter] = []
    for i, href in enumerate(epub.spine_hrefs):
        try:
            blocks, ids, id_xpaths = extract_blocks_and_ids(epub.chapter_html(href))
        except Exception:
            # One malformed/missing spine item must not lose the whole book.
            # Emit an empty chapter so spine_index stays aligned with Kavita's
            # page model (position write-back is keyed on spine_index).
            log.exception("epub: skipping unreadable spine item %s (%s)", i, href)
            blocks, ids, id_xpaths = [], {}, {}
        title = None
        for b in blocks:
            if b.tag.startswith("h"):
                title = b.text
                break
        chapters.append(Chapter(spine_index=i, href=href, title=title,
                                blocks=blocks, ids=ids, id_xpaths=id_xpaths))
    return chapters


# -- Sentence segmentation (§8.5) ----------------------------------------------

MIN_SEG = 15
MAX_SEG = 280

_CLAUSE_SPLIT = re.compile(r"(?<=[,;:])\s+")


def _split_long(text: str) -> list[str]:
    """Split a monster sentence on clause boundaries into ≤ MAX_SEG chunks."""
    if len(text) <= MAX_SEG:
        return [text]
    parts, cur = [], ""
    for clause in _CLAUSE_SPLIT.split(text):
        if cur and len(cur) + len(clause) + 1 > MAX_SEG:
            parts.append(cur)
            cur = clause
        else:
            cur = f"{cur} {clause}".strip()
    if cur:
        parts.append(cur)
    # hard-wrap anything still oversized (no clause boundaries at all)
    out: list[str] = []
    for p in parts:
        while len(p) > MAX_SEG:
            cut = p.rfind(" ", 0, MAX_SEG)
            cut = cut if cut > 0 else MAX_SEG
            out.append(p[:cut])
            p = p[cut:].strip()
        out.append(p)
    return [p for p in out if p]


def segment_chapter(blocks: list[Block]) -> list[Sentence]:
    """Blocks → merged sentence windows (15–280 chars), the synthesis unit."""
    try:
        import pysbd
        seg = pysbd.Segmenter(language="en", clean=False)
        split = seg.segment
    except ImportError:  # fallback: naive splitter
        naive = re.compile(r"(?<=[.!?])\s+(?=[\"'A-Z0-9])")
        split = lambda t: naive.split(t)  # noqa: E731

    # 1) per-block sentence split (+ monster-sentence clause split)
    prelim: list[tuple[int, str]] = []  # (block_index, text)
    for block in blocks:
        for s in split(block.text):
            s = s.strip()
            if s:
                prelim.extend((block.index, part) for part in _split_long(s))

    # 2) merge tiny fragments FORWARD across block boundaries (§8.5) — one-word
    # dialogue paragraphs ("No.", "What?") join the next sentence; the merged
    # segment keeps the FIRST block's index (slight overlap aids re-entry, D3)
    merged: list[tuple[int, str]] = []
    i = 0
    while i < len(prelim):
        bi, text = prelim[i]
        while len(text) < MIN_SEG and i + 1 < len(prelim) \
                and len(text) + len(prelim[i + 1][1]) + 1 <= MAX_SEG:
            i += 1
            text = f"{text} {prelim[i][1]}"
        if len(text) < MIN_SEG and merged \
                and len(merged[-1][1]) + len(text) + 1 <= MAX_SEG:
            # trailing tiny fragment: merge backward as last resort
            pbi, ptext = merged[-1]
            merged[-1] = (pbi, f"{ptext} {text}")
        else:
            merged.append((bi, text))
        i += 1

    return [Sentence(block_index=bi, seg_index=n, text=t)
            for n, (bi, t) in enumerate(merged)]
