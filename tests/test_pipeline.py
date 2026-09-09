"""EPUB pipeline tests: parsing, content rules, normalization, segmentation."""

import io
import zipfile

from echo.epub import Epub, MAX_SEG, MIN_SEG, parse_epub, segment_chapter
from echo.textnorm import normalize


def _build_epub(entries, spine, opf_path="OEBPS/content.opf", rootfile=True):
    """Minimal EPUB from (name, bytes) entries + [(item_id, href)] spine.

    `entries` are literal zip names; `href`s are OPF manifest hrefs (relative to
    the OPF dir, and may be URI-encoded or use ../). Used to exercise the reader
    against the malformed-but-real books that turn up in the wild."""
    items = "".join(
        f'<item id="{iid}" href="{href}" media-type="application/xhtml+xml"/>'
        for iid, href in spine)
    refs = "".join(f'<itemref idref="{iid}"/>' for iid, _ in spine)
    opf = (f'<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf"'
           f' version="3.0" unique-identifier="uid"><metadata/>'
           f'<manifest>{items}</manifest><spine>{refs}</spine></package>').encode()
    rf = (f'<rootfiles><rootfile full-path="{opf_path}"'
          f' media-type="application/oebps-package+xml"/></rootfiles>' if rootfile else "")
    container = (f'<?xml version="1.0"?><container'
                 f' xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
                 f' version="1.0">{rf}</container>').encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr(opf_path, opf)
        for name, data in entries:
            z.writestr(name, data)
    return buf.getvalue()


def test_relative_parent_href():
    """Spine href with ../ (OPF in a subdir) must resolve; zipfile doesn't
    normalize paths, so the raw joined name is absent from the archive."""
    data = _build_epub(
        [("Text/ch1.html", b"<html><body><p>Up and over.</p></body></html>")],
        [("c1", "../Text/ch1.html")])
    chapters = parse_epub(data)
    assert len(chapters) == 1
    assert any("Up and over" in b.text for b in chapters[0].blocks)


def test_one_bad_chapter_does_not_lose_the_book():
    """An empty spine file and a missing-from-zip spine file must each degrade to
    an empty chapter, not abort the whole book — and spine_index stays aligned
    with Kavita's page model (position write-back is keyed on spine_index)."""
    data = _build_epub(
        [("OEBPS/a.html", b""),  # empty file
         ("OEBPS/c.html", b"<html><body><p>Good chapter.</p></body></html>")],
        [("c1", "a.html"), ("c2", "missing.html"), ("c3", "c.html")])
    chapters = parse_epub(data)
    assert [c.spine_index for c in chapters] == [0, 1, 2]
    assert [len(c.blocks) for c in chapters] == [0, 0, 1]
    assert any("Good chapter" in b.text for b in chapters[2].blocks)


def test_calibre_div_paragraphs_are_read():
    """Calibre/pdf-converted books emit each paragraph within a <div> section
    (prose in a child <span>) with an <h*> heading and no <p> at all. Regression:
    A book read only its titles because <div> prose was
    dropped. A wrapper <div> containing the paragraphs must still be skipped so
    only the leaf paragraph divs become blocks (no duplication)."""
    ch = (b"<html><body>"
          b'<h2 class="c"><span>STORYLINE</span></h2>'
          b'<div class="wrap">'
          b'  <div class="p"><span>First paragraph of real prose here.</span></div>'
          b'  <div class="spacer"></div>'
          b'  <div class="p"><span>Second paragraph, also worth reading aloud.</span></div>'
          b"</div>"
          b'<div class="img"><img src="../Images/x.jpg"/></div>'
          b"</body></html>")
    data = _build_epub([("OEBPS/ch.html", ch)], [("c1", "ch.html")])
    chapters = parse_epub(data)
    texts = [b.text for b in chapters[0].blocks]
    assert chapters[0].title == "STORYLINE"
    assert any("First paragraph of real prose" in t for t in texts)
    assert any("Second paragraph" in t for t in texts)
    # heading + two leaf paragraphs only — the wrapper div and empty/img divs
    # must not add blocks (no duplicated prose from the wrapper)
    assert len(chapters[0].blocks) == 3


def test_missing_rootfile_raises_clearly():
    """container.xml without a rootfile → a clear ValueError, not an opaque
    AttributeError on None.get(...)."""
    data = _build_epub([("OEBPS/b.html", b"<p>x</p>")], [("c1", "b.html")],
                       rootfile=False)
    try:
        Epub(data)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "rootfile" in str(e)


def test_uri_encoded_spine_href(fixture_epub):
    """OPF hrefs are URI-encoded (%20, %27) but zip entries store the decoded
    literal filename. Books whose internal files contain spaces/punctuation must
    still parse instead of KeyError-ing in chapter_html."""
    container = (b'<?xml version="1.0"?>'
                 b'<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"'
                 b' version="1.0"><rootfiles><rootfile full-path="OEBPS/content.opf"'
                 b' media-type="application/oebps-package+xml"/></rootfiles></container>')
    opf = (b'<?xml version="1.0"?>'
           b'<package xmlns="http://www.idpf.org/2007/opf" version="3.0"'
           b' unique-identifier="uid"><metadata/><manifest>'
           b'<item id="c1" href="Text/A%20B%20Name%20-%20Guide%27s_split_003.html"'
           b' media-type="application/xhtml+xml"/></manifest>'
           b'<spine><itemref idref="c1"/></spine></package>')
    ch = b"<html><body><p>Hello from the encoded chapter.</p></body></html>"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/Text/A B Name - Guide's_split_003.html", ch)

    chapters = parse_epub(buf.getvalue())
    assert len(chapters) == 1
    assert any("encoded chapter" in b.text for b in chapters[0].blocks)


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


def test_block_html_preserves_emphasis(fixture_epub):
    """Read-along display HTML keeps em/strong, unwraps other inlines, escapes text."""
    ch2 = parse_epub(fixture_epub)[1]
    anchor = next(b for b in ch2.blocks if "Anchor target" in b.text)
    assert "<em>inline</em>" in anchor.html   # emphasis kept
    assert "<span" not in anchor.html          # non-emphasis inline unwrapped, text kept
    assert "Anchor target with" in anchor.html


def test_block_html_keeps_original_typography_and_escapes():
    """Display HTML is for the eye: smart quotes stay; angle brackets are escaped."""
    from echo.epub import extract_blocks
    html = ("<html><body>"
            "<p>She said <em>“no”</em> &amp; left &lt;quietly&gt;.</p>"
            "</body></html>").encode("utf-8")
    b = extract_blocks(html)[0]
    assert "<em>“no”</em>" in b.html   # curly quotes preserved, not normalized
    assert "&amp;" in b.html and "&lt;quietly&gt;" in b.html  # text re-escaped, no raw markup


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
