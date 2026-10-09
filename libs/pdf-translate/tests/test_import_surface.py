#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The scripts are a library: importable stages, structured gate verdicts.

Existing tests import the modules by inserting ``scripts/`` on ``sys.path``
and drive ``main()`` / the stage functions. This file is the consumer-facing
surface: ``import pdf_translate`` must work without that path hack, must not
run a CLI as an import side effect, and verify / QA must return verdict
objects rather than only printing and exiting.
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pymupdf

SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / 'scripts'
CLI_SCRIPTS = (
    'bilingual.py',
    'compare.py',
    'extract_segments.py',
    'field_fonts.py',
    'pipeline.py',
    'prepare_font.py',
    'qa_check.py',
    'render_pages.py',
    'retypeset.py',
    'strip_text.py',
    'verify.py',
)
# No-args prints __doc__ and returns 2. The rest IndexError on argv[0].
USAGE_CLIS = ('pipeline.py', 'qa_check.py', 'bilingual.py')
# A Windows console on cp1252: CJK in qa_check's help is the tell.
LEGACY_ENV = {
    'PYTHONUTF8': '0',
    'PYTHONIOENCODING': 'cp1252',
}


def _tiny_pdf(path, text='Hello world.'):
    doc = pymupdf.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 72), text)
        doc.save(path)
    finally:
        doc.close()


class PackageImportTests(unittest.TestCase):
    def test_import_has_no_cli_side_effects(self):
        buf, err = io.StringIO(), io.StringIO()
        with redirect_stdout(buf), redirect_stderr(err):
            import pdf_translate
        self.assertEqual(buf.getvalue(), '')
        self.assertEqual(err.getvalue(), '')
        self.assertTrue(callable(pdf_translate.strip_text))
        self.assertTrue(callable(pdf_translate.extract_segments))
        self.assertTrue(callable(pdf_translate.retypeset))
        self.assertTrue(callable(pdf_translate.prepare_font))
        self.assertTrue(callable(pdf_translate.field_fonts))
        self.assertTrue(callable(pdf_translate.render_pages))
        self.assertTrue(callable(pdf_translate.compare))
        self.assertTrue(callable(pdf_translate.interleave))
        self.assertTrue(callable(pdf_translate.verify))
        self.assertTrue(callable(pdf_translate.qa_check))
        self.assertTrue(callable(pdf_translate.run_verify))
        self.assertTrue(callable(pdf_translate.run_qa))

    def test_submodules_are_importable_without_scripts_on_path(self):
        # The historical path hack must not be required for a consumer.
        cleaned = [p for p in sys.path if Path(p).resolve() != SCRIPTS.resolve()]
        self.assertNotIn(str(SCRIPTS), cleaned)
        from pdf_translate.strip_text import strip_text
        from pdf_translate.extract_segments import extract_segments
        from pdf_translate.retypeset import retypeset
        from pdf_translate.prepare_font import prepare_font
        from pdf_translate.field_fonts import field_fonts
        from pdf_translate.verify import verify, run_verify
        from pdf_translate.qa_check import qa_check, run_qa
        for fn in (strip_text, extract_segments, retypeset, prepare_font,
                   field_fonts, verify, run_verify, qa_check, run_qa):
            self.assertTrue(callable(fn))


class MappingFormatsTests(unittest.TestCase):
    """What an installed engine says it can read, before a caller authors for it.

    A consumer that writes a typography-1 mapping against an engine too old to
    read it gets a refusal at build time, after the authoring is done. The
    tuple lets it ask first, and it describes implemented format support - not
    public-release readiness, and not that any given document will succeed.
    """

    def test_the_tuple_names_both_readable_formats(self):
        import pdf_translate
        self.assertEqual(pdf_translate.MAPPING_FORMATS, ('legacy', 'typography-1'))
        self.assertIn('MAPPING_FORMATS', pdf_translate.__all__)

    def test_the_tuple_cannot_be_mutated_by_a_consumer(self):
        import pdf_translate
        self.assertIsInstance(pdf_translate.MAPPING_FORMATS, tuple)
        with self.assertRaises(AttributeError):
            pdf_translate.MAPPING_FORMATS.append('typography-2')

    def test_the_documented_capability_check_passes_on_this_engine(self):
        # Copied verbatim from references/typography.md. If this stops being
        # the example a consumer reads, the example is what is wrong.
        import pdf_translate

        formats = getattr(pdf_translate, 'MAPPING_FORMATS', ())
        if 'typography-1' not in formats:
            raise RuntimeError('This installed engine does not support typography-1')

    def test_an_older_engine_without_the_attribute_fails_the_same_check(self):
        # getattr's default is the whole point: an engine from before the
        # export raises AttributeError otherwise, which a consumer cannot
        # distinguish from a broken install.
        class OldEngine:
            pass

        formats = getattr(OldEngine(), 'MAPPING_FORMATS', ())
        self.assertEqual(formats, ())
        self.assertNotIn('typography-1', formats)


class RequirementsPreflightTests(unittest.TestCase):
    """A machine without the three packages should be told which ones.

    Installing the plugin needs no Python, so the first thing a new user runs
    is also the first thing that can fail. Before this, it failed with
    `ModuleNotFoundError: No module named 'pikepdf'` - one name, from a
    traceback, with no mention of the other two or of how to fix it.
    """

    def test_nothing_is_missing_in_a_working_install(self):
        from pdf_translate import _requirements
        self.assertEqual(_requirements.missing_requirements(), ())

    def test_every_missing_package_is_named_by_its_pip_name(self):
        from pdf_translate import _requirements
        absent = {'pikepdf', 'fontTools'}
        real = _requirements.importlib.util.find_spec

        def fake(name, *a, **k):
            return None if name in absent else real(name, *a, **k)

        with mock.patch.object(_requirements.importlib.util, 'find_spec', fake):
            # fontTools is imported under that name but installed as fonttools;
            # the message has to say the one you can type into pip.
            self.assertEqual(_requirements.missing_requirements(),
                             ('pikepdf', 'fonttools'))

    def test_the_message_names_them_and_the_interpreter_to_install_into(self):
        from pdf_translate import _requirements
        text = _requirements.requirements_message(('pikepdf', 'fonttools'))
        self.assertIn('pikepdf', text)
        self.assertIn('fonttools', text)
        self.assertIn('-m pip install', text)
        # The usual cause is packages sitting in a different interpreter, so
        # the message has to say which one is actually running.
        self.assertIn(sys.executable, text)
        self.assertNotIn('pymupdf', text, 'do not name what is not missing')

    def test_the_cli_guard_prints_the_message_and_exits_two(self):
        from pdf_translate import _requirements
        buf = io.StringIO()
        with mock.patch.object(_requirements, 'missing_requirements',
                               lambda: ('pikepdf',)), redirect_stderr(buf):
            with self.assertRaises(SystemExit) as caught:
                _requirements.guard_cli()
        self.assertEqual(caught.exception.code, 2)
        self.assertIn('pikepdf', buf.getvalue())
        self.assertNotIn('Traceback', buf.getvalue())

    def test_the_guard_is_silent_and_returns_when_nothing_is_missing(self):
        from pdf_translate import _requirements
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            _requirements.guard_cli()
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(err.getvalue(), '')

    def test_the_preflight_module_imports_nothing_it_is_checking_for(self):
        # It has to be importable on the machine that has none of them, which
        # is the only machine where it matters.
        source = (SKILL / 'pdf_translate' / '_requirements.py').read_text(encoding='utf-8')
        for forbidden in ('import pymupdf', 'import pikepdf', 'import fontTools',
                          'from pymupdf', 'from pikepdf', 'from fontTools'):
            self.assertNotIn(forbidden, source)


class VerifyVerdictTests(unittest.TestCase):
    def test_run_verify_returns_a_verdict_and_does_not_print(self):
        from pdf_translate.verify import VerifyVerdict, run_verify, verify

        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 'orig.pdf')
            _tiny_pdf(src)
            buf = io.StringIO()
            with redirect_stdout(buf):
                verdict = run_verify(src, src)
            self.assertEqual(buf.getvalue(), '',
                             msg='run_verify must not print; the CLI wrapper does')
            self.assertIsInstance(verdict, VerifyVerdict)
            self.assertEqual(verdict.exit_code, 0)
            self.assertTrue(verdict.ok)
            self.assertTrue(verdict.gates)
            names = {g.name for g in verdict.gates}
            self.assertIn('field-parity', names)
            statuses = {g.status for g in verdict.gates}
            self.assertTrue(statuses <= {'PASS', 'FAIL', 'SKIP', 'REVIEW'})

            printed = io.StringIO()
            with redirect_stdout(printed):
                rc = verify(src, src)
            self.assertEqual(rc, 0)
            self.assertEqual(rc, verdict.exit_code)
            self.assertIn('PASS field parity', printed.getvalue())

    def test_run_verify_fail_is_exit_code_1_not_sys_exit(self):
        from pdf_translate.verify import run_verify

        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 'orig.pdf')
            other = os.path.join(tmp, 'other.pdf')
            doc = pymupdf.open()
            try:
                page = doc.new_page()
                page.insert_text((72, 72), 'Alpha source sentence here.')
                widget = pymupdf.Widget()
                widget.field_name = 'ApplicantName'
                widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
                widget.rect = pymupdf.Rect(72, 100, 280, 118)
                page.add_widget(widget)
                doc.save(src)
            finally:
                doc.close()
            _tiny_pdf(other, 'Alpha source sentence here.')
            verdict = run_verify(src, other)
            self.assertEqual(verdict.exit_code, 1)
            self.assertFalse(verdict.ok)
            self.assertTrue(any(g.status == 'FAIL' for g in verdict.gates))


RTL_FONT = SKILL / 'tests' / 'fonts' / 'NotoNaskhArabic-Regular.ttf'
AR_PHRASE = 'السلام عليكم ورحمة الله'
GATE_STATUSES = ('PASS', 'FAIL', 'REVIEW', 'SKIP')


def _job(tmp, source='Hello world.', target='Hola mundo.', lang=None,
         declare=None, overrides=None, segments=None, scale_report=None):
    """A one-line job on disk: original, output, mapping, and optionally the
    segments.json beside the mapping and the scale report beside the output
    that the --translations gates read."""
    src = os.path.join(tmp, 'orig.pdf')
    out = os.path.join(tmp, 'out.pdf')
    tr = os.path.join(tmp, 'translations.json')
    _tiny_pdf(src, source)
    doc = pymupdf.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 72), target)
        if declare:
            doc.set_language(declare)
        doc.save(out)
    finally:
        doc.close()
    conf = {'translations': {source: target}, 'skip': []}
    if lang:
        conf['lang'] = lang
    if overrides:
        conf['overrides'] = overrides
    Path(tr).write_text(json.dumps(conf, ensure_ascii=False), encoding='utf-8')
    if segments is not None:
        Path(tmp, 'segments.json').write_text(
            json.dumps({'segments': segments}, ensure_ascii=False), encoding='utf-8')
    if scale_report is not None:
        Path(tmp, 'scale_report.json').write_text(
            json.dumps(scale_report), encoding='utf-8')
    return src, out, tr


def _printed_gate_lines(src, out, **kw):
    """Every PASS/FAIL/REVIEW/SKIP line the CLI prints for this job."""
    from pdf_translate.verify import verify
    buf = io.StringIO()
    with redirect_stdout(buf):
        verify(src, out, **kw)
    return [ln for ln in buf.getvalue().splitlines()
            if ln.split(' ', 1)[0] in GATE_STATUSES]


def _gates(verdict):
    names = [g.name for g in verdict.gates]
    assert len(names) == len(set(names)), f'a gate recorded twice: {names}'
    return {g.name: g.status for g in verdict.gates}


class VerdictCompletenessTests(unittest.TestCase):
    """PR #2 review F1: every outcome the CLI prints is in the verdict.

    A consumer explains ``exit_code == 1`` by reading ``verdict.gates``.
    That only works if every gate that can print FAIL also records it, and
    every REVIEW and SKIP a human would see on the console is there too.
    """

    def test_trivial_job_records_every_printed_outcome(self):
        from pdf_translate.verify import run_verify

        with tempfile.TemporaryDirectory() as tmp:
            src, out, tr = _job(tmp)
            printed = _printed_gate_lines(src, out, translations=tr, min_ink=0.1)
            verdict = run_verify(src, out, translations=tr, min_ink=0.1)
            gates = _gates(verdict)
            self.assertEqual(verdict.exit_code, 0, gates)
            # The six this job prints that F1 found missing.
            self.assertEqual(gates.get('leak-isolated'), 'PASS', gates)
            self.assertEqual(gates.get('caption-width'), 'PASS', gates)
            self.assertEqual(gates.get('override-markers'), 'SKIP', gates)
            self.assertEqual(gates.get('metadata'), 'PASS', gates)
            self.assertEqual(gates.get('metadata-lang'), 'REVIEW', gates)
            self.assertEqual(gates.get('scaled-runs'), 'SKIP', gates)
            # And nothing prints without recording: one line, one entry.
            self.assertEqual(
                len(printed), len(verdict.gates),
                msg=f'{len(printed)} printed gate lines vs {len(verdict.gates)} '
                    f'recorded:\n' + '\n'.join(printed) + f'\n{gates}')

    def test_every_recorded_name_is_a_declared_gate(self):
        from pdf_translate.verify import GATE_NAMES, run_verify

        with tempfile.TemporaryDirectory() as tmp:
            src, out, tr = _job(tmp, lang='es', declare='es', scale_report=[])
            verdict = run_verify(src, out, translations=tr, min_ink=0.1)
            names = {g.name for g in verdict.gates}
            self.assertTrue(names <= set(GATE_NAMES), names - set(GATE_NAMES))
            for name in ('arabic-letterforms', 'shaped-actualtext',
                         'conjunct-shaping', 'leak-scan'):
                self.assertIn(name, GATE_NAMES)

    def test_metadata_gate_is_recorded(self):
        from pdf_translate.verify import run_verify

        with tempfile.TemporaryDirectory() as tmp:
            src, out, tr = _job(tmp, lang='es')
            verdict = run_verify(src, out, translations=tr, min_ink=0.1)
            gates = _gates(verdict)
            self.assertEqual(gates.get('metadata'), 'FAIL', gates)
            self.assertNotIn('metadata-lang', gates)
            self.assertEqual(verdict.exit_code, 1)
            self.assertIn('FAIL', gates.values())
        with tempfile.TemporaryDirectory() as tmp:
            src, out, tr = _job(tmp, lang='es', declare='es')
            gates = _gates(run_verify(src, out, translations=tr, min_ink=0.1))
            self.assertEqual(gates.get('metadata'), 'PASS', gates)
            self.assertNotIn('metadata-lang', gates)

    def test_scaled_runs_gate_is_recorded(self):
        from pdf_translate.verify import run_verify

        cases = (
            (None, 'SKIP'),
            ([], 'PASS'),
            ([{'page': 0, 'ratio': 0.85, 'key': 'Hello world.'}], 'REVIEW'),
        )
        for report, status in cases:
            with self.subTest(report=report):
                with tempfile.TemporaryDirectory() as tmp:
                    src, out, tr = _job(tmp, scale_report=report)
                    verdict = run_verify(src, out, translations=tr, min_ink=0.1)
                    gates = _gates(verdict)
                    self.assertEqual(gates.get('scaled-runs'), status, gates)
                    self.assertEqual(verdict.exit_code, 0, gates)

    def test_override_marker_gate_is_recorded(self):
        from pdf_translate.verify import run_verify

        segments = [{'page': 0, 'text': 'd. Hello world.        .... $',
                     'marker': 'd.', 'core': 'Hello world.',
                     'dots': '....', 'tail': '$'}]
        keep = [{'page': 0, 'contains': 'Hello world.',
                 'parts': [{'text': 'd. Hola mundo.', 'x': 72.0},
                           {'text': '.... $', 'x': 180.0}]}]
        drop = [{'page': 0, 'contains': 'Hello world.',
                 'parts': [{'text': 'Hola mundo.', 'x': 72.0}]}]
        with tempfile.TemporaryDirectory() as tmp:
            src, out, tr = _job(tmp, overrides=keep, segments=segments)
            gates = _gates(run_verify(src, out, translations=tr, min_ink=0.1))
            self.assertEqual(gates.get('override-markers'), 'PASS', gates)
        with tempfile.TemporaryDirectory() as tmp:
            src, out, tr = _job(tmp, overrides=drop, segments=segments)
            verdict = run_verify(src, out, translations=tr, min_ink=0.1)
            gates = _gates(verdict)
            self.assertEqual(gates.get('override-markers'), 'FAIL', gates)
            self.assertEqual(verdict.exit_code, 1)
        with tempfile.TemporaryDirectory() as tmp:
            src, out, tr = _job(tmp, overrides=keep)   # no segments.json
            gates = _gates(run_verify(src, out, translations=tr, min_ink=0.1))
            self.assertEqual(gates.get('override-markers'), 'SKIP', gates)

    def test_arabic_and_actualtext_gates_are_recorded(self):
        """Gate 12 (letterforms) and gate 17 (/ActualText) both FAIL a run
        drawn glyph by glyph and both PASS the library's own retypeset
        output; a Latin job records neither, exactly as the CLI prints
        neither."""
        from pdf_translate.extract_segments import extract_segments
        from pdf_translate.retypeset import retypeset
        from pdf_translate.strip_text import strip_text
        from pdf_translate.verify import run_verify

        if not RTL_FONT.is_file():
            raise unittest.SkipTest(f'{RTL_FONT.name} not fetched (tools/fetch_test_fonts.py)')
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, 'orig.pdf')
            bad = os.path.join(tmp, 'glyph-by-glyph.pdf')
            good = os.path.join(tmp, 'retypeset.pdf')
            tr = os.path.join(tmp, 'translations.json')
            source = 'Peace be upon you and mercy'
            _tiny_pdf(src, source)
            font = str(RTL_FONT)
            Path(tr).write_text(json.dumps({
                'fonts': {'regular': font, 'bold': font,
                          'italic': font, 'bold_italic': font},
                'translations': {source: AR_PHRASE},
                'merges': [], 'overrides': [], 'center': [], 'skip': [],
            }, ensure_ascii=False), encoding='utf-8')
            # Glyph by glyph: the logical string is in the text layer, but
            # nothing shaped it and nothing marks it.
            doc = pymupdf.open()
            try:
                page = doc.new_page(width=612, height=792)
                tw = pymupdf.TextWriter(page.rect)
                tw.append((72, 80), AR_PHRASE, font=pymupdf.Font(fontfile=font),
                          fontsize=12, right_to_left=True)
                tw.write_text(page)
                doc.save(bad)
            finally:
                doc.close()
            # The library's own path: strip, segment, retypeset through the
            # Story engine, which marks each shaped run with /ActualText.
            stripped = os.path.join(tmp, 'stripped.pdf')
            with redirect_stdout(io.StringIO()):
                strip_text(src, stripped)
                extract_segments(src, outdir=tmp)
                rc = retypeset(stripped, os.path.join(tmp, 'segments.json'), tr, good)
            self.assertEqual(rc, 0)

            verdict = run_verify(src, bad, translations=tr, min_ink=0.05)
            gates = _gates(verdict)
            self.assertEqual(gates.get('arabic-letterforms'), 'FAIL', gates)
            self.assertEqual(gates.get('shaped-actualtext'), 'FAIL', gates)
            self.assertEqual(verdict.exit_code, 1)

            verdict = run_verify(src, good, translations=tr, min_ink=0.05)
            gates = _gates(verdict)
            self.assertEqual(gates.get('arabic-letterforms'), 'PASS', gates)
            self.assertEqual(gates.get('shaped-actualtext'), 'PASS', gates)
            self.assertEqual(verdict.exit_code, 0, gates)

        with tempfile.TemporaryDirectory() as tmp:
            src, out, tr = _job(tmp)
            gates = _gates(run_verify(src, out, translations=tr, min_ink=0.1))
            self.assertNotIn('arabic-letterforms', gates)
            self.assertNotIn('shaped-actualtext', gates)


class QAVerdictTests(unittest.TestCase):
    def test_run_qa_returns_a_verdict_and_does_not_print(self):
        from pdf_translate.qa_check import QAVerdict, qa_check, run_qa

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'translations.json')
            Path(path).write_text(json.dumps({
                'translations': {
                    'Pay $1,250.00 now': 'Pague ahora.',
                    'City': 'Ciudad',
                },
                'skip': [],
            }), encoding='utf-8')
            buf = io.StringIO()
            with redirect_stdout(buf):
                verdict = run_qa(path)
            self.assertEqual(buf.getvalue(), '')
            self.assertIsInstance(verdict, QAVerdict)
            self.assertEqual(verdict.findings, qa_check(path))
            self.assertGreater(verdict.error_count, 0)
            self.assertEqual(verdict.exit_code, 1)
            self.assertFalse(verdict.ok)
            kinds = {f['kind'] for f in verdict.findings}
            self.assertIn('numbers', kinds)

    def test_run_qa_clean_mapping_is_ok(self):
        from pdf_translate.qa_check import run_qa

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'translations.json')
            Path(path).write_text(json.dumps({
                'translations': {'City': 'Ciudad'},
                'skip': [],
            }), encoding='utf-8')
            verdict = run_qa(path)
            self.assertEqual(verdict.findings, [])
            self.assertEqual(verdict.exit_code, 0)
            self.assertTrue(verdict.ok)


def _run_cli(name, extra_env=None):
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name)],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        env=env)


class CliPathTests(unittest.TestCase):
    def test_documented_cli_scripts_still_exist(self):
        for name in CLI_SCRIPTS:
            self.assertTrue((SCRIPTS / name).is_file(), msg=name)

    def test_pipeline_no_args_still_prints_usage_and_exits_2(self):
        r = _run_cli('pipeline.py')
        self.assertEqual(r.returncode, 2)
        self.assertIn('rebuild', r.stdout)
        self.assertIn('from-cores', r.stdout)

    def test_qa_check_no_args_still_prints_usage_and_exits_2(self):
        r = _run_cli('qa_check.py')
        self.assertEqual(r.returncode, 2)
        self.assertIn('qa_check.py', r.stdout)

    def test_usage_clis_print_help_on_a_legacy_windows_console(self):
        """Help text must not die with UnicodeEncodeError on cp1252.

        qa_check's docstring contains CJK. pipeline's is ASCII. bilingual's
        has an em dash, which cp1252 can encode. All three must still exit 2.
        """
        for name in USAGE_CLIS:
            with self.subTest(name=name):
                r = _run_cli(name, LEGACY_ENV)
                combined = r.stdout + r.stderr
                self.assertNotIn('UnicodeEncodeError', combined, msg=combined)
                self.assertEqual(r.returncode, 2, msg=combined)
                self.assertTrue(r.stdout.strip(), msg=combined)

    def test_qa_check_help_keeps_cjk_on_a_legacy_windows_console(self):
        r = _run_cli('qa_check.py', LEGACY_ENV)
        self.assertEqual(r.returncode, 2, msg=r.stdout + r.stderr)
        self.assertIn('養子支援', r.stdout)

    def test_missing_args_clis_do_not_die_on_encoding_under_cp1252(self):
        crash = [n for n in CLI_SCRIPTS if n not in USAGE_CLIS]
        for name in crash:
            with self.subTest(name=name):
                r = _run_cli(name, LEGACY_ENV)
                combined = r.stdout + r.stderr
                self.assertNotIn('UnicodeEncodeError', combined, msg=combined)
                self.assertEqual(r.returncode, 1, msg=combined)
                self.assertIn('Traceback', combined)

    def test_importing_the_package_does_not_reconfigure_stdio(self):
        code = (
            'import sys\n'
            'print(sys.stdout.encoding)\n'
            'print(sys.stderr.encoding)\n'
            'import pdf_translate\n'
            'print(sys.stdout.encoding)\n'
            'print(sys.stderr.encoding)\n'
        )
        env = dict(os.environ)
        env.update(LEGACY_ENV)
        r = subprocess.run(
            [sys.executable, '-c', code],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            env=env, cwd=str(SKILL))
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 4, msg=r.stdout)
        self.assertEqual(lines[0], lines[2])
        self.assertEqual(lines[1], lines[3])
        self.assertEqual(lines[0].lower().replace('-', ''), 'cp1252')


class ConsoleHandlerTests(unittest.TestCase):
    """E3 Task 2: the one handler that reproduces today's console exactly.

    `pipeline.py` calls five library functions directly (strip_text at :87,
    retypeset at :337, render_pages at :354, field_fonts at :362, compare at
    :365). Once each of those is a loud wrapper that attaches a handler,
    `pipeline.main()`'s handler and the wrapper's would both be attached and
    every line would print twice. Re-entrancy is what prevents that, and it is
    the single most likely way the parity runner goes red.
    """

    def setUp(self):
        import logging
        self.pkg = logging.getLogger('pdf_translate')
        self.before = list(self.pkg.handlers)

    def test_console_is_reentrant(self):
        import logging
        from pdf_translate._console import console
        log = logging.getLogger('pdf_translate.test_reentrant')
        buf = io.StringIO()
        with redirect_stdout(buf):
            with console():
                with console():
                    log.info('once')
        self.assertEqual(buf.getvalue(), 'once\n')

    def test_console_removes_its_handler_on_exit(self):
        from pdf_translate._console import console
        with console():
            pass
        # A library that leaves a handler attached has configured logging for
        # its host.
        self.assertEqual(list(self.pkg.handlers), self.before)

    def test_console_removes_its_handler_even_when_the_body_raises(self):
        from pdf_translate._console import console
        with self.assertRaises(ValueError):
            with console():
                raise ValueError('boom')
        self.assertEqual(list(self.pkg.handlers), self.before)

    def test_console_formats_a_record_as_the_bare_message(self):
        # print(x) wrote `x\n`. Any prefix — level, logger name, timestamp —
        # is a console change, and the console is the contract.
        import logging
        from pdf_translate._console import console
        log = logging.getLogger('pdf_translate.test_format')
        buf = io.StringIO()
        with redirect_stdout(buf):
            with console():
                log.info('FAIL field parity: 265/266')
        self.assertEqual(buf.getvalue(), 'FAIL field parity: 265/266\n')

    def test_run_verify_leaves_the_callers_stdout_as_it_found_it(self):
        # Necessary but not sufficient: `redirect_stdout` restores on exit, so
        # this passes even with the defect. What it pins is that run_verify
        # never writes to the caller's stream and never leaves it swapped.
        # The defect itself — the process's stdout replaced FOR THE DURATION,
        # which silences the host's other threads — is caught by
        # test_two_threads_running_run_verify_do_not_swallow_each_other.
        import pdf_translate

        class Sentinel:
            def __init__(self):
                self.text = []

            def write(self, s):
                self.text.append(s)
                return len(s)

            def flush(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            src = _one_page_pdf(Path(tmp) / 'orig.pdf')
            out = _one_page_pdf(Path(tmp) / 'out.pdf')
            sentinel = Sentinel()
            real = sys.stdout
            sys.stdout = sentinel
            try:
                pdf_translate.run_verify(src, out, min_ink=0.0)
                still_ours = sys.stdout is sentinel
            finally:
                sys.stdout = real
        self.assertTrue(still_ours, 'run_verify replaced sys.stdout')
        self.assertEqual(''.join(sentinel.text), '',
                         'run_verify wrote to the caller\'s stdout')

    def test_run_verify_is_silent_and_verify_is_loud(self):
        import pdf_translate
        from pdf_translate.verify import verify as loud
        with tempfile.TemporaryDirectory() as tmp:
            src = _one_page_pdf(Path(tmp) / 'orig.pdf')
            out = _one_page_pdf(Path(tmp) / 'out.pdf')
            quiet_buf = io.StringIO()
            with redirect_stdout(quiet_buf):
                verdict = pdf_translate.run_verify(src, out, min_ink=0.0)
            loud_buf = io.StringIO()
            with redirect_stdout(loud_buf):
                rc = loud(src, out, min_ink=0.0)
        self.assertEqual(quiet_buf.getvalue(), '')
        self.assertTrue(loud_buf.getvalue().strip())
        self.assertEqual(rc, verdict.exit_code)

    def test_two_threads_running_run_verify_do_not_swallow_each_other(self):
        # C5. With redirect_stdout, one thread inside run_verify silences
        # every other thread in the process for the duration — a service
        # running two jobs loses the other one's output entirely.
        import threading
        import pdf_translate

        with tempfile.TemporaryDirectory() as tmp:
            src = _one_page_pdf(Path(tmp) / 'orig.pdf')
            out = _one_page_pdf(Path(tmp) / 'out.pdf')

            import time
            started = threading.Event()
            done = threading.Event()
            printed = 0

            def verifying():
                started.set()
                for _ in range(3):
                    pdf_translate.run_verify(src, out, min_ink=0.0)
                done.set()

            worker = threading.Thread(target=verifying)
            worker.start()
            started.wait(10)
            buf = io.StringIO()
            # Bounded on both sides: at most 400 lines, at most ~4 s, and it
            # stops as soon as the worker is finished. A test that races has
            # to be cheap when it loses.
            with redirect_stdout(buf):
                for _ in range(400):
                    if done.is_set():
                        break
                    print('host output')
                    printed += 1
                    time.sleep(0.01)
            worker.join(60)
        self.assertGreater(printed, 0, 'the host thread never printed')
        self.assertEqual(buf.getvalue().count('host output'), printed,
                         'run_verify swallowed the host thread\'s output')


class LoggerCallShapeTests(unittest.TestCase):
    """Every `log.info` takes exactly one already-formatted argument.

    `print(a, b)` writes "a b". `log.info(a, b)` treats `b` as a %-format
    argument, and when `a` carries no placeholder logging swallows the record
    and writes a traceback to stderr instead — **the line disappears**. The
    mechanical print→log conversion produced exactly this twice, in
    `verify.py` (the field-parity FAIL) and `retypeset.py` (the `saved` line),
    and neither was caught by the suite or by the parity runner because
    neither fires on a job that succeeds.

    This is a structural check rather than a behavioural one for that precise
    reason: the sites it protects are the ones only a failing job reaches.
    """

    def test_no_log_info_call_takes_more_than_one_argument(self):
        import ast
        offenders = []
        for path in sorted((SKILL / 'pdf_translate').glob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ('info', 'warning', 'error')
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == 'log'):
                    if len(node.args) != 1 or node.keywords:
                        offenders.append(f'{path.name}:{node.lineno}')
        self.assertEqual(offenders, [], f'log call with != 1 argument: {offenders}')

    def test_no_except_name_shadows_a_value_read_after_the_handler(self):
        """`except ... as name` unbinds `name` when the handler exits.

        So a handler that borrows a name the function still needs leaves it
        UNBOUND, and the next read raises UnboundLocalError. `capture.py`'s
        `_write_bundle(capture_dir, exc, ...)` did exactly that: a read whose
        handler was also called `exc` erased the refusal it had been handed,
        and because capture is best effort the failure was swallowed and the
        whole bundle was abandoned.

        Structural for the same reason as the log-shape check above: this
        fires only when the handler actually runs, which is the case a green
        suite never reaches.
        """
        import ast
        offenders = []
        for path in sorted((SKILL / 'pdf_translate').glob('*.py')):
            tree = ast.parse(path.read_text(encoding='utf-8'))
            parent = {child: node for node in ast.walk(tree)
                      for child in ast.iter_child_nodes(node)}

            def enclosing_function(node):
                while node in parent:
                    node = parent[node]
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        return node
                return None

            def span(node):
                lines = [getattr(n, 'lineno', node.lineno) for n in ast.walk(node)]
                return min(lines), max(lines)

            for handler in [n for n in ast.walk(tree)
                            if isinstance(n, ast.ExceptHandler) and n.name]:
                function = enclosing_function(handler)
                if function is None:
                    continue
                args = function.args
                params = {a.arg for a in
                          args.posonlyargs + args.args + args.kwonlyargs}
                # A read inside another handler binding the same name is that
                # handler's own exception, not this one's corpse.
                siblings = [span(h) for h in ast.walk(function)
                            if isinstance(h, ast.ExceptHandler)
                            and h.name == handler.name and h is not handler]
                _, ends = span(handler)
                read_after = any(
                    isinstance(n, ast.Name) and n.id == handler.name
                    and isinstance(n.ctx, ast.Load) and n.lineno > ends
                    and not any(lo <= n.lineno <= hi for lo, hi in siblings)
                    for n in ast.walk(function))
                if handler.name in params or read_after:
                    offenders.append(
                        f'{path.name}:{handler.lineno} in {function.name}() '
                        f'as {handler.name!r}')
        self.assertEqual(offenders, [],
                         f'except name outlives its handler: {offenders}')

    def test_no_stage_module_prints(self):
        # C10: through the logging module on the package's own logger, never
        # `print`. Listed explicitly so adding a module is a deliberate act.
        import ast
        converted = ('verify.py', 'retypeset.py', 'strip_text.py',
                     'extract_segments.py', 'prepare_font.py',
                     'field_fonts.py', 'qa_check.py', 'review.py',
                     'compare.py', 'render_pages.py', 'bilingual.py',
                     'pipeline.py')
        offenders = []
        for name in converted:
            path = SKILL / 'pdf_translate' / name
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == 'print'):
                    offenders.append(f'{name}:{node.lineno}')
        self.assertEqual(offenders, [], f'print() survives in: {offenders}')


def _one_page_pdf(path, text='Income and Expense Declaration'):
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    doc.save(path)
    doc.close()
    return str(path)


if __name__ == '__main__':
    unittest.main()
