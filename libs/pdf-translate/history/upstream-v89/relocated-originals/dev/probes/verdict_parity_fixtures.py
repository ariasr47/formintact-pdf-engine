# -*- coding: utf-8 -*-
"""Build the jobs that exercise every gate PR #2 F1 touched, once, on disk,
so the same files can be verified from two checkouts (main and a branch)
and their printed output diffed with verdict_parity_runner.py.

    python dev/probes/verdict_parity_fixtures.py <dir>
(fetch the faces first: python pdf-translate/tools/fetch_test_fonts.py)
"""
import io
import json
import shutil
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pymupdf

ROOT = Path(sys.argv[1])
FONT = (Path(__file__).resolve().parents[2] / 'pdf-translate' / 'tests' / 'fonts'
        / 'NotoNaskhArabic-Regular.ttf')
AR_PHRASE = 'السلام عليكم ورحمة الله'


def tiny(path, text, declare=None):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    if declare:
        doc.set_language(declare)
    doc.save(path)
    doc.close()


def job(name, target='Hola mundo.', lang=None, declare=None, overrides=None,
        segments=None, scale_report=None):
    d = ROOT / name
    d.mkdir(parents=True)
    tiny(d / 'orig.pdf', 'Hello world.')
    tiny(d / 'out.pdf', target, declare)
    conf = {'translations': {'Hello world.': target}, 'skip': []}
    if lang:
        conf['lang'] = lang
    if overrides:
        conf['overrides'] = overrides
    (d / 'translations.json').write_text(json.dumps(conf, ensure_ascii=False), encoding='utf-8')
    if segments is not None:
        (d / 'segments.json').write_text(json.dumps({'segments': segments}), encoding='utf-8')
    if scale_report is not None:
        (d / 'scale_report.json').write_text(json.dumps(scale_report), encoding='utf-8')


if ROOT.exists():
    shutil.rmtree(ROOT)
job('trivial')
job('meta-fail', lang='es')
job('meta-pass', lang='es', declare='es')
job('scaled-review', scale_report=[{'page': 0, 'ratio': 0.85, 'key': 'Hello world.'}])
job('scaled-pass', scale_report=[])
SEGS = [{'page': 0, 'text': 'd. Hello world.        .... $', 'marker': 'd.',
         'core': 'Hello world.', 'dots': '....', 'tail': '$'}]
job('override-pass', segments=SEGS, overrides=[{'page': 0, 'contains': 'Hello world.',
    'parts': [{'text': 'd. Hola mundo.', 'x': 72.0}, {'text': '.... $', 'x': 180.0}]}])
job('override-fail', segments=SEGS, overrides=[{'page': 0, 'contains': 'Hello world.',
    'parts': [{'text': 'Hola mundo.', 'x': 72.0}]}])

# Arabic: glyph by glyph (bad) and the library's own retypeset (good).
from pdf_translate.extract_segments import extract_segments  # noqa: E402
from pdf_translate.retypeset import retypeset  # noqa: E402
from pdf_translate.strip_text import strip_text  # noqa: E402

d = ROOT / 'arabic'
d.mkdir()
source = 'Peace be upon you and mercy'
tiny(d / 'orig.pdf', source)
font = str(FONT)
(d / 'translations.json').write_text(json.dumps({
    'fonts': {'regular': font, 'bold': font, 'italic': font, 'bold_italic': font},
    'translations': {source: AR_PHRASE},
    'merges': [], 'overrides': [], 'center': [], 'skip': [],
}, ensure_ascii=False), encoding='utf-8')
doc = pymupdf.open()
page = doc.new_page(width=612, height=792)
tw = pymupdf.TextWriter(page.rect)
tw.append((72, 80), AR_PHRASE, font=pymupdf.Font(fontfile=font), fontsize=12, right_to_left=True)
tw.write_text(page)
doc.save(d / 'bad.pdf')
doc.close()
with redirect_stdout(io.StringIO()):
    strip_text(str(d / 'orig.pdf'), str(d / 'stripped.pdf'))
    extract_segments(str(d / 'orig.pdf'), outdir=str(d))
    rc = retypeset(str(d / 'stripped.pdf'), str(d / 'segments.json'),
                   str(d / 'translations.json'), str(d / 'good.pdf'))
assert rc == 0, rc
print('fixtures in', ROOT)
