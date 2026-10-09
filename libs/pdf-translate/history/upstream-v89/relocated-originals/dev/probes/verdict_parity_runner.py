# -*- coding: utf-8 -*-
"""Print verify's console output (stdout) and its recorded verdict (stderr)
for every job verdict_parity_fixtures.py built, from whichever
pdf_translate is first on sys.path. Run once per checkout and diff the
stdout halves: they must be identical.

    PYTHONPATH=<checkout>/pdf-translate python dev/probes/verdict_parity_runner.py <dir>
"""
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pdf_translate
from pdf_translate.verify import run_verify, verify

ROOT = Path(sys.argv[1])
print('# package:', pdf_translate.__file__, file=sys.stderr)

JOBS = [
    ('trivial', 'out.pdf', 0.1), ('meta-fail', 'out.pdf', 0.1), ('meta-pass', 'out.pdf', 0.1),
    ('scaled-review', 'out.pdf', 0.1), ('scaled-pass', 'out.pdf', 0.1),
    ('override-pass', 'out.pdf', 0.1), ('override-fail', 'out.pdf', 0.1),
    ('arabic', 'bad.pdf', 0.05), ('arabic', 'good.pdf', 0.05),
]
for name, out, min_ink in JOBS:
    d = ROOT / name
    src, tr = str(d / 'orig.pdf'), str(d / 'translations.json')
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = verify(src, str(d / out), translations=tr, min_ink=min_ink)
    print(f'=== {name}/{out} exit={rc}')
    print(buf.getvalue().rstrip())
    v = run_verify(src, str(d / out), translations=tr, min_ink=min_ink)
    print(f'--- verdict exit={v.exit_code} gates={len(v.gates)} '
          f'findings={sum(len(getattr(g, "findings", ())) for g in v.gates)}', file=sys.stderr)
    for g in v.gates:
        print(f'    {g.name}: {g.status}' + (f' ({g.message})' if g.message else ''), file=sys.stderr)
