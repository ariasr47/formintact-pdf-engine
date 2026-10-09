"""Acceptance across source evidence, final files and actual old runtimes."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from fontTools.ttLib import TTFont
import pymupdf

from pdf_translate import run_extract, run_retypeset, run_strip, run_verify
from pdf_translate.results import FontError, PdfTranslateError
from tests.typography_fixtures import latin_font_sets, make_job, make_mapping


ROOT = Path(__file__).resolve().parents[2]
OLD_REVISIONS = ('9675c6196c14eb2a2d37d34e371391eb1955a386',
                 'a5629fbd6bc084f9ab849ae3db74992c15807fcc')


class TypographyAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.job = make_job(self.work)

    def build_and_check(self):
        built = run_retypeset(*(str(self.job[k]) for k in ('stripped', 'segments', 'mapping', 'output')),
                               original=str(self.job['original']))
        verdict = run_verify(str(self.job['original']), str(self.job['output']),
                             translations=str(self.job['mapping']), segments=str(self.job['segments']), typography=True)
        for key in ('original', 'output'):
            with pymupdf.open(self.job[key]) as doc:
                self.assertEqual(doc.page_count, 1)
        self.assertEqual(built.pages, 1)
        return next(g for g in verdict.gates if g.name == 'typography')

    def test_repeated_labels_preserve_actual_final_typography(self):
        check = self.build_and_check()
        self.assertEqual(check.status, 'PASS', check.to_dict())

    def test_unknown_source_class_with_authored_resolution_remains_review(self):
        uncertain = self.work / 'unknown-source.ttf'
        with TTFont(latin_font_sets()['sans']['regular']) as font:
            font['OS/2'].panose.bFamilyType = 0
            font['OS/2'].sFamilyClass = 0
            font.save(uncertain)
        with pymupdf.open() as doc:
            page = doc.new_page(width=400, height=240)
            page.insert_text((30, 60), 'Payment', fontname='ActualSource', fontfile=str(uncertain), fontsize=12)
            doc.save(self.job['original'])
        run_strip(str(self.job['original']), str(self.job['stripped']))
        run_extract(str(self.job['original']), str(self.work), typography=True)
        extraction = json.loads(self.job['segments'].read_text(encoding='utf-8'))
        run = extraction['segments'][0]['style_runs'][0]
        self.assertEqual(run['class'], 'unknown')
        self.assertIsNotNone(run['font_id'])
        conf = make_mapping(extraction, latin_font_sets())
        conf['source_font_resolutions'] = [{'font_id': run['font_id'], 'class': 'sans', 'bold': False,
                                            'italic': False, 'reason': 'Fixture operator identifies this source face.'}]
        self.job['mapping'].write_text(json.dumps(conf), encoding='utf-8')
        check = self.build_and_check()
        self.assertEqual(check.status, 'REVIEW', check.to_dict())
        self.assertTrue(any('Caller-authored' in f.text for f in check.findings))

    def test_recorded_old_runtimes_refuse_build_and_capability_check(self):
        for revision in OLD_REVISIONS:
            with self.subTest(revision=revision):
                snapshot = self.work / revision[:8]
                snapshot.mkdir()
                archive = subprocess.run(['git', 'archive', '--format=zip', revision, 'pdf-translate'],
                                         cwd=ROOT, capture_output=True, check=True)
                with zipfile.ZipFile(io.BytesIO(archive.stdout)) as files:
                    for item in files.infolist():
                        self.assertTrue((snapshot / item.filename).resolve().is_relative_to(snapshot.resolve()))
                    files.extractall(snapshot)
                package = snapshot / 'pdf-translate'
                env = dict(os.environ, PYTHONPATH=str(package), PYTHONUTF8='1')
                output = snapshot / 'must-not-exist.pdf'
                built = subprocess.run([sys.executable, str(package / 'scripts' / 'retypeset.py'),
                    str(self.job['stripped']), str(self.job['segments']), str(self.job['mapping']), str(output)],
                    cwd=package, env=env, capture_output=True, encoding='utf-8', timeout=30)
                self.assertNotEqual(built.returncode, 0, built.stdout + built.stderr)
                self.assertFalse(output.exists())
                guarded = subprocess.run([sys.executable, '-c',
                    "import pdf_translate,sys; supported='typography-1' in getattr(pdf_translate,'MAPPING_FORMATS',()); "
                    "print('capability supported:',supported); sys.exit(0 if supported else 2)"],
                    cwd=package, env=env, capture_output=True, encoding='utf-8', timeout=30)
                self.assertEqual(guarded.returncode, 2, guarded.stdout + guarded.stderr)
                self.assertIn('capability supported: False', guarded.stdout)

    def test_unsupported_source_paint_cannot_silently_flatten(self):
        for case in ('opacity', 'stroke', 'shear', 'clip', 'overpaint', 'unit'):
            with self.subTest(case=case):
                with pymupdf.open() as doc:
                    page = doc.new_page(width=400, height=240)
                    page.insert_text((30, 60), 'Payment', fontname='helv', fontsize=12,
                                     fill_opacity=0.2 if case == 'opacity' else 1,
                                     render_mode=1 if case == 'stroke' else 0)
                    if case in ('shear', 'clip'):
                        page.clean_contents()
                        xref = page.get_contents()[0]
                        prefix = b'q 1 0 .5 1 -90 0 cm\n' if case == 'shear' else b'q 0 0 63 240 re W n\n'
                        doc.update_stream(xref, prefix + doc.xref_stream(xref) + b'\nQ')
                    if case == 'overpaint':
                        page.draw_rect(pymupdf.Rect(55, 48, 80, 64), fill=(1, 1, 1), color=None)
                    if case == 'unit':
                        doc.xref_set_key(page.xref, 'UserUnit', '2')
                    doc.save(self.job['original'])
                run_strip(str(self.job['original']), str(self.job['stripped']))
                run_extract(str(self.job['original']), str(self.work), typography=True)
                extraction = json.loads(self.job['segments'].read_text(encoding='utf-8'))
                self.job['mapping'].write_text(json.dumps(make_mapping(extraction, latin_font_sets())), encoding='utf-8')
                self.job['output'].write_bytes(b'previous PDF')
                with self.assertRaises(PdfTranslateError) as caught:
                    run_retypeset(*(str(self.job[k]) for k in ('stripped','segments','mapping','output')),
                                   original=str(self.job['original']))
                self.assertEqual(caught.exception.refusals['typography'][0]['reason'], 'unsupported-typography-construct')
                self.assertEqual(self.job['output'].read_bytes(), b'previous PDF')

    def test_full_saved_probe_and_unavailable_cjk_slants(self):
        probe = self.work / 'acceptance'
        command = [sys.executable, str(ROOT / 'dev' / 'probes' / 'typography_acceptance.py'),
                   '--work', str(probe), '--fonts', str(ROOT / 'pdf-translate' / 'tests' / 'fonts')]
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'pdf-translate'), PYTHONUTF8='1')
        # The six cases instance and subset real CJK variable faces, so this is
        # the most expensive test in the suite. Measured: 82 s on a developer
        # machine, and a CI runner has been seen at 3.2x that machine over the
        # whole suite. 300 s left no headroom and timed out on windows/py3.10.
        # 900 s is about 11x the measured cost: generous enough that only a
        # hang reaches it, short enough that a hang still ends the job.
        result = subprocess.run(command, cwd=ROOT / 'pdf-translate', env=env,
                                capture_output=True, encoding='utf-8', timeout=900)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads((probe / 'summary.json').read_text(encoding='utf-8'))
        self.assertEqual(len(summary['cases']), 6)
        for case in summary['cases']:
            self.assertEqual(case['typography']['status'], 'PASS', case)
            self.assertTrue(case['same_page_geometry'])
            self.assertEqual(case['page_counts'][0], case['page_counts'][1])
            self.assertEqual(case['codes']['verify'], 0)
            self.assertGreater(case['field_font']['cmap_entries'], 1000)
            self.assertTrue(case['actual_drawn'])
            self.assertTrue(case['selected_font_hashes'])
            self.assertTrue((probe / case['case'] / 'source-1.png').is_file())
            self.assertTrue((probe / case['case'] / 'final-1.png').is_file())
        for lang in ('ja', 'zh-Hans'):
            for cls, names in [('sans', ('heit', 'hebi')), ('serif', ('tiit', 'tibi'))]:
                for role, name in zip(('italic', 'bold_italic'), names):
                    with self.subTest(lang=lang, font_class=cls, role=role):
                        with pymupdf.open() as doc:
                            page = doc.new_page(width=400, height=240)
                            page.insert_text((30, 60), 'Payment', fontname=name, fontsize=12)
                            doc.save(self.job['original'])
                        run_strip(str(self.job['original']), str(self.job['stripped']))
                        run_extract(str(self.job['original']), str(self.work), typography=True)
                        extraction = json.loads(self.job['segments'].read_text(encoding='utf-8'))
                        upright = probe / lang / 'selected' / f'{cls}-regular.ttf'
                        conf = make_mapping(extraction, {cls: {role: str(upright)}})
                        conf['lang'] = lang
                        conf['targets'][0]['runs'][0]['text'] = '日本語の記入例' if lang == 'ja' else '简体中文示例'
                        self.job['mapping'].write_text(json.dumps(conf), encoding='utf-8')
                        with self.assertRaises(FontError) as caught:
                            run_retypeset(*(str(self.job[k]) for k in ('stripped','segments','mapping','output')),
                                           original=str(self.job['original']))
                        self.assertEqual(caught.exception.reason, 'wrong-font-role')
                        self.assertFalse(self.job['output'].exists())
        before = (probe / 'summary.json').read_bytes()
        refused = subprocess.run(command, cwd=ROOT / 'pdf-translate', env=env,
                                 capture_output=True, encoding='utf-8', timeout=30)
        self.assertEqual(refused.returncode, 2)
        self.assertIn('empty output directory', refused.stderr)
        self.assertEqual((probe / 'summary.json').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
