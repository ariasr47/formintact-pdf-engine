"""Reproduce the v58 readiness findings using synthetic PDFs.

Run from the repository root with its Python environment. Requires the fetched
Latin test font. Writes only runs/public-readiness-audit-2026-09-19/.
This records observed behavior; it does not change the library or remediate it.
"""
import hashlib
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'pdf-translate'))
import pymupdf
from pdf_translate import run_extract, run_strip, run_retypeset, run_review
from pdf_translate import compare, run_verify, render_pages
pipeline = importlib.import_module('pdf_translate.pipeline')
from tests.test_pipeline import find_test_font

WORK = ROOT / 'runs/public-readiness-audit-2026-09-19/repro'
WORK.mkdir(parents=True, exist_ok=True)
FONT = str(find_test_font())
outcomes = {}

def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding='utf-8')

def make_pdf(path, pages):
    with pymupdf.open() as doc:
        for text in pages:
            doc.new_page().insert_text((72, 100), text, fontsize=12)
        doc.save(str(path))

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

source = WORK / 'original.pdf'
make_pdf(source, ['Hello world'])
run_extract(str(source), outdir=str(WORK))
stripped = WORK / 'stripped.pdf'
run_strip(str(source), str(stripped))
mapping = WORK / 'translations.json'
conf = {'fonts': {'regular': FONT, 'bold': FONT},
        'translations': {'Hello world': 'Hola mundo'},
        'merges': [], 'overrides': [], 'center': [], 'skip': []}
write_json(mapping, conf)
built = WORK / 'out.pdf'
rebuild_args = [str(source), str(built), '--work', str(WORK)]
first_rc = pipeline.cmd_rebuild(rebuild_args)
report = WORK / 'verify_report.json'
report_obj = json.loads(report.read_text(encoding='utf-8'))
outcomes['rebuild_default_verify'] = {
    'rc': first_rc, 'report': report_obj,
}
before_pdf, before_report = digest(built), digest(report)
conf['translations'] = {}
write_json(mapping, conf)
failed_rc = pipeline.cmd_rebuild(rebuild_args)
outcomes['stale_after_failed_rebuild'] = {
    'first_rc': first_rc, 'failed_rc': failed_rc,
    'pdf_remains_unchanged': digest(built) == before_pdf,
    'verify_report_remains_unchanged': digest(report) == before_report,
    'old_verify_exit_code': report_obj.get('exit_code'),
}
conf['translations'] = {'Hello world': 'Hola mundo'}
write_json(mapping, conf)
write_json(WORK / 'review.json', {})
verdict = run_review(str(WORK), ingest='review.json', generate=False)
final = WORK / 'final.pdf'
finish_rc = pipeline.cmd_finish([str(source), str(built), FONT,
                                str(final), str(WORK / 'comparison.html'),
                                '--work', str(WORK)])
outcomes['invalid_review_delivery'] = {
    'review_errors': verdict.errors, 'review_exit_code': verdict.exit_code,
    'blocks_delivery': verdict.blocks_delivery,
    'finish_rc': finish_rc, 'final_created': final.exists(),
    'review_state': json.loads((WORK / 'review_state.json').read_text(encoding='utf-8')),
}
outcomes['non_form_temp_file'] = {
    'temporary_pdf_remains': Path(str(final) + '.tmp_withfont.pdf').exists(),
}
conf['overrides'] = [{'page': 0, 'contains': '', 'parts': [{'text': 'Hola mundo'}]}]
write_json(mapping, conf)
try:
    run_retypeset(str(stripped), str(WORK / 'segments.json'), str(mapping), str(WORK / 'override.pdf'))
    outcomes['empty_override_contains'] = {'exception': None}
except Exception as exc:
    outcomes['empty_override_contains'] = {'exception': type(exc).__name__, 'message': str(exc)}
long_source = WORK / 'two-pages.pdf'
make_pdf(long_source, ['First page', 'Second page'])
comparison = WORK / 'mismatched.html'
rc = compare(str(long_source), str(built), str(comparison))
outcomes['mismatched_comparison'] = {
    'rc': rc, 'source_pages': 2, 'target_pages': 1,
    'rendered_page_sections': comparison.read_text(encoding='utf-8').count('<section class="page">'),
}
two_translated = WORK / 'two-translated.pdf'
make_pdf(two_translated, ['Hola mundo', 'Pagina adicional'])
for label, original, translated in [
        ('more_pages', source, two_translated), ('fewer_pages', long_source, built)]:
    try:
        page_verdict = run_verify(str(original), str(translated))
        outcomes['verify_' + label] = {'exit_code': page_verdict.exit_code,
            'gates': [{'name': g.name, 'status': g.status} for g in page_verdict.gates]}
    except Exception as exc:
        outcomes['verify_' + label] = {'exception': type(exc).__name__, 'message': str(exc)}
outcomes['mismatched_renders'] = {
    'files_written': len(render_pages(str(long_source), str(built), str(WORK / 'renders')))}
blank_job = WORK / 'empty-target'
blank_job.mkdir(exist_ok=True)
blank_source = blank_job / 'original.pdf'
with pymupdf.open() as doc:
    page = doc.new_page()
    page.insert_text((72, 100), 'Hello world', fontsize=12)
    page.insert_text((72, 130), 'Another label', fontsize=12)
    doc.save(str(blank_source))
run_extract(str(blank_source), outdir=str(blank_job))
run_strip(str(blank_source), str(blank_job / 'stripped.pdf'))
blank_mapping = blank_job / 'translations.json'
write_json(blank_mapping, {'fonts': {'regular': FONT, 'bold': FONT},
    'translations': {'Hello world': 'Hola mundo', 'Another label': ''}})
blank_output = blank_job / 'out.pdf'
blank_default_rc = pipeline.cmd_rebuild([
    str(blank_source), str(blank_output), '--work', str(blank_job)])
blank_full = run_verify(str(blank_source), str(blank_output), translations=str(blank_mapping))
outcomes['empty_target_default_vs_explicit_mapping'] = {
    'default_rebuild_rc': blank_default_rc, 'explicit_mapping_verify_rc': blank_full.exit_code,
    'explicit_failures': [g.name for g in blank_full.gates if g.status == 'FAIL']}
write_json(WORK.parent / 'reproduction-results.json', outcomes)
print(json.dumps({k: v for k, v in outcomes.items() if k != 'rebuild_default_verify'}, indent=2))
print('Default rebuild gate IDs:', [g.get('name', g.get('id')) for g in report_obj.get('gates', [])])
