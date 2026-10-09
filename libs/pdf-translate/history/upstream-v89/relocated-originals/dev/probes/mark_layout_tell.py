# -*- coding: utf-8 -*-
"""Is there a mechanical tell for mark-stacking scripts (Thai, Hebrew niqqud)?

The glyph-count tell (gate 18) is blind to them: marks stack onto a base
without changing the count. But shaping still leaves a trace in the drawn
glyphs: Thai fonts substitute mark variants (GSUB) and anchor stacked or
below-base marks (GPOS); Hebrew fonts anchor every niqqud mark (GPOS).
So compare the SIGNATURE of a run — the multiset of (glyph id, origin)
relative to the first glyph — between the naive arm (insert_text, no
shaping) and the Story arm (insert_htmlbox, HarfBuzz), off the same face.

Measured 2026-09-15 (PyMuPDF 1.28.2, the fetched Noto faces):

  Thai      shaped != naive on all four probes (variant glyph ids on all
            four; positions on the two with a stacked or below-base mark).
            A face with GSUB and GPOS both removed, through the Story
            engine, reproduces the naive signature exactly — the red case —
            and at 600 dpi the tone marks collide with po pla's ascender
            and sit on top of sara i / sara ii instead of above them. A
            pyftsubset subset (16 glyphs, 7.5 KB) keeps GDEF/GPOS/GSUB and
            the shaped signature.
  Hebrew    shaped != naive on both probes: marks move 5–20 pt at 24 px.
            A face with both tables removed does NOT reproduce the naive
            signature: HarfBuzz composes presentation forms (U+FB2A,
            U+FB31, U+FB4B; 7 -> 5, 3 -> 2 glyphs) and centres the rest.
            retypeset keeps Hebrew on TextWriter (glyph by glyph) today;
            rendered at 600 dpi with Noto Sans Hebrew that output is
            readable — the mark glyphs carry negative side bearings that
            land them over the base — and the Story engine refines each
            mark by 1–2 pt at 36 px. Not a broken-output defect with this
            face; font-dependent.

What the tell attests, exactly as gate 18 does for conjuncts: the face's
layout tables fire on the probe. It does not attest that every mark in a
document lands where a reader expects; the visual pass stays.

    python dev/probes/mark_layout_tell.py [--render DIR]
(fetch the faces first: python pdf-translate/tools/fetch_test_fonts.py)
--render writes <DIR>/<script>-{naive,story,bare}.png at 600 dpi.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pymupdf
from fontTools.ttLib import TTFont

FONTS = Path(__file__).resolve().parents[2] / 'pdf-translate' / 'tests' / 'fonts'
PROBES = [
    ('Thai', 'NotoSansThai-Regular.ttf', 'ป้า', 'tone mark over po pla, then sara aa'),
    ('Thai', 'NotoSansThai-Regular.ttf', 'กิ่', 'sara i above, mai ek stacked on it'),
    ('Thai', 'NotoSansThai-Regular.ttf', 'ที่', 'sara ii above, mai ek stacked'),
    ('Thai', 'NotoSansThai-Regular.ttf', 'ปู่', 'sara uu below, mai ek above'),
    ('Hebrew niqqud', 'NotoSansHebrew-Regular.ttf', 'שָׁלוֹם', 'shin dot + qamats + holam'),
    ('Hebrew niqqud', 'NotoSansHebrew-Regular.ttf', 'בְּ', 'bet + dagesh + sheva'),
]
RENDER_TEXT = {'Thai': 'ป้า กิ่ ปู่ ที่', 'Hebrew niqqud': 'שָׁלוֹם בְּרוּכִים'}
SIZE = 24


def psname(fontfile):
    with TTFont(fontfile) as f:
        for rec in f['name'].names:
            if rec.nameID == 6:
                return rec.toUnicode()
    return ''


def strip_tables(src, dst, *tables):
    with TTFont(src) as f:
        for table in tables:
            if table in f:
                del f[table]
        f.save(dst)
    return dst


def subset(src, dst, text):
    charfile = dst + '.chars.txt'
    Path(charfile).write_text(text, encoding='utf-8')
    subprocess.run([sys.executable, '-m', 'fontTools.subset', src,
                    f'--text-file={charfile}', f'--output-file={dst}'], check=True)
    return dst


def trace(page):
    """[(unicode, glyph id, (x, y), font)] in draw order, from get_texttrace."""
    out = []
    for span in page.get_texttrace():
        for ch in span.get('chars') or []:
            out.append((ch[0], ch[1], ch[2], span.get('font')))
    return out


def naive_page(doc, fontfile, text, size=SIZE):
    page = doc.new_page()
    page.insert_text((36, 100), text, fontsize=size, fontname='probe', fontfile=fontfile)
    return page


def story_page(doc, fontfile, text, size=SIZE, rtl=False):
    page = doc.new_page()
    css = ('@font-face {font-family: probe; src: url(%s);} '
           'body {font-family: probe; margin: 0; padding: 0;}' % os.path.basename(fontfile))
    html = '<div%s style="font-size:%dpx; line-height:1.6">%s</div>' % (
        ' dir="rtl"' if rtl else '', size, text)
    page.insert_htmlbox(pymupdf.Rect(36, 60, 500, 140), html, css=css,
                        archive=pymupdf.Archive(os.path.dirname(fontfile)))
    return page


def naive(fontfile, text):
    doc = pymupdf.open()
    t = trace(naive_page(doc, fontfile, text))
    doc.close()
    return t


def shaped(fontfile, text):
    doc = pymupdf.open()
    t = trace(story_page(doc, fontfile, text))
    doc.close()
    return t


def signature(t):
    """Order-independent (glyph id, dx, dy) on a 0.25 pt grid, relative to the first glyph."""
    if not t:
        return frozenset()
    x0, y0 = t[0][2]
    return frozenset((g, round((x - x0) * 4) / 4, round((y0 - y) * 4) / 4) for _, g, (x, y), _ in t)


def faces_of(t):
    return sorted({f for *_, f in t})


def render(page, out):
    b = page.get_text('dict')['blocks'][0]['bbox']
    page.get_pixmap(dpi=600, clip=pymupdf.Rect(b[0] - 4, b[1] - 6, b[2] + 4, b[3] + 10)).save(out)


def main(argv):
    render_dir = None
    if '--render' in argv:
        render_dir = argv[argv.index('--render') + 1]
        os.makedirs(render_dir, exist_ok=True)
    tmp = tempfile.mkdtemp()
    rendered = set()
    for script, face, text, note in PROBES:
        fontfile = str(FONTS / face)
        if not os.path.isfile(fontfile):
            print(f'{script}: {face} not fetched')
            continue
        expected = psname(fontfile)
        bare = strip_tables(fontfile, os.path.join(tmp, 'bare-' + face), 'GSUB', 'GPOS')
        small = subset(fontfile, os.path.join(tmp, 'subset-' + face), text)
        arms = {
            'naive': naive(fontfile, text),
            'shaped': shaped(fontfile, text),
            'shaped, subset': shaped(small, text),
            'shaped, no GSUB/GPOS': shaped(bare, text),
        }
        print(f'\n=== {script} {text!r} ({note}); {len(text)} code points; face {expected}')
        for label, t in arms.items():
            faces = faces_of(t)
            ok = faces == [expected]
            print(f'  {label:<22} {len(t)} glyphs, drawn by {",".join(faces)}'
                  f'{"" if ok else "  <-- NOT the probe face"}')
            x0, y0 = t[0][2]
            for u, g, (x, y), _ in t:
                print(f'      {("U+%04X" % u) if u else "-":<8} gid {g:<5} '
                      f'dx {round(x - x0, 2):<8} dy {round(y0 - y, 2)}')
        nv = signature(arms['naive'])
        print(f'  signature != naive?  shaped: {signature(arms["shaped"]) != nv}   '
              f'subset: {signature(arms["shaped, subset"]) != nv}   '
              f'no tables: {signature(arms["shaped, no GSUB/GPOS"]) != nv}')
        if render_dir and script not in rendered:
            rendered.add(script)
            sample, rtl = RENDER_TEXT[script], script.startswith('Hebrew')
            slug = script.split()[0].lower()
            doc = pymupdf.open()
            render(naive_page(doc, fontfile, sample, 36), os.path.join(render_dir, f'{slug}-naive.png'))
            render(story_page(doc, fontfile, sample, 36, rtl), os.path.join(render_dir, f'{slug}-story.png'))
            render(story_page(doc, bare, sample, 36, rtl), os.path.join(render_dir, f'{slug}-bare.png'))
            doc.close()
            print(f'  rendered {slug}-{{naive,story,bare}}.png in {render_dir}')
    print('\nA tell exists where "shaped" differs from naive and "no tables" does not '
          '(Thai). Where "no tables" also differs (Hebrew), HarfBuzz composed '
          'presentation forms; the naive signature is then the glyph-by-glyph path.')


if __name__ == '__main__':
    main(sys.argv[1:])
