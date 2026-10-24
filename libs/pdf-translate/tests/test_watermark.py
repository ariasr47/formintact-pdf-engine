"""A stamp printed over the page keeps to its source length.

CMS-1490S (Spanish corpus, 2026-10-23): the Spanish of a diagonal "DO NOT SEND THIS PAGE WITH YOUR CLAIM"
stamp is longer, so it was drawn in a larger box than the source. That box blocked the lines just outside the
source box, and the engine kept those lines, every copy of them, and the stamp itself in English: 92 lines
on 15 pages. Nothing raises; only where the stamp's ink lands shows it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf

from pdf_translate import run_extract, run_retypeset, run_strip

FONTS = Path(__file__).resolve().parents[3] / 'apps' / 'engine' / 'pdft_engine' / 'assets' / 'fonts'
FULL = {role: FONTS / f'FormIntactSans-{name}.ttf' for role, name in (
    ('regular', 'Regular'), ('bold', 'Bold'), ('italic', 'Italic'), ('bold_italic', 'BoldItalic'))}
STAMP = 'DO NOT SEND THIS PAGE WITH YOUR CLAIM'


def _source(path: Path) -> None:
    """Text lines down a page, and a 45-degree stamp printed across them."""
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=400)
        for row in range(12):
            page.insert_text((40, 60 + 24 * row), f'Line number {row}', fontname='helv', fontsize=11)
        writer = pymupdf.TextWriter(page.rect)
        writer.append((40, 370), STAMP, fontsize=14)
        writer.write_text(page, morph=(pymupdf.Point(40, 370), pymupdf.Matrix(45)))
        document.save(path)


def test_a_stamp_over_text_is_drawn_within_its_source_box(tmp_path):
    original = tmp_path / 'original.pdf'
    _source(original)
    data = json.loads(Path(run_extract(str(original), str(tmp_path)).segments_path).read_text(encoding='utf-8'))
    segments = data['segments'] if isinstance(data, dict) else data
    stamp = next(s for s in segments if s['core'] == STAMP)
    assert stamp['dir'] != [1.0, 0.0] and stamp['run_len'] > 200
    stripped, output = tmp_path / 'stripped.pdf', tmp_path / 'output.pdf'
    run_strip(str(original), str(stripped))
    mapping = tmp_path / 'mapping.json'
    mapping.write_text(json.dumps({
        'lang': 'es', 'fonts': {role: str(path) for role, path in FULL.items()},
        'translations': {STAMP: 'NO ENVÍE ESTA PÁGINA CON SU RECLAMACIÓN',
                         **{f'Line number {row}': f'Línea número {row}' for row in range(12)}},
    }), encoding='utf-8')
    run_retypeset(str(stripped), str(tmp_path / 'segments.json'), str(mapping), str(output),
                  original=str(original))
    source = pymupdf.Rect(stamp['bbox'])
    with pymupdf.open(output) as document:
        drawn = None
        for block in document[0].get_text('rawdict')['blocks']:
            for line in block.get('lines', []):
                if line['dir'][1] != 0:
                    for span in line['spans']:
                        for char in span['chars']:
                            box = pymupdf.Rect(char['bbox'])
                            drawn = box if drawn is None else drawn | box
    assert drawn is not None
    assert drawn in source + (-3, -3, 3, 3), (drawn, source)
