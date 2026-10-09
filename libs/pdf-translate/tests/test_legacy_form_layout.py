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
