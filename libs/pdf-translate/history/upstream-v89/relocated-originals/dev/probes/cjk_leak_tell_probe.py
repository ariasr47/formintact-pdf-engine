#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Task F, measured first: can the leak scan tell Japanese from Chinese when
source and output share the spaceless CJK family?

Today verify prints one REVIEW for a ZH<->JA job and cannot gate. retypeset
already refuses a segment the mapping omits, so the leak that reaches a
delivered document is an ECHO: the translator returns the source text as the
target, and the mapping says target == source, exactly as it would for a
number or a name. Two candidate rules:

  V  (the brief's): a source segment's text found verbatim in the output while
     its mapped target differs. Cannot see an echo by construction — measured
     here to show that, not to argue it.
  S  (script tell): characters that cannot belong to the target language.
       target zh-Hans: any kana; any Han character outside GB 2312.
       target ja:      any Han character outside the Japanese repertoire
                       (not encodable in cp932 = JIS X 0208 + extensions).
     Numbers, Latin and Han shared by both repertoires are never a tell, so a
     legitimately equal target (2026年3月31日, 山田太郎) is not flagged.

Sections:
  1. the tell on constructed texts and on corpus/ja_source.pdf — counts per
     text, distinct tell characters, so false positives can be seen;
  2. a real ja -> zh-Hans delivery through the pipeline (extract -> strip ->
     prepare_font -> retypeset) with one segment echoed and two legitimately
     equal; rule V and rule S on its output text; what verify says today.

  PYTHONUTF8=1 python dev/probes/cjk_leak_tell_probe.py
"""
import io
import json
import re
import sys
import tempfile
import unicodedata
from contextlib import redirect_stdout
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SKILL = ROOT / 'pdf-translate'
sys.path.insert(0, str(SKILL))
from tests.test_pipeline import (extract_segments, prepare_font, retypeset,  # noqa: E402
                                 strip_text, write_mapping)
from pdf_translate.verify import verify  # noqa: E402

FONTS = SKILL / 'tests' / 'fonts'
JP, SC = FONTS / 'NotoSansJP-VF.ttf', FONTS / 'NotoSansSC-VF.ttf'

KANA = re.compile(r'[ぁ-ゟ゠-ヿㇰ-ㇿｦ-ﾟ]')
HAN = ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x2FA1F))


def is_han(ch):
    o = ord(ch)
    return any(a <= o <= b for a, b in HAN)


def encodable(ch, enc):
    try:
        ch.encode(enc)
        return True
    except UnicodeEncodeError:
        return False


def tell_for_ja(ch):
    """A character that cannot be written in Japanese: Han outside cp932."""
    return is_han(ch) and not encodable(ch, 'cp932')


def tell_for_zh_hans(ch):
    """A character that cannot be written in Simplified Chinese: kana, or Han outside GB 2312."""
    return bool(KANA.match(ch)) or (is_han(ch) and not encodable(ch, 'gb2312'))


def tell_for_zh_hant(ch):
    """A character that cannot be written in Traditional Chinese: kana, or Han outside Big5."""
    return bool(KANA.match(ch)) or (is_han(ch) and not encodable(ch, 'big5'))


TEXTS = {
    'ja: form paragraph 1': '申請者は本日中にこの書類を提出しなければなりません。指定された欄に氏名と住所を正確に記入し、署名欄に署名してください。記入漏れのある書類は返送されます。',
    'ja: form paragraph 2': 'この申請書は在留許可の延長を申請するためのものです。有効なパスポートの写し、最近撮影した写真二枚、および手数料の支払い証明を添付してください。手続きには約二十営業日かかります。',
    'ja: kinsoku paragraph (test_cjk)': 'データー、翻訳「テスト」の結果。ページーレイアウトは、ちょっとした「工夫」で、きっちり保たれます。',
    'ja: kanji-only line': '東京都千代田区霞が関一丁目 国税庁 所得税確定申告書',
    'zh-Hans: form paragraph 1': '申请人必须在今天提交此表格。请在指定栏目中准确填写您的姓名和地址，并在签名处签字。未按要求填写的表格将被退回。',
    'zh-Hans: form paragraph 2': '本申请表用于申请居留许可的延期。请附上有效护照复印件、两张近期照片和缴费凭证。办理时间约为二十个工作日。',
    'zh-Hans: form paragraph 3': '如有疑问，请拨打服务热线或访问我们的网站。工作时间为周一至周五上午九点至下午五点。',
    'zh-Hans: shared-form sentence': '日本国东京都 2026年3月31日 山田太郎 电话 03-1234-5678',
    'zh-Hant: form sentence': '申請人必須在今天提交此表格。請在指定欄位中準確填寫您的姓名和地址，並在簽名處簽字。',
    'mixed: zh-Hans with a Japanese name in kana': '申请人：やまだ たろう（山田太郎），出生于1990年。',
}


def measure(label, text):
    letters = [c for c in text if is_han(c) or KANA.match(c)]
    kana = sum(1 for c in text if KANA.match(c))
    han = sum(1 for c in text if is_han(c))
    text = unicodedata.normalize('NFKC', text)
    t_ja = sorted({c for c in text if tell_for_ja(c)})
    t_zh = sorted({c for c in text if tell_for_zh_hans(c)})
    t_tc = sorted({c for c in text if tell_for_zh_hant(c)})
    n_ja = sum(1 for c in text if tell_for_ja(c))
    n_zh = sum(1 for c in text if tell_for_zh_hans(c))
    n_tc = sum(1 for c in text if tell_for_zh_hant(c))
    print(f'{label:46s} letters {len(letters):3d} kana {kana:3d} han {han:3d} | '
          f'target ja: {n_ja:3d} {"".join(t_ja)[:16]:16s} | '
          f'target zh-Hans: {n_zh:3d} {"".join(t_zh)[:16]:16s} | '
          f'target zh-Hant: {n_tc:3d} {"".join(t_tc)[:16]}')


def main():
    for p in (JP, SC):
        if not p.is_file():
            print(f'missing {p}; run tools/fetch_test_fonts.py')
            return 1
    print('== 1. the tell on constructed texts and the corpus ==')
    print('   (a text of the target language should show 0 tells for that target; '
          'the other language should show many)')
    for label, text in TEXTS.items():
        measure(label, text)
    corpus = SKILL / 'corpus' / 'ja_source.pdf'
    if corpus.is_file():
        with pymupdf.open(str(corpus)) as doc:
            text = '\n'.join(p.get_text() for p in doc)
        measure('ja: corpus/ja_source.pdf (all pages)', text)

    print('\n== 2. a real ja -> zh-Hans delivery with one echoed segment ==')
    sc = pymupdf.Font(fontfile=str(SC))
    print('   Noto Sans SC has kana:', all(bool(sc.has_glyph(ord(c))) for c in 'あいうカタカナ'))
    lines = [
        ('申請者は本日中にこの書類を提出してください。', '申请人必须在今天提交此表格。'),
        ('指定された欄に氏名と住所を記入してください。', '指定された欄に氏名と住所を記入してください。'),   # echoed
        ('2026年3月31日', '2026年3月31日'),                                                            # equal, legitimate
        ('山田太郎', '山田太郎'),                                                                       # equal, legitimate
        ('手続きには約二十営業日かかります。', '办理时间约为二十个工作日。'),
    ]
    work = Path(tempfile.mkdtemp(prefix='cjk-leak-'))
    src, stripped, out = work / 'orig.pdf', work / 'stripped.pdf', work / 'out.pdf'
    tr, subset = work / 'translations.json', work / 'sc-subset.ttf'
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    tw = pymupdf.TextWriter(page.rect)
    jp = pymupdf.Font(fontfile=str(JP))
    for i, (s, _) in enumerate(lines):
        tw.append((72, 100 + 40 * i), s, font=jp, fontsize=12)
    tw.write_text(page)
    doc.save(str(src))
    doc.close()
    extract_segments.extract_segments(str(src), outdir=str(work))
    segs = json.load(open(work / 'segments.json', encoding='utf-8'))['segments']
    seg_texts = [s['text'] for s in segs]
    print('   segments extracted:', len(seg_texts), '| equal to the typed source lines:',
          [t in dict(lines) for t in seg_texts])
    # A mapping is authored from segments.json, never from what the author typed:
    # a source PDF's ToUnicode can drift (here the variable font's cmap maps the
    # compatibility ideographs U+F98E 年 / U+F92C 郎 to the same glyphs as
    # U+5E74 / U+90CE, and MuPDF's reverse mapping picks them — audit finding
    # H4), and the mapping key must be the extracted text. The echoed and the
    # legitimately equal targets are the extracted text too, as an echoing
    # translator would return them — so the drift reaches the OUTPUT through an
    # authored target, and a tell that does not fold it (NFKC) flags 年 as foreign.
    assert len(seg_texts) == len(lines), seg_texts
    mapping = {seg: (seg if t == s else t) for seg, (s, t) in zip(seg_texts, lines)}
    strip_text.strip_text(str(src), str(stripped))
    write_mapping(str(tr), mapping, JP, lang='zh-Hans')
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = prepare_font.prepare_font(str(SC), str(tr), str(subset), instance='wght=400')
    print('   prepare_font (SC face, lang zh-Hans):', 'rc', rc, '|',
          [l for l in buf.getvalue().splitlines() if 'han-forms' in l or l.startswith(('FAIL', 'OK'))][-1:])
    if rc:
        print(buf.getvalue()[-600:])
        return 1
    write_mapping(str(tr), mapping, subset, lang='zh-Hans')
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = retypeset.retypeset(str(stripped), str(work / 'segments.json'), str(tr), str(out))
    print('   retypeset rc', rc)
    if rc:
        print(buf.getvalue()[-800:])
        return 1
    with pymupdf.open(str(out)) as done:
        out_text = '\n'.join(p.get_text() for p in done)
    print('   output text lines:', [l for l in out_text.splitlines() if l.strip()])

    print('\n   rule V — source text verbatim in the output while the mapped target differs:')
    for s, t in mapping.items():
        hit = s in out_text and t != s
        print(f'      {"LEAK" if hit else "  - "}  {s[:22]:22s} target {"== source" if t == s else "differs"} '
              f'| source in output: {s in out_text}')
    print('   rule S — characters that cannot belong to a zh-Hans target, per output line '
          '(raw, then NFKC-folded):')
    for line in (l for l in out_text.splitlines() if l.strip()):
        raw = [c for c in line if tell_for_zh_hans(c)]
        folded = [c for c in unicodedata.normalize('NFKC', line) if tell_for_zh_hans(c)]
        print(f'      raw {"LEAK" if raw else "  - "} / NFKC {"LEAK" if folded else "  - "}  {line[:24]:24s} '
              f'tells raw {len(raw):2d} {"".join(sorted(set(raw)))[:12]:12s} folded {len(folded):2d} '
              f'{"".join(sorted(set(folded)))[:16]}')

    print('\n   verify today (with --source-words-from segments.json):')
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = verify(str(src), str(out), translations=str(tr), segments=str(work / 'segments.json'),
                    source_words_from=str(work / 'segments.json'), min_ink=0.1)
    for l in buf.getvalue().splitlines():
        if 'leak' in l.lower() or l.startswith(('FAIL', 'REVIEW')):
            print('     ', l[:150])
    print('   exit', rc, '| work', work)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
