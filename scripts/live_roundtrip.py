#!/usr/bin/env python3
"""Live mapper validation (§7.3 acceptance) against your real Kavita book.

READ-ONLY unless --write is passed, and even then the write is a round-trip
of the position Kavita already has (a no-op), same as spike 3.

Usage (from repo root):
    pip3 install -r requirements-dev.txt --break-system-packages
    KAVITA_URL=http://YOUR_KAVITA:5000 KAVITA_API_KEY=<key> \
        python3 scripts/live_roundtrip.py            # read-only
    ... python3 scripts/live_roundtrip.py --write    # includes no-op write-back

What it proves on a real EPUB:
  1. Echo downloads and parses the book Kavita is tracking.
  2. Kavita's bookScrollId resolves to a specific paragraph (prints its text —
     verify it's the paragraph you're actually on!).
  3. Echo's regenerated XPath round-trips to the same paragraph.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from echo.epub import parse_epub, segment_chapter  # noqa: E402
from echo.kavita import KavitaClient  # noqa: E402
from echo.mapper import resolve_scroll_id, scroll_id_for_block  # noqa: E402


def main():
    write = "--write" in sys.argv
    client = KavitaClient(os.environ["KAVITA_URL"], os.environ["KAVITA_API_KEY"])

    ondeck = client.on_deck()
    if not ondeck:
        sys.exit("Nothing on deck — read a page of an EPUB in Kavita first.")
    series = ondeck[0]
    sid = series["id"]
    print(f"Series: {series.get('name')!r} (id={sid})")

    chapters_meta = client.chapters(sid)
    print(f"Kavita chapters: {len(chapters_meta)}")
    ch_meta = chapters_meta[0]
    chid = ch_meta["id"]

    prog = client.get_progress(chid)
    print(f"Progress: page {prog.page_num}, scrollId={prog.book_scroll_id!r}")

    print("Downloading EPUB via /api/Download/chapter …")
    data = client.download_epub(sid, chid)
    print(f"  {len(data)} bytes")

    chapters = parse_epub(data)
    kavita_pages = ch_meta.get("pages")
    print(f"EPUB spine items: {len(chapters)} · Kavita 'pages': {kavita_pages}")
    match = "MATCH" if kavita_pages == len(chapters) else "MISMATCH — paste back!"
    print(f"  R6 check (pageNum = spine index requires these to match): {match}")

    # Kavita's pageNum IS the 0-based spine index (verified 2026-07-17)
    if prog.page_num >= len(chapters):
        sys.exit(f"[FAIL] pageNum {prog.page_num} out of range for {len(chapters)} spine items")
    ch = chapters[prog.page_num]
    print(f"Spine[{prog.page_num}]: {ch.title or ch.href} ({len(ch.blocks)} blocks)")
    if not ch.blocks:
        sys.exit("[FAIL] spine item has no readable blocks — paste back")

    pos = resolve_scroll_id(prog.book_scroll_id, ch.blocks, ch.ids)
    if not pos.exact:
        print(f"[WARN] anchor fell back to {pos.reason} — paste back")
    block = ch.blocks[pos.block_index]
    print(f"\n[PASS] anchor resolved: spine {ch.spine_index} "
          f"({ch.title or ch.href}), paragraph {pos.block_index} ({pos.reason})")
    print(f'  ↳ "{block.text[:160]}{"…" if len(block.text) > 160 else ""}"')
    print("\n  >>> Is that the paragraph you're on in Kavita? (eyeball check) <<<")

    regen = scroll_id_for_block(block)
    back = resolve_scroll_id(regen, ch.blocks)
    ok = back.exact and back.block_index == pos.block_index
    print(f"\n[{'PASS' if ok else 'FAIL'}] Echo-regenerated XPath round-trip: "
          f"{regen!r} → block {back.block_index}")

    segs = segment_chapter(ch.blocks)
    est_min = sum(len(s.text.split()) for s in segs) / 150
    print(f"\nSegmentation: {len(segs)} segments, est. {est_min:.0f} min of audio "
          f"for this spine item")

    if write:
        print("\nNo-op write-back (re-posting Kavita's own values) …")
        client.set_progress(prog)
        prog2 = client.get_progress(chid)
        same = (prog2.page_num == prog.page_num
                and prog2.book_scroll_id == prog.book_scroll_id)
        print(f"[{'PASS' if same else 'FAIL'}] progress unchanged after write")


if __name__ == "__main__":
    main()
