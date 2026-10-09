# -*- coding: utf-8 -*-
"""Three experiments that decide where the conjunct-shaping probe can run.

    python dev/probes/shaping_design_experiments.py
(fetch the faces first: python pdf-translate/tools/fetch_test_fonts.py)

1. After retypeset draws a Devanagari run, what font program is embedded in
   the output: the whole file, or a subset? Can it be extracted and probed?
2. Does a prepare_font subset (pyftsubset, default layout features) still
   shape the probe string once the probe glyphs are in the charset?
3. Does deleting GSUB from the face make the Story engine draw the probe
   unshaped (the red case), without falling back to another font?
"""
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path, PurePath

import pymupdf
from fontTools.ttLib import TTFont

SK = Path(__file__).resolve().parents[2] / 'pdf-translate'
sys.path.insert(0, str(SK / 'scripts'))
import retypeset, strip_text, extract_segments, prepare_font  # noqa: E402

FONT = SK / 'tests' / 'fonts' / 'NotoSansDevanagari-Regular.ttf'
PROBE = 'क्षत्रिय'
SOURCE = 'Peace be upon you and mercy'
TARGET = 'नमस्ते क्षत्रिय'


def build_one_line_pdf(path, text=SOURCE, x=72, y=80, size=12):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((x, y), text, fontsize=size)
    page.draw_line((60, 84), (300, 84))
    doc.save(path)
    doc.close()


def count(page):
    glyphs, fonts = 0, set()
    for span in page.get_texttrace():
        glyphs += len(span.get('chars') or [])
        fonts.add(span.get('font'))
    return glyphs, fonts


def probe(fontfile, text=PROBE, fs=24):
    d = pymupdf.open(); p = d.new_page()
    p.insert_text((40, 80), text, fontname='probe', fontfile=str(fontfile), fontsize=fs)
    naive = count(p)
    d = pymupdf.open(); p = d.new_page()
    css = '@font-face {font-family: probe; src: url("%s");}' % PurePath(fontfile).as_posix()
    p.insert_htmlbox(pymupdf.Rect(40, 40, 500, 120),
                     f'<p style="font-family: probe; font-size:{fs}pt; margin:0">{text}</p>',
                     css=css, archive=pymupdf.Archive('.'), scale_low=0)
    shaped = count(p)
    return naive, shaped


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    src, stripped, out, tr = tmp / 'orig.pdf', tmp / 'stripped.pdf', tmp / 'out.pdf', tmp / 'tr.json'
    build_one_line_pdf(str(src))
    strip_text.strip_text(str(src), str(stripped))
    extract_segments.extract_segments(str(src), outdir=str(tmp))

    # ---- experiment 2: prepare_font subset, with and without probe glyphs in charset
    for label, extra in (('subset WITHOUT probe glyphs', ''), ('subset WITH probe glyphs', PROBE)):
        conf = {'fonts': {'regular': str(FONT)}, 'translations': {SOURCE: TARGET + ' ' + extra},
                'merges': [], 'overrides': [], 'center': [], 'skip': []}
        tr.write_text(json.dumps(conf, ensure_ascii=False), encoding='utf-8')
        sub = tmp / f'sub-{len(extra)}.ttf'
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = prepare_font.prepare_font(str(FONT), str(tr), str(sub), sample=TARGET)
        with TTFont(str(sub)) as f:
            has_gsub = 'GSUB' in f
            cmap = f.getBestCmap()
            covers = all(ord(c) in cmap for c in PROBE)
        print(f'[2] {label}: prepare_font rc={rc} size={sub.stat().st_size} GSUB={has_gsub} '
              f'cmap covers probe={covers} probe={probe(sub) if covers else "n/a"}')

    # ---- experiment 1: what does retypeset embed?
    conf = {'fonts': {'regular': str(FONT), 'bold': str(FONT)}, 'translations': {SOURCE: TARGET},
            'merges': [], 'overrides': [], 'center': [], 'skip': []}
    tr.write_text(json.dumps(conf, ensure_ascii=False), encoding='utf-8')
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = retypeset.retypeset(str(stripped), str(tmp / 'segments.json'), str(tr), str(out))
    print(f'[1] retypeset rc={rc}; source font size={FONT.stat().st_size}')
    doc = pymupdf.open(str(out))
    for pg in doc:
        for xref, ext, ftype, basefont, name, enc, *_ in [tuple(f) for f in pg.get_fonts(full=True)]:
            info = doc.extract_font(xref)
            bname, bext, btype, buf_ = info[0], info[1], info[2], info[3]
            print(f'[1]  page {pg.number} xref={xref} ext={ext} type={ftype} base={basefont} '
                  f'extracted ext={bext} type={btype} bytes={len(buf_) if buf_ else 0}')
            if buf_ and bext in ('ttf', 'otf'):
                ef = tmp / f'embedded-{xref}.{bext}'
                ef.write_bytes(buf_)
                try:
                    with TTFont(str(ef)) as f:
                        has_gsub = 'GSUB' in f
                        cmap = f.getBestCmap() or {}
                        covers = all(ord(c) in cmap for c in PROBE)
                        nglyphs = len(f.getGlyphOrder())
                    print(f'[1]    loadable: GSUB={has_gsub} glyphs={nglyphs} cmap covers probe={covers} '
                          f'probe={probe(ef) if covers else "n/a"}')
                except Exception as exc:
                    print(f'[1]    fontTools cannot load: {exc}')
    doc.close()

    # ---- experiment 3: GSUB-stripped face (red case)
    bad = tmp / 'no-gsub.ttf'
    with TTFont(str(FONT)) as f:
        del f['GSUB']
        f.save(str(bad))
    print(f'[3] GSUB-stripped face: probe={probe(bad)}   (expect naive == shaped == 8)')
    print(f'[3] real face:          probe={probe(FONT)} (expect 8 / 4)')
