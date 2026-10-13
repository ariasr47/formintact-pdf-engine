"""Content this reader cannot vouch never fails a job.

A page whose segments were all translated reaches source_retention.prepare
only for the optional ink envelope (the vertical bound the target text may
use). Content that the strict operator parser cannot read, such as an
unbalanced Q or an operator with no operand, used to raise on that page and
fail the whole paid job although MuPDF renders the page and no source text
is kept there. The envelope is now dropped for that page. A page with an
identity occurrence no longer refuses either (dogfood F1, 2026-10-09): nothing
is retained there, the occurrence is redrawn and reported.
"""
import json

import pymupdf
import pytest
from fontTools.ttLib import TTLibError

from pdf_translate import source_retention
from .test_source_retention import _spans
from .test_source_retention_guards import _assert_redrawn, _job, _render

SHOW = b'BT /helv 14 Tf 1 0 0 1 40 180 Tm (KEEP) Tj ET\n'
DEFECTS = {
    'unbalanced-Q': b'Q\n',
    'missing-operand': b'Tc\n',
}


def _translate_everything(job):
    mapping = json.loads(job['mapping'].read_text())
    mapping['translations']['KEEP'] = 'Keep'
    job['mapping'].write_text(json.dumps(mapping))


@pytest.mark.parametrize('defect', DEFECTS.values(), ids=DEFECTS.keys())
def test_unreadable_page_content_drops_only_the_optional_envelope(tmp_path, defect):
    job = _job(tmp_path, body=SHOW + defect)
    assert len(_spans(job['original'], 'KEEP')) == 1
    _translate_everything(job)

    _render(job)

    assert len(_spans(job['output'], 'Keep')) == 1
    with pymupdf.open(job['output']) as delivered:
        assert delivered.page_count == 1


@pytest.mark.parametrize('error', [RuntimeError('mupdf could not read the page'),
                                   TTLibError('unreadable font program')],
                         ids=['RuntimeError', 'TTLibError'])
def test_native_reader_errors_drop_only_the_optional_envelope(tmp_path, monkeypatch, error):
    job = _job(tmp_path, body=SHOW)
    _translate_everything(job)

    def unreadable(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(source_retention, 'page_drawing', unreadable)

    _render(job)

    assert len(_spans(job['output'], 'Keep')) == 1


@pytest.mark.parametrize('defect', [DEFECTS['unbalanced-Q']], ids=['unbalanced-Q'])
def test_unreadable_page_content_redraws_required_retention(tmp_path, defect):
    job = _job(tmp_path, body=SHOW + defect)
    assert len(_spans(job['original'], 'KEEP')) == 1

    _assert_redrawn(job, 'unreadable-content')
