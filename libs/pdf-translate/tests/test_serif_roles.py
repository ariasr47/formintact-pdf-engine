"""A line the mapping lists in `serif` is drawn in the serif roles.

Nothing fails when the choice is lost: a serif title simply comes back sans
(homepage invoice, 2026-10-26), so the face actually drawn is the thing to check.
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf
import pytest

from pdf_translate import run_extract, run_retypeset, run_strip
from pdf_translate.results import MappingError

FONTS = Path(__file__).resolve().parents[3] / 'apps' / 'engine' / 'pdft_engine' / 'assets' / 'fonts'
SANS = {role: FONTS / f'FormIntactSans-{name}.ttf' for role, name in (
    ('regular', 'Regular'), ('bold', 'Bold'), ('italic', 'Italic'), ('bold_italic', 'BoldItalic'))}
SERIF = {f'{role}_serif': FONTS / f'NotoSerif-{name}.ttf' for role, name in (
    ('regular', 'Regular'), ('bold', 'Bold'), ('italic', 'Italic'), ('bold_italic', 'BoldItalic'))}


def _job(work: Path, fonts: dict, serif: list[str]) -> dict[str, Path]:
    original = work / 'original.pdf'
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=200)
        page.insert_text((40, 40), 'Invoice', fontname='tiro', fontsize=20)
        page.insert_text((40, 80), 'Thank you', fontname='tiit', fontsize=12)
        page.insert_text((40, 120), 'Total due', fontname='helv', fontsize=12)
        document.save(original)
    stripped = work / 'stripped.pdf'
    run_strip(str(original), str(stripped))
    segments = Path(run_extract(str(original), str(work)).segments_path)
    mapping = work / 'mapping.json'
    mapping.write_text(json.dumps({
        'lang': 'es',
        'fonts': {role: str(path) for role, path in fonts.items()},
        'translations': {'Invoice': 'Factura', 'Thank you': 'Gracias', 'Total due': 'Total a pagar'},
        'serif': serif,
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


def test_listed_cores_are_drawn_serif_in_their_style_and_the_rest_sans(tmp_path):
    job = _job(tmp_path, {**SANS, **SERIF}, ['Invoice', 'Thank you'])
    _render(job)
    faces = _faces(job['output'])
    assert faces['Factura'].endswith('NotoSerif-Regular')
    assert faces['Gracias'].endswith('NotoSerif-Italic')
    assert faces['Total a pagar'].endswith('FormIntactSans-Regular')


def test_serif_without_its_regular_role_is_refused_by_name(tmp_path):
    job = _job(tmp_path, SANS, ['Invoice'])
    with pytest.raises(MappingError) as caught:
        _render(job)
    assert 'regular_serif' in caught.value.message
    assert not job['output'].exists()


def _black_job(work: Path, fonts: dict) -> dict[str, Path]:
    job = _job(work, fonts, [])
    conf = json.loads(job['mapping'].read_text(encoding='utf-8'))
    conf.pop('serif')
    conf['black'] = ['Invoice']
    job['mapping'].write_text(json.dumps(conf), encoding='utf-8')
    return job


def test_a_listed_core_is_drawn_in_the_black_face_and_the_rest_unchanged(tmp_path):
    """DS-64's Arial Black headings came back in Bold, visibly lighter (2026-10-29)."""
    job = _black_job(tmp_path, {**SANS, 'black': FONTS / 'FormIntactSans-Black.ttf'})
    _render(job)
    faces = _faces(job['output'])
    assert faces['Factura'].endswith('FormIntactSans-Black')
    assert faces['Total a pagar'].endswith('FormIntactSans-Regular')


def test_black_without_its_role_is_refused_by_name(tmp_path):
    job = _black_job(tmp_path, SANS)
    with pytest.raises(MappingError) as caught:
        _render(job)
    assert 'black' in caught.value.message
    assert not job['output'].exists()
