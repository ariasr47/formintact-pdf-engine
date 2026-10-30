"""A cmap-less embedded Arial is decoded only when Arial's widths vouch its order.

A wrong letter table is silent: the page reads, translates and ships with the
wrong words. So the order is never assumed; these tests build the DS-64 shape
(Type0 Identity-H, no ToUnicode, no cmap, no glyph names) in Arial's order and
in an order with two glyphs swapped.
"""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pymupdf
from fontTools.agl import toUnicode
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.standardGlyphOrder import standardGlyphOrder

from pdft_engine import glyph_recovery

CHECK_FONTS = Path(glyph_recovery.__file__).parent / "assets" / "check-fonts"
LIBERATION = CHECK_FONTS / "LiberationSans-Regular.ttf"
NAME = pikepdf.Name("/ABCDEF+ArialMT")
TEXT = "Hamburgefonstiv"


def _font(path: Path, swap: tuple[str, str] | None = None, black: bool = False) -> Path:
    """A TrueType in the standard Macintosh order with Arial's widths, nothing else.

    `black` widens every letter the way Arial Black does: no width matches any
    Liberation Sans face, but narrow letters stay narrow and wide ones wide.
    """
    reference = TTFont(LIBERATION)
    widths = {toUnicode(name): reference["hmtx"].metrics[name][0]
              for name in reference.getGlyphOrder() if len(toUnicode(name)) == 1}
    order = list(standardGlyphOrder)
    names = list(order)
    if swap:
        first, second = order.index(swap[0]), order.index(swap[1])
        names[first], names[second] = names[second], names[first]
    builder = FontBuilder(2048, isTTF=True)
    builder.setupGlyphOrder(order)
    pen = TTGlyphPen(None)
    pen.moveTo((100, 0))
    pen.lineTo((100, 700))
    pen.lineTo((500, 700))
    pen.closePath()
    box = pen.glyph()
    builder.setupGlyf({name: box for name in order})
    def width(name: str) -> int:
        base = widths.get(toUnicode(name), 1000)
        return round(base * 1.12 + 37) if black else base

    builder.setupHorizontalMetrics({glyph: (width(name), 0)
                                    for glyph, name in zip(order, names, strict=True)})
    builder.setupHorizontalHeader(ascent=1854, descent=-434)
    builder.setupCharacterMap({})
    builder.setupOS2()
    builder.setupPost(formatType=3.0)
    builder.setupMaxp()
    builder.setupNameTable({"familyName": "Test", "styleName": "Regular"})
    # The DS-64 shape: no cmap and no glyph names, so nothing names a letter.
    del builder.font["cmap"]
    builder.font["post"].formatType = 3.0
    builder.save(path)
    return path


def _pdf(path: Path, program: Path, *others: tuple[str, Path]) -> Path:
    """One line of TEXT per font: `program` as ABCDEF+ArialMT, then each (name, program)."""
    pdf = pikepdf.new()
    gids = [next(gid for gid, name in enumerate(standardGlyphOrder) if toUnicode(name) == char)
            for char in TEXT]
    shown = b"".join(f"{gid:04X}".encode() for gid in gids)
    fonts, lines = {}, []
    for index, (name, face) in enumerate(((NAME, program), *others), start=1):
        font_name = pikepdf.Name(name) if isinstance(name, str) else name
        fonts[f"F{index}"] = pdf.make_indirect(_type0(pdf, font_name, face))
        start = f"BT /F{index} 12 Tf 72 {720 - 20 * index} Td <".encode()
        lines.append(start + shown + b"> Tj ET")
    page = pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name.Page, MediaBox=[0, 0, 612, 792],
        Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(**fonts)),
        Contents=pdf.make_stream(b" ".join(lines))))
    pdf.pages.append(page)
    pdf.save(path)
    return path


def _type0(pdf, name, program: Path):
    descendant = pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.CIDFontType2, BaseFont=name,
        CIDSystemInfo=pikepdf.Dictionary(Registry=pikepdf.String("Adobe"),
                                         Ordering=pikepdf.String("Identity"), Supplement=0),
        FontDescriptor=pikepdf.Dictionary(
            Type=pikepdf.Name.FontDescriptor, FontName=name, Flags=32,
            FontBBox=[0, -434, 2048, 1854], ItalicAngle=0, Ascent=905, Descent=-212,
            CapHeight=716, StemV=80,
            FontFile2=pdf.make_stream(program.read_bytes())),
        CIDToGIDMap=pikepdf.Name.Identity)
    return pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type0,
                              BaseFont=name, Encoding=pikepdf.Name("/Identity-H"),
                              DescendantFonts=[pdf.make_indirect(descendant)])


def _text(path: Path) -> str:
    with pymupdf.open(path) as document:
        return document[0].get_text().strip()


def _lines(path: Path) -> list[str]:
    with pymupdf.open(path) as document:
        return document[0].get_text().split()


def test_arial_order_is_decoded(tmp_path):
    source = _pdf(tmp_path / "in.pdf", _font(tmp_path / "arial-order.ttf"))
    assert _text(source) != TEXT
    decoded, report = glyph_recovery.decoded_copy(source, tmp_path)
    assert report == {"decoded": ["ABCDEF+ArialMT"], "not_decoded": [], "by_pattern": []}
    assert _text(decoded) == TEXT


def test_a_swapped_order_is_left_unreadable(tmp_path):
    # `i` and `m` differ in width, so the widths cannot vouch this order.
    source = _pdf(tmp_path / "in.pdf", _font(tmp_path / "swapped.ttf", swap=("i", "m")))
    decoded, report = glyph_recovery.decoded_copy(source, tmp_path)
    assert decoded == source
    assert report == {"decoded": [], "not_decoded": ["ABCDEF+ArialMT"], "by_pattern": []}


BLACK = "/ABCDEF+Arial-Black"


def test_a_black_face_beside_a_proven_font_of_its_subset_is_decoded_by_its_width_pattern(tmp_path):
    """DS-64's four Arial Black headings, 2026-10-29: no width twin, same producer."""
    source = _pdf(tmp_path / "in.pdf", _font(tmp_path / "arial.ttf"),
                  (BLACK, _font(tmp_path / "black.ttf", black=True)))
    decoded, report = glyph_recovery.decoded_copy(source, tmp_path)
    assert report["by_pattern"] == ["ABCDEF+Arial-Black"]
    assert _lines(decoded) == [TEXT, TEXT]


def test_a_black_face_in_a_scrambled_order_stays_unreadable(tmp_path):
    source = _pdf(tmp_path / "in.pdf", _font(tmp_path / "arial.ttf"),
                  (BLACK, _font(tmp_path / "black.ttf", swap=("i", "m"), black=True)))
    _, report = glyph_recovery.decoded_copy(source, tmp_path)
    assert report["not_decoded"] == ["ABCDEF+Arial-Black"] and report["by_pattern"] == []


def test_a_black_face_with_no_proven_font_of_its_subset_stays_unreadable(tmp_path):
    source = _pdf(tmp_path / "in.pdf", _font(tmp_path / "arial.ttf"),
                  ("/GHIJKL+Arial-Black", _font(tmp_path / "black.ttf", black=True)))
    _, report = glyph_recovery.decoded_copy(source, tmp_path)
    assert report["not_decoded"] == ["GHIJKL+Arial-Black"] and report["by_pattern"] == []
