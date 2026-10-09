"""R-47: typography verify's page loads, peak memory and verdicts on a filled form.

From pdf-translate/, naming the checkout under test the way the parity runners do:

    PYTHONPATH=<checkout>/pdf-translate python ../dev/probes/r47_verify_page_loads.py \
        make  WORK/fl100-sized.pdf 3 158 50
    PYTHONPATH=<checkout>/pdf-translate python ../dev/probes/r47_verify_page_loads.py \
        build WORK/fl100-sized.pdf WORK/fl100 tests/fonts
    PYTHONPATH=<checkout>/pdf-translate python ../dev/probes/r47_verify_page_loads.py \
        measure WORK/fl100 [final.pdf|out.pdf]

`make` draws a form sized like a real one: base-14 Helvetica lines on the left
and filled text fields on the right (FL-100: 3 pages, 158 widgets, 50 lines a
page; N-400: 14 pages, 440 widgets, 60 lines a page). The wild forms
themselves name non-embedded Arial twice, which a typography mapping refuses.

`build` authors an identity typography-1 mapping, prepares Noto Sans, builds
`out.pdf` and runs `field_fonts` to `final.pdf`, which sets NeedAppearances.

`measure` runs one typography verify and prints page loads of any document,
the process's peak RSS, the time, and digests of the typography gate and of
every gate. Run each measurement in its own process: ru_maxrss is a
process-lifetime peak. The identity mapping leaves the source text in place,
so the leak gate FAILs and the exit code is 1 on every tree; the digests are
what to compare.
"""
import hashlib
import json
from pathlib import Path
import resource
import sys
import time

import pymupdf

import pdf_translate
from pdf_translate import run_extract, run_field_fonts, run_prepare_font, run_retypeset, run_strip, run_verify
from pdf_translate.mapping import role_name

WORDS = ('petition marriage custody support property residence county court date child '
         'respondent attorney address telephone employer income request order hearing').split()
SUFFIX = {'regular': 'Regular', 'bold': 'Bold', 'italic': 'Italic', 'bold_italic': 'BoldItalic'}


def make(out, pages, widgets, lines):
    out, pages, widgets, lines = Path(out), int(pages), int(widgets), int(lines)
    per_page = [widgets // pages + (1 if i < widgets % pages else 0) for i in range(pages)]
    glyphs = 0
    with pymupdf.open() as doc:
        for pno in range(pages):
            page = doc.new_page(width=612, height=792)
            for i in range(lines):
                words = [WORDS[(pno * 7 + i * 3 + k) % len(WORDS)] for k in range(6)]
                text = f'{i + 1} ' + ' '.join(words).capitalize()
                page.insert_text((36, 60 + i * 11.5), text, fontname='helv', fontsize=8)
                glyphs += len(text)
            rows = (per_page[pno] + 1) // 2
            for k in range(per_page[pno]):
                col, row = divmod(k, rows)
                y = 52 + row * (680 / max(rows, 1))
                widget = pymupdf.Widget()
                widget.field_name = f'p{pno}.f{k}'
                widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
                widget.rect = pymupdf.Rect(330 + col * 125, y, 450 + col * 125, y + 10)
                widget.field_value = f'Answer {pno}-{k}'
                widget.text_font, widget.text_fontsize = 'Helv', 8
                page.add_widget(widget)
        doc.save(out)
    print(json.dumps({'form': out.name, 'pages': pages, 'widgets': widgets,
                      'lines': pages * lines, 'characters': glyphs}))


def build(source, work, fonts):
    source, work, fonts = Path(source), Path(work), Path(fonts).resolve()
    work.mkdir(parents=True)
    (work / 'selected').mkdir()
    original, stripped, segments = work / 'original.pdf', work / 'stripped.pdf', work / 'segments.json'
    original.write_bytes(source.read_bytes())
    run_strip(str(original), str(stripped))
    run_extract(str(original), str(work), typography=True)
    extraction = json.loads(segments.read_text(encoding='utf-8'))
    conf = {'format': 'typography-1', 'extraction_id': extraction['typography']['extraction_id'],
            'lang': 'en', 'font_sets': {}, 'source_font_resolutions': [], 'allow_scale': [],
            'document_targets': {}, 'targets': []}
    fonts_seen = extraction['typography']['fonts']
    for segment in extraction['segments']:
        runs = segment['style_runs']
        conf['targets'].append({'occurrence_id': segment['occurrence_id'],
                                'runs': [{'text': r['text'], 'source_runs': [r['run_id']]} for r in runs]})
        for run in runs:
            font = fonts_seen[run['font_id']]
            cls, role = font['class'], role_name(font['bold'], font['italic'])
            conf['font_sets'].setdefault(cls, {})[role] = f'selected/{cls}-{role}.ttf'
    mapping = work / 'translations.json'
    mapping.write_text(json.dumps(conf, ensure_ascii=False, indent=2), encoding='utf-8')
    for cls, roles in conf['font_sets'].items():
        family = 'NotoSerif' if cls == 'serif' else 'NotoSans'
        for role, relative in roles.items():
            run_prepare_font(str(fonts / f'{family}-{SUFFIX[role]}.ttf'), str(mapping), str(work / relative),
                             font_class=cls, font_role=role, reference_fonts=str(fonts))
    built = run_retypeset(str(stripped), str(segments), str(mapping), str(work / 'out.pdf'),
                          original=str(original))
    run_field_fonts(built.output, str(fonts / 'NotoSans-Regular.ttf'), str(work / 'final.pdf'))
    print(json.dumps({'segments': len(extraction['segments']), 'pages': built.pages,
                      'font_sets': {c: sorted(r) for c, r in conf['font_sets'].items()}}))


def measure(work, final='final.pdf'):
    work = Path(work)
    loads = [0]
    load_page = pymupdf.Document.load_page

    def counted(doc, *args, **kwargs):
        loads[0] += 1
        return load_page(doc, *args, **kwargs)

    pymupdf.Document.load_page = counted
    start = time.perf_counter()
    verdict = run_verify(str(work / 'original.pdf'), str(work / final),
                         translations=str(work / 'translations.json'),
                         segments=str(work / 'segments.json'), typography=True)
    elapsed = time.perf_counter() - start
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_bytes = peak if sys.platform == 'darwin' else peak * 1024  # macOS reports bytes, Linux KiB
    gates = [g.to_dict() for g in verdict.gates]
    gate = next(g for g in gates if g['name'] == 'typography')

    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]

    print(json.dumps({
        'package': str(Path(pdf_translate.__file__).parent), 'version': pdf_translate.__version__,
        'final': final, 'typography': gate['status'], 'occurrences': gate['message'],
        'typography_digest': digest(gate), 'all_gates_digest': digest(gates),
        'exit_code': verdict.exit_code, 'page_loads': loads[0],
        'peak_rss_mb': round(peak_bytes / 2**20), 'seconds': round(elapsed, 2),
    }))


if __name__ == '__main__':
    {'make': make, 'build': build, 'measure': measure}[sys.argv[1]](*sys.argv[2:])
