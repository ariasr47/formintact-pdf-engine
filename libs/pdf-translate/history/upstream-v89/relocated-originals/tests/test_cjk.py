#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Gate 19, kinsoku: no drawn CJK line begins with a character JIS X 4051 /
JLREQ forbids at line start, or ends with an opening bracket. And the two CJK
test faces: Noto Sans JP lacks 东, Noto Sans SC has it, and a job whose face
lacks a character is refused, not drawn from another face. Spec:
docs/reviews/2026-09-16-cjk-request-assessment.md §3."""
import importlib
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import pymupdf

from pdf_translate.verify import GATE_NAMES, kinsoku_report, run_verify, verify

verify_mod = importlib.import_module('pdf_translate.verify')

FONTS = Path(__file__).resolve().parents[1] / 'tests' / 'fonts'
JP_FACE = FONTS / 'NotoSansJP-VF.ttf'
SC_FACE = FONTS / 'NotoSansSC-VF.ttf'

# Three lines a translator split by hand: the first ends with an opening
# bracket, the third begins with a full stop. The middle line is clean.
SPLIT_BAD = ['申立人は以下の情報を「', '記入欄」に正確に記入してください', '。氏名と住所も書いてください']
# The same text split where JLREQ allows.
SPLIT_GOOD = ['申立人は以下の情報を', '「記入欄」に正確に記入してください。', '氏名と住所も書いてください']
# A paragraph dense in the characters the rules protect, for the Story engine to wrap.
PARA = ('データー、翻訳「テスト」の結果。ページーレイアウトは、ちょっとした「工夫」で、'
        'きっちり保たれます。' * 4)


def split_lines_page(doc, lines, x=60, y=100, leading=14):
    """Lines drawn the way retypeset draws a hand-split target: one TextWriter,
    successive baselines, MuPDF's bundled CJK face."""
    page = doc.new_page(width=595, height=842)
    font = pymupdf.Font('cjk')
    tw = pymupdf.TextWriter(page.rect)
    for i, text in enumerate(lines):
        tw.append((x, y + leading * i), text, font=font, fontsize=10)
    tw.write_text(page)
    return page


def story_page(doc, html, fontfile=None, width=140):
    """A paragraph wrapped by the Story engine in a box `width` points wide."""
    page = doc.new_page(width=595, height=842)
    css, arch = '', None
    if fontfile:
        arch = pymupdf.Archive(os.path.dirname(fontfile))
        css = ('@font-face {font-family: F; src: url(%s);} p {font-family: F;}'
               % os.path.basename(fontfile))
    page.insert_htmlbox(pymupdf.Rect(60, 100, 60 + width, 800),
                        '<p style="font-size:10pt">%s</p>' % html,
                        css=css, archive=arch, scale_low=0)
    return page


def runs_page(doc, runs):
    """Independent runs at given origins — cells of a form — in the bundled CJK face."""
    page = doc.new_page(width=595, height=842)
    font = pymupdf.Font('cjk')
    tw = pymupdf.TextWriter(page.rect)
    for x, y, text in runs:
        tw.append((x, y), text, font=font, fontsize=10)
    tw.write_text(page)
    return page


class KinsokuSetTests(unittest.TestCase):

    def test_members_from_the_public_classes(self):
        # cl-06 。 cl-07 、 cl-02 」） cl-10 ー and half-width ｰ, cl-11 ぁッ and
        # half-width ｧ, half-width ｡ ｣, cl-04 ？
        for ch in '。、」）ーｰぁッｧ｡｣？':
            self.assertIn(ch, verify_mod.KINSOKU_LINE_START, f'U+{ord(ch):04X}')
        # cl-01 「（【 and half-width ｢
        for ch in '「（【｢':
            self.assertIn(ch, verify_mod.KINSOKU_LINE_END, f'U+{ord(ch):04X}')

    def test_the_two_sets_are_disjoint(self):
        self.assertEqual(verify_mod.KINSOKU_LINE_START & verify_mod.KINSOKU_LINE_END,
                         frozenset())

    def test_ordinary_letters_are_in_neither(self):
        for ch in 'あ漢アA1':
            self.assertNotIn(ch, verify_mod.KINSOKU_LINE_START)
            self.assertNotIn(ch, verify_mod.KINSOKU_LINE_END)


class KinsokuReportTests(unittest.TestCase):

    def test_hand_split_lines_review_with_a_finding_per_broken_line(self):
        doc = pymupdf.open()
        split_lines_page(doc, SPLIT_BAD)
        lines, status, findings = kinsoku_report(doc)
        self.assertEqual(status, 'REVIEW')
        self.assertEqual([(f.page, f.where, f.text) for f in findings],
                         [(1, 'line-end', SPLIT_BAD[0]), (1, 'line-start', SPLIT_BAD[2])])
        self.assertTrue(lines[0].startswith('REVIEW kinsoku: 2 line(s) of CJK text'), lines)
        self.assertEqual(lines[1], f'   p1 line-end: {SPLIT_BAD[0]}')
        self.assertEqual(lines[2], f'   p1 line-start: {SPLIT_BAD[2]}')

    def test_hand_split_lines_that_respect_the_rules_pass(self):
        doc = pymupdf.open()
        split_lines_page(doc, SPLIT_GOOD)
        lines, status, findings = kinsoku_report(doc)
        self.assertEqual((status, findings), ('PASS', []))
        self.assertEqual(lines, ['PASS kinsoku: 3 CJK line(s) break within the rules'])

    def test_the_story_engine_paragraph_passes(self):
        doc = pymupdf.open()
        story_page(doc, PARA)
        lines, status, findings = kinsoku_report(doc)
        self.assertEqual((status, findings), ('PASS', []))
        self.assertRegex(lines[0], r'^PASS kinsoku: \d+ CJK line\(s\) break within the rules$')
        # The engine really wrapped it: several lines, not one.
        self.assertGreater(int(lines[0].split()[2]), 3, lines)

    def test_a_lone_label_is_not_a_break(self):
        # A block's only line has no break before or after it: a dash used
        # as "none", a closing bracket after a checkbox, are labels, not
        # violations.
        doc = pymupdf.open()
        split_lines_page(doc, ['ー'], y=100)
        split_lines_page(doc, ['）該当なし'], y=100)
        lines, status, findings = kinsoku_report(doc)
        self.assertEqual((status, findings), ('PASS', []))
        self.assertEqual(lines, ['PASS kinsoku: 2 CJK line(s) break within the rules'])

    def test_first_and_last_lines_of_a_block_are_exempt(self):
        # A block whose first line starts with 。 had no break before it;
        # one whose last line ends with 「 had none after it.
        doc = pymupdf.open()
        split_lines_page(doc, ['。氏名', '住所を書く', '記入欄「'])
        lines, status, findings = kinsoku_report(doc)
        self.assertEqual((status, findings), ('PASS', []))

    def test_pages_without_cjk_print_nothing(self):
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), 'Hello world.')
        page.insert_text((72, 90), '(continued)')
        self.assertEqual(kinsoku_report(doc), ([], None, []))

    def test_a_latin_line_inside_a_cjk_block_keeps_the_neighbours(self):
        # Every line of a CJK stack counts for adjacency and is judged; the
        # Latin middle line matches no member and neither hides the break
        # before the third line nor is flagged.
        doc = pymupdf.open()
        split_lines_page(doc, ['申立人は', 'Form 1040', '。氏名と住所'])
        lines, status, findings = kinsoku_report(doc)
        self.assertEqual(status, 'REVIEW')
        self.assertEqual([(f.where, f.text) for f in findings],
                         [('line-start', '。氏名と住所')])
        self.assertEqual(lines[0].split(':')[0], 'REVIEW kinsoku')

    def test_a_punctuation_only_line_of_two_or_more_characters_is_judged(self):
        # A hand split can leave 」。 on its own line: no letter, two characters,
        # judged by its stack like any other line.
        doc = pymupdf.open()
        split_lines_page(doc, ['\u5f7c\u306f\u300c\u306f\u3044', '\u300d\u3002'])
        lines, status, findings = kinsoku_report(doc)
        self.assertEqual(status, 'REVIEW')
        self.assertEqual([(f.page, f.where, f.text) for f in findings],
                         [(1, 'line-start', '\u300d\u3002')])

    def test_a_single_character_line_is_a_value_not_a_break(self):
        # ー for "none" in a column of cells, a bracket after a field, a lone
        # 「 — a form sets these on purpose. (A lone 「 left by a hand split is
        # therefore not caught: documented in gates.md.)
        doc = pymupdf.open()
        split_lines_page(doc, ['\u6c0f\u540d\uff1a\u7530\u4e2d', '\u30fc', '\u4f4f\u6240\uff1a\u6771\u4eac'])
        split_lines_page(doc, ['\u3042\u308b\u6587\u7ae0\u3067\u3059', '\u300c', '\u7d9a\u304f\u6587\u7ae0'])
        split_lines_page(doc, ['\u6ce8\u8a18\u4e8b\u9805', '\uff09', '\u5099\u8003'])
        lines, status, findings = kinsoku_report(doc)
        self.assertEqual((status, findings), ('PASS', []))
        self.assertEqual(lines, ['PASS kinsoku: 9 CJK line(s) break within the rules'])

    def test_a_marker_that_begins_two_or_more_lines_is_a_list(self):
        doc = pymupdf.open()
        split_lines_page(doc, ['\u30fb\u6c0f\u540d', '\u30fb\u4f4f\u6240', '\u30fb\u96fb\u8a71\u756a\u53f7'])
        self.assertEqual(kinsoku_report(doc)[1:], ('PASS', []))
        # One ・ alone at a line start is still a break before a middle dot.
        doc = pymupdf.open()
        split_lines_page(doc, ['\u30ad\u30e3\u30c3\u30b7\u30e5', '\u30fb\u30b5\u30fc\u30d0\u30fc'])
        lines, status, findings = kinsoku_report(doc)
        self.assertEqual(status, 'REVIEW')
        self.assertEqual([(f.where, f.text) for f in findings],
                         [('line-start', '\u30fb\u30b5\u30fc\u30d0\u30fc')])

    def test_a_repeated_full_stop_at_line_start_is_not_a_marker(self):
        # Two lines of a hand-split paragraph beginning with 。 are two breaks,
        # not a list; only the bullets in KINSOKU_LIST_MARKERS can be markers.
        doc = pymupdf.open()
        split_lines_page(doc, ['\u7533\u7acb\u4eba\u306f\u6b21\u306e\u60c5\u5831\u3092\u8a18\u5165',
                               '\u3002\u6c0f\u540d\u3068\u4f4f\u6240\u3092\u66f8\u3044\u3066',
                               '\u304f\u3060\u3055\u3044\u3002\u305d\u306e\u5f8c\u63d0\u51fa',
                               '\u3002\u671f\u9650\u306f\u4e09\u5341\u65e5\u3067\u3059'])
        lines, status, findings = kinsoku_report(doc)
        self.assertEqual(status, 'REVIEW')
        self.assertEqual([(f.where, f.text[0]) for f in findings],
                         [('line-start', '\u3002'), ('line-start', '\u3002')])

    def test_the_sets_match_the_probe_that_measured_them(self):
        # verify.py and dev/probes/cjk_kinsoku_probe.py carry the same members
        # by hand; the probe's protected counts mean nothing if they drift.
        import importlib.util
        path = Path(__file__).resolve().parents[2] / 'dev' / 'probes' / 'cjk_kinsoku_probe.py'
        spec = importlib.util.spec_from_file_location('cjk_kinsoku_probe', path)
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        self.assertEqual(verify_mod.KINSOKU_LINE_START, frozenset(probe.START_SET))
        self.assertEqual(verify_mod.KINSOKU_LINE_END, frozenset(probe.END_SET))

    def test_side_by_side_cells_are_not_a_stack(self):
        # Two cells on one baseline never sit one under the other, whatever
        # block MuPDF puts them in; the next row stacks under the first cell.
        doc = pymupdf.open()
        runs_page(doc, [(60, 100, '\u9805\u76ee\u540d'),
                        (200, 100, '\uff09\u5185\u306b\u8a18\u5165'),
                        (60, 114, '\u5099\u8003')])
        lines, status, findings = kinsoku_report(doc)
        self.assertEqual((status, findings), ('PASS', []))
        self.assertEqual(lines, ['PASS kinsoku: 3 CJK line(s) break within the rules'])

    def test_hand_split_lines_are_caught_up_to_three_times_the_line_height(self):
        # MuPDF's blocks split above ~1.6x leading; the stacks do not.
        for leading in (16, 24, 30):
            doc = pymupdf.open()
            split_lines_page(doc, SPLIT_BAD, leading=leading)
            lines, status, findings = kinsoku_report(doc)
            self.assertEqual(status, 'REVIEW', leading)
            self.assertEqual(len(findings), 2, (leading, findings))
        # The documented bound: at four times the line height the lines are
        # independent labels, and nothing is judged against its neighbour.
        doc = pymupdf.open()
        split_lines_page(doc, SPLIT_BAD, leading=40)
        self.assertEqual(kinsoku_report(doc)[1:], ('PASS', []))

    def test_a_block_without_any_cjk_letter_is_never_judged(self):
        # Latin lines ending in a curly opening quote are not Japanese
        # typography's business, whatever face drew them.
        doc = pymupdf.open()
        split_lines_page(doc, ['He said \u201c', 'hello.\u201d'])
        self.assertEqual(kinsoku_report(doc), ([], None, []))

    def test_the_gate_consults_the_sets(self):
        # Remove the two members the fixture breaks and the fixture passes:
        # the gate judges by the sets, not by a hard-coded character.
        doc = pymupdf.open()
        split_lines_page(doc, SPLIT_BAD)
        start = verify_mod.KINSOKU_LINE_START - {'。'}
        end = verify_mod.KINSOKU_LINE_END - {'「'}
        with mock.patch.object(verify_mod, 'KINSOKU_LINE_START', start), \
                mock.patch.object(verify_mod, 'KINSOKU_LINE_END', end):
            lines, status, findings = kinsoku_report(doc)
        self.assertEqual((status, findings), ('PASS', []))


class KinsokuVerifyTests(unittest.TestCase):
    """The gate through run_verify and the CLI: recorded, printed once, exit 1."""

    def _job(self, tmp, out_lines):
        orig = os.path.join(tmp, 'orig.pdf')
        out = os.path.join(tmp, 'out.pdf')
        doc = pymupdf.open()
        page = doc.new_page(width=595, height=842)
        for i, text in enumerate(SPLIT_GOOD):
            page.insert_text((60, 100 + 14 * i), text, fontname='japan', fontsize=10)
        doc.save(orig)
        doc.close()
        doc = pymupdf.open()
        split_lines_page(doc, out_lines)
        doc.save(out)
        doc.close()
        return orig, out

    def test_the_gate_has_a_name(self):
        self.assertIn('kinsoku', GATE_NAMES)
        self.assertEqual(GATE_NAMES.index('kinsoku'), GATE_NAMES.index('conjunct-shaping') + 1)

    def test_a_broken_delivery_records_kinsoku_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig, out = self._job(tmp, SPLIT_BAD)
            v = run_verify(orig, out, min_ink=0.1)
            gate = {g.name: g for g in v.gates}['kinsoku']
            self.assertEqual(gate.status, 'REVIEW')
            self.assertEqual(gate.message, '2 line(s)')
            self.assertEqual([(f.page, f.where, f.text) for f in gate.findings],
                             [(1, 'line-end', SPLIT_BAD[0]), (1, 'line-start', SPLIT_BAD[2])])

    def test_a_clean_delivery_records_kinsoku_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig, out = self._job(tmp, SPLIT_GOOD)
            v = run_verify(orig, out, min_ink=0.1)
            gate = {g.name: g for g in v.gates}['kinsoku']
            self.assertEqual((gate.status, gate.message, gate.findings), ('PASS', '', ()))

    def test_the_console_prints_the_gate_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig, out = self._job(tmp, SPLIT_BAD)
            buf = io.StringIO()
            with redirect_stdout(buf):
                verify(orig, out, min_ink=0.1)
            printed = buf.getvalue().splitlines()
            heads = [ln for ln in printed if ln.startswith(('REVIEW kinsoku', 'PASS kinsoku'))]
            self.assertEqual(len(heads), 1, printed)
            self.assertIn(f'   p1 line-start: {SPLIT_BAD[2]}', printed)

    def test_a_latin_job_prints_no_kinsoku_line_and_records_none(self):
        from tests.test_import_surface import _job
        with tempfile.TemporaryDirectory() as tmp:
            src, out, tr = _job(tmp)
            buf = io.StringIO()
            with redirect_stdout(buf):
                verify(src, out, translations=tr)
            self.assertNotIn('kinsoku', buf.getvalue())
            v = run_verify(src, out, translations=tr)
            self.assertNotIn('kinsoku', [g.name for g in v.gates])


def _face(path):
    if not path.is_file():
        raise unittest.SkipTest(f'{path.name} not fetched (tools/fetch_test_fonts.py)')
    return str(path)


def _drawing_fonts(page):
    """Names of the fonts that drew text on the page, spaces removed.

    MuPDF names the embedded face 'Noto Sans JP Thin', and a page's resource
    dictionary can list faces nothing draws with (strip_text leaves the
    original Helvetica behind), so the spans are asked, not get_fonts()."""
    names = set()
    for block in page.get_text('dict').get('blocks', []):
        for line in block.get('lines', []):
            for span in line.get('spans', []):
                if span.get('text', '').strip():
                    names.add(span.get('font', '').replace(' ', ''))
    return names


class NotoSansJpTests(unittest.TestCase):
    """The fetched Japanese face, through the Story engine."""

    def test_a_noto_sans_jp_paragraph_breaks_within_the_rules(self):
        jp = _face(JP_FACE)
        doc = pymupdf.open()
        page = story_page(doc, PARA, fontfile=jp)
        fonts = _drawing_fonts(page)
        self.assertTrue(fonts and all('NotoSansJP' in n for n in fonts), fonts)
        lines, status, findings = kinsoku_report(doc)
        self.assertEqual((status, findings), ('PASS', []))
        self.assertGreater(int(lines[0].split()[2]), 3, lines)


class CjkFaceTests(unittest.TestCase):
    """2d: a character the selected face cannot draw is refused, never drawn
    from another face. The precondition is asserted so a font update cannot
    make the test pass for the wrong reason."""

    def setUp(self):
        self.jp = _face(JP_FACE)
        self.sc = _face(SC_FACE)

    def test_the_japanese_face_lacks_east_and_the_chinese_face_has_it(self):
        jp = pymupdf.Font(fontfile=self.jp)
        sc = pymupdf.Font(fontfile=self.sc)
        self.assertFalse(jp.has_glyph(0x4E1C), 'Noto Sans JP now carries 东: 2d needs a new separator')
        self.assertTrue(sc.has_glyph(0x4E1C))
        for cp in (0x76F4, 0x9AA8, 0x6D77, 0x6771):   # 直 骨 海 東
            self.assertTrue(jp.has_glyph(cp) and sc.has_glyph(cp), f'U+{cp:04X}')

    def test_a_character_the_japanese_face_lacks_is_refused_not_borrowed(self):
        from tests.test_pipeline import (SOURCE_SENTENCE, build_plain_pdf, extract_segments,
                                         retypeset, strip_text, write_mapping)
        self.assertFalse(pymupdf.Font(fontfile=self.jp).has_glyph(0x4E1C))
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 'orig.pdf')
            stripped = os.path.join(tmp, 'stripped.pdf')
            out = os.path.join(tmp, 'out.pdf')
            tr = os.path.join(tmp, 'translations.json')
            build_plain_pdf(src)
            extract_segments.extract_segments(src, outdir=tmp)
            strip_text.strip_text(src, stripped)
            write_mapping(tr, {SOURCE_SENTENCE: '\u6771\u4eac \u4e1c'}, Path(self.jp))
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = retypeset.retypeset(stripped, os.path.join(tmp, 'segments.json'), tr, out)
            log = buf.getvalue()
            self.assertNotEqual(rc, 0, msg=log)
            self.assertIn('the chosen font cannot draw', log)
            self.assertIn('U+4E1C', log)
            self.assertFalse(os.path.exists(out), msg=log)
            # Control: the same job without 东 is drawn, and only by the JP face.
            write_mapping(tr, {SOURCE_SENTENCE: '\u6771\u4eac'}, Path(self.jp))
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = retypeset.retypeset(stripped, os.path.join(tmp, 'segments.json'), tr, out)
            self.assertEqual(rc, 0, msg=buf.getvalue())
            with pymupdf.open(out) as done:
                names = set().union(*(_drawing_fonts(p) for p in done))
            self.assertTrue(names and all('NotoSansJP' in n for n in names), names)


retypeset_mod = importlib.import_module('pdf_translate.retypeset')
NL = chr(10)


class WrappedCjkTargetTests(unittest.TestCase):
    # A Story-engine paragraph in a spaceless script wraps with no space at
    # the break, so the layer holds a newline the authored text never had:
    # every wrapped Japanese merge and the notice on the FL-150 job came
    # back "missing" (2026-09-17). Latin keeps its spaces: a wrap there is
    # a space on both sides, and a target missing one is still missing.

    def test_wrapped_japanese_merge_is_found(self):
        layer = """氏名
私との続柄（例：息
子）
その人の月間総収入
"""
        self.assertEqual(verify_mod.missing_translation_targets(
            layer, ['私との続柄（例：息子）']), [])

    def test_wrapped_notice_with_a_latin_title_is_found(self):
        layer = """ん。 これは「FL-150 INCOME AND EXPENSE
DECLARATION」の非公式な日本語訳で、書式の内
容を理解するためのものです。"""
        target = ('これは「FL-150 INCOME AND EXPENSE DECLARATION」の非公式な'
                  '日本語訳で、書式の内容を理解するためのものです。')
        self.assertEqual(verify_mod.missing_translation_targets(layer, [target]), [])

    def test_latin_target_still_needs_its_spaces(self):
        self.assertEqual(verify_mod.missing_translation_targets(
            'one' + NL + 'two', ['one two']), [])
        self.assertEqual(verify_mod.missing_translation_targets(
            'onetwo', ['one two']), ['one two'])

    def test_a_missing_japanese_character_is_still_missing(self):
        layer = """私との続柄（例：息
子）"""
        self.assertEqual(verify_mod.missing_translation_targets(
            layer, ['私との続柄（例：娘）']), ['私との続柄（例：娘）'])


class LoclAlternateTests(unittest.TestCase):
    # Noto Sans JP substitutes its digits through `locl` when HarfBuzz
    # shapes a Latin run under the Japanese language tag — 150 after
    # "FL-", 11 in "11-inch" — so the layer read Ɍɐɋ for 150 on the FL-150
    # job (2026-09-17; 2019年 was untouched: digits inside a CJK run take
    # the CJK path). The alternates are single substitutions with no cmap
    # entry, the same orphaned-glyph problem as a ligature; the canonical
    # layer maps each one back to its base glyph's character.

    def test_single_substitution_alternates_map_to_their_base(self):
        m = retypeset_mod.ligature_gid_map(str(JP_FACE), set('150'))
        self.assertTrue({'1', '5', '0'} <= set(m.values()), m)

    def test_alternates_of_unauthored_characters_are_not_mapped(self):
        m = retypeset_mod.ligature_gid_map(str(JP_FACE), set('あ'))
        self.assertFalse(set('0123456789') & set(m.values()), m)

    def test_a_story_run_reads_back_as_authored(self):
        text = 'FL-150 11-inch'
        with tempfile.TemporaryDirectory() as tmp:
            doc = pymupdf.open()
            story_page(doc, text, fontfile=str(JP_FACE), width=300)
            path = os.path.join(tmp, 'story.pdf')
            doc.save(path)
            doc.close()
            with pymupdf.open(path) as before:
                drifted = before[0].get_text()
            self.assertNotIn(text, drifted, 'premise: the layer drifts before the rewrite')
            retypeset_mod.canonicalize_text_layer(path, [str(JP_FACE)], [text])
            with pymupdf.open(path) as after:
                self.assertIn(text, after[0].get_text())


if __name__ == '__main__':
    unittest.main()
