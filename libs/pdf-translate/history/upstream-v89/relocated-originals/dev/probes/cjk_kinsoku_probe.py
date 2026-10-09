#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Does the Story engine (insert_htmlbox) honour kinsoku on the lines it draws?

Three measurements, all on lines read back from the page the engine drew:

  1. a paragraph dense in closing punctuation, small kana and the long-vowel
     mark, wrapped at six widths: how many drawn lines BEGIN with a character
     JLREQ forbids at line start, how many END with an opening bracket;
  2. the same text through a naive greedy breaker (break before any character
     that would overflow) with the same face and widths -- the control that
     shows the paragraph can be broken wrongly;
  3. every member of the JLREQ classes below, one at a time, in a box sized so
     a naive breaker would put it at the boundary.

Sets transcribed from public references only: JIS X 4051 and W3C JLREQ
("Requirements for Japanese Text Layout", Appendix A character classes;
cl-01 opening brackets, cl-02 closing brackets, cl-03 hyphens, cl-04 dividing
punctuation, cl-05 middle dots, cl-06 full stops, cl-07 commas, cl-09
iteration marks, cl-10 prolonged sound mark, cl-11 small kana), plus their
half-width forms (U+FF61..U+FF70). Nothing here comes from any other code.

  python dev/probes/cjk_kinsoku_probe.py [--font PATH]

Without --font, tries tests/fonts/NotoSansJP-VF.ttf, then the Windows
Noto Sans JP, then MuPDF's bundled CJK face (Droid Sans Fallback). Line
breaking is the engine's, not the face's, so the face only needs the glyphs.
Measured 2026-09-16 (PyMuPDF 1.28.2, Noto Sans JP 2.04): 0 violations on
158 drawn lines against 38 + 8 for the naive breaker; 93/94 line-start and
15/16 line-end members protected. Record: docs/reviews/2026-09-16-cjk-request-assessment.md
"""
import os
import sys
import tempfile

import pymupdf

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
CANDIDATES = (
    os.path.join(ROOT, 'pdf-translate', 'tests', 'fonts', 'NotoSansJP-VF.ttf'),
    r'C:\Windows\Fonts\NotoSansJP-VF.ttf',
)
FS = 10.0
WIDTHS = (120, 131, 143, 157, 173, 190)
FILL = '\u3042'  # あ: full-width, carries no prohibition of its own

NO_START = {
    'cl-02 closing brackets': '\u300d\u300f\uff09\uff3d\uff5d\u3009\u300b\u3011\u3015\u3019\u3017\u2019\u201d\uff60\u2986',
    'cl-02 half-width closer': '\uff63',
    'cl-03 hyphens': '\u2010\u301c\u30a0\u2013\uff5e',
    'cl-04 dividing punctuation': '\uff1f\uff01\u203c\u2047\u2048\u2049',
    'cl-05 middle dots': '\u30fb\uff1a\uff1b\uff65',
    'cl-06 full stops': '\u3002\uff0e',
    'cl-06 half-width': '\uff61',
    'cl-07 commas': '\u3001\uff0c',
    'cl-07 half-width': '\uff64',
    'cl-09 iteration marks': '\u3005\u303b\u309d\u309e\u30fd\u30fe',
    'cl-10 prolonged sound mark': '\u30fc',
    'cl-10 half-width': '\uff70',
    'cl-11 small kana, hiragana': '\u3041\u3043\u3045\u3047\u3049\u3063\u3083\u3085\u3087\u308e\u3095\u3096',
    'cl-11 small kana, katakana': '\u30a1\u30a3\u30a5\u30a7\u30a9\u30c3\u30e3\u30e5\u30e7\u30ee\u30f5\u30f6',
    'cl-11 half-width small kana': '\uff67\uff68\uff69\uff6a\uff6b\uff6c\uff6d\uff6e\uff6f',
    'cl-11 small katakana extension': '\u31f0\u31f1\u31f2\u31f3\u31f4\u31f5\u31f6\u31f7\u31f8\u31f9\u31fa\u31fb\u31fc\u31fd\u31fe\u31ff',
}
NO_END = {
    'cl-01 opening brackets': '\u300c\u300e\uff08\uff3b\uff5b\u3008\u300a\u3010\u3014\u3018\u3016\u2018\u201c\uff5f\u2985',
    'cl-01 half-width opener': '\uff62',
}
START_SET = set(''.join(NO_START.values()))
END_SET = set(''.join(NO_END.values()))

PARA = ('データー、翻訳「テスト」の結果。ページーレイアウトは、ちょっとした「工夫」で、'
        'きっちり保たれます。キャッシュ・サーバーへの接続（安全な）を確認して、ファイルを'
        '保存します。ユーザーは、「もっと」使いやすい（かつ）速いツールを求めていますよ。') * 3


def resolve_font(arg):
    if arg:
        return arg
    for path in CANDIDATES:
        if os.path.isfile(path):
            return path
    path = os.path.join(tempfile.gettempdir(), 'pdf-translate-probe-cjk.ttf')
    with open(path, 'wb') as fh:
        fh.write(pymupdf.Font('cjk').buffer)
    return path


def drawn_lines(page):
    out = []
    for blk in page.get_text('dict')['blocks']:
        for ln in blk.get('lines', []):
            text = ''.join(sp['text'] for sp in ln['spans'])
            if text.strip():
                out.append((text, ln['bbox'][2]))
    return out


def story(fontfile, width, html_text):
    """Draw html_text in a box `width` wide through the Story engine; return its lines."""
    arch = pymupdf.Archive(os.path.dirname(fontfile))
    css = ('@font-face {font-family: F; src: url(%s);} p {font-family: F; font-size: %gpt;}'
           % (os.path.basename(fontfile), FS))
    doc = pymupdf.open()
    page = doc.new_page(width=width + 40, height=1400)
    rect = pymupdf.Rect(20, 20, 20 + width, 1380)
    page.insert_htmlbox(rect, '<p>%s</p>' % html_text, css=css, archive=arch, scale_low=0)
    lines = drawn_lines(page)
    doc.close()
    return lines, rect


def paragraph(fontfile):
    total = starts = ends = over = 0
    for w in WIDTHS:
        lines, rect = story(fontfile, w, PARA)
        for text, x1 in lines:
            t = text.strip()
            total += 1
            starts += t[0] in START_SET
            ends += t[-1] in END_SET
            over += x1 > rect.x1 + 0.5
    return total, starts, ends, over


def naive(font):
    total = starts = ends = 0
    for w in WIDTHS:
        line = ''
        for ch in PARA:
            if line and font.text_length(line + ch, fontsize=FS) > w:
                total += 1
                starts += line[0] in START_SET
                ends += line[-1] in END_SET
                line = ch
            else:
                line += ch
        total += 1
        starts += line[0] in START_SET
        ends += line[-1] in END_SET
    return total, starts, ends


def probe_char(fontfile, font, ch, at_start):
    """True: protected on every width tried. False: violated at least once.
    None: never reached a line boundary. 'noglyph': the face cannot draw it."""
    if not font.has_glyph(ord(ch)):
        return 'noglyph'
    violated = boundary = False
    for k in (4, 5, 6, 7):
        text = (FILL * k + ch + FILL * (k + 3)) if at_start else (FILL * (k - 1) + ch + FILL * (k + 3))
        w = font.text_length(FILL * k, fontsize=FS) + 0.6
        lines, _ = story(fontfile, w, text)
        if len(lines) < 2:
            continue
        boundary = True
        first, second = lines[0][0], lines[1][0]
        if at_start and second.startswith(ch):
            violated = True
        if not at_start and first.endswith(ch):
            violated = True
    return None if not boundary else not violated


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    fontfile = resolve_font(argv[argv.index('--font') + 1] if '--font' in argv else None)
    font = pymupdf.Font(fontfile=fontfile)
    print(f'face: {font.name} ({fontfile}); PyMuPDF {pymupdf.__version__}')

    total, starts, ends, over = paragraph(fontfile)
    print(f'1. Story engine, paragraph at {len(WIDTHS)} widths: {total} lines drawn; '
          f'{starts} begin with a prohibited character; {ends} end with an opener; '
          f'{over} run past the box')
    total, starts, ends = naive(font)
    print(f'2. naive greedy breaker, same face and widths: {total} lines; '
          f'{starts} begin with a prohibited character; {ends} end with an opener')

    print('3. per character (Story engine):')
    bad_total = 0
    for title, table, at_start in (('never begins a line', NO_START, True),
                                   ('never ends a line', NO_END, False)):
        for cls, chars in table.items():
            res = {c: probe_char(fontfile, font, c, at_start) for c in chars}
            ok = sum(1 for r in res.values() if r is True)
            bad = [c for c, r in res.items() if r is False]
            other = [c for c, r in res.items() if r in (None, 'noglyph')]
            bad_total += len(bad)
            line = f'   {cls} ({title}): protected {ok}/{len(chars)}'
            if bad:
                line += '  VIOLATED ' + ' '.join(f'U+{ord(c):04X}' for c in bad)
            if other:
                line += '  not judged ' + ' '.join(f'U+{ord(c):04X}' for c in other)
            print(line)
    print('kinsoku violations by the Story engine:', bad_total)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
