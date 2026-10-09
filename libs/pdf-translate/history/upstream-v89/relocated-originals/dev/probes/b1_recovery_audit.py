"""Independently audit an existing B1 probe run from the repository root.

Invocation: python dev/probes/b1_recovery_audit.py --work <existing-probe-directory>
Writes raw-audit.json beside that directory's results.json.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path

import pymupdf
from fontTools.ttLib import TTFont

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--work', type=Path, required=True,
                    help='Existing B1 probe results directory')
args = parser.parse_args()
ROOT = Path.cwd()
if not (ROOT / 'dev/probes/b1_cases.json').is_file():
    parser.error('Run from the repository root containing dev/probes/b1_cases.json')
WORK = args.work.resolve()
if not (WORK / 'results.json').is_file():
    parser.error('--work must contain an existing probe results.json')
REPORT = json.loads((WORK / 'results.json').read_text(encoding='utf-8'))
MANIFEST = json.loads((ROOT / 'dev/probes/b1_cases.json').read_text(encoding='utf-8'))

def compact(s):
    return re.sub(r'\s+', '', s)

def state(doc):
    return (len(doc), [list(p.rect) for p in doc],
            [(p.number, w.field_name, w.field_type, list(w.rect)) for p in doc for w in p.widgets()])

audit = []
for item in REPORT['stress']:
    case = item['case']
    work = WORK / 'stress' / case['id']
    assert item['baseline']['status'] == 'refused'
    assert item['baseline']['error']['error'] == 'PlacementError'
    assert len(item['baseline']['error']['refusals']['overflow']) == 1
    assert item['baseline']['error']['refusals']['overflow'][0]['core'] == item['selected']['core']
    assert not (work / 'single-line/out.pdf').exists()
    if item['wrapped']['status'] == 'refused':
        assert not (work / 'authored-box/out.pdf').exists()
        audit.append({'id': case['id'], 'baseline': 'overflow-refused', 'wrapped': 'overflow-refused'})
        continue
    src = WORK / (case['id'] + '-source.pdf') if case['source'].startswith('@') else ROOT / 'pdf-translate/corpus' / case['source']
    out = work / 'authored-box/out.pdf'
    target = MANIFEST[case['payload'] + '_payload']
    with pymupdf.open(src) as original, pymupdf.open(out) as doc:
        assert state(original) == state(doc)
        page = doc[case['page']]
        source_spans = [s for b in original[case['page']].get_text('dict')['blocks'] for line in b.get('lines', []) for s in line['spans'] if s['text'].strip() == case['text']]
        assert len(source_spans) == 1
        source_span = source_spans[0]
        assert all(abs(a - b) < .001 for a, b in zip(source_span['origin'], item['selected']['origin']))
        original_contents = page.get_contents()
        assert compact(target) in compact(page.get_text())
        for p in doc:
            if p.number != case['page']:
                assert compact(target) not in compact(p.get_text())
        # The actual final content stream invokes one nested form. Walk its
        # resource references and attest the exact font used by each Tf.
        xobjects = page.get_xobjects()
        fonts = page.get_fonts(full=True)
        used = []
        def walk(xref, invoker):
            stream = doc.xref_stream(xref).decode('latin1')
            for name in re.findall(r'/([A-Za-z0-9]+)\s+Do', stream):
                matching = [x for x in xobjects if x[1] == name and x[2] == invoker]
                assert len(matching) == 1, matching
                walk(matching[0][0], matching[0][0])
            for font_name, size in re.findall(r'/([A-Za-z0-9]+)\s+([\d.]+)\s+Tf', stream):
                matching = [f for f in fonts if f[4] == font_name and f[6] == invoker]
                assert len(matching) == 1, matching
                f = matching[0]
                used.append({'xref': f[0], 'resource': font_name, 'face': f[3], 'Tf_size': float(size), 'hash': hashlib.sha256(doc.extract_font(f[0])[3]).hexdigest()})
        walk(original_contents[-1], 0)
        fontfile = ROOT / 'pdf-translate/tests/fonts' / ('NotoSansJP-VF-wght400.ttf' if case['payload'] == 'cjk' else 'NotoSans-Regular.ttf')
        expected = hashlib.sha256(fontfile.read_bytes()).hexdigest()
        assert used and all(f['hash'] == expected for f in used)
        # Isolate the target by the verified final stream, rather than selecting
        # spans by position, to independently recompute origin and scale.
        page.set_contents(original_contents[-1])
        assert compact(page.get_text()) == compact(target)
        spans = [s for b in page.get_text('dict')['blocks'] for line in b.get('lines', []) for s in line['spans']]
        delta = [round(spans[0]['origin'][i] - source_span['origin'][i], 6) for i in (0, 1)]
        scales = sorted({round(s['size'] / source_span['size'], 6) for s in spans})
        assert min(scales) >= .7
        assert scales == item['observation']['effective_scales']
        assert delta == item['observation']['first_origin_delta_pt']
        # Remove implicit form BBox clips and explicit W/n clips in-memory.
        # A clipped renderer could otherwise make the ink-containment check
        # tautological while extracted characters still look complete.
        for xref, name, invoker, bounds in xobjects:
            doc.xref_set_key(xref, 'BBox', '[-10000 -10000 10000 10000]')
            content = doc.xref_stream(xref)
            content = re.sub(rb'\bW\*?\s+n\b', b'n', content)
            doc.update_stream(xref, content)
        # At higher raster resolution, confirm ink, not font ascent rectangles,
        # stays in the author box (one pixel allowance).
        zoom = 6
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True, annots=False)
        raw = pix.samples
        ink_pixels = []
        for y in range(pix.height):
            row = raw[y*pix.stride + pix.n-1:(y+1)*pix.stride:pix.n]
            if row.strip(b'\x00'):
                ink_pixels.append((len(row)-len(row.lstrip(b'\x00')), y, len(row.rstrip(b'\x00')), y+1))
        ink = pymupdf.Rect(min(r[0] for r in ink_pixels)/zoom, min(r[1] for r in ink_pixels)/zoom, max(r[2] for r in ink_pixels)/zoom, max(r[3] for r in ink_pixels)/zoom)
        box = pymupdf.Rect(case['box'])
        assert (box + (-1/zoom, -1/zoom, 1/zoom, 1/zoom)).contains(ink)
        assert not any(ink.intersects(w.rect) for w in page.widgets())
        other_xref = doc.get_new_xref()
        doc.update_object(other_xref, '<<>>')
        doc.update_stream(other_xref, b'\n'.join(doc.xref_stream(x) for x in original_contents[:-1]))
        page.set_contents(other_xref)
        other_spans = [s for b in page.get_text('dict')['blocks'] for line in b.get('lines', []) for s in line['spans']]
        overlaps = [s['text'] for s in other_spans if ink.intersects(pymupdf.Rect(s['bbox']))]
        assert not overlaps, overlaps
        weight = TTFont(fontfile)['OS/2'].usWeightClass
        audit.append({'id':case['id'], 'pages':len(doc), 'target_stream_exact':True, 'raw_font':used, 'font_weight_class':weight, 'first_origin_delta_pt':delta, 'effective_scales':scales, 'unclipped_ink_432dpi':list(ink), 'other_text_overlap':overlaps})

with pymupdf.open(WORK / 'duplicate-control/second-occurrence-request/out.pdf') as doc:
    page = doc[0]
    short = [s for b in page.get_text('dict')['blocks'] for line in b.get('lines', []) for s in line['spans'] if s['text'] == 'Short label']
    assert len(short) == 1 and short[0]['origin'] == (40.0,190.0)
    assert not any(abs(c['origin'][1] - 70) < .01 for b in page.get_text('dict')['blocks'] for line in b.get('lines', []) for c in line['spans'])
    duplicate = {'short_label_origin':list(short[0]['origin']), 'first_occurrence_absent':True, 'pages':len(doc)}

result = {'stress':audit, 'duplicate':duplicate, 'result':'PASS independent raw-PDF checks; approved first-baseline requirement remains unsatisfied'}
(WORK / 'raw-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
