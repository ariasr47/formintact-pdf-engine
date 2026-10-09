#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Every CLI's console and exit code, from one checkout, in a diffable block.

E3 converts 172 `print` sites across seven modules to the package logger. The
promise is that the eleven CLIs stay **byte-identical** while it happens. This
is what holds E3 to that promise: run it from two checkouts over the same
fixture job and diff the two outputs. Anything that is not empty is a console
change, and the console is the contract.

`verdict_parity_runner.py` covers `verify` at the LIBRARY level over nine gate
fixtures and is still the right tool for the verdict/console split. This one
covers the CLIs themselves, as subprocesses, the way a user runs them:

    python dev/probes/cli_parity_runner.py <workdir> [--font FONT.ttf]

It builds its own job — a one-page source, a stripped copy, segments, a
mapping, a subset font — so the two checkouts are compared on identical
inputs, and it normalises the two things that legitimately differ between any
two runs: elapsed times and absolute paths.

**Pass `--font` when comparing against a worktree.** `tests/fonts/` is fetched,
never committed (`tools/fetch_test_fonts.py`), so a fresh worktree has none and
every font-dependent CLI fails there for a reason that has nothing to do with
the change under test. Point both runs at the same face.

Nothing here is a gate and nothing here ships.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / 'pdf-translate'
SCRIPTS = SKILL / 'scripts'
DEFAULT_FONT = SKILL / 'tests' / 'fonts' / 'NotoSans-Regular.ttf'
FONT = DEFAULT_FONT   # replaced by --font at startup

SOURCE = 'Income and Expense Declaration'
TARGET = 'Declaracion de ingresos y gastos'

# Elapsed times and absolute paths differ between any two runs of anything.
# Everything else must be identical.
NOISE = (
    (re.compile(r'elapsed \d+\.\d+s'), 'elapsed <T>s'),
    (re.compile(r'\d+\.\d+ ?s\b'), '<T>s'),
    (re.compile(r'\d+ KB'), '<N> KB'),
)


def scrub(text, work):
    """Remove what cannot be equal between two runs, and nothing else."""
    for form in (str(work), str(work).replace('\\', '/'),
                 str(REPO), str(REPO).replace('\\', '/')):
        text = text.replace(form, '<PATH>')
    for pattern, replacement in NOISE:
        text = pattern.sub(replacement, text)
    return text


def build_job(work):
    """A complete job: every CLI below has real inputs to run against."""
    import pymupdf
    work.mkdir(parents=True, exist_ok=True)
    src = work / 'orig.pdf'
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), SOURCE)
    doc.save(src)
    doc.close()

    font = str(FONT)
    (work / 'translations.json').write_text(json.dumps({
        'fonts': {'regular': font, 'bold': font,
                  'italic': font, 'bold_italic': font},
        'lang': 'es',
        'translations': {SOURCE: TARGET},
        'merges': [], 'overrides': [], 'center': [], 'skip': [],
    }, ensure_ascii=False), encoding='utf-8')
    return src


def invocations(work):
    """(label, argv) for each of the eleven CLIs, in dependency order.

    Ordered so each one's inputs exist by the time it runs: strip and extract
    first, then the font, then the build, then everything that reads a built
    document.
    """
    w = str(work)

    def p(name):
        return os.path.join(w, name)

    return [
        ('strip_text', ['strip_text.py', p('orig.pdf'), p('stripped.pdf')]),
        ('extract_segments', ['extract_segments.py', p('orig.pdf'),
                              '--outdir', w]),
        # No --instance: the bundled test face is a static build, and asking
        # fontTools to instance it raises. The subsetting path is what this
        # runner is here to compare.
        ('prepare_font', ['prepare_font.py', str(FONT),
                          p('translations.json'), p('font-sub.ttf')]),
        ('qa_check', ['qa_check.py', p('translations.json'),
                      '--segments', p('segments.json')]),
        ('retypeset', ['retypeset.py', p('stripped.pdf'), p('segments.json'),
                       p('translations.json'), p('out.pdf')]),
        ('verify', ['verify.py', p('orig.pdf'), p('out.pdf'),
                    '--source-words-from', p('segments.json'),
                    '--translations', p('translations.json')]),
        ('field_fonts', ['field_fonts.py', p('out.pdf'), str(FONT),
                         p('final.pdf')]),
        ('compare', ['compare.py', p('orig.pdf'), p('final.pdf'),
                     p('comparison.html')]),
        ('render_pages', ['render_pages.py', p('orig.pdf'), p('final.pdf'),
                          p('renders')]),
        ('bilingual', ['bilingual.py', p('orig.pdf'), p('final.pdf'),
                       p('both.pdf')]),
        # pipeline is the eleventh; its own subcommands are covered by the
        # stages above, so the invocation that matters here is the one that
        # composes them.
        ('pipeline rebuild', ['pipeline.py', 'rebuild', '--work', w,
                              p('orig.pdf'), p('out2.pdf'),
                              '--source-words-from', p('segments.json'),
                              '--translations', p('translations.json')]),
        # Row 32's command, so its console is ratcheted too.
        ('pipeline review', ['pipeline.py', 'review', '--work', w]),
    ]


def run(argv, work):
    env = dict(os.environ, PYTHONUTF8='1')
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / argv[0])] + argv[1:],
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        env=env, cwd=str(REPO))
    return proc.returncode, (proc.stdout or ''), (proc.stderr or '')


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    global FONT
    if '--font' in argv:
        FONT = Path(argv[argv.index('--font') + 1]).resolve()
        argv = [a for i, a in enumerate(argv)
                if i not in (argv.index('--font'), argv.index('--font') + 1)]
    if not FONT.is_file():
        raise SystemExit(f'no font at {FONT}; pass --font, or run '
                         f'pdf-translate/tools/fetch_test_fonts.py')
    work = Path(argv[0]).resolve()
    if work.exists():
        shutil.rmtree(work)
    sys.path.insert(0, str(SKILL))

    import pdf_translate
    print(f'# package: {pdf_translate.__file__}', file=sys.stderr)

    build_job(work)
    failures = 0
    for label, args in invocations(work):
        rc, out, err = run(args, work)
        print(f'=== {label} exit={rc}')
        print(scrub(out.rstrip(), work))
        if err.strip():
            print(f'--- {label} stderr')
            print(scrub(err.rstrip(), work))
        if rc not in (0, 1):
            failures += 1
    print(f'\n=== {len(invocations(work))} CLI invocation(s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
