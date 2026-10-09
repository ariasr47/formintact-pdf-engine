#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A02: a translation has exactly the original's pages.

Legacy verify compared only the pages both files had. A missing page failed
only when a mapping target happened to sit on it, so without a mapping it
passed; an extra page, or a page whose box or rotation changed, always
passed. compare and render showed only the common pages and exited 0, so
their output read as a complete comparison. Pinned here through the API and
the shipped CLIs.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import pymupdf

SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / 'scripts'
FONT = SKILL / 'tests' / 'fonts' / 'NotoSans-Regular.ttf'

SOURCE = ('Hello world from page one.', 'Second page text is here.')
TARGET = ('Hola mundo desde la pagina uno.', 'Aqui esta el texto de la pagina dos.')


def _pdf(path, texts):
    doc = pymupdf.open()
    try:
        for text in texts:
            doc.new_page(width=612, height=792).insert_text((72, 72), text)
        doc.save(str(path))
    finally:
        doc.close()
    return str(path)


def _reshape(path, out, page, rotation=None, mediabox=None):
    doc = pymupdf.open(str(path))
    try:
        if rotation is not None:
            doc[page].set_rotation(rotation)
        if mediabox is not None:
            doc[page].set_mediabox(pymupdf.Rect(*mediabox))
        doc.save(str(out))
    finally:
        doc.close()
    return str(out)


def _cases(tmp):
    tmp = Path(tmp)
    src = _pdf(tmp / 'orig.pdf', SOURCE)
    equal = _pdf(tmp / 'equal.pdf', TARGET)
    return {
        'src': src,
        'equal': equal,
        'missing': _pdf(tmp / 'missing.pdf', TARGET[:1]),
        'extra': _pdf(tmp / 'extra.pdf', TARGET + ('Pagina adicional.',)),
        'rotated': _reshape(equal, tmp / 'rotated.pdf', 1, rotation=90),
        'resized': _reshape(equal, tmp / 'resized.pdf', 1, mediabox=(0, 0, 595, 842)),
    }


def _gate(verdict, name):
    hits = [g for g in verdict.gates if g.name == name]
    assert len(hits) == 1, [g.name for g in verdict.gates]
    return hits[0]


def _cli(*argv):
    env = dict(os.environ, PYTHONPATH=str(SKILL))
    done = subprocess.run([sys.executable, *map(str, argv)], capture_output=True,
                          text=True, encoding='utf-8', errors='replace', env=env)
    return done.returncode, done.stdout + done.stderr


class VerifyPageParityTests(unittest.TestCase):
    """The gate, through run_verify. No mapping: page parity must not depend on one."""

    def _verdict(self, key):
        from pdf_translate.verify import run_verify
        with tempfile.TemporaryDirectory() as tmp:
            c = _cases(tmp)
            return run_verify(c['src'], c[key], min_ink=0.1)

    def test_matching_pages_pass(self):
        v = self._verdict('equal')
        gate = _gate(v, 'page-parity')
        self.assertEqual((gate.status, gate.findings), ('PASS', ()))
        self.assertEqual(v.exit_code, 0, [(g.name, g.status) for g in v.gates])

    def test_a_missing_page_fails_and_is_named(self):
        v = self._verdict('missing')
        gate = _gate(v, 'page-parity')
        self.assertEqual(gate.status, 'FAIL')
        self.assertEqual([(f.page, f.where) for f in gate.findings], [(2, 'missing')])
        self.assertEqual(v.exit_code, 1)

    def test_an_extra_page_fails_and_is_named(self):
        v = self._verdict('extra')
        gate = _gate(v, 'page-parity')
        self.assertEqual(gate.status, 'FAIL')
        self.assertEqual([(f.page, f.where) for f in gate.findings], [(3, 'extra')])
        self.assertEqual(v.exit_code, 1)

    def test_a_rotated_page_fails(self):
        gate = _gate(self._verdict('rotated'), 'page-parity')
        self.assertEqual(gate.status, 'FAIL')
        self.assertEqual([(f.page, f.where, f.text) for f in gate.findings],
                         [(2, 'rotation', 'original 0, translation 90')])

    def test_a_resized_page_fails(self):
        gate = _gate(self._verdict('resized'), 'page-parity')
        self.assertEqual(gate.status, 'FAIL')
        self.assertIn((2, 'media-box'), [(f.page, f.where) for f in gate.findings])

    def test_a_long_list_is_cut_on_the_console_but_complete_in_the_verdict(self):
        import re
        from pdf_translate.verify import run_verify, verify
        with tempfile.TemporaryDirectory() as tmp:
            src = _pdf(Path(tmp) / 'orig.pdf', [f'Source page number {n}.' for n in range(1, 13)])
            out = _pdf(Path(tmp) / 'out.pdf', ['Pagina uno.'])
            buf = io.StringIO()
            with redirect_stdout(buf):
                verify(src, out, min_ink=0.1)
            details = [ln for ln in buf.getvalue().splitlines()
                       if re.match(r'^   page \d+ (missing|extra|media-box|crop-box|rotation): ', ln)]
            self.assertEqual(len(details), 10, buf.getvalue())
            self.assertIn('   … 1 more not shown', buf.getvalue())
            gate = _gate(run_verify(src, out, min_ink=0.1), 'page-parity')
            self.assertEqual([(f.page, f.where) for f in gate.findings],
                             [(n, 'missing') for n in range(2, 13)])

    def test_the_box_tolerance_holds_at_its_boundary(self):
        """Boxes are read as 32-bit floats: 612.01 comes back as 612.0100098,
        which must still count as 0.01 pt, the documented tolerance. Found by
        the independent verification pass."""
        from pdf_translate.verify import page_parity
        with tempfile.TemporaryDirectory() as tmp:
            src = _pdf(Path(tmp) / 'orig.pdf', SOURCE)
            for width, differs in ((612.01, False), (612.02, True)):
                out = _reshape(src, Path(tmp) / f'w{width}.pdf', 1, mediabox=(0, 0, width, 792))
                with pymupdf.open(src) as a, pymupdf.open(out) as b:
                    wheres = [f.where for f in page_parity(a, b)]
                self.assertEqual('media-box' in wheres, differs, (width, wheres))

    def test_the_gate_is_declared(self):
        from pdf_translate.verify import GATE_NAMES
        self.assertEqual(GATE_NAMES[GATE_NAMES.index('fill-roundtrip') + 1], 'page-parity')

    def test_the_console_prints_one_status_line_per_outcome(self):
        """The verdict mirrors the console: one PASS or FAIL line for the gate,
        its details indented so they are not read as further gate lines."""
        from pdf_translate.verify import verify
        with tempfile.TemporaryDirectory() as tmp:
            c = _cases(tmp)
            for key, status in (('equal', 'PASS'), ('extra', 'FAIL')):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    verify(c['src'], c[key], min_ink=0.1)
                lines = [ln for ln in buf.getvalue().splitlines() if 'page parity' in ln]
                self.assertEqual(len(lines), 1, buf.getvalue())
                self.assertTrue(lines[0].startswith(f'{status} page parity'), lines[0])


class VerifyCliTests(unittest.TestCase):
    """The shipped script, as a subprocess, with --report."""

    def test_cli_exit_code_console_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = _cases(tmp)
            for key, rc_expected, status in (('equal', 0, 'PASS'), ('missing', 1, 'FAIL'),
                                             ('extra', 1, 'FAIL')):
                report = Path(tmp) / f'report-{key}.json'
                rc, out = _cli(SCRIPTS / 'verify.py', c['src'], c[key], '--min-ink', '0.1',
                               '--report', report)
                self.assertEqual(rc, rc_expected, out)
                self.assertIn(f'{status} page parity', out)
                gates = {g['name']: g for g in json.loads(report.read_text(encoding='utf-8'))['gates']}
                self.assertEqual(gates['page-parity']['status'], status)


class CompareTests(unittest.TestCase):
    """compare shows every page of both files and fails on a count mismatch."""

    def _compare(self, key):
        """compare() logs through the package logger; only the CLI attaches a
        console handler, so the lines are read from the log records."""
        from pdf_translate.compare import compare
        with tempfile.TemporaryDirectory() as tmp:
            c = _cases(tmp)
            html = Path(tmp) / 'comparison.html'
            with self.assertLogs('pdf_translate', level='INFO') as logs:
                rc = compare(c['src'], c[key], str(html))
            return (rc, html.read_text(encoding='utf-8'),
                    '\n'.join(r.getMessage() for r in logs.records))

    def test_matching_pages_compare_as_before(self):
        rc, text, _ = self._compare('equal')
        self.assertEqual(rc, 0)
        self.assertEqual(text.count('<section class="page">'), 2)
        self.assertNotIn('role="alert"', text)

    def test_a_missing_page_is_shown_and_fails(self):
        rc, text, console = self._compare('missing')
        self.assertEqual(rc, 1)
        self.assertEqual(text.count('<section class="page">'), 2)
        self.assertIn('role="alert"', text)
        self.assertIn('No page 2 in this file.', text)
        self.assertIn('FAIL page count: 2 original / 1 translated', console)

    def test_an_extra_page_is_shown_and_fails(self):
        rc, text, _ = self._compare('extra')
        self.assertEqual(rc, 1)
        self.assertEqual(text.count('<section class="page">'), 3)
        self.assertIn('No page 3 in this file.', text)

    def test_cli_exit_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = _cases(tmp)
            html = Path(tmp) / 'cmp.html'
            self.assertEqual(_cli(SCRIPTS / 'compare.py', c['src'], c['equal'], html)[0], 0)
            self.assertEqual(_cli(SCRIPTS / 'compare.py', c['src'], c['extra'], html)[0], 1)


class RenderTests(unittest.TestCase):
    """The inspect loop renders every page and fails on a count mismatch."""

    def test_every_page_of_both_files_is_rendered(self):
        from pdf_translate.render_pages import render_pages
        with tempfile.TemporaryDirectory() as tmp:
            c = _cases(tmp)
            for key, names in (
                    ('equal', ['orig_p1.png', 'out_p1.png', 'orig_p2.png', 'out_p2.png']),
                    ('missing', ['orig_p1.png', 'out_p1.png', 'orig_p2.png']),
                    ('extra', ['orig_p1.png', 'out_p1.png', 'orig_p2.png', 'out_p2.png', 'out_p3.png'])):
                outdir = Path(tmp) / f'renders-{key}'
                with redirect_stdout(io.StringIO()):
                    written = render_pages(c['src'], c[key], str(outdir), dpi=30)
                self.assertEqual([Path(p).name for p in written], names)

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            c = _cases(tmp)
            for key, rc_expected in (('equal', 0), ('missing', 1), ('extra', 1)):
                outdir = Path(tmp) / f'r-{key}'
                rc, out = _cli(SCRIPTS / 'pipeline.py', 'render', c['src'], c[key], outdir, '--dpi', '30')
                self.assertEqual(rc, rc_expected, out)
                rc, out = _cli(SCRIPTS / 'render_pages.py', c['src'], c[key], Path(tmp) / f's-{key}',
                               '--dpi', '30')
                self.assertEqual(rc, rc_expected, out)


class FinishTests(unittest.TestCase):
    """finish packages what it was given, and says when the page count is wrong."""

    def test_finish_with_a_missing_page_writes_everything_and_exits_1(self):
        if not FONT.is_file():
            raise unittest.SkipTest(f'{FONT.name} not fetched (tools/fetch_test_fonts.py)')
        from pdf_translate import pipeline
        with tempfile.TemporaryDirectory() as tmp:
            c = _cases(tmp)
            work = Path(tmp) / 'work'
            work.mkdir()
            final, html = Path(tmp) / 'final.pdf', Path(tmp) / 'comparison.html'
            for key, rc_expected in (('equal', 0), ('missing', 1)):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = pipeline.main(['finish', c['src'], c[key], str(FONT), str(final),
                                        str(html), '--work', str(work), '--no-review'])
                self.assertEqual(rc, rc_expected, buf.getvalue())
                self.assertTrue(final.is_file() and html.is_file())
                self.assertTrue((Path(tmp) / 'review_state.json').is_file())


if __name__ == '__main__':
    unittest.main()
