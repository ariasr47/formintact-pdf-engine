"""B5 RED regressions for retaining proven legacy source text operators.

The source is deliberately authored with Helvetica while the legacy mapping
selects a generated standalone target font. A successful repair therefore has to
retain source operators for unchanged occurrences; redrawing the same text
from the translation map changes its metrics and origin.
"""

from __future__ import annotations

import json
import importlib
from pathlib import Path

import pymupdf
import pytest
import pikepdf
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from pdf_translate import run_extract, run_retypeset, run_strip
from pdf_translate.results import MappingError


def _target_font(path: Path, *, chars=" 0123456789ABCDEFGHKMPÉacdeghnkpté") -> Path:
    """Create a tiny deterministic TrueType face for this standalone test."""
    names = {" ": "space"}
    names.update({char: f"g{ord(char):04x}" for char in chars if char != " "})
    order = [".notdef", *names.values()]
    glyphs = {".notdef": TTGlyphPen(None).glyph()}
    glyphs["space"] = TTGlyphPen(None).glyph()
    for glyph_name in order[2:]:
        pen = TTGlyphPen(None)
        pen.moveTo((80, 0))
        pen.lineTo((80, 700))
        pen.lineTo((650, 700))
        pen.lineTo((650, 0))
        pen.closePath()
        glyphs[glyph_name] = pen.glyph()
    metrics = {name: (360 if name == "space" else 760, 0) for name in order}
    cmap = {ord(char): glyph_name for char, glyph_name in names.items()}
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap(cmap)
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": "RetentionTarget",
            "styleName": "Regular",
            "uniqueFontIdentifier": "RetentionTarget Regular 1.0",
            "fullName": "RetentionTarget Regular",
            "psName": "RetentionTarget-Regular",
        }
    )
    builder.setupOS2(
        sTypoAscender=800,
        sTypoDescender=-200,
        usWinAscent=800,
        usWinDescent=200,
    )
    builder.setupPost()
    builder.setupMaxp()
    builder.save(path)
    return path


def _legacy_job(work: Path) -> dict[str, Path]:
    work.mkdir(parents=True, exist_ok=True)
    target_font = _target_font(work / "retention-target.ttf")
    original = work / "original.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=240)
        page.insert_text((40, 60), "KEEP", fontname="helv", fontsize=14)
        page.insert_text((40, 110), "CHANGE", fontname="helv", fontsize=14)
        page = document.new_page(width=400, height=240)
        page.insert_text((40, 60), "KEEP", fontname="helv", fontsize=14)
        page.insert_text((210, 150), "KEEP", fontname="helv", fontsize=14)
        document.save(original)

    stripped = work / "stripped.pdf"
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(work))
    mapping = work / "translations.json"
    mapping.write_text(
        json.dumps(
            {
                "fonts": {role: str(target_font) for role in (
                    "regular", "bold", "italic", "bold_italic"
                )},
                "lang": "es",
                "translations": {"KEEP": "KEEP", "CHANGE": "Changed"},
            }
        ),
        encoding="utf-8",
    )
    return {
        "original": original,
        "stripped": stripped,
        "segments": Path(extracted.segments_path),
        "mapping": mapping,
        "output": work / "out.pdf",
    }


def _spans(path: Path, text: str) -> list[dict]:
    rows = []
    with pymupdf.open(path) as document:
        for page_number, page in enumerate(document):
            for block in page.get_text("dict")["blocks"]:
                if block["type"] != 0:
                    continue
                for line in block["lines"]:
                    for span in line["spans"]:
                        if span["text"] == text:
                            rows.append(
                                {
                                    "page": page_number,
                                    "text": span["text"],
                                    "font": span["font"],
                                    "origin": tuple(span["origin"]),
                                    "bbox": tuple(span["bbox"]),
                                }
                            )
    return sorted(rows, key=lambda row: (row["page"], row["origin"]))


def _assert_first_keep_redrawn(job, reason, *, stripped, original):
    """The unvouched page-1 KEEP is redrawn and reported; the job delivers.

    a-job-always-delivers: retention leaves an occurrence it cannot vouch out
    instead of refusing the document. The source operator is never replayed
    for it, and the vouched occurrences on page 2 are still retained.
    """
    result = run_retypeset(str(stripped), str(job["segments"]), str(job["mapping"]),
                           str(job["output"]), original=str(original))
    assert job["output"].exists()
    rows = [row for row in result.warnings if row["kind"] == "source_not_retained"]
    assert [(row["page"], row["core"]) for row in rows] == [(0, "KEEP")]
    if reason is not None:
        assert rows[0]["reason"] == reason
    source_font = _spans(job["original"], "KEEP")[0]["font"]
    drawn = [(span["page"], span["font"]) for span in _spans(job["output"], "KEEP")]
    assert len(drawn) == 3
    assert [font == source_font for page, font in sorted(drawn)] == [False, True, True]


def _render(job: dict[str, Path]) -> None:
    run_retypeset(
        str(job["stripped"]),
        str(job["segments"]),
        str(job["mapping"]),
        str(job["output"]),
        original=str(job["original"]),
    )


def test_legacy_retention_keeps_three_source_identities_and_translates_one(tmp_path):
    job = _legacy_job(tmp_path)
    source_keep = _spans(job["original"], "KEEP")
    source_change = _spans(job["original"], "CHANGE")
    # A vacuous assertion must never pass because extraction returned no rows.
    assert len(source_keep) == 3
    assert len(source_change) == 1
    assert {row["page"] for row in source_keep} == {0, 1}

    _render(job)
    delivered_keep = _spans(job["output"], "KEEP")
    delivered_changed = _spans(job["output"], "Changed")
    assert len(delivered_keep) == 3
    assert len(delivered_changed) == 1
    for expected, actual in zip(source_keep, delivered_keep):
        assert actual["page"] == expected["page"]
        assert actual["font"] == expected["font"]
        assert actual["origin"] == pytest.approx(expected["origin"], abs=0.01)
        assert actual["bbox"] == pytest.approx(expected["bbox"], abs=0.01)


def test_legacy_retention_binds_duplicate_labels_by_page_and_origin(tmp_path):
    job = _legacy_job(tmp_path)
    source_keep = _spans(job["original"], "KEEP")
    assert len(source_keep) == 3
    assert len({(row["page"], row["origin"]) for row in source_keep}) == 3

    _render(job)
    delivered_keep = _spans(job["output"], "KEEP")
    assert len(delivered_keep) == 3
    assert [(row["page"], row["origin"]) for row in delivered_keep] == [
        (row["page"], row["origin"]) for row in source_keep
    ]


def test_legacy_retention_does_not_keep_mapped_different_target(tmp_path):
    job = _legacy_job(tmp_path)
    mapping = json.loads(job["mapping"].read_text(encoding="utf-8"))
    mapping["translations"]["KEEP"] = "Kept"
    job["mapping"].write_text(json.dumps(mapping), encoding="utf-8")
    _render(job)
    delivered = _spans(job["output"], "Kept")
    source = _spans(job["original"], "KEEP")
    assert len(delivered) == len(source) == 3
    assert {row["font"] for row in delivered}.isdisjoint(
        {row["font"] for row in source}
    )


def test_legacy_retention_keeps_extracted_numeric_passthrough_identity(tmp_path):
    work = Path(tmp_path)
    original = work / "numeric-original.pdf"
    with pymupdf.open() as document:
        document.new_page(width=400, height=240).insert_text(
            (40, 60), "2024", fontname="helv", fontsize=14)
        document.save(original)
    stripped = work / "numeric-stripped.pdf"
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(work))
    segments = json.loads(Path(extracted.segments_path).read_text(encoding="utf-8"))
    numeric = next(item for item in segments["segments"] if item["text"].strip() == "2024")
    assert numeric.get("passthrough") is True
    segments_path = work / "numeric-segments.json"
    segments_path.write_text(json.dumps(segments), encoding="utf-8")
    font = _target_font(work / "retention-target.ttf")
    mapping = work / "numeric-translations.json"
    mapping.write_text(json.dumps({
        "fonts": {role: str(font) for role in
                  ("regular", "bold", "italic", "bold_italic")},
        "lang": "es", "translations": {"2024": "2024"},
    }), encoding="utf-8")
    output = work / "numeric-out.pdf"
    run_retypeset(str(stripped), str(segments_path), str(mapping), str(output),
                  original=str(original))
    source = _spans(original, "2024")
    delivered = _spans(output, "2024")
    assert len(source) == len(delivered) == 1
    assert delivered[0]["font"] == source[0]["font"]
    assert delivered[0]["origin"] == pytest.approx(source[0]["origin"], abs=0.01)
    assert delivered[0]["bbox"] == pytest.approx(source[0]["bbox"], abs=0.01)


def test_legacy_retention_allows_unrelated_multiline_translation(tmp_path):
    job = _legacy_job(tmp_path)
    mapping = json.loads(job["mapping"].read_text(encoding="utf-8"))
    mapping["translations"]["CHANGE"] = "Changed\nChanged"
    job["mapping"].write_text(json.dumps(mapping), encoding="utf-8")
    _render(job)
    assert len(_spans(job["output"], "KEEP")) == 3
    assert len(_spans(job["output"], "Changed")) == 2


@pytest.mark.parametrize("rotation", (0, 90, 180, 270))
def test_legacy_retention_preserves_asymmetric_crop_and_rotation(tmp_path, rotation):
    job = _legacy_job(tmp_path)
    asymmetric = tmp_path / f"asymmetric-{rotation}.pdf"
    with pikepdf.open(job["original"]) as document:
        for page in document.pages:
            page.CropBox = pikepdf.Array([25, 35, 375, 225])
            page.Rotate = rotation
        document.save(asymmetric)
    job["original"] = asymmetric
    run_strip(str(job["original"]), str(job["stripped"]))
    extracted = run_extract(str(job["original"]), str(tmp_path))
    job["segments"] = Path(extracted.segments_path)
    _render(job)
    source = _spans(job["original"], "KEEP")
    delivered = _spans(job["output"], "KEEP")
    assert len(source) == len(delivered) == 3
    for expected, actual in zip(source, delivered):
        assert actual["page"] == expected["page"]
        assert actual["font"] == expected["font"]
        assert actual["origin"] == pytest.approx(expected["origin"], abs=0.01)
        assert actual["bbox"] == pytest.approx(expected["bbox"], abs=0.01)


def test_legacy_retention_preserves_embedded_source_font(tmp_path):
    work = Path(tmp_path)
    font = _target_font(work / "embedded-source.ttf")
    original = work / "embedded-original.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=240)
        face = pymupdf.Font(fontfile=str(font))
        writer = pymupdf.TextWriter(page.rect)
        writer.append((40, 60), "EMBED", font=face, fontsize=14)
        writer.write_text(page)
        document.save(original)
    stripped = work / "embedded-stripped.pdf"
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(work))
    target = _target_font(work / "retention-target.ttf")
    mapping = work / "embedded-translations.json"
    mapping.write_text(json.dumps({
        "fonts": {role: str(target) for role in
                  ("regular", "bold", "italic", "bold_italic")},
        "lang": "es", "translations": {"EMBED": "EMBED"},
    }), encoding="utf-8")
    output = work / "embedded-out.pdf"
    run_retypeset(str(stripped), str(extracted.segments_path), str(mapping),
                  str(output), original=str(original))
    source = _spans(original, "EMBED")
    delivered = _spans(output, "EMBED")
    assert len(source) == len(delivered) == 1
    assert delivered[0]["origin"] == pytest.approx(source[0]["origin"], abs=0.01)
    assert delivered[0]["bbox"] == pytest.approx(source[0]["bbox"], abs=0.01)


def test_legacy_retention_does_not_reject_bbox_only_underline_contact(tmp_path):
    work = Path(tmp_path)
    font = _target_font(work / "underline-source.ttf")
    original = work / "underline-original.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=240)
        face = pymupdf.Font(fontfile=str(font))
        writer = pymupdf.TextWriter(page.rect)
        writer.append((40, 60), "KEEP", font=face, fontsize=14)
        writer.write_text(page)
        page.draw_line((40, 61), (160, 61), color=(0, 0, 0), width=0.5)
        document.save(original)
    with pymupdf.open(original) as document:
        trace = next(row for row in document[0].get_texttrace()
                     if "".join(chr(int(char[0])) for char in row["chars"]) == "KEEP")
    rule = pymupdf.Rect(40, 60.5, 160, 61.5)
    assert pymupdf.Rect(trace["bbox"]).intersects(rule)
    stripped = work / "underline-stripped.pdf"
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(work))
    target = _target_font(work / "retention-target.ttf")
    mapping = work / "underline-translations.json"
    mapping.write_text(json.dumps({
        "fonts": {role: str(target) for role in
                  ("regular", "bold", "italic", "bold_italic")},
        "lang": "es", "translations": {"KEEP": "KEEP"},
    }), encoding="utf-8")
    output = work / "underline-out.pdf"
    run_retypeset(str(stripped), str(extracted.segments_path), str(mapping),
                  str(output), original=str(original))
    source_rows, delivered_rows = _spans(original, "KEEP"), _spans(output, "KEEP")
    assert len(source_rows) == len(delivered_rows) == 1
    assert delivered_rows[0]["font"] == source_rows[0]["font"]
    assert delivered_rows[0]["origin"] == pytest.approx(source_rows[0]["origin"], abs=0.01)
    assert delivered_rows[0]["bbox"] == pytest.approx(source_rows[0]["bbox"], abs=0.01)
    clip = pymupdf.Rect(35, 45, 170, 75)
    with pymupdf.open(original) as source_doc, pymupdf.open(output) as output_doc:
        source_pixels = source_doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2),
                                                 clip=clip, alpha=False).samples
        output_pixels = output_doc[0].get_pixmap(matrix=pymupdf.Matrix(2, 2),
                                                 clip=clip, alpha=False).samples
    assert output_pixels == source_pixels


def _single_tj_job(work: Path, text: bytes) -> dict[str, Path]:
    work.mkdir(parents=True, exist_ok=True)
    seed = work / "seed.pdf"
    with pymupdf.open() as document:
        document.new_page(width=800, height=240).insert_text(
            (40, 60), "SEED", fontname="helv", fontsize=14)
        document.save(seed)
    original = work / "single-tj-original.pdf"
    with pikepdf.open(seed) as document:
        page = document.pages[0]
        page.Contents = document.make_stream(
            b"BT /helv 14 Tf 1 0 0 1 40 180 Tm " + text + b" TJ ET"
        )
        document.save(original)
    stripped = work / "single-tj-stripped.pdf"
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(work))
    target = _target_font(work / "retention-target.ttf")
    mapping = work / "single-tj-translations.json"
    mapping.write_text(json.dumps({
        "fonts": {role: str(target) for role in
                  ("regular", "bold", "italic", "bold_italic")},
        "lang": "es", "translations": {"KEEP": "KEEP", "CHANGE": "Changed"},
    }), encoding="utf-8")
    return {"original": original, "stripped": stripped,
            "segments": Path(extracted.segments_path), "mapping": mapping,
            "output": work / "single-tj-out.pdf"}


def test_legacy_retention_replays_two_spatial_occurrences_from_one_tj(tmp_path):
    job = _single_tj_job(tmp_path, b"[(KEEP) -15000 (KEEP)]")
    segments = json.loads(job["segments"].read_text(encoding="utf-8"))
    keeps = [item for item in segments["segments"] if item["text"].strip() == "KEEP"]
    assert len(keeps) == 2
    _render(job)
    source, delivered = _spans(job["original"], "KEEP"), _spans(job["output"], "KEEP")
    assert len(source) == len(delivered) == 2
    for expected, actual in zip(source, delivered):
        assert actual["font"] == expected["font"]
        assert actual["origin"] == pytest.approx(expected["origin"], abs=0.01)
        assert actual["bbox"] == pytest.approx(expected["bbox"], abs=0.01)


def test_legacy_retention_slices_mixed_text_from_one_tj(tmp_path):
    job = _single_tj_job(tmp_path, b"[(KEEP) -15000 (CHANGE)]")
    segments = json.loads(job["segments"].read_text(encoding="utf-8"))
    assert {item["text"].strip() for item in segments["segments"]} >= {"KEEP", "CHANGE"}
    _render(job)
    source_keep, delivered_keep = _spans(job["original"], "KEEP"), _spans(job["output"], "KEEP")
    assert len(source_keep) == len(delivered_keep) == 1
    assert delivered_keep[0]["font"] == source_keep[0]["font"]
    assert delivered_keep[0]["origin"] == pytest.approx(source_keep[0]["origin"], abs=0.01)
    assert delivered_keep[0]["bbox"] == pytest.approx(source_keep[0]["bbox"], abs=0.01)
    assert len(_spans(job["output"], "Changed")) == 1
    assert not _spans(job["output"], "CHANGE")


def test_legacy_retention_preserves_contiguous_mixed_font_source_segment(tmp_path):
    work = Path(tmp_path)
    original = work / "mixed-font-original.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=800, height=240)
        page.insert_text((40, 60), "KEEP", fontname="helv", fontsize=14)
        x = 40 + pymupdf.get_text_length("KEEP", fontname="helv", fontsize=14)
        page.insert_text((x, 60), " CHANGE", fontname="hebo", fontsize=14)
        document.save(original)
    stripped = work / "mixed-font-stripped.pdf"
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(work))
    segments = json.loads(Path(extracted.segments_path).read_text(encoding="utf-8"))
    logical = [item for item in segments["segments"]
               if item["text"].strip() == "KEEP CHANGE"]
    assert len(logical) == 1
    target = _target_font(work / "retention-target.ttf")
    mapping = work / "mixed-font-translations.json"
    mapping.write_text(json.dumps({
        "fonts": {role: str(target) for role in
                  ("regular", "bold", "italic", "bold_italic")},
        "lang": "es", "translations": {"KEEP CHANGE": "KEEP CHANGE"},
    }), encoding="utf-8")
    output = work / "mixed-font-out.pdf"
    run_retypeset(str(stripped), str(extracted.segments_path), str(mapping),
                  str(output), original=str(original))
    for text in ("KEEP", " CHANGE"):
        source, delivered = _spans(original, text), _spans(output, text)
        assert len(source) == len(delivered) == 1
        assert delivered[0]["font"] == source[0]["font"]
        assert delivered[0]["origin"] == pytest.approx(source[0]["origin"], abs=0.01)
        assert delivered[0]["bbox"] == pytest.approx(source[0]["bbox"], abs=0.01)


def test_legacy_retention_joins_adjacent_same_font_bt_span(tmp_path):
    work = Path(tmp_path)
    original = work / "same-font-original.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=800, height=240)
        page.insert_text((40, 60), "KEEP", fontname="helv", fontsize=14)
        x = 40 + pymupdf.get_text_length("KEEP", fontname="helv", fontsize=14)
        page.insert_text((x, 60), " CHANGE", fontname="helv", fontsize=14)
        document.save(original)
    source_spans = [span for span in _spans(original, "KEEP CHANGE")]
    assert len(source_spans) == 1
    stripped = work / "same-font-stripped.pdf"
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(work))
    segments = json.loads(Path(extracted.segments_path).read_text(encoding="utf-8"))
    logical = [item for item in segments["segments"]
               if item["text"].strip() == "KEEP CHANGE"]
    assert len(logical) == 1
    target = _target_font(work / "retention-target.ttf")
    mapping = work / "same-font-translations.json"
    mapping.write_text(json.dumps({
        "fonts": {role: str(target) for role in
                  ("regular", "bold", "italic", "bold_italic")},
        "lang": "es", "translations": {"KEEP CHANGE": "KEEP CHANGE"},
    }), encoding="utf-8")
    output = work / "same-font-out.pdf"
    run_retypeset(str(stripped), str(extracted.segments_path), str(mapping),
                  str(output), original=str(original))
    delivered = _spans(output, "KEEP CHANGE")
    assert len(delivered) == 1
    assert delivered[0]["font"] == source_spans[0]["font"]
    assert delivered[0]["origin"] == pytest.approx(source_spans[0]["origin"], abs=0.01)
    assert delivered[0]["bbox"] == pytest.approx(source_spans[0]["bbox"], abs=0.01)


def test_legacy_retention_refuses_tampered_final_publication_atomically(tmp_path, monkeypatch):
    job = _legacy_job(tmp_path)
    prior = b"prior-output-bytes"
    job["output"].write_bytes(prior)
    prior_report = b"prior-report-for-prior-output"
    report = tmp_path / "scale_report.json"
    report.write_bytes(prior_report)
    source_keep = _spans(job["original"], "KEEP")[0]
    module = importlib.import_module("pdf_translate.retypeset")
    original_canonicalize = module.canonicalize_text_layer

    def tamper(path, fontfiles, texts, **kwargs):
        result = original_canonicalize(path, fontfiles, texts, **kwargs)
        tampered = tmp_path / "tampered-final.pdf"
        with pymupdf.open(path) as document:
            page = document[source_keep["page"]]
            page.draw_rect(pymupdf.Rect(source_keep["bbox"]),
                           color=(1, 1, 1), fill=(1, 1, 1), overlay=True)
            document.save(tampered)
        tampered.replace(path)
        return result

    monkeypatch.setattr(module, "canonicalize_text_layer", tamper)
    # With no rebuild left, the final check's refusal stands and is atomic.
    monkeypatch.setattr(module, "_RETAINED_OUTPUT_RETRIES", 0)
    with pytest.raises(MappingError) as caught:
        _render(job)
    assert caught.value.refusals["source_retention"]
    assert caught.value.refusals["source_retention"][0]["reason"] == "retained-output"
    assert job["output"].read_bytes() == prior
    assert report.read_bytes() == prior_report


def test_retained_run_the_final_file_cannot_vouch_is_redrawn_not_refused(tmp_path, monkeypatch):
    # Regression (dogfood F1, 2026-10-09): one retained run covered in the
    # delivered file failed the paid job at the final check. The build now
    # runs again with that run redrawn; the other runs stay retained.
    job = _legacy_job(tmp_path)
    source_keep = _spans(job["original"], "KEEP")[0]
    module = importlib.import_module("pdf_translate.retypeset")
    original_canonicalize = module.canonicalize_text_layer
    calls = []

    def tamper_once(path, fontfiles, texts, **kwargs):
        result = original_canonicalize(path, fontfiles, texts, **kwargs)
        calls.append(path)
        if len(calls) == 1:
            tampered = tmp_path / "tampered-final.pdf"
            with pymupdf.open(path) as document:
                document[source_keep["page"]].draw_rect(
                    pymupdf.Rect(source_keep["bbox"]), color=(1, 1, 1), fill=(1, 1, 1),
                    overlay=True)
                document.save(tampered)
            tampered.replace(path)
        return result

    monkeypatch.setattr(module, "canonicalize_text_layer", tamper_once)
    result = run_retypeset(str(job["stripped"]), str(job["segments"]), str(job["mapping"]),
                           str(job["output"]), original=str(job["original"]))
    assert len(calls) == 2
    rows = [row for row in result.warnings if row["kind"] == "source_not_retained"]
    assert [(row["page"], row["core"], row["reason"]) for row in rows] == [
        (source_keep["page"], "KEEP", "retained-output")]
    fonts = sorted((span["page"], span["font"] == source_keep["font"])
                   for span in _spans(job["output"], "KEEP"))
    assert fonts == [(0, False), (1, True), (1, True)]


def test_legacy_retention_keeps_winansi_accented_identity(tmp_path):
    work = Path(tmp_path)
    original = work / "accent-original.pdf"
    with pymupdf.open() as document:
        document.new_page(width=400, height=240).insert_text(
            (40, 60), "CAFÉ", fontname="helv", fontsize=14)
        document.save(original)
    stripped = work / "accent-stripped.pdf"
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(work))
    target = _target_font(work / "retention-target.ttf")
    mapping = work / "accent-translations.json"
    mapping.write_text(json.dumps({
        "fonts": {role: str(target) for role in
                  ("regular", "bold", "italic", "bold_italic")},
        "lang": "es", "translations": {"CAFÉ": "CAFÉ"},
    }), encoding="utf-8")
    output = work / "accent-out.pdf"
    run_retypeset(str(stripped), str(extracted.segments_path), str(mapping),
                  str(output), original=str(original))
    source, delivered = _spans(original, "CAFÉ"), _spans(output, "CAFÉ")
    assert len(source) == len(delivered) == 1
    assert delivered[0]["font"] == source[0]["font"]
    assert delivered[0]["bbox"] == pytest.approx(source[0]["bbox"], abs=0.01)


def test_legacy_retention_skips_unrelated_source_multiple_show(tmp_path):
    job = _legacy_job(tmp_path)
    unrelated = tmp_path / "unrelated-source.pdf"
    with pikepdf.open(job["original"]) as document:
        page = document.pages[0]
        contents = page.Contents
        streams = contents if isinstance(contents, pikepdf.Array) else [contents]
        stream = b"\n".join(item.read_bytes() for item in streams)
        page.Contents = document.make_stream(
            stream + b"\nBT /helv 14 Tf 1 0 0 1 40 200 Tm (CHANGE) Tj (CHANGE) Tj ET\n"
        )
        document.save(unrelated)
    _render({**job, "original": unrelated})
    assert len(_spans(job["output"], "KEEP")) == 3


@pytest.mark.parametrize("matrix", (b"0 -1 1 0 0 240 cm", b"0.75 0 0 -0.75 0 240 cm"))
def test_legacy_retention_preserves_source_outer_flip_scale(tmp_path, matrix):
    job = _legacy_job(tmp_path)
    transformed = tmp_path / "outer-context-source.pdf"
    with pikepdf.open(job["original"]) as document:
        for page in document.pages:
            contents = page.Contents
            streams = contents if isinstance(contents, pikepdf.Array) else [contents]
            stream = b"\n".join(item.read_bytes() for item in streams)
            page.Contents = document.make_stream(b"q\n" + matrix + b"\n" + stream + b"\nQ\n")
        document.save(transformed)
    stripped = tmp_path / "outer-context-stripped.pdf"
    run_strip(str(transformed), str(stripped))
    extracted = run_extract(str(transformed), str(tmp_path))
    job["original"], job["stripped"], job["segments"] = transformed, stripped, Path(extracted.segments_path)
    _render(job)
    source = _spans(transformed, "KEEP")
    delivered = _spans(job["output"], "KEEP")
    assert len(source) == len(delivered) == 3
    for expected, actual in zip(source, delivered):
        assert actual["origin"] == pytest.approx(expected["origin"], abs=0.01)
        assert actual["bbox"] == pytest.approx(expected["bbox"], abs=0.01)


def test_legacy_retention_refuses_stale_rotated_extraction_before_output(tmp_path):
    job = _legacy_job(tmp_path)
    stale = tmp_path / "stale-stripped.pdf"
    with pymupdf.open(job["stripped"]) as document:
        document[1].set_rotation(90)
        document.save(stale)

    with pytest.raises(MappingError) as caught:
        run_retypeset(
            str(stale),
            str(job["segments"]),
            str(job["mapping"]),
            str(job["output"]),
            original=str(job["original"]),
        )
    assert caught.value.refusals["stale_extraction"]
    assert not job["output"].exists()


def test_legacy_retention_refuses_stale_same_origin_font_size(tmp_path):
    job = _legacy_job(tmp_path)
    stale = tmp_path / "stale-font-source.pdf"
    with pikepdf.open(job["original"]) as document:
        page = document.pages[0]
        contents = page.Contents
        streams = contents if isinstance(contents, pikepdf.Array) else [contents]
        stream = b"\n".join(item.read_bytes() for item in streams)
        assert b"14 Tf" in stream
        page.Contents = document.make_stream(stream.replace(b"14 Tf", b"24 Tf", 1))
        document.save(stale)
    _assert_first_keep_redrawn(job, "stale-occurrence", stripped=str(job["stripped"]), original=str(stale))


def test_legacy_retention_refuses_transformed_q_cm_context(tmp_path):
    job = _legacy_job(tmp_path)
    transformed = tmp_path / "transformed-source.pdf"
    with pikepdf.open(job["original"]) as document:
        page = document.pages[0]
        contents = page.Contents
        streams = contents if isinstance(contents, pikepdf.Array) else [contents]
        stream = b"\n".join(item.read_bytes() for item in streams)
        page.Contents = document.make_stream(
            b"q\n2 0 0 2 0 0 cm\n" + stream + b"\nQ\n"
        )
        document.save(transformed)

    _assert_first_keep_redrawn(job, None, stripped=str(job["stripped"]), original=str(transformed))


def test_legacy_retention_refuses_later_opaque_paint_over_source(tmp_path):
    job = _legacy_job(tmp_path)
    covered = tmp_path / "covered-source.pdf"
    keep = _spans(job["original"], "KEEP")[0]
    x0, y0, x1, y1 = keep["bbox"]
    with pymupdf.open(job["original"]) as source_document:
        height = source_document[keep["page"]].mediabox.height
    pdf_y0, pdf_y1 = height - y1, height - y0
    with pikepdf.open(job["original"]) as document:
        page = document.pages[0]
        contents = page.Contents
        streams = contents if isinstance(contents, pikepdf.Array) else [contents]
        stream = b"\n".join(item.read_bytes() for item in streams)
        page.Contents = document.make_stream(stream + (
            f"\nq\n{x0} {pdf_y0} {x1 - x0} {pdf_y1 - pdf_y0} re\nf\nQ\n"
        ).encode("ascii"))
        document.save(covered)
    _assert_first_keep_redrawn(job, "covered-source", stripped=str(job["stripped"]), original=str(covered))


def test_legacy_retention_refuses_inherited_text_scale_state(tmp_path):
    job = _legacy_job(tmp_path)
    scaled = tmp_path / "scaled-source.pdf"
    with pikepdf.open(job["original"]) as document:
        page = document.pages[0]
        contents = page.Contents
        streams = contents if isinstance(contents, pikepdf.Array) else [contents]
        stream = b"\n".join(item.read_bytes() for item in streams)
        page.Contents = document.make_stream(b"80 Tz\n" + stream)
        document.save(scaled)
    _assert_first_keep_redrawn(job, "inherited-state", stripped=str(job["stripped"]), original=str(scaled))


def test_legacy_retention_refuses_multiple_source_shows(tmp_path):
    job = _legacy_job(tmp_path)
    malformed = tmp_path / "multiple-show-source.pdf"
    with pikepdf.open(job["original"]) as document:
        page = document.pages[0]
        contents = page.Contents
        streams = contents if isinstance(contents, pikepdf.Array) else [contents]
        stream = b"\n".join(item.read_bytes() for item in streams)
        token = b"TJ" if b"TJ" in stream else b"Tj"
        page.Contents = document.make_stream(stream.replace(
            token, token + b" (KEEP) " + token, 1))
        document.save(malformed)
    _assert_first_keep_redrawn(job, "multiple-show", stripped=str(job["stripped"]), original=str(malformed))


def test_legacy_retention_cancellation_has_no_output(tmp_path):
    job = _legacy_job(tmp_path)
    result = run_retypeset(
        str(job["stripped"]),
        str(job["segments"]),
        str(job["mapping"]),
        str(job["output"]),
        original=str(job["original"]),
        cancel=lambda: True,
    )
    assert result.cancelled is True
    assert result.output is None
    assert not job["output"].exists()


def test_legacy_retention_tokenizes_et_inside_literal_text(tmp_path):
    work = Path(tmp_path)
    original = work / "original.pdf"
    authored = work / "authored.pdf"
    text = "KEEP ET KEEP"
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=240)
        page.insert_text((40, 60), text, fontname="helv", fontsize=14)
        document.save(authored)
    with pikepdf.open(authored) as document:
        page = document.pages[0]
        body = pikepdf.unparse_content_stream(pikepdf.parse_content_stream(page))
        assert b"(KEEP ET KEEP)" in body, body
        page.Contents = document.make_stream(body)
        document.save(original)
    stripped = work / "stripped.pdf"
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(work))
    mapping = work / "translations.json"
    target_font = _target_font(work / "retention-target.ttf")
    mapping.write_text(json.dumps({
        "fonts": {role: str(target_font) for role in
                  ("regular", "bold", "italic", "bold_italic")},
        "lang": "es", "translations": {text: text},
    }), encoding="utf-8")
    job = {"original": original, "stripped": stripped,
           "segments": Path(extracted.segments_path), "mapping": mapping,
           "output": work / "out.pdf"}
    expected = _spans(original, text)
    assert len(expected) == 1
    _render(job)
    actual = _spans(job["output"], text)
    assert len(actual) == 1
    assert actual[0] == expected[0]


@pytest.mark.parametrize("mutation", ("encoding", "widths", "tounicode"))
def test_legacy_retention_refuses_changed_same_alias_resource(tmp_path, mutation):
    job = _legacy_job(tmp_path)
    conflicting = tmp_path / "conflicting-stripped.pdf"
    with pikepdf.open(job["original"]) as source_pdf:
        alias = next(iter(source_pdf.pages[0].Resources.Font.keys()))
    with pikepdf.open(job["stripped"]) as document:
        page = document.pages[0]
        fonts = page.Resources.get("/Font")
        if fonts is None:
            fonts = pikepdf.Dictionary()
            page.Resources.Font = fonts
        changed = pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name("/Type1"),
            BaseFont=pikepdf.Name("/Helvetica"),
        )
        if mutation == "encoding":
            changed.Encoding = pikepdf.Name("/Symbol")
        elif mutation == "widths":
            changed.FirstChar = 0
            changed.LastChar = 255
            changed.Widths = pikepdf.Array([500] * 256)
        else:
            changed.ToUnicode = document.make_stream(
                b"/CIDInit /ProcSet findresource begin"
            )
        fonts[alias] = document.make_indirect(changed)
        document.save(conflicting)

    _assert_first_keep_redrawn(job, "resource-collision", stripped=str(conflicting), original=str(job["original"]))


def test_authored_numeric_passthrough_translation_is_not_retained(tmp_path):
    original = tmp_path / "original.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=240)
        page.insert_text((40, 60), "2024", fontname="helv", fontsize=14)
        document.save(original)
    stripped = tmp_path / "stripped.pdf"
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(tmp_path))
    segments = json.loads(Path(extracted.segments_path).read_text())
    assert len(segments["segments"]) == 1
    assert segments["segments"][0]["passthrough"] is True
    target = _target_font(tmp_path / "target.ttf")
    mapping = tmp_path / "mapping.json"
    mapping.write_text(json.dumps({"lang": "es", "fonts": {"regular": str(target)},
                                  "translations": {"2024": "2025"}}))
    output = tmp_path / "output.pdf"
    run_retypeset(str(stripped), extracted.segments_path, str(mapping), str(output),
                  original=str(original))
    assert not _spans(output, "2024")
    assert len(_spans(output, "2025")) == 1
