"""Fixture EPUB built in-memory — no copyrighted content, no downloads (§15.1)."""

import io
import zipfile

import pytest

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""

OPF = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">echo-fixture-1</dc:identifier>
    <dc:title>Echo Test Fixture</dc:title><dc:language>en</dc:language>
  </metadata>
  <manifest>
    <item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
    <item id="cover" href="cover.png" media-type="image/png"/>
  </manifest>
  <spine><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""

# Chapter 1: nasty light-novel typography — em dashes, smart quotes, soft hyphens,
# footnote markers, sup markers, images, a table, nested blockquote, list items.
CH1 = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>One</title></head>
<body>
  <h1 id="ch1">Chapter One</h1>
  <p>The morning was cold—colder than she expected—and the road was empty.</p>
  <p>“You’re late,” he said. “Again…”</p>
  <p>The inn­keeper<sup>1</sup> shrugged [2] and turned away.</p>
  <img src="cover.png" alt="a picture that must not be read"/>
  <table><tr><td>skip</td><td>this</td></tr></table>
  <blockquote><p>An old proverb about rivers and patience.</p></blockquote>
  <ul><li>First item in a list.</li><li>Second item in a list.</li></ul>
  <p>Tiny.</p>
  <p>A closing paragraph long enough to be its own segment without merging.</p>
</body></html>"""

# Chapter 2: deep nesting (div wrappers) — the XPath robustness case (R3),
# plus one monster sentence to exercise clause splitting.
LONG_SENT = ("It was a truth universally acknowledged, at least among the caravan guards, "
             "the merchants, the cooks, and the three exhausted cartographers, "
             "that no map survived the first day of travel, that every river had moved "
             "since the last survey, and that the mountains, contemptuous of ink, "
             "kept their own counsel about where the passes actually were.")

CH2 = f"""<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Two</title></head>
<body>
  <div class="wrapper"><div class="inner">
    <h2>Chapter Two</h2>
    <p>First paragraph inside nested divs.</p>
    <p><span>Anchor target with <em>inline</em> children.</span></p>
    <p>{LONG_SENT}</p>
  </div></div>
  <p>Final paragraph outside the wrappers.</p>
</body></html>"""


def build_fixture_epub() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", CONTAINER)
        z.writestr("OEBPS/content.opf", OPF)
        z.writestr("OEBPS/ch1.xhtml", CH1)
        z.writestr("OEBPS/ch2.xhtml", CH2)
        z.writestr("OEBPS/cover.png", b"\x89PNG\r\n\x1a\n")
    return buf.getvalue()


@pytest.fixture(scope="session")
def fixture_epub() -> bytes:
    return build_fixture_epub()
