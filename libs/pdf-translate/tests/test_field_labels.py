"""Labels beside form controls keep off them (fitting ladder release 2).

Both defects shipped silently as complete documents in the Spanish corpus of 2026-10-20: a
printed checkbox in a dingbat font was translated with its label and drawn as an apostrophe
over which the label started (AO 240), and a "label ____" line grew its longer translation
onto the field over its blank (HUD-92900-A). Neither raises anything; only where the ink
lands shows it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf

from pdf_translate import run_extract, run_retypeset, run_strip

FONTS = Path(__file__).resolve().parents[3] / 'apps' / 'engine' / 'pdft_engine' / 'assets' / 'fonts'
FULL = {role: FONTS / f'FormIntactSans-{name}.ttf' for role, name in (
    ('regular', 'Regular'), ('bold', 'Bold'), ('italic', 'Italic'), ('bold_italic', 'BoldItalic'))}


def _source(path: Path) -> pymupdf.Rect:
    """A dingbat box before "Yes", and "Name: ______" with a text field over its blank.
    Returns the field's rectangle."""
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=200)
        page.insert_text((40, 60), 'o', fontname='zadb', fontsize=11)
        page.insert_text((52, 60), 'Yes', fontname='helv', fontsize=11)
        page.insert_text((40, 120), 'Name: ' + '_' * 30, fontname='helv', fontsize=11)
        start = 40 + pymupdf.get_text_length('Name: ', fontname='helv', fontsize=11)
        field = pymupdf.Widget()
        field.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        field.field_name = 'name'
        field.rect = pymupdf.Rect(start, 108, 300, 122)
        page.add_widget(field)
        document.save(path)
    return pymupdf.Rect(start, 108, 300, 122)


def _segments(work: Path, original: Path) -> list[dict]:
    data = json.loads(Path(run_extract(str(original), str(work)).segments_path).read_text(encoding='utf-8'))
    return data['segments'] if isinstance(data, dict) else data


def test_a_dingbat_box_is_its_own_drawing_and_its_label_starts_after_it(tmp_path):
    original = tmp_path / 'original.pdf'
    _source(original)
    segments = _segments(tmp_path, original)
    box = [s for s in segments if s.get('drawing')]
    assert len(box) == 1 and box[0]['passthrough']
    label = next(s for s in segments if s['core'] == 'Yes')
    assert label['origin'][0] >= box[0]['bbox'][2] - 0.5


def test_a_label_before_a_field_stops_where_the_field_starts(tmp_path):
    original = tmp_path / 'original.pdf'
    field = _source(original)
    segments = _segments(tmp_path, original)
    name = next(s for s in segments if s['core'] == 'Name:')
    assert name['dots'].startswith('_') and name['core_x1'] < field.x0
    stripped, output = tmp_path / 'stripped.pdf', tmp_path / 'output.pdf'
    run_strip(str(original), str(stripped))
    mapping = tmp_path / 'mapping.json'
    mapping.write_text(json.dumps({
        'lang': 'es', 'fonts': {role: str(path) for role, path in FULL.items()},
        'translations': {'Yes': 'Sí', 'Name:': 'Nombre:'},
    }), encoding='utf-8')
    run_retypeset(str(stripped), str(tmp_path / 'segments.json'), str(mapping), str(output),
                  original=str(original))
    with pymupdf.open(output) as document:
        label = document[0].search_for('Nombre:')
        yes = document[0].search_for('Sí')
    assert label and label[0].x1 <= field.x0
    assert yes and yes[0].x0 >= 50
