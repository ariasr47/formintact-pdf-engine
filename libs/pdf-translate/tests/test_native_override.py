"""Native public-API regression for legacy override placement."""

import hashlib
import json
from pathlib import Path

import pymupdf
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from pdf_translate import run_extract, run_retypeset, run_strip


TEXT = "Please pay today."
TARGET = "Tax due now."


def _fixture_font(path: Path) -> Path:
    chars = " .ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    names = {" ": "space"}
    names.update({char: f"g{ord(char):04x}" for char in chars if char != " "})
    order = [".notdef", *names.values()]
    glyphs = {".notdef": TTGlyphPen(None).glyph(), "space": TTGlyphPen(None).glyph()}
    for glyph_name in order[2:]:
        pen = TTGlyphPen(None)
        pen.moveTo((80, 0))
        pen.lineTo((80, 700))
        pen.lineTo((650, 700))
        pen.lineTo((650, 0))
        pen.closePath()
        glyphs[glyph_name] = pen.glyph()
    metrics = {name: (360 if name == "space" else 760, 0) for name in order}
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({ord(char): glyph_name for char, glyph_name in names.items()})
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({
        "familyName": "NativeOverrideFixture",
        "styleName": "Regular",
        "uniqueFontIdentifier": "NativeOverrideFixture Regular 1.0",
        "fullName": "NativeOverrideFixture Regular",
        "psName": "NativeOverrideFixture-Regular",
    })
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200,
                     usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    builder.setupMaxp()
    builder.save(path)
    return path


def _job(work: Path, contains: str) -> dict[str, Path]:
    work.mkdir()
    font = _fixture_font(work / "fixture.ttf")
    original = work / "original.pdf"
    with pymupdf.open() as document:
        document.new_page(width=400, height=240).insert_text(
            (72, 72), TEXT, fontsize=12
        )
        document.save(original)
    stripped = work / "stripped.pdf"
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(work))
    mapping = work / "mapping.json"
    mapping.write_text(json.dumps({
        "fonts": {role: str(font) for role in ("regular", "bold", "italic", "bold_italic")},
        "translations": {}, "merges": [], "center": [], "skip": [],
        "overrides": [{"page": 0, "contains": contains,
                       "parts": [{"text": TARGET, "max_width": 200}] }],
    }), encoding="utf-8")
    return {"original": original, "stripped": stripped,
            "segments": Path(extracted.segments_path), "mapping": mapping,
            "output": work / "output.pdf"}


def _render(job: dict[str, Path]) -> tuple[int, int, str]:
    run_retypeset(str(job["stripped"]), str(job["segments"]),
                  str(job["mapping"]), str(job["output"]),
                  original=str(job["original"]), scale_report=None)
    with pymupdf.open(job["output"]) as document:
        assert len(document) == 1
        assert TARGET in document[0].get_text()
        pix = document[0].get_pixmap(matrix=pymupdf.Matrix(2, 2),
                                     colorspace=pymupdf.csRGB, alpha=False)
        return pix.width, pix.height, hashlib.sha256(pix.samples).hexdigest()


def test_empty_contains_override_and_truthy_control_are_native_and_pixel_stable(tmp_path):
    truthy = _render(_job(tmp_path / "truthy", TEXT))
    assert truthy[:3] == (800, 480, "e3bd9fb3c26bbd3dcd2f06043e32b85fd9899098ef6c49fd63821879c7a621df")
    empty = _render(_job(tmp_path / "empty", ""))
    assert empty[:2] == (800, 480)
