"""EPUB pipeline tests: parsing, content rules, normalization, segmentation."""

from echo.epub import MAX_SEG, MIN_SEG, parse_epub, segment_chapter
from echo.textnorm import normalize


def test_spine_order_and_chapter_count(fixture_epub):
    chapters = parse_epub(fixture_epub)
    assert len(chapters) == 2
    assert chapters[0].href == "ch1.xhtml"
    assert chapters[0].title == "Chapter One"
    assert chapters[1].title == "Chapter Two"


def test_content_rules(fixture_epub):
    ch1 = parse_epub(fixture_epub)[0]
    texts = [b.text for b in ch1.blocks]
    joined = " ".join(texts)
    # read: headings, paragraphs, blockquote paragraphs, list items
    assert "Chapter One" in texts[0]
    assert any("rivers and patience" in t for t in texts)
    assert any("First item" in t for t in texts)
    # skip: images, tables
    assert "must not be read" not in joined
    assert "skip" not in joined
    # footnote markers stripped (sup element and [2] bracket)
    assert "[2]" not in joined
    assert "innkeeper1" not in joined.replace(" ", "")
    # soft hyphen removed, word rejoined
    assert "innkeeper" in joined


def test_normalization_rules():
    assert normalize("cold—colder—and") == "cold, colder, and"
    assert normalize("“You’re late,” he said.") == '"You\'re late," he said.'
    assert normalize("Again…") == "Again..."
    assert normalize("inn­keeper") == "innkeeper"
    assert normalize("wait [12] here") == "wait here"
    assert normalize("already, — twice") == "already, twice"
    # idempotent
    s = normalize("“A—B…” [3]")
    assert normalize(s) == s


def test_block_coordinates_monotonic(fixture_epub):
    for ch in parse_epub(fixture_epub):
        prev_end = -1
        for i, b in enumerate(ch.blocks):
            assert b.index == i
            assert b.char_start > prev_end - 1
            assert b.char_end > b.char_start
            prev_end = b.char_end
            assert b.xpath.startswith("//body/")


def test_segmentation_windows(fixture_epub):
    for ch in parse_epub(fixture_epub):
        segs = segment_chapter(ch.blocks)
        assert segs, ch.href
        for s in segs:
            assert len(s.text) <= MAX_SEG, s.text
        # tiny fragments merged: no lone "Tiny." segment
        assert all(s.text != "Tiny." for s in segs)
        # seg_index is contiguous
        assert [s.seg_index for s in segs] == list(range(len(segs)))


def test_monster_sentence_split(fixture_epub):
    ch2 = parse_epub(fixture_epub)[1]
    segs = segment_chapter(ch2.blocks)
    long_segs = [s for s in segs if "universally acknowledged" in s.text
                 or "kept their own counsel" in s.text]
    assert len(long_segs) >= 2  # split on clause boundaries
    for s in segs:
        assert len(s.text) <= MAX_SEG


def test_utf8_without_xml_declaration():
    """Regression: praise.xhtml mojibake — UTF-8 must be default, not Latin-1."""
    from echo.epub import extract_blocks
    html = ("<html><head><title>x</title></head><body>"
            "<p>“A viciously vibrant epic.”—Entertainment Weekly</p>"
            "</body></html>").encode("utf-8")  # no XML declaration
    blocks = extract_blocks(html)
    assert blocks and "â" not in blocks[0].text
    assert '"A viciously vibrant epic."' in blocks[0].text


def test_declared_non_utf8_encoding():
    html = ('<?xml version="1.0" encoding="iso-8859-1"?>'
            "<html><body><p>caf\xe9 r\xe9sum\xe9 is done.</p></body></html>"
            ).encode("iso-8859-1")
    from echo.epub import extract_blocks
    blocks = extract_blocks(html)
    assert blocks and "café résumé" in blocks[0].text


def test_nested_divs_still_extract(fixture_epub):
    ch2 = parse_epub(fixture_epub)[1]
    texts = [b.text for b in ch2.blocks]
    assert any("nested divs" in t for t in texts)
    assert any("outside the wrappers" in t for t in texts)
    # nested path is reflected in xpath
    nested = next(b for b in ch2.blocks if "nested divs" in b.text)
    assert "div[1]/div[1]/p[1]" in nested.xpath
