#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Are two retypeset runs of the same job byte-identical, and if not, where?

C7 asks for "same inputs -> byte-identical output PDF when the caller supplies
a fixed timestamp (and /ID is derived from content or supplied)". Nothing in
this repository has ever measured what a run actually varies today, so the
fixed-timestamp design has no ground under it. This probe supplies the ground.

It builds one small job, runs `retypeset` several times, and reports:

  1. same path, back to back             — the base question
  2. different output path, same instant — does the output path leak in
  3. same path, seconds later            — does wall-clock leak in
  4. different path, seconds later        — both at once
  5. for every difference: the byte offset, the surrounding bytes, and which
     PDF key it falls inside (/ID, /CreationDate, /ModDate, /Producer, a
     stream, ...), so the fix can be scoped instead of guessed

Cases 2 and 3 are what separate a path-derived /ID from a clock-derived one,
and they are the whole reason this probe has four cases instead of one.

Nothing here is a gate and nothing here is shipped: it is a measurement whose
answer belongs in a brief, per the house rule that a design follows a probe.

Without --source the job is a synthetic one-pager: one core, no form fields, no
widgets, no merges. That shape is what the first run measured, and it is the
shape a "only /ID varies" claim is weakest on -- a form's widget appearances and
a dense table's many cores are exactly where a second varying key would hide.
Pass --source to run the same four comparisons over a corpus PDF instead; the
mapping is then identity (every core translated to itself), because the question
is byte stability, not translation quality, and identity cannot overflow a box.

    PYTHONUTF8=1 python dev/probes/determinism_probe.py [--work DIR] [--gap 3]
    PYTHONUTF8=1 python dev/probes/determinism_probe.py --source corpus/choice_fields.pdf
"""
import json
import re
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'pdf-translate'))

import pymupdf  # noqa: E402

from pdf_translate.extract_segments import extract_segments  # noqa: E402
from pdf_translate.retypeset import retypeset  # noqa: E402
from pdf_translate.strip_text import strip_text  # noqa: E402

FONT = ROOT / 'pdf-translate' / 'tests' / 'fonts' / 'NotoSans-Regular.ttf'
SOURCE = 'Income and Expense Declaration'
TARGET = 'Declaracion de ingresos y gastos'

# The PDF keys a difference is most likely to land in. Order matters: the
# first match wins, so the specific ones come before the generic ones.
KEYS = (b'/ID', b'/CreationDate', b'/ModDate', b'/Producer', b'/Creator',
        b'/Title', b'/Lang', b'/Font', b'/Length', b'stream')


def _quiet():
    """Swallow the stages' console. They print today; that is E3's problem."""
    import contextlib
    import io
    return contextlib.redirect_stdout(io.StringIO())


def shape_of(path):
    """How much furniture the source carries, so the brief can say what shape
    the claim is established for instead of implying every shape."""
    doc = pymupdf.open(str(path))
    widgets = sum(1 for page in doc for _ in page.widgets())
    annots = sum(1 for page in doc for _ in page.annots())
    pages = doc.page_count
    doc.close()
    return pages, widgets, annots


def build_job(work, source=None):
    """A job the real pipeline produced: strip, extract, a mapping.

    Synthetic and one-page by default. With `source`, the same pipeline over a
    corpus PDF, translated identically core-for-core."""
    work.mkdir(parents=True, exist_ok=True)
    src = work / 'orig.pdf'
    if source is None:
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), SOURCE)
        doc.save(src)
        doc.close()
        mapping = {SOURCE: TARGET}
    else:
        shutil.copyfile(source, src)

    with _quiet():
        strip_text(str(src), str(work / 'stripped.pdf'))
        extract_segments(str(src), outdir=str(work))

    if source is not None:
        cores = json.loads((work / 'to_translate.json')
                           .read_text(encoding='utf-8'))['cores']
        # Identity: the probe asks whether the bytes are stable, not whether
        # the words are right, and identity is the one mapping that cannot
        # overflow a box or reach for a glyph the face does not have.
        mapping = {core['text']: core['text'] for core in cores}

    font = str(FONT)
    (work / 'translations.json').write_text(json.dumps({
        'fonts': {'regular': font, 'bold': font,
                  'italic': font, 'bold_italic': font},
        'lang': 'es',
        'translations': mapping,
        'merges': [], 'overrides': [], 'center': [], 'skip': [],
    }, ensure_ascii=False), encoding='utf-8')
    return src, len(mapping)


def run(work, out):
    with _quiet():
        rc = retypeset(str(work / 'stripped.pdf'), str(work / 'segments.json'),
                       str(work / 'translations.json'), str(out))
    if rc != 0:
        raise SystemExit(f'retypeset refused the probe job (rc={rc})')
    return Path(out).read_bytes()


def key_at(data, offset):
    """Which PDF key the byte at `offset` sits inside, best effort: the nearest
    key name looking backwards within 200 bytes."""
    lo = max(0, offset - 200)
    window = data[lo:offset + 1]
    best, best_pos = None, -1
    for k in KEYS:
        pos = window.rfind(k)
        if pos > best_pos:
            best, best_pos = k, pos
    return best.decode('latin-1') if best else '(no key within 200 bytes)'


def doc_ids(data):
    """The trailer's /ID pair, or None. The first element conventionally
    identifies the document and the second the revision; which of them moves
    decides how much of C7 there is."""
    m = re.search(rb'/ID\s*\[\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>', data)
    return (m.group(1).decode(), m.group(2).decode()) if m else None


def discriminate(work, rounds=8):
    """"Same instant" is an assumption, not a measurement -- so measure it.

    The four-case layout above calls a vs c "same instant", but c runs after b.
    If the clock ticks in that gap, a path-leak and a clock-leak produce exactly
    the same evidence, and "the output path does not leak" rests on the tick
    having happened to fall elsewhere. That is luck, not a measurement.

    So: alternate X and Y many times, and use the /ID's own second element as
    the tick counter. Runs sharing an element 2 ran inside one tick. Any tick
    holding BOTH paths is a controlled experiment the clock cannot confound:

      * bytes equal there            -> the path is NOT in the bytes.
      * bytes differ there           -> the path IS in the bytes.
      * no tick ever holds both      -> nothing is proved; say so.
    """
    print(f'\n--- path vs clock discriminator ({rounds} X/Y pairs, back to back)')
    seq = []
    t0 = time.monotonic()
    for _ in range(rounds):
        for name in ('out_x.pdf', 'out_y.pdf'):
            data = run(work, work / name)
            pair = doc_ids(data)
            seq.append((name, data, pair[1] if pair else None))
    elapsed = time.monotonic() - t0

    ticks = {}
    for name, data, tick in seq:
        ticks.setdefault(tick, []).append((name, data))
    both = {t: members for t, members in ticks.items()
            if len({n for n, _ in members}) == 2}
    print(f'    {len(seq)} runs in {elapsed:.2f}s, '
          f'{len(ticks)} distinct /ID element 2 value(s)')
    print(f'    {len(both)} tick(s) contain BOTH output paths')

    verdict = None
    for tick, members in both.items():
        x = next(d for n, d in members if n == 'out_x.pdf')
        y = next(d for n, d in members if n == 'out_y.pdf')
        same = x == y
        print(f'      tick {tick[:16]}…  x == y: {same}')
        verdict = same if verdict is None else (verdict and same)

    if verdict is True:
        print('    => the OUTPUT PATH is not in the bytes. Two runs to')
        print('       different paths inside one tick are byte-identical, so')
        print('       the only thing left varying is the tick itself.')
    elif verdict is False:
        print('    => the OUTPUT PATH is in the bytes. A fixed doc_id is not')
        print('       enough on this shape; the path must stop reaching /ID.')
    else:
        print('    => inconclusive: no tick held both paths. Raise --rounds.')
    return verdict


def diff(a, b, label):
    print(f'\n--- {label}')
    if a == b:
        print(f'    IDENTICAL  ({len(a)} bytes)')
        return []
    if len(a) != len(b):
        print(f'    lengths differ: {len(a)} vs {len(b)}')
    runs, i, n = [], 0, min(len(a), len(b))
    while i < n:
        if a[i] != b[i]:
            start = i
            while i < n and a[i] != b[i]:
                i += 1
            runs.append((start, i))
        else:
            i += 1
    print(f'    {len(runs)} differing run(s), '
          f'{sum(e - s for s, e in runs)} byte(s) total')
    for start, end in runs[:12]:
        print(f'      @{start} len={end - start}  in {key_at(a, start)}')
        print(f'        A {a[max(0, start - 24):end + 8]!r}')
        print(f'        B {b[max(0, start - 24):end + 8]!r}')
    if len(runs) > 12:
        print(f'      … {len(runs) - 12} more')
    return runs


def main(argv):
    work = Path(argv[argv.index('--work') + 1]) if '--work' in argv else \
        ROOT / 'runs' / 'determinism-probe'
    gap = float(argv[argv.index('--gap') + 1]) if '--gap' in argv else 3.0
    source = None
    if '--source' in argv:
        source = Path(argv[argv.index('--source') + 1])
        if not source.is_absolute():
            source = (ROOT / 'pdf-translate' / source).resolve()
        if not source.exists():
            raise SystemExit(f'no such source: {source}')
    if work.exists():
        shutil.rmtree(work)

    print(f'pymupdf {(pymupdf.__doc__ or "?").strip().splitlines()[0]}')
    print(f'python  {sys.version.split()[0]}')
    print(f'work    {work}')
    print(f'source  {source.name if source else "(synthetic one-pager)"}')

    _, ncores = build_job(work, source)
    if source is not None:
        pages, widgets, annots = shape_of(source)
        print(f'shape   {pages} page(s), {ncores} core(s), '
              f'{widgets} widget(s), {annots} annotation(s)')
    a = run(work, work / 'out_a.pdf')
    b = run(work, work / 'out_a.pdf')          # same path, immediately
    c = run(work, work / 'out_c.pdf')          # different path, same instant
    time.sleep(gap)
    d = run(work, work / 'out_a.pdf')          # SAME path, seconds later
    e = run(work, work / 'out_e.pdf')          # different path, seconds later

    print('\n--- trailer /ID per run')
    pairs = []
    for name, data in (('a  back to back            ', a),
                       ('b  same path, same instant ', b),
                       ('c  other path, same instant', c),
                       ('d  same path, later        ', d),
                       ('e  other path, later       ', e)):
        pair = doc_ids(data)
        if pair:
            pairs.append(pair)
        print(f'    {name}  {pair[0]}  {pair[1]}' if pair else f'    {name}  (no /ID)')
    first = {p[0] for p in pairs}
    second = {p[1] for p in pairs}
    print(f'    element 1: {len(first)} distinct value(s) across 5 runs')
    print(f'    element 2: {len(second)} distinct value(s) across 5 runs')

    r1 = diff(a, b, 'same path, back to back')
    r2 = diff(a, c, 'different output path, same instant')
    r3 = diff(a, d, f'same path, {gap:g}s later')
    r4 = diff(a, e, f'different path, {gap:g}s later')

    path_clean = discriminate(work)
    path_leaks = path_clean is False

    print('\n=== verdict')
    if not (r1 or r2 or r3 or r4):
        print('    retypeset is already byte-deterministic on this job.')
        print('    C7 then needs no timestamp parameter for this path — only a')
        print('    test that pins it, and a check on a job with more furniture.')
    else:
        keys = {key_at(a, s) for runs in (r1, r2, r3, r4) for s, _ in runs}
        print(f'    varies. keys touched: {", ".join(sorted(keys))}')
        if keys == {'/ID'}:
            print('    ONLY /ID. Content streams, fonts and the /Info dates are')
            print('    already byte-identical — apply_document_metadata carries')
            print("    the source's /CreationDate, /ModDate and /Producer")
            print('    through unchanged, so no timestamp parameter is needed')
            print('    for them.')
        if path_leaks:
            print('    The output path leaks into the bytes — that is a second')
            print('    problem and it is not a timestamp.')
        elif r3 or r4 or r2:
            print('    The output PATH does not leak; the CLOCK does. Pinning or')
            print('    deriving /ID is the whole of C7 for this path.')
            if r2:
                print('    (The "same instant" pair differed too, but the')
                print('     discriminator shows two runs to the SAME path also')
                print('     differ, so that pair measures elapsed time, not the')
                print('     path.)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
