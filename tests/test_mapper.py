"""Position mapper tests, incl. the §7.3 acceptance round-trip."""

from echo.epub import parse_epub
from echo.kavita import Progress, may_write_back
from echo.mapper import (page_num_for_chapter, resolve_scroll_id,
                         scroll_id_for_block)


def test_id_anchor_resolution(fixture_epub):
    """Kavita's second anchor dialect: id("ch1") — seen live on ACOMAF."""
    ch1 = parse_epub(fixture_epub)[0]
    assert "ch1" in ch1.ids
    pos = resolve_scroll_id('id("ch1")', ch1.blocks, ch1.ids)
    assert pos.exact and pos.reason == "id"
    assert ch1.blocks[pos.block_index].text == "Chapter One"
    # single quotes variant too
    assert resolve_scroll_id("id('ch1')", ch1.blocks, ch1.ids).exact


def test_compound_id_anchor_resolution(fixture_epub):
    """Kavita's third anchor dialect: id("X")/rel/path — an id'd wrapper div plus a
    relative XPath (seen live on 0.9.1 for books whose paragraphs sit in id'd divs).
    Must land on the exact paragraph, not the top of the chapter (the old fallback)."""
    ch2 = parse_epub(fixture_epub)[1]
    assert "sec221" in ch2.id_xpaths
    pos = resolve_scroll_id('id("sec221")/p[2]', ch2.blocks, ch2.ids, ch2.id_xpaths)
    assert pos.exact and pos.reason.startswith("xpath")
    assert "Anchor target" in ch2.blocks[pos.block_index].text
    assert pos.block_index != 0  # regression: without the fix this fell back to chapter top
    # single-quote variant resolves identically
    assert resolve_scroll_id("id('sec221')/p[2]", ch2.blocks, ch2.ids,
                             ch2.id_xpaths).block_index == pos.block_index
    # unknown id in the compound form → safe item-start fallback, not a crash
    miss = resolve_scroll_id('id("nope")/p[2]', ch2.blocks, ch2.ids, ch2.id_xpaths)
    assert not miss.exact and miss.reason == "item-start"


def test_exact_xpath_resolution(fixture_epub):
    ch1 = parse_epub(fixture_epub)[0]
    # Kavita-style anchor for the second <p> in body
    pos = resolve_scroll_id("//body/p[2]", ch1.blocks)
    assert pos.exact
    assert "late" in ch1.blocks[pos.block_index].text


def test_case_and_index_normalization(fixture_epub):
    ch1 = parse_epub(fixture_epub)[0]
    a = resolve_scroll_id("//BODY/P[2]", ch1.blocks)
    b = resolve_scroll_id("//body/p[2]", ch1.blocks)
    assert a.exact and a.block_index == b.block_index


def test_anchor_inside_block_resolves_to_enclosing(fixture_epub):
    ch2 = parse_epub(fixture_epub)[1]
    # anchor points at the span INSIDE a p (Kavita can store child anchors)
    pos = resolve_scroll_id("//body/div[1]/div[1]/p[2]/span[1]", ch2.blocks)
    assert pos.exact
    assert "Anchor target" in ch2.blocks[pos.block_index].text


def test_item_start_fallback(fixture_epub):
    """pageNum locates the spine document; a dead anchor falls back to its start."""
    ch1 = parse_epub(fixture_epub)[0]
    for bad in (None, "///garbage!!", "//body/p[999]"):
        pos = resolve_scroll_id(bad, ch1.blocks)
        assert not pos.exact or bad == "//body/p[999]"  # p[999] may hit 'following'
        if not pos.exact:
            assert pos.block_index == 0 and pos.reason == "item-start"


def test_round_trip_every_block(fixture_epub):
    """§7.3 acceptance analogue: block → scrollId → resolve → same block, all blocks."""
    for ch in parse_epub(fixture_epub):
        for block in ch.blocks:
            sid = scroll_id_for_block(block)
            pos = resolve_scroll_id(sid, ch.blocks)
            assert pos.exact, (ch.href, block.index, sid)
            assert pos.block_index == block.index, (ch.href, sid)


def test_page_num_is_spine_index(fixture_epub):
    """Kavita paginates EPUBs by spine document (verified live, 0.9.0.2)."""
    chapters = parse_epub(fixture_epub)
    for ch in chapters:
        assert page_num_for_chapter(ch.spine_index) == ch.spine_index


# -- write-back safety rules (§7.3) -----------------------------------------

def _prog(page, ch=1868):
    return Progress(volume_id=918, chapter_id=ch, page_num=page,
                    series_id=610, library_id=1, book_scroll_id="//body/p[1]")


def test_reader_wins_within_two_minutes():
    ok, why = may_write_back(_prog(10), _prog(12), now_utc=1000.0,
                             kavita_last_modified_utc=950.0)
    assert not ok and "reader-active" in why


def test_never_move_backwards():
    ok, why = may_write_back(_prog(10), _prog(5), now_utc=1000.0,
                             kavita_last_modified_utc=0.0)
    assert not ok and "backwards" in why


def test_backwards_allowed_after_sustained_listen():
    ok, _ = may_write_back(_prog(10), _prog(5), now_utc=1000.0,
                           kavita_last_modified_utc=0.0,
                           listened_since_skip_back=90.0)
    assert ok


def test_forward_write_allowed():
    ok, _ = may_write_back(_prog(10), _prog(11), now_utc=1000.0,
                           kavita_last_modified_utc=0.0)
    assert ok
