#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Can a verify gate tell Japanese Han forms from Chinese ones in a delivered PDF?

Han unification: 直 骨 海 are one code point each but print differently under
Japanese and Chinese convention. The product asked for a gate that
rasterizes each probe character from the DELIVERED PDF and compares it with
the other face at the same geometry, warning that two synthetic renders only
prove the font files differ, and that the probe characters must actually be
drawn. This probe measures what such a gate can rely on:

  1. references — each probe character rendered from Noto Sans JP and Noto
     Sans SC at identical geometry; the diff ratio JP-vs-JP must be 0.0 and
     JP-vs-SC clearly above it;
  2. weight — the same at wght 400 (what prepare_font --instance produces)
     against the variable fonts' default instance (Thin): does the regional
     difference dominate the weight difference, or must references be
     instanced at the delivered weight?
  3. a real delivery — build → extract → strip → prepare_font (instance 400,
     subset) → retypeset with the JP face, then judge it two ways:
       A. re-render the probe characters from the EMBEDDED font program
          (the way gate 18 re-probes conjunct faces);
       B. clip each probe character from the delivered page and compare with
          a control drawn at the same origin and size with each face;
  4. other families — Yu Gothic (JP) and Microsoft YaHei (SC), if present,
     against the Noto references: what a non-Noto delivery looks like;
  5. not drawn — a delivery whose text has no probe character: does the
     embedded subset carry the probes at all?

  PYTHONUTF8=1 python dev/probes/han_forms_probe.py [--work DIR]

Diff ratio = pixels that differ (ink in one render, not the other) / pixels
that are ink in either, at 4x zoom, 48 pt, grey; 0.0 means identical.
"""
import io
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SKILL = ROOT / 'pdf-translate'
sys.path.insert(0, str(SKILL))
from tests.test_pipeline import (SOURCE_SENTENCE, build_plain_pdf, extract_segments,  # noqa: E402
                                 prepare_font, retypeset, strip_text, write_mapping)

FONTS = SKILL / 'tests' / 'fonts'
JP = FONTS / 'NotoSansJP-VF.ttf'
SC = FONTS / 'NotoSansSC-VF.ttf'
SYSTEM = {'Yu Gothic (JP)': Path(r'C:\Windows\Fonts\YuGothR.ttc'),
          'Microsoft YaHei (SC)': Path(r'C:\Windows\Fonts\msyh.ttc')}
PROBES = '\u76f4\u9aa8\u6d77\u6771'   # 直 骨 海 東
EAST = '\u4e1c'                        # 东: in SC, not in JP
SIZE, ZOOM, ORIGIN = 48, 4, (40, 120)


def render(font, ch, origin=ORIGIN, size=SIZE):
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    tw = pymupdf.TextWriter(page.rect)
    tw.append(origin, ch, font=font, fontsize=size)
    tw.write_text(page)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM), colorspace=pymupdf.csGRAY, alpha=False)
    doc.close()
    return bytes(pix.samples), pix.width, pix.height


def ratio(a, b):
    """Differing ink pixels over the union of ink pixels."""
    inka = [px < 128 for px in a[0]]
    inkb = [px < 128 for px in b[0]]
    union = sum(x or y for x, y in zip(inka, inkb))
    diff = sum(x != y for x, y in zip(inka, inkb))
    return diff / union if union else float('nan')


def instance(path, wght, out):
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
    t0 = time.time()
    f = instancer.instantiateVariableFont(TTFont(str(path)), {'wght': wght})
    f.save(str(out))
    return time.time() - t0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    work = Path(argv[argv.index('--work') + 1]) if '--work' in argv else Path(tempfile.mkdtemp(prefix='han-forms-'))
    work.mkdir(parents=True, exist_ok=True)
    for p in (JP, SC):
        if not p.is_file():
            print(f'missing {p}; run tools/fetch_test_fonts.py'); return 1
    jp, sc = pymupdf.Font(fontfile=str(JP)), pymupdf.Font(fontfile=str(SC))
    print(f'faces: {jp.name} / {sc.name}; PyMuPDF {pymupdf.__version__}; work {work}')
    print('has 东:', 'JP', bool(jp.has_glyph(ord(EAST))), 'SC', bool(sc.has_glyph(ord(EAST))))

    print('\n== 1. references at the default instance (Thin): diff ratio per character ==')
    print(f'{"char":6s} {"JP-vs-JP":>9s} {"JP-vs-SC":>9s} {"SC-vs-SC":>9s}')
    for ch in PROBES:
        rj, rs = render(jp, ch), render(sc, ch)
        print(f'{ch:6s} {ratio(rj, render(jp, ch)):9.3f} {ratio(rj, rs):9.3f} {ratio(rs, render(sc, ch)):9.3f}')

    print('\n== 2. weight: instanced at wght 400 (what prepare_font --instance gives) ==')
    jp400p, sc400p = work / 'NotoSansJP-400.ttf', work / 'NotoSansSC-400.ttf'
    tj = instance(JP, 400, jp400p); ts = instance(SC, 400, sc400p)
    print(f'instancing time: JP {tj:.1f}s, SC {ts:.1f}s')
    jp400, sc400 = pymupdf.Font(fontfile=str(jp400p)), pymupdf.Font(fontfile=str(sc400p))
    print(f'{"char":6s} {"JP400-JP100":>12s} {"JP400-SC400":>12s} {"JP400-SC100":>12s} {"SC400-SC100":>12s}')
    for ch in PROBES:
        a, b, c, d = render(jp400, ch), render(jp, ch), render(sc400, ch), render(sc, ch)
        print(f'{ch:6s} {ratio(a, b):12.3f} {ratio(a, c):12.3f} {ratio(a, d):12.3f} {ratio(c, d):12.3f}')

    print('\n== 3. a real delivery through the pipeline (JP face, instance 400, subset) ==')
    src, stripped, out = work / 'orig.pdf', work / 'stripped.pdf', work / 'out.pdf'
    tr, subset = work / 'translations.json', work / 'jp-subset.ttf'
    build_plain_pdf(str(src))
    extract_segments.extract_segments(str(src), outdir=str(work))
    strip_text.strip_text(str(src), str(stripped))
    target = '\u76f4\u9aa8\u6d77\u6771\u4eac'   # 直骨海東京
    write_mapping(str(tr), {SOURCE_SENTENCE: target}, JP)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = prepare_font.prepare_font(str(JP), str(tr), str(subset), instance='wght=400')
    print(f'prepare_font rc={rc}; subset {subset.stat().st_size // 1024 if subset.is_file() else "-"} KB')
    if rc:
        print(buf.getvalue()[-800:]); return 1
    write_mapping(str(tr), {SOURCE_SENTENCE: target}, subset)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = retypeset.retypeset(str(stripped), str(work / 'segments.json'), str(tr), str(out))
    print(f'retypeset rc={rc}')
    if rc:
        print(buf.getvalue()[-800:]); return 1
    doc = pymupdf.open(str(out))
    page = doc[0]
    print('   A. embedded face re-rendered vs references instanced at 400:')
    print(f'      {"face":28s} {"char":5s} {"vs JP400":>9s} {"vs SC400":>9s}')
    for entry in page.get_fonts(full=True):
        name, ext, _, program = doc.extract_font(entry[0])[:4]
        if not program:
            print(f'      {name:28s} (no program)'); continue
        emb = pymupdf.Font(fontbuffer=program)
        for ch in PROBES:
            if not emb.has_glyph(ord(ch)):
                print(f'      {name:28s} {ch:5s} not in the embedded subset'); continue
            r = render(emb, ch)
            print(f'      {name:28s} {ch:5s} {ratio(r, render(jp400, ch)):9.3f} {ratio(r, render(sc400, ch)):9.3f}')
    print('   B. clip from the delivered page vs a control drawn at the same origin and size:')
    raw = page.get_text('rawdict')
    zoom = pymupdf.Matrix(ZOOM, ZOOM)
    emb_font = None
    for entry in page.get_fonts(full=True):
        program = doc.extract_font(entry[0])[3]
        if program and pymupdf.Font(fontbuffer=program).has_glyph(ord(PROBES[0])):
            emb_font = pymupdf.Font(fontbuffer=program)
    found = 0
    for block in raw.get('blocks', []):
        for line in block.get('lines', []):
            for span in line.get('spans', []):
                for c in span.get('chars', []):
                    if c['c'] not in PROBES:
                        continue
                    found += 1
                    bbox = pymupdf.Rect(c['bbox'])
                    origin, size = tuple(c['origin']), span['size']
                    clip = page.get_pixmap(matrix=zoom, clip=bbox, colorspace=pymupdf.csGRAY, alpha=False)
                    def control(font):
                        d = pymupdf.open(); p = d.new_page(width=page.rect.width, height=page.rect.height)
                        tw = pymupdf.TextWriter(p.rect); tw.append(origin, c['c'], font=font, fontsize=size); tw.write_text(p)
                        px = p.get_pixmap(matrix=zoom, clip=bbox, colorspace=pymupdf.csGRAY, alpha=False)
                        return (bytes(px.samples), px.width, px.height)
                    me = (bytes(clip.samples), clip.width, clip.height)
                    print(f'      {c["c"]} at {origin[0]:.1f},{origin[1]:.1f} size {size:.2f}: '
                          f'vs embedded {ratio(me, control(emb_font)):.3f}  vs JP400 {ratio(me, control(jp400)):.3f}  '
                          f'vs SC400 {ratio(me, control(sc400)):.3f}')
    print(f'      probe characters found in the page text: {found}')
    doc.close()

    print('\n== 4. other families vs the Noto references (Thin), same geometry ==')
    for label, path in SYSTEM.items():
        if not path.is_file():
            print(f'   {label}: not on this machine'); continue
        f = pymupdf.Font(fontfile=str(path))
        cells = []
        for ch in PROBES:
            r = render(f, ch)
            cells.append(f'{ch} JP {ratio(r, render(jp, ch)):.3f} SC {ratio(r, render(sc, ch)):.3f}')
        print(f'   {label}: ' + ' | '.join(cells))

    print('\n== 5. not drawn: a delivery whose text has no probe character ==')
    tr2, subset2 = work / 'translations2.json', work / 'jp-subset2.ttf'
    write_mapping(str(tr2), {SOURCE_SENTENCE: '\u7533\u8acb\u66f8\u3092\u63d0\u51fa'}, JP)   # 申請書を提出
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = prepare_font.prepare_font(str(JP), str(tr2), str(subset2), instance='wght=400')
    s2 = pymupdf.Font(fontfile=str(subset2)) if subset2.is_file() else None
    print(f'   prepare_font rc={rc}; subset carries 直: {bool(s2 and s2.has_glyph(0x76f4))} '
          f'(gate 18 has prepare_font add its probe cluster; a Han-forms gate would need the same)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
