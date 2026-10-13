"""RED layout regressions for translated labels beside legacy form controls.

These tests exercise the public retypeset entry point with ordinary horizontal
labels.  The geometry is authored by the fixture, while the assertions inspect
the produced text layer and the actual control rectangles; no product crop or
segment identifiers are involved.
"""

from __future__ import annotations

import json
import string
from pathlib import Path

import pymupdf
import pytest
from fontTools.ttLib import TTFont

from pdf_translate import run_extract, run_retypeset, run_strip
from pdf_translate.results import PlacementError

from .test_source_retention import _spans, _target_font


def _job(work: Path, *, control: str) -> dict[str, Path | tuple[float, ...]]:
    target = _target_font(work / "form-target.ttf", chars=" " + string.ascii_letters + string.digits)
    original = work / "form-original.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=260, height=180)
        if control == "checkbox":
            # The source label ends before this empty closed control.  A
            # longer ordinary translation must remain outside its interior.
            page.draw_rect(pymupdf.Rect(84, 42, 144, 72), color=(0, 0, 0), width=1)
            page.insert_text((40, 62), "YES", fontname="helv", fontsize=14)
            obstacle = (84.0, 42.0, 144.0, 72.0)
            mapping = {"YES": "YES translated label"}
        else:
            # The source glyphs legitimately reach the nearby rule.  This
            # prevents a blanket source-overlap rejection from satisfying the
            # test while the longer target must still clear the rule area.
            # The short source label is left of the writing blank.  The rule
            # begins at x=84; a nearby stroke at x=40..80 touches only the
            # source font's nominal box, not its printed glyph ink.
            page.draw_line((40, 64.5), (80, 64.5), color=(0, 0, 0), width=1)
            page.draw_line((84, 65), (190, 65), color=(0, 0, 0), width=1)
            page.insert_text((40, 62), "Name", fontname="helv", fontsize=14)
            obstacle = (84.0, 49.0, 190.0, 64.5)
            mapping = {"Name": "Name translated"}
        document.save(original)

    stripped = work / "form-stripped.pdf"
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(work))
    segments = Path(extracted.segments_path)
    mapping_path = work / "form-translations.json"
    mapping_path.write_text(
        json.dumps(
            {
                "fonts": {role: str(target) for role in (
                    "regular", "bold", "italic", "bold_italic"
                )},
                "lang": "es",
                "translations": mapping,
            }
        ),
        encoding="utf-8",
    )
    return {
        "original": original,
        "stripped": stripped,
        "segments": segments,
        "mapping": mapping_path,
        "output": work / "form-output.pdf",
        "obstacle": obstacle,
        "source_text": next(iter(mapping)),
        "target_text": next(iter(mapping.values())),
    }


def _render(job: dict[str, Path | tuple[float, ...]]) -> None:
    run_retypeset(
        str(job["stripped"]),
        str(job["segments"]),
        str(job["mapping"]),
        str(job["output"]),
        original=str(job["original"]),
    )


def _rect(row: dict) -> pymupdf.Rect:
    return pymupdf.Rect(*row["bbox"])


def _crop_bytes(path: Path, clip: tuple[float, ...]) -> bytes:
    with pymupdf.open(path) as document:
        pixmap = document[0].get_pixmap(
            matrix=pymupdf.Matrix(2, 2),
            clip=pymupdf.Rect(*clip),
            alpha=False,
        )
        return bytes(pixmap.samples)


def _is_scale_floor_refusal(exc: PlacementError) -> bool:
    """Accept only a refusal carrying an explicit below-floor scale value."""
    rows = getattr(exc, 'refusals', {}).get('overflow', [])
    return bool(rows) and all(isinstance(row.get('scale'), (int, float)) and
                             0 < row['scale'] < 0.7 for row in rows)


def _assert_target_clear(job: dict[str, Path | tuple[float, ...]]) -> None:
    target = _spans(Path(job["output"]), str(job["target_text"]))
    assert target, "translated label must remain visible in the output text layer"


def test_translated_label_stays_outside_empty_closed_checkbox(tmp_path):
    job = _job(tmp_path, control="checkbox")
    source = _spans(Path(job["original"]), str(job["source_text"]))
    assert len(source) == 1
    assert not _rect(source[0]).intersects(pymupdf.Rect(*job["obstacle"]))
    try:
        _render(job)
    except PlacementError as exc:
        assert _is_scale_floor_refusal(exc)
        return
    _assert_target_clear(job)
    assert _crop_bytes(Path(job["original"]), job["obstacle"]) == _crop_bytes(
        Path(job["output"]), job["obstacle"]
    )


def test_translated_label_clears_adjacent_writing_underline(tmp_path):
    job = _job(tmp_path, control="underline")
    source = _spans(Path(job["original"]), str(job["source_text"]))
    assert len(source) == 1
    assert not _rect(source[0]).intersects(pymupdf.Rect(*job["obstacle"]))
    # The source crop contains the nearby authored stroke, while the
    # protected writing blank itself is empty before translation.
    assert any(_crop_bytes(Path(job["original"]), clip) for clip in (
        (40.0, 60.0, 80.0, 66.0),
    ))
    try:
        _render(job)
    except PlacementError as exc:
        assert _is_scale_floor_refusal(exc)
        return
    _assert_target_clear(job)
    assert _crop_bytes(Path(job["original"]), job["obstacle"]) == _crop_bytes(
        Path(job["output"]), job["obstacle"]
    )


def test_translated_caption_preserves_source_vertical_ink_clearance(tmp_path):
    source_font = _target_font(tmp_path / 'source-font.ttf')
    with TTFont(source_font) as face:
        for name in face.getGlyphOrder():
            glyph = face['glyf'][name]
            if glyph.numberOfContours > 0:
                glyph.coordinates = type(glyph.coordinates)((x, y * 5 / 7) for x, y in glyph.coordinates)
        face.save(tmp_path / 'source-short.ttf')
    target = _target_font(tmp_path / 'target.ttf')
    original = tmp_path / 'original.pdf'
    with pymupdf.open() as document:
        page = document.new_page(width=260, height=180)
        page.insert_font(fontname='source', fontfile=str(tmp_path / 'source-short.ttf'))
        page.insert_text((40, 62), 'CAP', fontname='source', fontsize=14)
        document.save(original)
    stripped = tmp_path / 'stripped.pdf'
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(tmp_path))
    mapping = tmp_path / 'mapping.json'
    mapping.write_text(json.dumps({'lang': 'es', 'fonts': {'regular': str(target)},
                                  'translations': {'CAP': 'Changed'}}))
    job = dict(original=original, stripped=stripped, segments=Path(extracted.segments_path),
               mapping=mapping, output=tmp_path / 'output.pdf', target_text='Changed',
               obstacle=(40, 54, 85, 54.5))
    # Original actual glyph tops are at55; the taller target enters this quiet
    # band despite an unchanged baseline and ample horizontal page room.
    assert set(_crop_bytes(original, job['obstacle'])) == {255}
    _render(job)
    _assert_target_clear(job)
    assert _crop_bytes(original, job['obstacle']) == _crop_bytes(job['output'], job['obstacle'])


def _span_size(path: Path, text: str) -> float:
    with pymupdf.open(path) as document:
        sizes = [span['size'] for block in document[0].get_text('dict')['blocks'] if block['type'] == 0
                 for line in block['lines'] for span in line['spans'] if span['text'] == text]
    assert len(sizes) == 1, sizes
    return sizes[0]


def test_short_letter_line_may_rise_to_its_faces_measured_cap_line(tmp_path):
    # Capitals ink to 700 units, lowercase to 500. "accuracy" inks no higher
    # than its x-height, but its face prints a flat capital at the same size on
    # the line above, so a translation with a capital reaches the cap line at
    # full size instead of shrinking to x-height (irs-fw4 page 2, 2026-10-13).
    source_font = _target_font(tmp_path / 'source-font.ttf', chars=' HEADacury')
    with TTFont(source_font) as face:
        lowercase = {f'g{ord(char):04x}' for char in 'acury'}
        for name in lowercase:
            glyph = face['glyf'][name]
            glyph.coordinates = type(glyph.coordinates)((x, y * 5 / 7) for x, y in glyph.coordinates)
        face.save(tmp_path / 'source-mixed.ttf')
    target = _target_font(tmp_path / 'target.ttf')
    original = tmp_path / 'original.pdf'
    with pymupdf.open() as document:
        page = document.new_page(width=260, height=180)
        page.insert_font(fontname='source', fontfile=str(tmp_path / 'source-mixed.ttf'))
        page.insert_text((40, 36), 'HEAD', fontname='source', fontsize=14)
        page.insert_text((40, 62), 'accuracy', fontname='source', fontsize=14)
        document.save(original)
    stripped = tmp_path / 'stripped.pdf'
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(tmp_path))
    mapping = tmp_path / 'mapping.json'
    mapping.write_text(json.dumps({'lang': 'es', 'fonts': {'regular': str(target)},
                                  'translations': {'HEAD': 'DEBE', 'accuracy': 'Changed'}}))
    job = dict(original=original, stripped=stripped, segments=Path(extracted.segments_path),
               mapping=mapping, output=tmp_path / 'output.pdf', target_text='Changed')
    _render(job)
    _assert_target_clear(job)
    # Without the measured cap line the 700-unit target is capped at the
    # 500-unit source ink: drawn at 14 * 5/7 = 10.
    assert _span_size(job['output'], 'Changed') == pytest.approx(14, abs=0.05)
    # Still never above the face's cap line: the band between it and the line
    # above stays as blank as in the source.
    band = (40, 42, 160, 51.6)
    assert set(_crop_bytes(original, band)) == {255}
    assert _crop_bytes(original, band) == _crop_bytes(job['output'], band)


@pytest.mark.parametrize('control', ['checkbox', 'underline'])
@pytest.mark.parametrize('overhang', [False, True])
def test_translated_last_glyph_ink_clears_source_field(tmp_path, control, overhang):
    target = _target_font(tmp_path / 'field-target.ttf')
    if overhang:
        # Keep the advance and every other glyph unchanged. Only the last
        # translated letter now paints beyond its nominal advance.
        from fontTools.pens.ttGlyphPen import TTGlyphPen

        with TTFont(target) as face:
            pen = TTGlyphPen(None)
            pen.moveTo((80, 0))
            pen.lineTo((80, 700))
            pen.lineTo((1400, 700))
            pen.lineTo((1400, 0))
            pen.closePath()
            face['glyf'][face.getBestCmap()[ord('d')]] = pen.glyph()
            face.save(target)
    original = tmp_path / 'field-original.pdf'
    with pymupdf.open() as document:
        page = document.new_page(width=260, height=180)
        if control == 'checkbox':
            obstacle = (90., 50., 102., 62.)
            page.draw_rect(pymupdf.Rect(*obstacle), color=(0, 0, 0), width=1)
        else:
            page.draw_line((90, 62), (165, 62), color=(0, 0, 0), width=1)
            obstacle = (90., 50., 165., 61.5)
        page.insert_text((40, 60), 'CHANGE', fontname='helv', fontsize=9)
        document.save(original)
    stripped = tmp_path / 'field-stripped.pdf'
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(tmp_path))
    mapping = tmp_path / 'field-mapping.json'
    mapping.write_text(json.dumps({'lang': 'es', 'fonts': {'regular': str(target)},
                                  'translations': {'CHANGE': 'Changed'}}))
    job = dict(original=original, stripped=stripped, segments=Path(extracted.segments_path),
               mapping=mapping, output=tmp_path / 'field-output.pdf', target_text='Changed')
    _render(job)
    spans = _spans(job['output'], 'Changed')
    assert spans, 'the translated label must be placed'
    with pymupdf.open(job['output']) as document:
        sizes = [span['size'] for page in document
                 for block in page.get_text('dict')['blocks'] if block['type'] == 0
                 for line in block['lines'] for span in line['spans']
                 if span['text'] == 'Changed']
    assert sizes and min(sizes) >= .75 * 9
    assert _crop_bytes(original, obstacle) == _crop_bytes(job['output'], obstacle)


def _glyph_boxes(path: Path, text: str) -> list[pymupdf.Rect]:
    with pymupdf.open(path) as document:
        return [pymupdf.Rect(char['bbox'])
                for block in document[0].get_text('rawdict')['blocks'] if block['type'] == 0
                for line in block['lines']
                if text in ''.join(char['c'] for span in line['spans'] for char in span['chars'])
                for span in line['spans'] for char in span['chars'] if char['c'].strip()]


@pytest.mark.parametrize('cell', ['stroked', 'filled'])
def test_mixed_bold_line_stays_out_of_a_drawn_answer_cell(tmp_path, cell):
    # A drawn amount cell whose fillable widget covers only its bottom, beside
    # a caption whose translation mixes bold and regular text. The writing area
    # is the whole drawn cell, so the line shrinks to end before it (irs-fw4
    # Step 4(a)/(b), 2026-10-13). A filled background is not a barrier: there
    # the same line keeps its full size and runs on.
    target = _target_font(tmp_path / 'target.ttf', chars=' ' + string.ascii_letters)
    original = tmp_path / 'original.pdf'
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=120)
        if cell == 'stroked':
            page.draw_rect(pymupdf.Rect(150, 44, 230, 76), color=(0, 0, 0), width=1)
        else:
            page.draw_rect(pymupdf.Rect(150, 44, 230, 76), color=None, fill=(.9, .9, .9))
        widget = pymupdf.Widget()
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.field_name = 'amount'
        widget.rect = pymupdf.Rect(155, 66, 228, 75)
        page.add_widget(widget)
        page.insert_text((40, 60), 'Amount', fontname='helv', fontsize=9)
        document.save(original)
    stripped = tmp_path / 'stripped.pdf'
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(tmp_path))
    mapping = tmp_path / 'mapping.json'
    mapping.write_text(json.dumps({'lang': 'es', 'fonts': {role: str(target) for role in (
        'regular', 'bold', 'italic', 'bold_italic')},
        'translations': {'Amount': '<b>Pay</b> amount each check'}}))
    job = dict(original=original, stripped=stripped, segments=Path(extracted.segments_path),
               mapping=mapping, output=tmp_path / 'output.pdf', target_text='Pay amount each check')
    _render(job)
    boxes = _glyph_boxes(job['output'], 'Pay amount each check')
    assert boxes, 'the translated line must be placed'
    right = max(box.x1 for box in boxes)
    size = _span_size(job['output'], 'Pay amount each check')
    if cell == 'stroked':
        # 21 advances of the 0.76/0.36 em test face are 132.8 pt at 9 pt; the
        # cell leaves 108.5, so the line is drawn at about 0.82 of its size.
        assert right <= 150
        assert .75 * 9 <= size < 8.5
    else:
        assert right > 150
        assert size == pytest.approx(9, abs=.05)
    with pymupdf.open(job['output']) as document:
        assert [w.rect for w in document[0].widgets()] == [pymupdf.Rect(155, 66, 228, 75)]
