# -*- coding: utf-8 -*-
"""Conjunct-shaping probe: a mechanical tell for scripts whose broken
conjuncts leave no code-point signal in the text layer.

The tell (AGENTS.md, measured facts): render a probe string that contains
a merging cluster twice off the SAME face — once glyph by glyph
(insert_text, no shaping) and once through the Story engine (HarfBuzz) —
and count drawn glyphs. Shaping merges the cluster, so the shaped count is
lower. Arabic goes the other way (shaping ADDS glyphs) and has its own
gate; Thai, Lao and Hebrew-with-niqqud never change count and are blind to
this tell, so they must never be reported as verified by it.

Every count here is read from page.get_texttrace(), one entry per drawn
glyph, and both arms assert which font actually drew the run, because
insert_text(fontfile=...) without fontname= silently falls back to
Helvetica and draws one middle dot per code point.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

from fontTools.ttLib import TTFont

SKILL = Path(__file__).resolve().parents[1]
FONTS = SKILL / 'tests' / 'fonts'
sys.path.insert(0, str(SKILL / 'scripts'))

from pdf_translate import shaping_probe  # noqa: E402

NOTO = {
    'Devanagari': 'NotoSansDevanagari-Regular.ttf',
    'Bengali': 'NotoSansBengali-Regular.ttf',
    'Tamil': 'NotoSansTamil-Regular.ttf',
    'Khmer': 'NotoSansKhmer-Regular.ttf',
    'Myanmar': 'NotoSansMyanmar-Regular.ttf',
    'Thai': 'NotoSansThai-Regular.ttf',
    'Hebrew': 'NotoSansHebrew-Regular.ttf',
}


def noto(script):
    path = FONTS / NOTO[script]
    if not path.is_file():
        raise unittest.SkipTest(f'{path.name} not fetched (tools/fetch_test_fonts.py)')
    return str(path)


def strip_gsub(src, dst):
    """A copy of the face that cannot shape: same glyphs, no GSUB table."""
    with TTFont(src) as font:
        del font['GSUB']
        font.save(dst)
    return dst


class ProbeTableTests(unittest.TestCase):
    def test_every_probe_row_shapes_on_its_noto_face(self):
        """The attestation: for each table row the naive arm draws one
        glyph per code point, the shaped arm draws the measured count, and
        both arms were drawn by the face we asked for."""
        for probe in shaping_probe.PROBES:
            with self.subTest(script=probe.script, probe=probe.text):
                results = shaping_probe.probe_font(noto(probe.script), scripts=[probe.script])
                self.assertEqual(len(results), 1, results)
                r = results[0]
                self.assertEqual(r.status, 'PASS', r)
                self.assertEqual(r.codepoints, len(probe.text))
                self.assertEqual(r.naive_glyphs, r.codepoints, r)
                self.assertEqual(r.shaped_glyphs, probe.expected_shaped, r)
                self.assertLess(r.shaped_glyphs, r.naive_glyphs)
                self.assertEqual(r.naive_font, r.expected_font, r)
                self.assertEqual(r.shaped_font, r.expected_font, r)

    def test_table_covers_the_conjunct_scripts_with_fetched_faces(self):
        scripts = {p.script for p in shaping_probe.PROBES}
        self.assertEqual(scripts, {'Devanagari', 'Bengali', 'Tamil', 'Khmer', 'Myanmar'})

    def test_arabic_is_not_probed_by_glyph_count(self):
        """Shaping ADDS glyphs in Arabic (5 -> 8). Gate 12 owns Arabic."""
        self.assertNotIn('Arabic', {p.script for p in shaping_probe.PROBES})
        self.assertNotIn('Arabic', shaping_probe.BLIND_SCRIPTS)

    def test_blind_scripts_are_reviewed_never_passed(self):
        """Mark stacking never changes the glyph count: a correct Thai or
        niqqud run and a broken one look identical to this tell."""
        for script in ('Thai', 'Hebrew niqqud'):
            self.assertIn(script, shaping_probe.BLIND_SCRIPTS)
        for script, face in (('Thai', 'Thai'), ('Hebrew niqqud', 'Hebrew')):
            with self.subTest(script=script):
                results = shaping_probe.probe_font(noto(face), scripts=[script])
                self.assertEqual(len(results), 1, results)
                self.assertEqual(results[0].status, 'REVIEW', results[0])
                self.assertNotEqual(results[0].status, 'PASS')
                self.assertIn('no glyph-count tell', results[0].reason)


class RedPathTests(unittest.TestCase):
    def test_gsub_stripped_face_fails_the_probe(self):
        """The red run: same face minus GSUB draws the conjunct probe
        unshaped (shaped == naive) and the probe says FAIL, naming counts."""
        with tempfile.TemporaryDirectory() as tmp:
            bad = strip_gsub(noto('Devanagari'), os.path.join(tmp, 'no-gsub.ttf'))
            results = shaping_probe.probe_font(bad, scripts=['Devanagari'])
            self.assertEqual(len(results), 1)
            r = results[0]
            self.assertEqual(r.status, 'FAIL', r)
            self.assertEqual(r.shaped_glyphs, r.naive_glyphs, r)
            self.assertEqual(r.naive_font, r.expected_font, r)
            self.assertIn('no conjunct formed', r.reason)

    def test_font_fallback_is_a_failure_not_a_pass(self):
        """AGENTS.md method warning: a run drawn by Helvetica must never be
        judged, whatever its counts say."""
        probe = shaping_probe.PROBES[0]
        face = 'NotoSansDevanagari-Regular'
        r = shaping_probe.judge(probe, naive_glyphs=8, shaped_glyphs=4,
                                naive_font='Helvetica', shaped_font=face,
                                expected_font=face)
        self.assertEqual(r.status, 'FAIL', r)
        self.assertIn('Helvetica', r.reason)
        r = shaping_probe.judge(probe, naive_glyphs=8, shaped_glyphs=4,
                                naive_font='NotoSansDevanagari-Regular',
                                shaped_font='Helvetica',
                                expected_font='NotoSansDevanagari-Regular')
        self.assertEqual(r.status, 'FAIL', r)
        self.assertIn('Helvetica', r.reason)

    def test_naive_arm_that_is_not_one_glyph_per_codepoint_fails(self):
        probe = shaping_probe.PROBES[0]
        face = 'NotoSansDevanagari-Regular'
        r = shaping_probe.judge(probe, naive_glyphs=6, shaped_glyphs=4,
                                naive_font=face, shaped_font=face,
                                expected_font=face)
        self.assertEqual(r.status, 'FAIL', r)


class CoverageTests(unittest.TestCase):
    def test_face_without_the_probe_glyphs_is_skipped_not_judged(self):
        """A Latin-only face cannot attest Devanagari; say so, do not fail."""
        latin = FONTS / 'NotoSans-Regular.ttf'
        if not latin.is_file():
            raise unittest.SkipTest('NotoSans-Regular.ttf not fetched')
        results = shaping_probe.probe_font(str(latin), scripts=['Devanagari'])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, 'SKIP', results[0])
        self.assertIn('does not cover', results[0].reason)

    def test_scripts_in_text(self):
        self.assertEqual(shaping_probe.scripts_in('नमस्ते'), {'Devanagari'})
        self.assertEqual(shaping_probe.scripts_in('ខ្មែរ and ဗမာ'), {'Khmer', 'Myanmar'})
        self.assertEqual(shaping_probe.scripts_in('สวัสดี'), {'Thai'})
        self.assertEqual(shaping_probe.scripts_in('שָׁלוֹם'), {'Hebrew niqqud'})
        self.assertEqual(shaping_probe.scripts_in('שלום'), set())
        self.assertEqual(shaping_probe.scripts_in('مكتبة'), set())
        self.assertEqual(shaping_probe.scripts_in('ગુજરાતી'), {'Gujarati'})
        self.assertEqual(shaping_probe.scripts_in('Hello 123'), set())

    def test_conjunct_script_without_a_measured_probe_is_reviewed(self):
        """Gujarati is conjunct-forming, but no face was measured, so there
        is no expected count to attest against. Never PASS by assumption."""
        results = shaping_probe.probe_font(noto('Devanagari'), scripts=['Gujarati'])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, 'REVIEW', results[0])
        self.assertIn('no probe measured', results[0].reason)


# ---------------------------------------------------------------------------
# Hooks: prepare_font attests the subset it writes; verify re-probes the
# font program embedded in the output.

import io  # noqa: E402
import json  # noqa: E402
from contextlib import redirect_stdout  # noqa: E402

import pymupdf  # noqa: E402

from pdf_translate.prepare_font import prepare_font  # noqa: E402
from pdf_translate.verify import run_verify, verify  # noqa: E402

SOURCE_LINE = 'Peace be upon you and mercy'
DV_TARGET = 'नमस्ते'          # a virama, but not the probe cluster
TH_TARGET = 'สวัสดี'
LATIN_TARGET = 'La paz sea contigo'
PROBE_CLUSTER = shaping_probe.PROBE_FOR['Devanagari'].text


def build_source_pdf(path, text=SOURCE_LINE):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 80), text, fontsize=12)
    doc.save(path)
    doc.close()


def draw_story_output(path, text, fontfile, size=12):
    """One line placed through the Story engine, the way retypeset does it,
    so the whole font program is embedded and can be extracted again."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    css = ('@font-face {font-family: t; src: url("%s");} '
           'body {font-family: t; margin: 0; padding: 0;}' % Path(fontfile).as_posix())
    page.insert_htmlbox(pymupdf.Rect(72, 80 - 0.8 * size, 540, 80 - 0.8 * size + 15),
                        '<div style="font-size:%dpx; line-height:1">%s</div>' % (size, text),
                        css=css, archive=pymupdf.Archive('.'))
    doc.save(path)
    doc.close()


def subset_to(src, dst, text):
    """A subset carrying only text's glyphs (so the probe cluster is absent)."""
    from fontTools import subset
    with TTFont(src) as font:
        subsetter = subset.Subsetter(subset.Options())
        subsetter.populate(text=text)
        subsetter.subset(font)
        font.save(dst)
    return dst


def write_mapping(path, translations, font):
    Path(path).write_text(json.dumps({
        'fonts': {'regular': str(font)}, 'translations': translations,
        'merges': [], 'overrides': [], 'center': [], 'skip': []},
        ensure_ascii=False), encoding='utf-8')


class PrepareFontHookTests(unittest.TestCase):
    def _prepare(self, font_in, target, sample):
        with tempfile.TemporaryDirectory() as tmp:
            tr = os.path.join(tmp, 'translations.json')
            out = os.path.join(tmp, 'sub.ttf')
            write_mapping(tr, {SOURCE_LINE: target}, font_in)
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = prepare_font(font_in, tr, out, sample=sample)
            covers = os.path.isfile(out) and shaping_probe.font_covers(out, PROBE_CLUSTER)
            return rc, buf.getvalue(), covers

    def test_fails_when_the_face_cannot_shape_the_job_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = strip_gsub(noto('Devanagari'), os.path.join(tmp, 'no-gsub.ttf'))
            rc, log, _ = self._prepare(bad, DV_TARGET, DV_TARGET)
        self.assertEqual(rc, 1, log)
        self.assertIn('FAIL conjunct shaping Devanagari', log)
        self.assertIn('no conjunct formed', log)

    def test_adds_the_probe_glyphs_and_attests_the_subset(self):
        """The job text has no ksha/tra, so a plain subset could not be
        probed later. prepare_font adds the probe's glyphs and probes the
        subset it actually wrote."""
        rc, log, covers = self._prepare(noto('Devanagari'), DV_TARGET, DV_TARGET)
        self.assertEqual(rc, 0, log)
        self.assertIn('PASS conjunct shaping Devanagari', log)
        self.assertTrue(covers, 'the written subset does not carry the probe glyphs')

    def test_reviews_a_blind_script_without_claiming_it(self):
        rc, log, _ = self._prepare(noto('Thai'), TH_TARGET, TH_TARGET)
        self.assertEqual(rc, 0, log)
        self.assertIn('REVIEW conjunct shaping Thai', log)
        self.assertNotIn('PASS conjunct shaping', log)

    def test_says_nothing_for_a_latin_job(self):
        latin = FONTS / 'NotoSans-Regular.ttf'
        if not latin.is_file():
            raise unittest.SkipTest('NotoSans-Regular.ttf not fetched')
        rc, log, _ = self._prepare(str(latin), LATIN_TARGET, LATIN_TARGET)
        self.assertEqual(rc, 0, log)
        self.assertNotIn('conjunct shaping', log)


class VerifyGateTests(unittest.TestCase):
    def _verify(self, src, out):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = verify(src, out, min_ink=0.1)
        return rc, buf.getvalue()

    def test_fails_an_output_whose_embedded_face_cannot_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 'orig.pdf')
            bad = os.path.join(tmp, 'bad.pdf')
            good = os.path.join(tmp, 'good.pdf')
            build_source_pdf(src)
            no_gsub = strip_gsub(noto('Devanagari'), os.path.join(tmp, 'no-gsub.ttf'))
            draw_story_output(bad, DV_TARGET, no_gsub)
            draw_story_output(good, DV_TARGET, noto('Devanagari'))
            rc, log = self._verify(src, bad)
            self.assertEqual(rc, 1, log)
            self.assertIn('FAIL conjunct shaping Devanagari', log)
            rc, log = self._verify(src, good)
            self.assertIn('PASS conjunct shaping Devanagari', log)
            self.assertEqual(rc, 0, log)

    def test_gate_is_recorded_in_the_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 'orig.pdf')
            bad = os.path.join(tmp, 'bad.pdf')
            build_source_pdf(src)
            no_gsub = strip_gsub(noto('Devanagari'), os.path.join(tmp, 'no-gsub.ttf'))
            draw_story_output(bad, DV_TARGET, no_gsub)
            verdict = run_verify(src, bad, min_ink=0.1)
            gates = {g.name: g.status for g in verdict.gates}
            self.assertEqual(gates.get('conjunct-shaping'), 'FAIL', gates)
            self.assertEqual(verdict.exit_code, 1)

    def test_reviews_thai_instead_of_passing_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 'orig.pdf')
            out = os.path.join(tmp, 'thai.pdf')
            build_source_pdf(src)
            draw_story_output(out, TH_TARGET, noto('Thai'))
            rc, log = self._verify(src, out)
            self.assertIn('REVIEW conjunct shaping Thai', log)
            self.assertNotIn('PASS conjunct shaping', log)
            self.assertEqual(rc, 0, log)

    def test_reviews_when_no_embedded_face_carries_the_probe(self):
        """An output built with a subset that lacks the probe cluster cannot
        be attested. That is a REVIEW naming the fix, not a PASS."""
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 'orig.pdf')
            out = os.path.join(tmp, 'subset.pdf')
            build_source_pdf(src)
            small = subset_to(noto('Devanagari'), os.path.join(tmp, 'small.ttf'), DV_TARGET)
            self.assertFalse(shaping_probe.font_covers(small, PROBE_CLUSTER))
            draw_story_output(out, DV_TARGET, small)
            rc, log = self._verify(src, out)
            self.assertIn('REVIEW conjunct shaping Devanagari', log)
            self.assertIn('cannot attest', log)
            self.assertNotIn('PASS conjunct shaping', log)
            self.assertEqual(rc, 0, log)

    def test_says_nothing_for_a_latin_output(self):
        latin = FONTS / 'NotoSans-Regular.ttf'
        if not latin.is_file():
            raise unittest.SkipTest('NotoSans-Regular.ttf not fetched')
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 'orig.pdf')
            out = os.path.join(tmp, 'latin.pdf')
            build_source_pdf(src)
            draw_story_output(out, LATIN_TARGET, str(latin))
            rc, log = self._verify(src, out)
            self.assertNotIn('conjunct shaping', log)


if __name__ == '__main__':
    unittest.main()
