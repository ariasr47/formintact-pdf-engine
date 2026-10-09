"""A02 false-FAIL sweep: page_parity must be silent where the pages are right.

1. Every corpus PDF against its own stripped copy (strip is the stage that
   rewrites pages; retypeset and field_fonts draw on what strip wrote).
2. A retained real delivery (read-only): source vs stripped, source vs output,
   and the gate's status inside a full run_verify.
usage: a02_page_parity_sweep.py <checkout>/pdf-translate <real-job-dir> <scratch-dir> <out.json>
"""
import json, sys
from pathlib import Path

root, job, scratch, out = (Path(a) for a in sys.argv[1:5])
sys.path.insert(0, str(root))
import pymupdf  # noqa: E402
import pdf_translate  # noqa: E402
from pdf_translate import run_strip, run_verify  # noqa: E402
from pdf_translate.verify import page_parity  # noqa: E402
assert Path(pdf_translate.__file__).resolve().is_relative_to(root.resolve())

scratch.mkdir(parents=True, exist_ok=False)
results = {'package': pdf_translate.__file__, 'corpus': {}, 'real_job': {}}
for pdf in sorted((root / 'corpus').glob('*.pdf')):
    stripped = scratch / f'{pdf.stem}-stripped.pdf'
    try:
        run_strip(str(pdf), str(stripped))
    except Exception as exc:  # a refusal is not a geometry question
        results['corpus'][pdf.name] = {'strip': f'refused: {type(exc).__name__}'}
        continue
    with pymupdf.open(str(pdf)) as a, pymupdf.open(str(stripped)) as b:
        findings = page_parity(a, b)
        results['corpus'][pdf.name] = {'pages': len(a), 'rotations': sorted({p.rotation for p in a}),
                                       'findings': [f.to_dict() for f in findings]}

for a_name, b_name in (('source.pdf', 'stripped.pdf'), ('source.pdf', 'output.pdf')):
    with pymupdf.open(str(job / a_name)) as a, pymupdf.open(str(job / b_name)) as b:
        results['real_job'][f'{a_name} vs {b_name}'] = {
            'pages': [len(a), len(b)], 'findings': [f.to_dict() for f in page_parity(a, b)]}
v = run_verify(str(job / 'source.pdf'), str(job / 'output.pdf'))
results['real_job']['run_verify page-parity'] = [
    (g.status, g.message) for g in v.gates if g.name == 'page-parity']
out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')

bad = {k: v for k, v in results['corpus'].items() if v.get('findings')}
print(f"corpus: {len(results['corpus'])} PDFs; stripped with findings: {len(bad)}; "
      f"strip refusals: {sum('strip' in v for v in results['corpus'].values())}")
for k, v in bad.items():
    print('  FINDINGS', k, v['findings'])
for k, v in results['real_job'].items():
    print('real job', k, v)
