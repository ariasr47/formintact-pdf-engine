"""A list marker keeps its own weight and its body's scale; a paragraph shares one scale.

The source is the W-4 Step 2(a) shape (2026-10-13): a bold "(a)" before a regular
sentence, and a continuation line under the body. Lost emphasis and uneven sizes ship
silently: every text check still passes.
"""

from __future__ import annotations

import json
import string
from pathlib import Path

import pymupdf
import pytest

from pdf_translate import run_extract, run_retypeset, run_strip

from .test_source_retention import _target_font

FIRST = 'Use the estimator for the most accurate result'
SECOND = 'if you have more than one job at a time'


def _render(tmp_path: Path, translations: dict, **layout) -> list[dict]:
    target = _target_font(tmp_path / 'target.ttf', chars=' ' + string.ascii_letters + '.,()')
    original = tmp_path / 'original.pdf'
    with pymupdf.open() as document:
        page = document.new_page(width=320, height=140)
        page.insert_text((40, 60), '(a)', fontname='hebo', fontsize=9)
        page.insert_text((54.4, 60), FIRST, fontname='helv', fontsize=9)
        page.insert_text((54.4, 70.8), SECOND, fontname='helv', fontsize=9)
        document.save(original)
    stripped = tmp_path / 'stripped.pdf'
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(tmp_path))
    mapping = tmp_path / 'mapping.json'
    mapping.write_text(json.dumps({'lang': 'es', 'fonts': {role: str(target) for role in (
        'regular', 'bold', 'italic', 'bold_italic')}, 'translations': translations, **layout}))
    output = tmp_path / 'output.pdf'
    run_retypeset(str(stripped), extracted.segments_path, str(mapping), str(output),
                  original=str(original))
    with pymupdf.open(output) as document:
        return [span for block in document[0].get_text('dict')['blocks'] if block['type'] == 0
                for line in block['lines'] for span in line['spans'] if span['text'].strip()]


def _span(spans: list[dict], text: str) -> dict:
    found = [span for span in spans if span['text'].strip() == text]
    assert len(found) == 1, [(span['text'], span['font']) for span in spans]
    return found[0]


def test_bold_marker_before_a_regular_sentence_stays_bold(tmp_path):
    spans = _render(tmp_path, {FIRST: 'Use it for the best result', SECOND: 'if you have two jobs'})
    assert 'Bold' in _span(spans, '(a)')['font']


def test_marker_of_a_mixed_bold_line_takes_its_body_scale(tmp_path):
    long_body = '<b>Use</b> the estimator for the most accurate result'
    spans = _render(tmp_path, {FIRST: long_body, SECOND: 'if you have two jobs'})
    marker = _span(spans, '(a)')['size']
    body = max(span['size'] for span in spans if 'estimator' in span['text'])
    assert body < 8.5
    assert marker == pytest.approx(body, abs=0.05)


def test_scale_cap_starts_a_paragraph_line_at_its_groups_scale(tmp_path):
    spans = _render(tmp_path, {FIRST: 'Use it for the best result', SECOND: 'if you have two jobs'},
                    scale_caps={'1': 0.8})
    assert _span(spans, 'if you have two jobs')['size'] == pytest.approx(7.2, abs=0.05)
    assert _span(spans, 'Use it for the best result')['size'] == pytest.approx(9, abs=0.05)
