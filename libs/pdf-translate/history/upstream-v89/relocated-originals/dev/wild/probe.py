#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wild corpus probe — run the shipped strip + extract over real public PDFs.

Dev material, never shipped. No translation happens here: the question is
whether the pipeline's first two stages give the right verdict (translate or
refuse, with the reason) on documents nobody constructed, and whether they
crash. Every file runs in its own subprocess so a MuPDF segfault on one
document is a recorded crash, not the end of the run.

Usage (from the repo root, with the pdf-translate venv):
  python dev/wild/probe.py --files dev/wild/files --out dev/wild [--timeout 900]
  python dev/wild/probe.py --one FILE.pdf --json out.json --work DIR   (child)

Writes <out>/results.json and <out>/RESULTS.md. Work directories go to
<out>/work/<file>/ and are gitignored, like the PDFs themselves.
"""
import argparse
import io
import json
import os
import subprocess
import sys
import time
import traceback
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCRIPTS = REPO / 'pdf-translate' / 'scripts'
sys.path.insert(0, str(SCRIPTS))


# ----------------------------------------------------------------- child ---

def document_facts(src):
    """What the file is, before any stage touches it."""
    import pymupdf
    import pikepdf
    facts = {}
    doc = pymupdf.open(src)
    try:
        facts['pages'] = doc.page_count
        facts['encrypted'] = bool(doc.is_encrypted)
        facts['needs_pass'] = bool(doc.needs_pass)
        facts['title'] = (doc.metadata or {}).get('title') or ''
        facts['producer'] = (doc.metadata or {}).get('producer') or ''
        facts['language'] = doc.language or ''
        widgets = Counter()
        annots = Counter()
        fonts = Counter()
        font_names = set()
        rotations = set()
        text_chars = []
        images = 0
        for page in doc:
            rotations.add(page.rotation)
            for w in page.widgets():
                widgets[w.field_type_string] += 1
            for a in page.annots():
                if a.type[1] != 'Widget':
                    annots[a.type[1]] += 1
            for f in page.get_fonts(full=True):
                fonts[f[2]] += 1
                font_names.add(f[3])
            images += len(page.get_images())
            text_chars.append(len(page.get_text() or ''))
        facts['widgets'] = dict(widgets)
        facts['widgets_total'] = sum(widgets.values())
        facts['annots_non_widget'] = dict(annots)
        facts['font_types'] = dict(fonts)
        facts['font_names'] = sorted(font_names)[:12]
        facts['rotations'] = sorted(rotations)
        facts['images'] = images
        facts['text_chars_min'] = min(text_chars) if text_chars else 0
        facts['text_chars_total'] = sum(text_chars)
        facts['pages_without_text'] = sum(1 for c in text_chars if c == 0)
        first = (doc[0].get_text() or '') if doc.page_count else ''
        facts['please_wait'] = 'please wait' in first.lower()
    finally:
        doc.close()
    try:
        with pikepdf.open(src) as pdf:
            root = pdf.Root
            af = root.get('/AcroForm')
            facts['acroform'] = af is not None
            facts['xfa'] = bool(af is not None and '/XFA' in af)
            facts['acroform_fields'] = len(af.get('/Fields', [])) if af is not None else 0
            facts['need_appearances'] = bool(af.get('/NeedAppearances', False)) if af is not None else False
            facts['perms'] = sorted(str(k) for k in root['/Perms'].keys()) if '/Perms' in root else []
            facts['lang'] = str(root.get('/Lang', '')) if '/Lang' in root else ''
            facts['marked'] = bool(root['/MarkInfo'].get('/Marked', False)) if '/MarkInfo' in root else False
            facts['struct_tree'] = '/StructTreeRoot' in root
            names = root.get('/Names')
            facts['javascript'] = bool(names is not None and '/JavaScript' in names)
    except Exception as exc:  # encrypted, damaged, or pikepdf refusing
        facts['pikepdf_error'] = f'{type(exc).__name__}: {exc}'[:200]
    if facts.get('xfa'):
        facts['xfa_kind'] = ('dynamic' if (facts.get('acroform_fields', 0) == 0
                                          or facts.get('please_wait')) else 'hybrid')
    return facts


def run_stage(fn):
    """Call fn() capturing stdout; return (rc_or_None, stdout, traceback_or_None, seconds)."""
    buf = io.StringIO()
    t0 = time.perf_counter()
    tb = None
    rc = None
    try:
        with redirect_stdout(buf):
            rc = fn()
    except SystemExit as exc:
        rc = exc.code
    except Exception:
        tb = traceback.format_exc()
    return rc, buf.getvalue(), tb, round(time.perf_counter() - t0, 2)


def probe_one(src, work):
    import pymupdf
    import strip_text
    import extract_segments
    os.makedirs(work, exist_ok=True)
    rec = {'file': os.path.basename(src), 'bytes': os.path.getsize(src)}
    try:
        rec['facts'] = document_facts(src)
    except Exception:
        rec['facts'] = {}
        rec['facts_traceback'] = traceback.format_exc()

    stripped = os.path.join(work, 'stripped.pdf')
    rc, out, tb, secs = run_stage(lambda: strip_text.main([src, stripped]))
    rec['strip'] = {'rc': rc, 'seconds': secs, 'traceback': tb,
                    'stdout_tail': out[-1500:], 'written': os.path.isfile(stripped)}
    if os.path.isfile(stripped):
        try:
            d = pymupdf.open(stripped)
            rec['strip']['widgets_after'] = sum(1 for p in d for _ in p.widgets())
            rec['strip']['text_chars_after'] = sum(len(p.get_text() or '') for p in d)
            d.close()
        except Exception:
            rec['strip']['reopen_traceback'] = traceback.format_exc()
    for line in out.splitlines():
        if line.startswith(('FAIL', 'NOTE', 'WARNING')):
            rec['strip'].setdefault('notes', []).append(line[:200])

    result = {}

    def _extract():
        result.update(extract_segments.extract_segments(src, outdir=work))
        return 0

    rc, out, tb, secs = run_stage(_extract)
    ex = {'rc': rc, 'seconds': secs, 'traceback': tb, 'stdout_tail': out[-1500:]}
    if result:
        segs = result.get('segments') or []
        ex['segments'] = len(segs)
        ex['cores'] = len(result.get('cores') or [])
        ex['passthrough'] = sum(1 for s in segs if s.get('passthrough'))
        ex['widget_text'] = len(result.get('widget_text') or {})
        # Candidates carry kind "merge-candidate" since row 22; a segments.json
        # from an earlier extractor has none — label those the same way.
        kinds = Counter((w.get('kind') or 'merge-candidate') for w in result.get('warnings') or [])
        ex['warnings'] = dict(kinds)
        ex['unextractable_pages'] = result.get('unextractable_pages') or []
        ex['invisible_text_pages'] = [p for p, _ in (result.get('invisible_text_pages') or [])]
        pages_with_segs = {s['page'] for s in segs}
        ex['pages_with_segments'] = len(pages_with_segs)
        if ex['unextractable_pages'] or ex['invisible_text_pages']:
            ex['rc'] = 1
    rec['extract'] = ex

    # Verdict, the way corpus/verdicts.json names them.
    f = rec['facts']
    if rec['strip']['traceback'] or ex.get('traceback'):
        verdict = 'crash'
    elif ex.get('invisible_text_pages'):
        verdict = 'refuse+ocr-layer'
    elif ex.get('unextractable_pages'):
        verdict = 'refuse+OCR'
    elif rec['strip']['rc'] not in (0, None):
        verdict = 'strip-fail'
    else:
        verdict = 'translate'
    rec['verdict'] = verdict

    # Things a person should look at, whatever the verdict says.
    suspects = []
    if f.get('xfa'):
        suspects.append(f"xfa-{f.get('xfa_kind')}")
    if f.get('please_wait'):
        suspects.append('please-wait-placeholder')
    if f.get('annots_non_widget'):
        suspects.append('visible-annots:' + ','.join(f"{k}={v}" for k, v in sorted(f['annots_non_widget'].items())))
    if 'Type3' in (f.get('font_types') or {}):
        suspects.append('type3-font')
    if f.get('encrypted'):
        suspects.append('encrypted')
    if f.get('perms'):
        suspects.append('perms:' + ','.join(f['perms']))
    if f.get('javascript'):
        suspects.append('document-javascript')
    if f.get('widgets_total') and rec['strip'].get('widgets_after') not in (None, f.get('widgets_total')):
        suspects.append(f"widgets-changed-by-strip:{f.get('widgets_total')}->{rec['strip'].get('widgets_after')}")
    if verdict == 'translate' and f.get('pages') and ex.get('pages_with_segments', 0) < f['pages']:
        suspects.append(f"pages-without-segments:{f['pages'] - ex.get('pages_with_segments', 0)}")
    rec['suspects'] = suspects
    return rec


# ---------------------------------------------------------------- parent ---

def run_all(files_dir, out_dir, timeout):
    files = sorted(p for p in Path(files_dir).glob('*.pdf'))
    work_root = Path(out_dir) / 'work'
    work_root.mkdir(parents=True, exist_ok=True)
    records = []
    for pdf in files:
        work = work_root / pdf.stem
        work.mkdir(exist_ok=True)
        js = work / 'result.json'
        if js.exists():
            js.unlink()
        cmd = [sys.executable, str(Path(__file__).resolve()), '--one', str(pdf),
               '--json', str(js), '--work', str(work)]
        t0 = time.perf_counter()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            status, rcode, err = 'done', proc.returncode, proc.stderr[-1500:]
        except subprocess.TimeoutExpired as exc:
            status, rcode, err = 'timeout', None, (exc.stderr or b'')[-1500:].decode('utf-8', 'replace') if isinstance(exc.stderr, bytes) else str(exc.stderr or '')[-1500:]
        secs = round(time.perf_counter() - t0, 1)
        if js.exists():
            rec = json.loads(js.read_text(encoding='utf-8'))
        else:
            rec = {'file': pdf.name, 'bytes': pdf.stat().st_size, 'facts': {},
                   'strip': {}, 'extract': {}, 'suspects': [],
                   'verdict': 'crash' if status == 'done' else 'timeout'}
        rec['child'] = {'status': status, 'returncode': rcode, 'seconds': secs,
                        'stderr_tail': err}
        if rec['verdict'] == 'crash' and status == 'done' and rcode not in (0, None):
            rec['child']['signal'] = -rcode if rcode < 0 else None
        records.append(rec)
        print(f"{pdf.name:24s} {rec['verdict']:18s} {secs:6.1f}s  "
              f"{rec.get('extract', {}).get('segments', '-')} segs  "
              f"{' '.join(rec.get('suspects') or [])}", flush=True)
    return records


def render_md(records, out_dir):
    import pymupdf, pikepdf
    lines = []
    lines.append('# Wild corpus — results\n')
    lines.append(f'Generated by `dev/wild/probe.py` on {time.strftime("%Y-%m-%d %H:%M")} with '
                 f'Python {sys.version.split()[0]}, PyMuPDF {pymupdf.__version__}, '
                 f'pikepdf {pikepdf.__version__}. Stages: shipped `strip_text.main` and '
                 f'`extract_segments.extract_segments`, no translation. One subprocess per file. '
                 f'Sources and sizes: `SOURCES.md`.\n')
    counts = Counter(r['verdict'] for r in records)
    lines.append('## Summary\n')
    lines.append('| Verdict | Files |\n|---|---|')
    for k, v in sorted(counts.items()):
        lines.append(f'| `{k}` | {v} |')
    lines.append(f'| total | {len(records)} |\n')
    lines.append('## Table\n')
    lines.append('| File | Pages | Widgets | AcroForm fields | XFA | Verdict | Segments | Cores | Warnings (by kind) | Strip s | Extract s | Suspects |')
    lines.append('|---|---|---|---|---|---|---|---|---|---|---|---|')
    for r in records:
        f, s, e = r.get('facts', {}), r.get('strip', {}), r.get('extract', {})
        warn = ', '.join(f'{k} {v}' for k, v in sorted((e.get('warnings') or {}).items())) or '—'
        xfa = f.get('xfa_kind', 'yes') if f.get('xfa') else 'no'
        lines.append(f"| `{r['file']}` | {f.get('pages', '?')} | {f.get('widgets_total', '?')} | "
                     f"{f.get('acroform_fields', '?')} | {xfa} | **{r['verdict']}** | "
                     f"{e.get('segments', '—')} | {e.get('cores', '—')} | {warn} | "
                     f"{s.get('seconds', '—')} | {e.get('seconds', '—')} | "
                     f"{'; '.join(r.get('suspects') or []) or '—'} |")
    lines.append('\n## Per file\n')
    for r in records:
        f, s, e, c = r.get('facts', {}), r.get('strip', {}), r.get('extract', {}), r.get('child', {})
        lines.append(f"### `{r['file']}` — {r['verdict']}\n")
        lines.append(f"- {r.get('bytes', 0):,} bytes, {f.get('pages', '?')} pages, producer `{(f.get('producer') or '')[:60]}`, "
                     f"title `{(f.get('title') or '')[:60]}`")
        lines.append(f"- fonts by type {f.get('font_types')}, images {f.get('images')}, rotations {f.get('rotations')}, "
                     f"pages without text {f.get('pages_without_text')}, /Lang `{f.get('lang', '')}`, "
                     f"tagged {f.get('marked')}, struct tree {f.get('struct_tree')}, /Perms {f.get('perms')}, "
                     f"JavaScript {f.get('javascript')}")
        lines.append(f"- widgets {f.get('widgets')} (AcroForm fields {f.get('acroform_fields')}, "
                     f"NeedAppearances {f.get('need_appearances')}, XFA {f.get('xfa')}); "
                     f"non-widget annotations {f.get('annots_non_widget')}")
        lines.append(f"- strip: rc {s.get('rc')}, {s.get('seconds')} s, written {s.get('written')}, "
                     f"widgets after {s.get('widgets_after')}, text chars after {s.get('text_chars_after')}")
        for n in s.get('notes') or []:
            lines.append(f"  - {n}")
        lines.append(f"- extract: rc {e.get('rc')}, {e.get('seconds')} s, segments {e.get('segments')}, "
                     f"passthrough {e.get('passthrough')}, cores {e.get('cores')}, widget text {e.get('widget_text')}, "
                     f"pages with segments {e.get('pages_with_segments')}/{f.get('pages')}, "
                     f"warnings {e.get('warnings')}, unextractable {e.get('unextractable_pages')}, "
                     f"invisible {e.get('invisible_text_pages')}")
        if c:
            lines.append(f"- child: {c.get('status')}, returncode {c.get('returncode')}, {c.get('seconds')} s"
                         + (f", signal {c.get('signal')}" if c.get('signal') else ''))
        for key, label in (('facts_traceback', 'facts'),):
            if r.get(key):
                lines.append(f"\n```\n{r[key].strip()[-1200:]}\n```")
        for stage, d in (('strip', s), ('extract', e)):
            if d.get('traceback'):
                lines.append(f"\n{stage} traceback:\n\n```\n{d['traceback'].strip()[-1500:]}\n```")
        if c.get('stderr_tail'):
            lines.append(f"\nchild stderr:\n\n```\n{c['stderr_tail'].strip()[-1200:]}\n```")
        lines.append('')
    Path(out_dir, 'RESULTS.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--files', default=str(HERE / 'files'))
    ap.add_argument('--out', default=str(HERE))
    ap.add_argument('--timeout', type=int, default=900)
    ap.add_argument('--one')
    ap.add_argument('--json')
    ap.add_argument('--work')
    a = ap.parse_args(argv)
    if a.one:
        rec = probe_one(a.one, a.work or str(Path(a.out) / 'work' / Path(a.one).stem))
        Path(a.json).write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding='utf-8')
        return 0
    records = run_all(a.files, a.out, a.timeout)
    Path(a.out, 'results.json').write_text(json.dumps(records, indent=1, ensure_ascii=False), encoding='utf-8')
    render_md(records, a.out)
    print(f"\n{len(records)} files -> {Path(a.out) / 'results.json'}, {Path(a.out) / 'RESULTS.md'}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
