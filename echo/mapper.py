"""Position mapper (§7.3): Kavita bookScrollId (XPath) ⇄ paragraph index.

Kavita's EPUB position model (verified live on Kavita 0.9.0.2, 2026-07-17):
  - `pageNum` IS the 0-based spine index (UI shows pageNum+1)
  - `bookScrollId` is a descoped XPath WITHIN that spine document

Read direction:  spine item = pageNum → resolve bookScrollId within it.
Write direction: pageNum = block's spine index, bookScrollId = block xpath.

Every fallback is logged — the fallback rate is a mapper-health metric (R3).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from .epub import Block

log = logging.getLogger(__name__)

_STEP = re.compile(r"([a-zA-Z][\w-]*)\[(\d+)\]|([a-zA-Z][\w-]*)")


@dataclass
class ResolvedPosition:
    block_index: int
    exact: bool          # False → proportional fallback was used
    reason: str = "xpath"


def _normalize_xpath(xp: str) -> str:
    """Canonical form: lowercase tags, explicit [1] indices, //body/ prefix."""
    xp = xp.strip()
    xp = re.sub(r"^/+", "", xp)
    parts = [p for p in xp.split("/") if p]
    if parts and parts[0].lower().startswith("body"):
        parts = parts[1:]
    norm: list[str] = []
    for part in parts:
        m = _STEP.fullmatch(part)
        if not m:
            return ""  # unparseable
        if m.group(1):
            norm.append(f"{m.group(1).lower()}[{m.group(2)}]")
        else:
            norm.append(f"{m.group(3).lower()}[1]")
    return "//body/" + "/".join(norm)


_ID_FORM = re.compile(r"""^id\(\s*["']([^"']+)["']\s*\)\s*$""")


def resolve_scroll_id(
    book_scroll_id: str | None,
    blocks: list[Block],
    ids: dict[str, int] | None = None,
) -> ResolvedPosition:
    """bookScrollId → block index WITHIN the spine item pageNum points at.

    Kavita stores two anchor dialects (both seen live on 0.9.0.2):
      - descoped path XPath: //body/p[7]
      - id() function form:  id("ch48")  — when the element has an id attribute

    Fallback: start of the item (pageNum already located the document, so
    worst case is re-hearing the top of the current 'page' — D3 overlap).
    """
    if blocks and book_scroll_id:
        m = _ID_FORM.match(book_scroll_id.strip())
        if m:
            if ids and m.group(1) in ids:
                return ResolvedPosition(ids[m.group(1)], exact=True, reason="id")
            log.warning("mapper: id() anchor %r not in id map", book_scroll_id)
    if blocks and book_scroll_id and not _ID_FORM.match(book_scroll_id.strip()):
        target = _normalize_xpath(book_scroll_id)
        if target:
            index = {_normalize_xpath(b.xpath): b.index for b in blocks}
            # exact hit
            if target in index:
                return ResolvedPosition(index[target], exact=True)
            # anchor may point at a child of a block (span/em inside a p) —
            # nearest enclosing block = longest block xpath that prefixes target
            best_idx, best_len = None, -1
            for xp, idx in index.items():
                if target.startswith(xp + "/") and len(xp) > best_len:
                    best_idx, best_len = idx, len(xp)
            if best_idx is not None:
                return ResolvedPosition(best_idx, exact=True, reason="xpath-enclosing")
            # nearest following block: first block whose xpath sorts after target
            # within the same parent chain — approximate by document order compare
            following = _first_following(target, blocks)
            if following is not None:
                return ResolvedPosition(following, exact=True, reason="xpath-following")

    # Fallback: start of the spine item (pageNum already located the document)
    log.warning("mapper fallback: item-start (scrollId=%r unresolvable)", book_scroll_id)
    return ResolvedPosition(0, exact=False, reason="item-start")


def _first_following(target: str, blocks: list[Block]) -> int | None:
    """First block at or after `target` in document order (both descoped xpaths)."""

    def key(xp: str) -> list[tuple[str, int]]:
        steps = _normalize_xpath(xp)
        if not steps:
            return []
        out = []
        for part in steps.removeprefix("//body/").split("/"):
            m = _STEP.fullmatch(part)
            if m and m.group(1):
                out.append((m.group(1), int(m.group(2))))
        return out

    tkey = key(target)
    if not tkey:
        return None
    # document order among siblings is index order regardless of tag; this is an
    # approximation (tag-relative indices aren't globally ordered), good enough
    # as a last resort before the proportional fallback.
    for b in blocks:
        bkey = key(b.xpath)
        if [i for _, i in bkey] >= [i for _, i in tkey]:
            return b.index
    return None


def scroll_id_for_block(block: Block) -> str:
    """Write direction: block → the descoped XPath Kavita stores."""
    return block.xpath


def page_num_for_chapter(spine_index: int) -> int:
    """Write direction pageNum = the block's spine index. Exact, not proportional.

    (Kavita paginates EPUBs by spine document — verified live; §7.3 v1.2.)
    """
    return spine_index
