"""A06 reproduction: does the default legacy rebuild verify against the mapping
it built from?

Each case is a real job directory (source PDF, run_extract, run_strip, an
authored translations.json) driven through `scripts/pipeline.py rebuild` as a
subprocess, exactly as a user runs it, against the checkout named by --root.

usage: a06_rebuild_mapping_probe.py --root <checkout>/pdf-translate --work <fresh dir> --out results.json
"""
import argparse, json, os, subprocess, sys
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument('--root', required=True)
ap.add_argument('--work', required=True)
ap.add_argument('--out', required=True)
args = ap.parse_args()
ROOT = Path(args.root).resolve()
WORK = Path(args.work).resolve()
if WORK.exists():
    sys.exit(f'refusing to reuse {WORK}')
WORK.mkdir(parents=True)
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402
import pdf_translate  # noqa: E402
from pdf_translate import run_extract, run_strip  # noqa: E402
assert Path(pdf_translate.__file__).resolve().is_relative_to(ROOT), pdf_translate.__file__

FONT = str(ROOT / 'tests' / 'fonts' / 'NotoSans-Regular.ttf')
PIPELINE = ROOT / 'scripts' / 'pipeline.py'
env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUTF8='1')
LABELS = ('Hello world', 'Another label')


def job(name, translations, overrides=None, lines=LABELS):
    d = WORK / name
    d.mkdir()
    src = d / 'original.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page()
        for k, text in enumerate(lines):
            page.insert_text((72, 100 + 30 * k), text, fontsize=12)
        doc.save(str(src))
    run_extract(str(src), outdir=str(d))
    run_strip(str(src), str(d / 'stripped.pdf'))
    conf = {'fonts': {'regular': FONT, 'bold': FONT}, 'translations': translations,
            'merges': [], 'overrides': overrides or [], 'center': [], 'skip': []}
    (d / 'translations.json').write_text(json.dumps(conf, ensure_ascii=False), encoding='utf-8')
    return d


def rebuild(d, *extra, cwd=None):
    done = subprocess.run([sys.executable, str(PIPELINE), 'rebuild', '--work', str(d),
                           str(d / 'original.pdf'), str(d / 'out.pdf'), *map(str, extra)],
                          capture_output=True, text=True, encoding='utf-8', errors='replace',
                          env=env, cwd=str(cwd or d))
    report = d / 'verify_report.json'
    rep = json.loads(report.read_text(encoding='utf-8')) if report.exists() else None
    return {
        'exit': done.returncode,
        'report_exit': rep and rep['exit_code'],
        'failing_gates': rep and [g['name'] for g in rep['gates'] if g['status'] == 'FAIL'],
        'mapping_gates_present': rep and sorted({g['name'] for g in rep['gates']} & {
            'empty-targets', 'placement', 'override-markers', 'identifiers', 'button-captions',
            'caption-width', 'metadata', 'metadata-lang', 'scaled-runs'}),
        'tail': done.stdout.strip().splitlines()[-4:] + done.stderr.strip().splitlines()[-2:],
    }


complete = {'Hello world': 'Hola mundo', 'Another label': 'Otra etiqueta'}
results = {'root': str(ROOT), 'package': pdf_translate.__file__, 'version': pdf_translate.__version__,
           'cases': {}}
cases = results['cases']
cases['complete_default'] = rebuild(job('complete', complete))
cases['empty_target_default'] = rebuild(job('empty', {'Hello world': 'Hola mundo', 'Another label': ''}))
cases['whitespace_target_default'] = rebuild(job('blank', {'Hello world': 'Hola mundo', 'Another label': '   '}))
cases['null_target_default'] = rebuild(job('null', {'Hello world': 'Hola mundo', 'Another label': None}))
cases['absent_core_default'] = rebuild(job('absent', {'Hello world': 'Hola mundo'}))
d = job('empty-explicit', {'Hello world': 'Hola mundo', 'Another label': ''})
cases['empty_target_explicit_flags'] = rebuild(d, '--translations', d / 'translations.json',
                                               '--segments', d / 'segments.json')
# An explicit mapping elsewhere is the caller's choice and is honoured.
d = job('explicit-elsewhere', complete)
alt = WORK / 'elsewhere'
alt.mkdir()
(alt / 'translations.json').write_text((d / 'translations.json').read_text(encoding='utf-8'), encoding='utf-8')
(alt / 'segments.json').write_text((d / 'segments.json').read_text(encoding='utf-8'), encoding='utf-8')
cases['complete_explicit_elsewhere'] = rebuild(d, '--translations', alt / 'translations.json')
# An override that drops the source's list marker: a mapping-dependent check.
d = job('override', {'a. Hello world': 'a. Hola mundo', 'Another label': 'Otra etiqueta'},
        overrides=[{'page': 0, 'contains': 'a. Hello world', 'parts': [{'text': 'Hola mundo'}]}],
        lines=('a. Hello world', 'Another label'))
cases['override_drops_marker_default'] = rebuild(d)

Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
for name, r in cases.items():
    print(f"{name:32} exit={r['exit']} report_exit={r['report_exit']} fails={r['failing_gates']} "
          f"mapping_gates={r['mapping_gates_present']}")
