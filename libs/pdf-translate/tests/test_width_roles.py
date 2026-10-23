"""A line the mapping lists in `widths` is drawn in that narrower width of the family.

The fitting ladder's step 3 swaps faces per line. Nothing fails when the swap is
lost: the line simply comes back at full width and overflows as before, so the
face actually drawn is the thing to check, in the plain path and inside inline
markup (whose <b> is resolved by CSS, not by a font role).
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from pdf_translate import run_extract, run_retypeset, run_strip
from pdf_translate.results import MappingError

FONTS = Path(__file__).resolve().parents[3] / 'apps' / 'engine' / 'pdft_engine' / 'assets' / 'fonts'
FULL = {role: FONTS / f'FormIntactSans-{name}.ttf' for role, name in (
    ('regular', 'Regular'), ('bold', 'Bold'), ('italic', 'Italic'), ('bold_italic', 'BoldItalic'))}
SEMI = {f'{role}_semi': FONTS / f'FormIntactSans-SemiCondensed-{name}.ttf' for role, name in (
    ('regular', 'Regular'), ('bold', 'Bold'))}


def _job(work: Path, fonts: dict, widths: dict) -> dict[str, Path]:
    original = work / 'original.pdf'
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=200)
        page.insert_text((40, 40), 'Name', fontname='helv', fontsize=14)
        page.insert_text((40, 80), 'Address', fontname='helv', fontsize=14)
        page.insert_text((40, 120), 'Notes', fontname='helv', fontsize=14)
        document.save(original)
    stripped = work / 'stripped.pdf'
    run_strip(str(original), str(stripped))
    segments = Path(run_extract(str(original), str(work)).segments_path)
    mapping = work / 'mapping.json'
    mapping.write_text(json.dumps({
        'lang': 'es',
        'fonts': {role: str(path) for role, path in fonts.items()},
        'translations': {'Name': 'Nombre completo', 'Address': 'Direccion postal',
                         'Notes': 'Notas <b>claras</b> aqui'},
        'widths': widths,
    }), encoding='utf-8')
    return {'original': original, 'stripped': stripped, 'segments': segments,
            'mapping': mapping, 'output': work / 'output.pdf'}


def _render(job: dict[str, Path]) -> None:
    run_retypeset(str(job['stripped']), str(job['segments']), str(job['mapping']),
                  str(job['output']), original=str(job['original']))


def _faces(path: Path) -> dict[str, str]:
    with pymupdf.open(path) as document:
        return {span['text'].strip(): span['font']
                for block in document[0].get_text('dict')['blocks'] if block['type'] == 0
                for line in block['lines'] for span in line['spans'] if span['text'].strip()}


def test_a_listed_core_is_drawn_in_its_narrow_face_and_the_rest_in_full_width(tmp_path):
    job = _job(tmp_path, {**FULL, **SEMI}, {'Name': 'semi', 'Notes': 'semi'})
    _render(job)
    faces = _faces(job['output'])
    assert 'SemiCondensed' in faces['Nombre completo']
    assert 'SemiCondensed' not in faces['Direccion postal']
    # Inline markup: the <b> inside a narrow line takes the narrow bold, not the full-width one.
    assert 'SemiCondensed' in faces['claras']
    assert 'SemiCondensed' in faces['Notas']


def test_a_width_without_its_regular_role_is_refused_by_name(tmp_path):
    job = _job(tmp_path, FULL, {'Name': 'semi'})
    with pytest.raises(MappingError) as caught:
        _render(job)
    assert 'regular_semi' in caught.value.message
    assert caught.value.refusals['widths'][0]['core'] == 'Name'
    assert not job['output'].exists()
