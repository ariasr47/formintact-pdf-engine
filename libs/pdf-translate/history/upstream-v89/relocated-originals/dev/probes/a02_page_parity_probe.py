"""A02 reproduction: does legacy verification (and the inspection outputs)
accept a translation with missing or extra pages?

Builds a real two-page legacy job through the shipped stages (strip, extract,
retypeset), then derives broken outputs from the good one. Drives the CLI as
subprocesses and the API in-process, against the checkout named by --root.

usage: a02_page_parity_probe.py --root <checkout>/pdf-translate --work <fresh dir> --out results.json
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
from pdf_translate import run_extract, run_strip, run_retypeset, run_verify  # noqa: E402
assert Path(pdf_translate.__file__).resolve().is_relative_to(ROOT), pdf_translate.__file__

FONT = str(ROOT / 'tests' / 'fonts' / 'NotoSans-Regular.ttf')
PY = sys.executable
env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUTF8='1')

def cli(*argv):
    done = subprocess.run([PY, *map(str, argv)], capture_output=True, text=True,
                          encoding='utf-8', env=env, cwd=str(WORK))
    return done.returncode, done.stdout + done.stderr

# --- a genuine two-page legacy job -----------------------------------------
source = WORK / 'original.pdf'
with pymupdf.open() as doc:
    for text in ('Hello world from the first page', 'Second page text is here'):
        doc.new_page().insert_text((72, 100), text, fontsize=12)
    doc.save(str(source))
run_extract(str(source), outdir=str(WORK))
run_strip(str(source), str(WORK / 'stripped.pdf'))
mapping = WORK / 'translations.json'
mapping.write_text(json.dumps({
    'fonts': {'regular': FONT, 'bold': FONT},
    'translations': {'Hello world from the first page': 'Hola mundo desde la primera página',
                     'Second page text is here': 'Aquí está el texto de la segunda página'},
    'merges': [], 'overrides': [], 'center': [], 'skip': []}, ensure_ascii=False), encoding='utf-8')
good = WORK / 'out-equal.pdf'
run_retypeset(str(WORK / 'stripped.pdf'), str(WORK / 'segments.json'), str(mapping), str(good))

# --- derived outputs --------------------------------------------------------
fewer = WORK / 'out-missing-page2.pdf'
with pymupdf.open(str(good)) as doc:
    doc.delete_page(1)
    doc.save(str(fewer))
more = WORK / 'out-extra-page3.pdf'
with pymupdf.open(str(good)) as doc:
    doc.new_page(width=doc[0].rect.width, height=doc[0].rect.height).insert_text(
        (72, 100), 'Página adicional', fontsize=12)
    doc.save(str(more))
rotated = WORK / 'out-page2-rotated.pdf'
with pymupdf.open(str(good)) as doc:
    doc[1].set_rotation(90)
    doc.save(str(rotated))
resized = WORK / 'out-page2-a4-to-letter.pdf'  # new_page() default is A4
with pymupdf.open(str(good)) as doc:
    doc[1].set_mediabox(pymupdf.Rect(0, 0, 612, 792))
    doc.save(str(resized))

cases = {'equal': good, 'missing_page_2': fewer, 'extra_page_3': more,
         'page2_rotated': rotated, 'page2_resized': resized}
results = {'root': str(ROOT), 'package': pdf_translate.__file__,
           'version': pdf_translate.__version__, 'pymupdf': pymupdf.VersionBind,
           'source_pages': 2, 'cases': {}}
verify_script = ROOT / 'scripts' / 'verify.py'
compare_script = ROOT / 'scripts' / 'compare.py'
pipeline_script = ROOT / 'scripts' / 'pipeline.py'
for name, out in cases.items():
    with pymupdf.open(str(out)) as doc:
        pages = len(doc)
    report = WORK / f'report-{name}.json'
    rc, text = cli(verify_script, source, out, '--fill-text', 'Prueba 123',
                   '--source-words-from', WORK / 'segments.json',
                   '--translations', mapping, '--segments', WORK / 'segments.json',
                   '--report', report)
    rep = json.loads(report.read_text(encoding='utf-8')) if report.exists() else None
    api = run_verify(str(source), str(out), fill_text='Prueba 123', translations=str(mapping),
                     segments=str(WORK / 'segments.json'),
                     source_words_from=str(WORK / 'segments.json'))
    api_nomap = run_verify(str(source), str(out))
    html = WORK / f'comparison-{name}.html'
    crc, ctext = cli(compare_script, source, out, html)
    chtml = html.read_text(encoding='utf-8') if html.exists() else ''
    rdir = WORK / f'renders-{name}'
    rrc, rtext = cli(pipeline_script, 'render', source, out, rdir)
    results['cases'][name] = {
        'output_pages': pages,
        'cli_verify_exit': rc,
        'cli_page_lines': [l for l in text.splitlines() if 'page' in l.lower() and ('PASS' in l or 'FAIL' in l) and 'ink' not in l],
        'report_exit': rep and rep['exit_code'],
        'report_gates': rep and [(g['name'], g['status']) for g in rep['gates']],
        'report_page_findings': rep and [g['findings'] for g in rep['gates'] if g['name'].startswith('page')],
        'api_exit': api.exit_code,
        'api_fail_gates': [g.name for g in api.gates if g.status == 'FAIL'],
        'api_nomapping_exit': api_nomap.exit_code,
        'api_nomapping_fail_gates': [g.name for g in api_nomap.gates if g.status == 'FAIL'],
        'compare_exit': crc,
        'compare_page_sections': chtml.count('<section class="page'),
        'compare_mentions_mismatch': 'mismatch' in chtml.lower() or 'missing' in chtml.lower(),
        'compare_console': ctext.strip().splitlines()[-3:],
        'render_exit': rrc,
        'render_files': sorted(p.name for p in rdir.glob('*.png')) if rdir.exists() else [],
        'render_console': rtext.strip().splitlines()[-3:],
    }
Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
for name, r in results['cases'].items():
    print(f"{name:15} pages={r['output_pages']} verify(cli={r['cli_verify_exit']}, api={r['api_exit']}, "
          f"fails={r['api_fail_gates']}; no-mapping api={r['api_nomapping_exit']} "
          f"fails={r['api_nomapping_fail_gates']}) compare(exit={r['compare_exit']}, sections={r['compare_page_sections']}) "
          f"render(exit={r['render_exit']}, files={len(r['render_files'])})")
