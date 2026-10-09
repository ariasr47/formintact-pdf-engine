# -*- coding: utf-8 -*-
"""Measure naive (insert_text) vs shaped (Story engine) glyph counts per probe.

The measurement behind pdf_translate/shaping_probe.py and AGENTS.md Rule 2.
    python dev/probes/measure_shaping_tell.py pdf-translate/tests/fonts
(fetch the faces first: python pdf-translate/tools/fetch_test_fonts.py)


Counts come from page.get_texttrace() (one entry per drawn glyph) and from
get_text('rawdict') chars, so the two counting methods can be compared.
Font identity is read from the texttrace span font name on BOTH arms.
"""
import sys
from pathlib import PurePath

import pymupdf
from fontTools.ttLib import TTFont

FONTS = sys.argv[1]
PROBES = [
    # (script, probe, font file, note)
    ('Devanagari', 'क्षत्रिय', 'NotoSansDevanagari-Regular.ttf', 'conjunct ksha + tra'),
    ('Devanagari', 'हिन्दी', 'NotoSansDevanagari-Regular.ttf', 'half-form nda'),
    ('Devanagari', 'कमल', 'NotoSansDevanagari-Regular.ttf', 'no conjunct (control)'),
    ('Tamil', 'க்ஷ', 'NotoSansTamil-Regular.ttf', 'ksha ligature'),
    ('Bengali', 'ক্ষ', 'NotoSansBengali-Regular.ttf', 'ksha ligature'),
    ('Bengali', 'বাংলা', 'NotoSansBengali-Regular.ttf', 'no conjunct (control)'),
    ('Khmer', 'ខ្មែរ', 'NotoSansKhmer-Regular.ttf', 'coeng ma'),
    ('Khmer', 'កា', 'NotoSansKhmer-Regular.ttf', 'no coeng (control)'),
    ('Myanmar', 'မန္တလေး', 'NotoSansMyanmar-Regular.ttf', 'stacked ta (virama 1039)'),
    ('Myanmar', 'သင်္ဘော', 'NotoSansMyanmar-Regular.ttf', 'kinzi + stacked'),
    ('Myanmar', 'မြန်မာ', 'NotoSansMyanmar-Regular.ttf', 'medial ra, asat (no stack)'),
    ('Thai', 'กำไร', 'NotoSansThai-Regular.ttf', 'sara am (may decompose)'),
    ('Thai', 'ป้า', 'NotoSansThai-Regular.ttf', 'mark stacking'),
    ('Hebrew', 'שָׁלוֹם', 'NotoSansHebrew-Regular.ttf', 'niqqud'),
    ('Arabic', 'مكتبة', 'NotoNaskhArabic-Regular.ttf', 'joining (adds glyphs)'),
]


def css_url(path):
    return '"' + PurePath(path).as_posix() + '"'


def psname(path):
    with TTFont(path) as f:
        return f['name'].getDebugName(6)


def trace_counts(page):
    """(glyphs, fonts) from get_texttrace; and rawdict char count."""
    glyphs, fonts = 0, set()
    for span in page.get_texttrace():
        if span.get('type', 'text') != 'text' and 'chars' not in span:
            continue
        chars = span.get('chars') or []
        glyphs += len(chars)
        fonts.add(span.get('font'))
    raw = sum(len(sp['chars']) for b in page.get_text('rawdict')['blocks']
              for ln in b.get('lines', []) for sp in ln['spans'])
    rawfonts = {sp['font'] for b in page.get_text('rawdict')['blocks']
                for ln in b.get('lines', []) for sp in ln['spans']}
    return glyphs, fonts, raw, rawfonts


def measure(script, probe, fontfile, fs=24):
    path = f'{FONTS}/{fontfile}'
    expect = psname(path)
    # naive arm
    d1 = pymupdf.open()
    p1 = d1.new_page()
    p1.insert_text((40, 80), probe, fontname='probe', fontfile=path, fontsize=fs)
    n_glyphs, n_fonts, n_raw, n_rawfonts = trace_counts(p1)
    # shaped arm (Story engine), same face
    d2 = pymupdf.open()
    p2 = d2.new_page()
    css = f'@font-face {{font-family: probe; src: url({css_url(path)});}}'
    body = f'<p style="font-family: probe; font-size: {fs}pt; margin:0">{probe}</p>'
    rect = pymupdf.Rect(40, 40, 500, 120)
    p2.insert_htmlbox(rect, body, css=css, archive=pymupdf.Archive('.'), scale_low=0)
    s_glyphs, s_fonts, s_raw, s_rawfonts = trace_counts(p2)
    cps = len(probe)
    return dict(script=script, probe=probe, note=None, cps=cps, expect=expect,
                naive=n_glyphs, naive_raw=n_raw, naive_fonts=sorted(map(str, n_fonts)),
                shaped=s_glyphs, shaped_raw=s_raw, shaped_fonts=sorted(map(str, s_fonts)))


print(f'pymupdf {pymupdf.version}')
print(f"{'script':10} {'probe':12} {'cps':>3} {'naive':>5} {'shaped':>6} {'ratio':>5}  "
      f"{'naiveRaw':>8} {'shapedRaw':>9}  fonts(naive | shaped)  expect")
for script, probe, ff, note in PROBES:
    r = measure(script, probe, ff)
    ratio = r['shaped'] / r['naive'] if r['naive'] else float('nan')
    print(f"{script:10} {probe:12} {r['cps']:>3} {r['naive']:>5} {r['shaped']:>6} {ratio:>5.2f}  "
          f"{r['naive_raw']:>8} {r['shaped_raw']:>9}  {r['naive_fonts']} | {r['shaped_fonts']}  {r['expect']}   # {note}")
