"""A06: run every verification example the documentation shows, as written.

Adapted from the 20 September A06 examples harness, which lives only in an
ignored run directory. That harness asserted the old default: a bare rebuild
exited 0 without the mapping. Here the default rebuild must fail an empty
target, and the rebuild examples no longer need the mapping flags.

Reads the `python3 scripts/verify.py` and `pipeline.py rebuild` lines from
README.md, SKILL.md and references/gates.md, and the two `run_verify` calls
from references/consumer-guide.md. Runs them against three cases: an empty
target, complete targets, and a fillable complete job.

usage: a06_doc_examples_probe.py <saved empty-target job dir> <fresh out dir>
The saved job needs original.pdf, stripped.pdf, segments.json and
translations.json (the 19 September audit's `empty-target` case).
"""
import ast
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / 'pdf-translate'
SAVED, BASE = Path(sys.argv[1]), Path(sys.argv[2])
BASE.mkdir(parents=True, exist_ok=False)
sys.path.insert(0, str(SKILL))
import pymupdf  # noqa: E402
import pdf_translate  # noqa: E402
assert Path(pdf_translate.__file__).resolve().is_relative_to(SKILL.resolve()), pdf_translate.__file__

FONT = SKILL / 'tests/fonts/NotoSans-Regular.ttf'
assert FONT.is_file()
commands = []
for name in ['README.md', 'SKILL.md', 'references/gates.md']:
    text = (SKILL / name).read_text(encoding='utf-8')
    for block in re.findall(r'```bash\n(.*?)```', text, flags=re.S):
        for line in block.replace('\\\n', ' ').splitlines():
            if line.startswith('python3 ') and ('scripts/verify.py ' in line or 'scripts/pipeline.py rebuild ' in line):
                line = line.replace('$SK', SKILL.as_posix()).replace('$TARGET_FILL', 'Prueba 123')
                args = shlex.split(line)
                script = Path(args[1])
                if not script.is_absolute():
                    script = SKILL / script
                args[:2] = [sys.executable, str(script)]
                assert '--fill-text' in args, args
                if 'verify.py' in args[1]:
                    # A direct verify must carry the mapping; rebuild supplies its own.
                    assert '--translations' in args and '--segments' in args, args
                commands.append((name, args))
kinds = sorted(('rebuild' if 'pipeline.py' in a[1] else 'verify') for _, a in commands)
print('documented commands:', len(commands), dict((k, kinds.count(k)) for k in set(kinds)))

guide = (SKILL / 'references/consumer-guide.md').read_text(encoding='utf-8')
six_calls = guide.split('## The six calls', 1)[1]   # an earlier block checks MAPPING_FORMATS
tree = ast.parse(re.findall(r'```python\n(.*?)```', six_calls, flags=re.S)[0])
api_nodes = [n for n in tree.body if isinstance(n, ast.Assign)
             and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
             and n.value.func.attr == 'run_verify']
assert len(api_nodes) == 2
for n in api_nodes:
    assert {'translations', 'segments', 'source_words_from', 'fill_text'} <= {k.arg for k in n.value.keywords}
api_code = '\n'.join(ast.unparse(n) for n in api_nodes)


def cli(args, cwd, log):
    env = dict(os.environ, PYTHONUTF8='1', PYTHONPATH=str(SKILL))
    p = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, encoding='utf-8')
    (BASE / log).write_text(p.stdout + p.stderr, encoding='utf-8')
    return p


results = []
for case, blank, form in [('empty-target', True, False), ('complete-targets', False, False),
                          ('fillable-complete', False, True)]:
    job = BASE / case
    job.mkdir()
    for name in ['original.pdf', 'stripped.pdf', 'segments.json', 'translations.json']:
        shutil.copyfile(SAVED / name, job / name)
    if form:
        with pymupdf.open(SAVED / 'original.pdf') as doc:
            widget = pymupdf.Widget()
            widget.field_name = 'sample'
            widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
            widget.rect = pymupdf.Rect(72, 180, 320, 210)
            doc[0].add_widget(widget)
            doc.save(job / 'original.pdf')
        pdf_translate.run_extract(str(job / 'original.pdf'), outdir=str(job))
        pdf_translate.run_strip(str(job / 'original.pdf'), str(job / 'stripped.pdf'))
    shutil.copyfile(job / 'original.pdf', job / 'source.pdf')   # the guide's example name
    conf = json.loads((job / 'translations.json').read_text(encoding='utf-8'))
    if not blank:
        conf['translations']['Another label'] = 'Otra etiqueta'
        conf['lang'] = 'es'
    (job / 'translations.json').write_text(json.dumps(conf), encoding='utf-8')
    expected = 1 if blank else 0

    # The bare default rebuild: the defect A06 fixes.
    default = cli([sys.executable, str(SKILL / 'scripts/pipeline.py'), 'rebuild', '--work', '.',
                   'original.pdf', 'out.pdf'], job, f'{case}-default.log')
    assert default.returncode == expected, (case, default.returncode, default.stdout[-800:])
    gates = {g['name']: g['status'] for g in
             json.loads((job / 'verify_report.json').read_text(encoding='utf-8'))['gates']}
    assert gates.get('empty-targets') == ('FAIL' if blank else 'PASS'), (case, gates)
    results.append({'case': case, 'document': '(bare default rebuild)', 'exit_code': default.returncode,
                    'empty_targets': gates.get('empty-targets')})

    if form:
        pdf_translate.run_field_fonts(str(job / 'out.pdf'), str(FONT), str(job / 'final.pdf'))
    else:
        shutil.copyfile(job / 'out.pdf', job / 'final.pdf')
    for i, (docname, args) in enumerate(commands):
        p = cli(args, job, f'{case}-example-{i}.log')
        assert p.returncode == expected, (case, i, p.returncode, p.stdout[-800:], p.stderr[-400:])
        reportname = args[args.index('--report') + 1] if '--report' in args else 'verify_report.json'
        report = json.loads((job / reportname).read_text(encoding='utf-8'))
        failures = [g['name'] for g in report['gates'] if g['status'] == 'FAIL']
        assert ('empty-targets' in failures) == blank, (case, i, failures)
        assert Path(report['output']).name == ('final.pdf' if 'final.pdf' in args else 'out.pdf')
        if form:
            fill = [g for g in report['gates'] if 'fill' in g['name']]
            assert fill and all(g['status'] == 'PASS' for g in fill), fill
        results.append({'case': case, 'document': docname, 'command': [Path(a).name if os.path.isabs(a) else a for a in args[1:]],
                        'exit_code': p.returncode, 'failures': failures, 'output': Path(report['output']).name})
    api_setup = f'''import json
from types import SimpleNamespace
import pdf_translate
work = {str(job)!r}
target_fill = 'Prueba 123'
built = SimpleNamespace(output='out.pdf')
final = SimpleNamespace(output='final.pdf')
extract = SimpleNamespace(segments_path=work + '/segments.json')
'''
    api_dump = "\nprint(json.dumps([v.to_dict() for v in (verdict, final_verdict)]))"
    p = cli([sys.executable, '-c', api_setup + api_code + api_dump], job, f'{case}-python.log')
    assert p.returncode == 0, p.stderr
    for data in json.loads(p.stdout):
        failures = [g['name'] for g in data['gates'] if g['status'] == 'FAIL']
        assert data['exit_code'] == expected and ('empty-targets' in failures) == blank, data
        results.append({'case': case, 'document': 'references/consumer-guide.md', 'exit_code': data['exit_code'],
                        'failures': failures, 'output': Path(data['output']).name})
    print(f'PASS {case}: bare default rebuild={default.returncode}; {len(commands)} documented CLI '
          f'examples + 2 documented Python calls={expected}; delivery path checked')

summary = {'version': pdf_translate.__version__, 'pymupdf': pymupdf.VersionBind,
           'documented_cli_commands': len(commands), 'cases': 3, 'results': results}
(BASE / 'results.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(f'PASS: {len(results)} outcomes, including 3 bare default rebuilds')
