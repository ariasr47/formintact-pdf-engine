"""Text the source font cannot map to Unicode keeps its source glyphs.

MuPDF extracts such a character as U+FFFD. Redrawn from that text it became a
row of replacement boxes in a face that has one (Spanish, Noto Sans) and a
glyph miss that failed the paid order in a face without one (the dogfood CJK
faces, 2026-10-10: Form 1040 of 1913 and DS-64). The run must never reach the
model and never be drawn from U+FFFD: it is drawn exactly as the source drew it.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pikepdf
import pymupdf
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from pdf_translate import run_extract, run_retypeset, run_strip
from tests.test_source_retention import _target_font


def _unmapped_face() -> bytes:
    """One visible glyph, reachable only by CID: no cmap, no ToUnicode."""
    pen = TTGlyphPen(None)
    pen.moveTo((100, -150))
    pen.lineTo((500, 350))
    pen.lineTo((100, 850))
    pen.closePath()
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "mark"])
    builder.setupCharacterMap({})
    builder.setupGlyf({".notdef": TTGlyphPen(None).glyph(), "mark": pen.glyph()})
    builder.setupHorizontalMetrics({".notdef": (600, 0), "mark": (600, 100)})
    builder.setupHorizontalHeader(ascent=850, descent=-150)
    builder.setupNameTable({"familyName": "Unmapped", "styleName": "Regular"})
    builder.setupOS2(sTypoAscender=850, sTypoDescender=-150, usWinAscent=850, usWinDescent=150)
    builder.setupPost()
    builder.setupMaxp()
    data = io.BytesIO()
    builder.save(data)
    return data.getvalue()


def _source(path: Path) -> None:
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(400, 240))
    descriptor = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.FontDescriptor, FontName=pikepdf.Name("/Unmapped"), Flags=4,
        FontBBox=[0, -150, 600, 850], ItalicAngle=0, Ascent=850, Descent=-150,
        CapHeight=700, StemV=80, FontFile2=pdf.make_stream(_unmapped_face())))
    cid_font = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.CIDFontType2,
        BaseFont=pikepdf.Name("/Unmapped"), FontDescriptor=descriptor, DW=600,
        CIDToGIDMap=pikepdf.Name.Identity,
        CIDSystemInfo=pikepdf.Dictionary(Registry=pikepdf.String("Adobe"),
                                         Ordering=pikepdf.String("Identity"), Supplement=0)))
    unmapped = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type0, BaseFont=pikepdf.Name("/Unmapped"),
        Encoding=pikepdf.Name("/Identity-H"), DescendantFonts=[cid_font]))
    helvetica = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica,
        Encoding=pikepdf.Name.WinAnsiEncoding))
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(U=unmapped, H=helvetica))
    # Line 2 mixes mapped and unmapped glyphs on one baseline. Line 3 sets
    # its character spacing outside BT, which source retention refuses to
    # vouch ("inherited-state"), so its glyphs must come back another way.
    page.Contents = pdf.make_stream(
        b"BT /H 14 Tf 40 190 Td (Name of the applicant) Tj ET\n"
        b"BT /H 14 Tf 40 130 Td (Total) Tj ET\n"
        b"BT /U 14 Tf 78 130 Td <0001> Tj ET\n"
        b"q 1 Tc BT /U 40 Tf 200 60 Td <00010001> Tj ET Q\n")
    pdf.save(path)


def _ink(path: Path, box) -> list[int]:
    with pymupdf.open(path) as document:
        pixmap = document[0].get_pixmap(dpi=144, clip=pymupdf.Rect(box), colorspace="gray")
        return list(pixmap.samples)


def test_unmapped_source_glyphs_are_kept_and_never_drawn_from_replacement_text(tmp_path):
    original, stripped = tmp_path / "original.pdf", tmp_path / "stripped.pdf"
    _source(original)
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(tmp_path))

    # Nothing holding U+FFFD is offered for translation; both runs pass through.
    assert [core["text"] for core in extracted.cores] == ["Name of the applicant"]
    unmapped = [segment for segment in extracted.segments if "\ufffd" in segment["text"]]
    assert [(segment["text"], segment["passthrough"]) for segment in unmapped] == [
        ("Total \ufffd", True), ("\ufffd\ufffd", True)]

    # The target face has no U+FFFD glyph, as the CJK faces had none.
    face = _target_font(tmp_path / "target.ttf", chars=" Nombredl")
    mapping = tmp_path / "translations.json"
    mapping.write_text(json.dumps({
        "fonts": {role: str(face) for role in ("regular", "bold", "italic", "bold_italic")},
        "lang": "es", "translations": {"Name of the applicant": "Nombre del"},
    }), encoding="utf-8")
    output = tmp_path / "out.pdf"
    result = run_retypeset(str(stripped), extracted.segments_path, str(mapping), str(output),
                           original=str(original))

    assert not [row for row in result.warnings if row["kind"] == "unmapped_text_not_drawn"]
    with pymupdf.open(output) as document:
        assert "\ufffd" not in document[0].get_text()
    # Rendered, not asserted: each run paints what the source painted.
    for segment in unmapped:
        box = pymupdf.Rect(segment["bbox"]) + (-2, -2, 2, 2)
        source, delivered = _ink(original, box), _ink(output, box)
        dark = sum(value < 128 for value in source)
        assert dark > 50
        changed = sum(abs(a - b) > 64 for a, b in zip(source, delivered))
        assert changed <= dark * 0.02, (segment["text"], changed, dark)


def _bullet_source(path: Path) -> None:
    """A list item: an unmapped bullet glyph, then mapped text on the same baseline."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(400, 240))
    _source(path)
    with pikepdf.open(path) as base:
        fonts = base.pages[0].Resources.Font
        page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(
            U=pdf.copy_foreign(fonts.U), H=pdf.copy_foreign(fonts.H)))
    page.Contents = pdf.make_stream(
        b"BT /U 14 Tf 40 130 Td <0001> Tj ET\n"
        b"BT /H 14 Tf 52 130 Td (Ask your provider.) Tj ET\n")
    pdf.save(path)


def test_a_leading_unmapped_bullet_passes_through_alone_and_its_text_is_translated(tmp_path):
    # Merged with its text, the whole list item passed through and shipped in the
    # source language with no outcome naming it (dogfood 2026-10-10, flu VIS).
    original, stripped = tmp_path / "original.pdf", tmp_path / "stripped.pdf"
    _bullet_source(original)
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(tmp_path))

    assert [core["text"] for core in extracted.cores] == ["Ask your provider."]
    assert [(segment["text"].strip(), segment["passthrough"])
            for segment in extracted.segments] == [
        ("�", True), ("Ask your provider.", False)]
    bullet = extracted.segments[0]

    face = _target_font(tmp_path / "target.ttf", chars=" Pregunt")
    mapping = tmp_path / "translations.json"
    mapping.write_text(json.dumps({
        "fonts": {role: str(face) for role in ("regular", "bold", "italic", "bold_italic")},
        "lang": "es", "translations": {"Ask your provider.": "Pregunte"},
    }), encoding="utf-8")
    output = tmp_path / "out.pdf"
    run_retypeset(str(stripped), extracted.segments_path, str(mapping), str(output),
                  original=str(original))

    with pymupdf.open(output) as document:
        text = document[0].get_text()
    assert "Pregunte" in text and "Ask" not in text and "�" not in text
    box = pymupdf.Rect(bullet["bbox"]) + (-1, -1, 1, 1)
    source, delivered = _ink(original, box), _ink(output, box)
    dark = sum(value < 128 for value in source)
    assert dark > 20
    assert sum(abs(a - b) > 64 for a, b in zip(source, delivered)) <= dark * 0.02
