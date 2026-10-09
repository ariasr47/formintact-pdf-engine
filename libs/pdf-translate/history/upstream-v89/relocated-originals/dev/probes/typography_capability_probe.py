"""Read-only capability probe using library Git snapshots and an authored PDF.

Run from the repository root with its existing Python environment. Each run
needs a new --work directory; snapshots and generated inputs stay there.
No product source, font download, translation model or renderer change.
"""
import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import pymupdf

ROOT = Path(__file__).resolve().parents[2]
REFS = {
    'app-pin-v54': '9675c6196c14eb2a2d37d34e371391eb1955a386',
    'main-v56': 'a5629fbd6bc084f9ab849ae3db74992c15807fcc',
    'branch-v58': '1d4f9707c4d9934fbc88d8f25576ac304b9bb139',
}


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', required=True)
    args = parser.parse_args()
    work = Path(args.work).resolve()
    if not work.is_relative_to(ROOT / 'runs'):
        parser.error('--work must be inside this repository\'s runs directory')
    # Resolve every ref before writing artifacts.
    for ref in REFS.values():
        git('rev-parse', '--verify', ref + '^{commit}')
    work.mkdir(parents=True, exist_ok=False)
    source = work / 'source.pdf'
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), 'Heading', fontname='tiro', fontsize=12)
        page.insert_text((72, 108), 'Label', fontname='helv', fontsize=12)
        for baseline, tail_font in [(144, 'heit'), (180, 'hebo')]:
            page.insert_text((72, baseline), 'Pay ', fontname='helv', fontsize=12)
            tail_x = 72 + pymupdf.get_text_length('Pay ', fontname='helv', fontsize=12)
            page.insert_text((tail_x, baseline), 'NOW', fontname=tail_font, fontsize=12)
        doc.save(source)
    with pymupdf.open(source) as doc:
        lines = [[{'text': span['text'], 'font': span['font'], 'flags': span['flags']}
                  for span in line['spans']]
                 for block in doc[0].get_text('dict')['blocks'] if block['type'] == 0
                 for line in block['lines']]
    assert [s['font'] for s in lines[0]] == ['Times-Roman'], lines
    assert [s['font'] for s in lines[1]] == ['Helvetica'], lines
    assert [s['font'] for s in lines[2]] == ['Helvetica', 'Helvetica-Oblique'], lines
    assert [s['font'] for s in lines[3]] == ['Helvetica', 'Helvetica-Bold'], lines

    result = {'pymupdf': pymupdf.VersionBind, 'source_lines': lines, 'snapshots': []}
    extraction_trees = []
    worker = '''import json, sys
import pdf_translate
from pathlib import Path
src, outdir = sys.argv[1:]
fn = getattr(pdf_translate, 'run_extract', None)
if fn:
    result = fn(src, outdir)
    segments, cores, warnings = result.segments, result.cores, result.warnings
else:
    result = pdf_translate.extract_segments(src, outdir=outdir)
    segments, cores, warnings = result['segments'], result['cores'], result['warnings']
Path(outdir, 'summary.json').write_text(json.dumps({
    'version': pdf_translate.__version__,
    'api': 'run_extract' if fn else 'extract_segments',
    'segments': segments, 'cores': cores, 'warnings': warnings,
}), encoding='utf-8')
'''
    for label, ref in REFS.items():
        snapshot = work / label
        paths = git('ls-tree', '-r', '--name-only', ref, '--', 'pdf-translate/pdf_translate').decode().splitlines()
        for name in paths:
            dest = snapshot / name
            assert dest.resolve().is_relative_to(snapshot.resolve())
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(git('show', f'{ref}:{name}'))
        module = snapshot / 'pdf-translate/pdf_translate/extract_segments.py'
        tree = ast.parse(module.read_text(encoding='utf-8'))
        extraction_trees.append(ast.dump(next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'extract_segments'), include_attributes=False))
        outdir = snapshot / 'job'
        outdir.mkdir()
        env = dict(os.environ, PYTHONPATH=str(snapshot / 'pdf-translate'), PYTHONUTF8='1')
        subprocess.run([sys.executable, '-c', worker, str(source), str(outdir)],
                       cwd=snapshot, env=env, check=True)
        data = json.loads((outdir / 'summary.json').read_text(encoding='utf-8'))
        segments = data['segments']
        assert len(segments) == 4, data
        assert [(s['bold'], s['italic']) for s in segments] == [(False, False), (False, False), (False, True), (True, False)], segments
        assert len({s['id'] for s in segments}) == 4
        assert not {'font', 'family', 'serif', 'spans', 'style_runs'}.intersection(set().union(*(s.keys() for s in segments)))
        assert next(c['count'] for c in data['cores'] if c['text'] == 'Pay NOW') == 2
        assert not data['warnings'], data['warnings']
        result['snapshots'].append({'label': label, 'commit': ref, **data})
        print(f"PASS {label}: 4 occurrence IDs, 3 unique cores; 2 mixed lines flattened; no family/span records; 0 extraction warnings")
    assert len(set(extraction_trees)) == 1
    result['extract_segments_function_identical_across_refs'] = True
    (work / 'results.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print('PASS: extract_segments function AST identical across all three commits; source font identities asserted.')


if __name__ == '__main__':
    main()
