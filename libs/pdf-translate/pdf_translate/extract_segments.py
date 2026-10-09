#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 2 of pdf-translate: extract translatable segments from the ORIGINAL PDF.

Reads text with exact geometry and writes two files:

  segments.json     every placeable segment: page, bbox, baseline origin,
                    font size, bold/italic, raw text, parsed marker / gap /
                    body_dx / core /
                    dot-leader info. retypeset.py consumes this.
  to_translate.json unique normalized "core" strings that need translation,
                    with occurrence counts. Author translations for ALL of
                    these (pass-through items are pre-filtered out).
  widget_text.json  the translatable strings that live in annotation
                    dictionaries and never reach a content stream: /TU
                    tooltips, choice /Opt display labels, text-field /V and
                    /DV defaults. Author the "target" of each, then pass the
                    file to strip_text.py --widget-text. Empty object when
                    the PDF has none. A value that is DATA — a 2D-barcode
                    payload such as USCIS's PDF417BarCode1
                    ("I-864|08/24/26|1"), an ID, a date stamp — gets its
                    source string back as the target: identity-mapped,
                    never translated (null refuses the build; a translation
                    corrupts what the form submits).

segments.json also carries a "document" block: the source /Lang, /Title and
the outline (bookmark) titles. The title and the outline entries are added
to to_translate.json as cores, because the window caption, the bookmarks
pane and every screen reader read them.

Page text is read from an annotation-free display list. get_text() includes
widget appearance streams, so a text field's default value and a combo
box's current choice would otherwise arrive as page text: the author
translates them, retypeset draws the translation on the page, and the
widget keeps drawing the source value on top of it.

Segmentation rules (why they matter):
- A visual line is split at horizontal gaps > GAP pt between spans: one line
  often holds several independent cells (CITY: STATE: ZIP:) or a label and a
  dot-leader run separated by a checkbox gap. Each piece is placed on its own.
- The line's direction vector is recorded per segment ("dir"). Rotated
  lines (side labels, stamps) keep their angle at re-typeset time; a
  rotated line is never gap-split, because the x distance between its
  spans is not a horizontal gap.
- List markers (a., (1), 12.) are peeled off and kept verbatim, together
  with the whitespace that followed them ("gap") and the measured distance
  from the origin to the body's first glyph ("body_dx", from the
  characters the source drew). retypeset draws the marker in Helvetica
  whatever the source font, so Helvetica's own advance is right only for
  a Helvetica-metric source; the measured offset is right for any.
- Trailing dot leaders + optional currency symbol are peeled off; retypeset
  refills dots anchored to the right so they never invade mid-line gaps.
- Segments whose core is empty or purely numbers/symbols are pass-through
  (re-inserted verbatim) and excluded from to_translate.json.
- Segments containing a run of >= 6 spaces INSIDE one span are flagged in
  "warnings": they usually hide a mid-line gap (checkboxes between phrases)
  that gap-splitting cannot see. Handle each with an "overrides" entry in
  translations.json rather than a plain translation.
- Write/find/say candidates (quoted strings, form/document names, URLs,
  emails) are flagged for confirmation — they often must stay verbatim.
- Image regions big enough to carry words (banners, seals, stamps,
  screenshots) are listed as "image-region" review items. Nothing here
  translates pixels; a person looks at each one.
- Consecutive same-column lines that look like a wrapped paragraph are
  flagged as merge *candidates* (kind "merge-candidate"). Never
  auto-merged; declare each merge
  explicitly in translations.json.
- Segments that share a right edge while their left edges differ, and
  sit within one em of a rule, a field or the next segment, are flagged
  "right-aligned": a longer translation anchored at the left grows past
  the edge it was tucked against. Justified text shares a right edge too
  and is a merge, never a column. The author lists those cores in
  "right"; nothing is realigned automatically.
- Three or more stacked cores sharing a skinny column (same left edge,
  bbox under 90 pt wide) are flagged "narrow-column": a pay-stub box or
  label stack, where translating each line-break on its own turns the
  box to crumbs. Warn only — never merged, and no verify gate.
- A page whose text is invisible (an OCR layer over a scanned image, or
  text hidden under an image) is refused: the words the reader sees are
  pixels, and retypeset would print the translation over them. The
  script exits non-zero and names the pages (kind: invisible-text).

--pages takes 1-based, inclusive page numbers ("1-10", "3", "11-20,25-")
so a long manual can be worked in slices: extract a range, author it, and
combine the mappings with `pipeline.py merge-mappings` before the single
retypeset over the whole document.

Usage:
  python3 extract_segments.py ORIGINAL.pdf [--gap 12] [--outdir .]
                                           [--pages 1-10] [--max-per-kind 10]
"""
import json
import logging
import os
import re
import sys

import pymupdf

from ._console import console, _arg
from ._pixels import VISIBLE_INK, dark_pixels
from .results import ExtractResult
from .strip_text import (
    GEOMETRY_SPACE, invisible_text_pages, page_textdict_without_annots,
    page_rawdict_without_annots, widget_text_is_authored, widget_text_scaffold)

MARKER = re.compile(
    r'^\s*(\(?[a-z]\.|\([a-z0-9]{1,3}\)|\d{1,2}\.)\s+'
    r'|^\s*(\(?[a-z]\.|\([a-z0-9]{1,3}\)|\d{1,2}\.)$')
DOTS = re.compile(r'^(.*?)([.…]{3,})(\s*[$€£¥]?\s*)$')
# Pass-through ONLY when the core contains no letters at all. An earlier
# version also passed any string of <=4 chars, which silently shipped short
# words ("may", "or", "Yes") in the source language — a defect no gate
# catches. Short real words must appear in to_translate.json; if a token
# genuinely should stay verbatim (a form number like "FL-150"), map it to
# itself in translations.json so the decision is explicit and reviewable.
PASS = re.compile(r'^[^\w]*$|^[\d\s.,:;$€£¥%–—\-()/§*+=&#\'"°]*$', re.UNICODE)
INNER_GAP = re.compile(r'\S(\s{6,})\S')
# A page with VISIBLE_INK dark pixels (_pixels) counts as "has visible ink"
# even if get_text() is empty: the scanned-PDF case.


def page_has_visible_content(page):
    """True if the page has an image or enough ink to not be blank."""
    if page.get_images():
        return True
    pix = page.get_pixmap(dpi=72)
    return dark_pixels(pix.samples, pix.n) >= VISIBLE_INK
# Write/find/say candidates: the reader may have to write, hand over, or
# search for these. Flag them; do not auto-passthrough (that decision is
# the mapping author's).
QUOTED = re.compile(r'[“"]([^”"]{3,80})[”"]')
FORM_NAME = re.compile(
    r'\b(?:Form|Schedule|Attachment|Exhibit)\s+[A-Z0-9][-A-Z0-9./]*\b')
URL_EMAIL = re.compile(
    r'https?://[^\s]+|\b[\w.+-]+@[\w.-]+\.\w{2,}\b', re.IGNORECASE)


def write_find_say_hits(text):
    """Return (kind, snippet) pairs that usually must stay verbatim."""
    hits = []
    for m in QUOTED.finditer(text):
        hits.append(('quoted', m.group(1)))
    for m in FORM_NAME.finditer(text):
        hits.append(('form-name', m.group(0)))
    for m in URL_EMAIL.finditer(text):
        hits.append(('url-or-email', m.group(0)))
    return hits


def raw_char_lines(page):
    """[[char, ...], ...] per line of the annotation-free text page."""
    lines = []
    for b in page_rawdict_without_annots(page)['blocks']:
        if b.get('type') != 0:
            continue
        for ln in b.get('lines', []):
            chars = [c for sp in ln.get('spans', []) for c in sp.get('chars', [])]
            if chars:
                lines.append(chars)
    return lines


def body_offset(group, marker, gap, core, lines):
    """Distance from the group's origin to the first glyph of the core,
    along the line direction, measured from the characters the source
    drew (row 23). None when the characters cannot be matched, in which
    case retypeset falls back to Helvetica's advance for marker + gap.
    """
    if not (marker and core):
        return None
    ox, oy = group['origin']
    dx, dy = (group.get('dir') or [1.0, 0.0])[:2]
    want = marker + gap + core[0]
    for chars in lines:
        for j, ch in enumerate(chars):
            cx, cy = ch['origin']
            if abs(cx - ox) > 1.0 or abs(cy - oy) > 1.0:
                continue
            k = j
            while k < len(chars) and chars[k]['c'].isspace():
                k += 1
            seq = ''.join(c['c'] for c in chars[k:k + len(want)])
            if seq != want:
                return None
            body = chars[k + len(marker) + len(gap)]
            bx, by = body['origin']
            return round((bx - ox) * dx + (by - oy) * dy, 2)
    return None


def merge_candidate_warnings(segments):
    """Suggest wrapped-paragraph merges. Never apply them automatically."""
    warnings = []
    by_page = {}
    for s in segments:
        if s['passthrough']:
            continue
        by_page.setdefault(s['page'], []).append(s)
    ender = re.compile(r'[.!?…:;]$')
    for page, segs in by_page.items():
        segs = sorted(segs, key=lambda s: (s['origin'][1], s['origin'][0]))
        run = []
        def flush():
            if len(run) >= 2:
                warnings.append({
                    'kind': 'merge-candidate',
                    'page': page,
                    'ids': [s['id'] for s in run],
                    'why': 'possible wrapped paragraph — declare a merge in '
                           'translations.json if these lines are one unit; '
                           'do not auto-merge (sibling list items share x); '
                           'a longer target may need a "box" you choose',
                    'lines': [s['text'].strip() for s in run],
                })
        for s in segs:
            if not run:
                run = [s]
                continue
            prev = run[-1]
            same_col = abs(s['origin'][0] - prev['origin'][0]) < 4
            dy = s['origin'][1] - prev['origin'][1]
            stacked = 0.5 < dy < max(prev['size'], s['size']) * 2.2
            prev_open = not ender.search(prev['core'].rstrip())
            if same_col and stacked and prev_open:
                run.append(s)
            else:
                flush()
                run = [s]
        flush()
    return warnings


NARROW_WIDTH = 90.0
NARROW_RUN = 3


def narrow_column_warnings(segments):
    """Warn on >=3 stacked short cores in a skinny column. Never merge them.

    Pay-stub boxes and label stacks are many one-word cores at the same x
    with a bbox about one word wide. merge_candidate_warnings only fires on
    *open sentences*, so a stack of short labels never trips it and the
    author translates each fragment independently -- the box turns to
    crumbs. Warn; the author decides whether one override with a max_width
    is right. Merging by geometry would swallow sibling list items, which
    is why this stays a warning.
    """
    warnings = []
    by_page = {}
    for s in segments:
        if s['passthrough']:
            continue
        if s['bbox'][2] - s['bbox'][0] >= NARROW_WIDTH:
            continue
        by_page.setdefault(s['page'], []).append(s)
    for page, segs in sorted(by_page.items()):
        segs = sorted(segs, key=lambda s: (s['bbox'][0], s['origin'][1]))
        run = []

        def flush():
            if len(run) >= NARROW_RUN:
                warnings.append({
                    'page': page,
                    'kind': 'narrow-column',
                    'ids': [s['id'] for s in run],
                    'width': round(max(s['bbox'][2] - s['bbox'][0]
                                       for s in run), 2),
                    'why': 'stacked cores share a column narrower than '
                           f'{NARROW_WIDTH:g} pt; translating each line on '
                           'its own breaks the box. Consider one merge, or '
                           'one override with max_width. Not auto-merged',
                    'lines': [s['core'] for s in run],
                })

        for s in segs:
            if not run:
                run = [s]
                continue
            prev = run[-1]
            same_col = abs(s['bbox'][0] - run[0]['bbox'][0]) < 4
            dy = s['origin'][1] - prev['origin'][1]
            stacked = 0.5 < dy < max(prev['size'], s['size']) * 2.2
            if same_col and stacked:
                run.append(s)
            else:
                flush()
                run = [s]
        flush()
    return warnings


def _catalog_key(doc, key):
    """(type, value) of a catalog key, or ('null', '') when absent."""
    try:
        cat = doc.pdf_catalog()
        if not cat:
            return 'null', ''
        kind, val = doc.xref_get_key(cat, key)
        return kind, val
    except Exception:
        return 'null', ''


def _is_marked(doc):
    kind, val = _catalog_key(doc, 'MarkInfo')
    return kind != 'null' and 'Marked' in str(val) and 'true' in str(val)


def _has_struct_tree(doc):
    kind, _ = _catalog_key(doc, 'StructTreeRoot')
    return kind != 'null'


def document_strings(src):
    """Document-level translatable text: /Lang, /Title and outline titles.

    None of this is page text, so strip-and-retypeset never touches it: an
    output keeps the English /Title in the window caption, the source
    /Lang for screen readers and hyphenation, and an untranslated
    bookmark tree. All three are visible to the reader and to assistive
    technology.
    """
    doc = pymupdf.open(src)
    try:
        meta = doc.metadata or {}
        try:
            lang = doc.language or ''
        except Exception:
            lang = ''
        toc = []
        for entry in doc.get_toc(simple=True) or []:
            if len(entry) >= 2 and str(entry[1]).strip():
                toc.append(str(entry[1]))
        return {
            'lang': lang,
            'title': (meta.get('title') or '').strip(),
            'subject': (meta.get('subject') or '').strip(),
            'keywords': (meta.get('keywords') or '').strip(),
            'outline': toc,
            'marked': _is_marked(doc),
            'struct_tree': _has_struct_tree(doc),
        }
    finally:
        doc.close()


RIGHT_EDGE_TOL = 1.5
RIGHT_LEFT_SPREAD = 4.0
# A label sits against the thing to its right by about one letter; column
# gutters are two ems or more. Measured on seventeen real documents
# (dev/wild/ANALYSIS.md): within one em the proposals fall from 1,485 groups
# to 158 and every caption stack on FL-100 survives; at two ems the
# justified columns of the IRS booklet come back.
TUCK_EM = 1.0
# An obstacle counts for a row it overlaps by at least this much of the
# row's height, so a rule that ends above the label does not count.
TUCK_ROW_OVERLAP = 0.3


def page_obstacles(page):
    """Verticals a label can be tucked against: [(x, top, bottom)].

    The left edge of every widget, and every vertical rule or box edge in
    the page's drawings. Segment left edges are added by
    right_alignment_warnings itself, because the next cell's text is an
    obstacle too.
    """
    out = []
    for w in page.widgets():
        r = w.rect
        out.append((r.x0, r.y0, r.y1))
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []
    for d in drawings:
        for it in d.get('items', []):
            if it[0] == 'l':
                p, q = it[1], it[2]
                if abs(p.x - q.x) < 1.5 and abs(p.y - q.y) > 4:
                    out.append((p.x, min(p.y, q.y), max(p.y, q.y)))
            elif it[0] == 're':
                r = it[1]
                if r.height > 4:
                    out.append((r.x0, r.y0, r.y1))
                    out.append((r.x1, r.y0, r.y1))
    return out


def _tucked(seg, verticals):
    """True when something starts within TUCK_EM of the segment's right
    edge and overlaps its row."""
    x0, y0, x1, y1 = seg['bbox']
    size = seg.get('size') or 10.0
    h = max(y1 - y0, 1.0)
    for x, top, bottom in verticals:
        if x < x1 - 1.0 or x - x1 > TUCK_EM * size:
            continue
        if bottom < y0 + TUCK_ROW_OVERLAP * h or top > y1 - TUCK_ROW_OVERLAP * h:
            continue
        return True
    return False


def right_alignment_warnings(segments, obstacles=None):
    """Propose cores that look right-aligned. Never applied automatically.

    Only `center` existed, so a right-aligned label grew rightward past its
    original right edge: "Total" at x1 540 became "Gesamt" ending at 555.3,
    over the rule it was tucked against.

    A shared right edge with spread left edges is also the shape of a
    justified paragraph with an indented first line, a hanging indent and
    a table of contents: that shape alone proposed 11,507 segments for
    "right" on the wild corpus (row 21). What makes a label column is what
    it sits against — a rule, a field or the next segment starting within
    one em of the edge — so a group is proposed only when at least half
    its members are tucked like that. `obstacles` maps page ->
    [(x, top, bottom)] from page_obstacles(); every segment's left edge
    counts as well. With nothing to sit against, nothing is proposed.
    """
    warnings = []
    by_page = {}
    all_by_page = {}
    for s in segments:
        all_by_page.setdefault(s['page'], []).append(s)
        if s['passthrough']:
            continue
        by_page.setdefault(s['page'], []).append(s)
    for page, segs in sorted(by_page.items()):
        verticals = list((obstacles or {}).get(page, []))
        verticals.extend((o['bbox'][0], o['bbox'][1], o['bbox'][3])
                         for o in all_by_page.get(page, []))
        buckets = {}
        for s in segs:
            buckets.setdefault(round(s['bbox'][2] / RIGHT_EDGE_TOL), []).append(s)
        for _, group in sorted(buckets.items()):
            if len(group) < 2:
                continue
            lefts = [s['bbox'][0] for s in group]
            if max(lefts) - min(lefts) <= RIGHT_LEFT_SPREAD:
                continue
            tucked = sum(1 for s in group if _tucked(s, verticals))
            if tucked < max(1, (len(group) + 1) // 2):
                continue
            warnings.append({
                'page': page,
                'kind': 'right-aligned',
                'ids': [s['id'] for s in group],
                'right_edge': round(max(s['bbox'][2] for s in group), 2),
                'why': 'these share a right edge but not a left one, and sit '
                       'against a rule, a field or the next segment: a '
                       'right-aligned column. A longer translation anchored '
                       'left grows past that edge — list these cores in "right"',
                'lines': [s['core'] for s in group],
            })
    return warnings


def parse_pages(spec, npages):
    """"2", "1-4", "1-4,9,12-" -> a sorted set of 0-based page indices.

    1-based and inclusive, the way a person reads a page number. An empty
    or missing spec means every page.
    """
    if not spec:
        return set(range(npages))
    out = set()
    for part in str(spec).split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            a, _, b = part.partition('-')
            start = int(a) if a.strip() else 1
            end = int(b) if b.strip() else npages
        else:
            start = end = int(part)
        for n in range(start, end + 1):
            if 1 <= n <= npages:
                out.add(n - 1)
    return out


IMAGE_REVIEW_MIN_AREA = 400.0


def image_region_warnings(doc, wanted):
    """List image regions per page as review items.

    Nothing in this pipeline can translate pixels. A banner, a masthead, a
    seal or a "SAMPLE" stamp with words baked into it survives untouched
    and looks deliberate — the page renders perfectly and every gate is
    green. The reader still meets the source language. Listing the regions
    is the whole fix: a person looks at each one and decides.
    """
    warnings = []
    for pno, page in enumerate(doc):
        if pno not in wanted:
            continue
        for info in page.get_image_info() or []:
            bbox = info.get('bbox')
            if not bbox:
                continue
            x0, y0, x1, y1 = bbox
            if (x1 - x0) * (y1 - y0) < IMAGE_REVIEW_MIN_AREA:
                continue
            warnings.append({
                'page': pno,
                'kind': 'image-region',
                'bbox': [round(v, 2) for v in bbox],
                'why': 'an image this size can carry words (banner, seal, '
                       'stamp, screenshot). Nothing here translates pixels: '
                       'look at it, and tell the user if text stays in the '
                       'source language',
            })
    return warnings


log = logging.getLogger(__name__)


def run_extract(src, outdir, *, typography=False, gap=12.0, pages=None):
    """Extract geometry and unique cores. Silent. Returns an ExtractResult.

    `outdir` is **required**, unlike `extract_segments`'s `outdir='.'`. The
    default there means "wherever this process happens to be", which is the
    same class of defect as `Archive('.')` and the one C1 forbids — a service
    must not have its output location decided by its working directory. The
    old default stays on the loud function, where the CLI depends on it.

    The thinnest of the seven twins, and the plan says so rather than leaving
    the asymmetry to be discovered: `extract_segments` never printed and
    already returned its refusal reasons as data (`unextractable_pages`,
    `invisible_text_pages`). This is a shape adapter so a consumer learns one
    result type instead of one dict schema per stage. The dict is not going
    anywhere — `extract_segments` keeps returning it.
    """
    result = extract_segments(src, outdir=outdir, gap=gap, pages=pages,
                              typography=typography)
    return ExtractResult(
        segments=tuple(result.get('segments') or ()),
        cores=tuple(result.get('cores') or ()),
        warnings=tuple(result.get('warnings') or ()),
        document=result.get('document') or {},
        pages=len(result.get('pages') or ()),
        unextractable_pages=tuple(result.get('unextractable_pages') or ()),
        invisible_text_pages=tuple(result.get('invisible_text_pages') or ()),
        segments_path=result.get('segments_path') or '',
        to_translate_path=result.get('to_translate_path') or '',
        widget_text_path=result.get('widget_text_path') or '',
        widget_text_kept=bool(result.get('widget_text_kept')),
        typography=result.get('typography'),
    )


def extract_segments(src, outdir='.', gap=12.0, pages=None, *, typography=False):
    """Extract geometry + unique cores. Writes segments.json and to_translate.json."""
    os.makedirs(outdir, exist_ok=True)
    doc = pymupdf.open(src)
    wanted = parse_pages(pages, len(doc))
    if typography:
        from . import typography as typo
        font_observations = typo.observe_fonts(doc)
        typography_data = {'schema': 1, 'source_sha256': typo.source_digest(src),
                           'options': {'gap': float(gap), 'pages': sorted(wanted)},
                           'page_geometry': typo.page_geometry(doc),
                           'fields': typo.field_identities(doc),
                           'fonts': font_observations['fonts']}
    segments, warnings = [], []
    obstacles = {}
    raw_lines = {}   # page -> character lines, read only where a marker needs one
    # Every coordinate below is in the unrotated page space get_text uses.
    # A consumer that draws over a render maps a rotated page's coordinates
    # through page.rotation_matrix; these entries say which pages need it.
    rotated_pages = {}
    sid = 0
    for pno, page in enumerate(doc):
        if pno not in wanted:
            continue
        if page.rotation:
            frame = page.rect * page.derotation_matrix
            rotated_pages[str(pno)] = {'rotation': page.rotation,
                                       'width': round(frame.width, 2),
                                       'height': round(frame.height, 2)}
        obstacles[pno] = page_obstacles(page)
        for b in page_textdict_without_annots(page)['blocks']:
            if b['type'] != 0:
                continue
            for line in b['lines']:
                # Line direction as a unit vector, (1, 0) for ordinary rows.
                # Gap-splitting is a horizontal idea: on a rotated line the
                # x distance between spans means nothing, so a rotated line
                # stays one group.
                ldir = tuple(line.get('dir') or (1.0, 0.0))[:2]
                if len(ldir) != 2:
                    ldir = (1.0, 0.0)
                rotated = abs(ldir[0] - 1.0) > 1e-6 or abs(ldir[1]) > 1e-6
                groups = []
                for s in line['spans']:
                    if not typography and not s['text'].strip() and not groups:
                        continue
                    if groups and (rotated
                                   or s['bbox'][0] - groups[-1]['bbox'][2] < gap):
                        g = groups[-1]
                        g['text'] += s['text']
                        # On a +x line each span starts right of the last, so
                        # the first span's left edge is the line's. A line
                        # that advances any other way (upside down, or at
                        # 135 or 225 degrees) puts later spans to the LEFT,
                        # and keeping the first span's edge dropped them.
                        g['bbox'] = [min(g['bbox'][0], s['bbox'][0]) if rotated
                                     else g['bbox'][0],
                                     min(g['bbox'][1], s['bbox'][1]),
                                     max(g['bbox'][2], s['bbox'][2]),
                                     max(g['bbox'][3], s['bbox'][3])]
                        g['bold'] = g['bold'] or ('Bold' in s['font'])
                        g['italic'] = g['italic'] or (
                            'Italic' in s['font'] or 'Oblique' in s['font'])
                        if typography:
                            g['spans'].append(s)
                    else:
                        groups.append({
                            'text': s['text'],
                            'bbox': list(s['bbox']),
                            'origin': [s['origin'][0], s['origin'][1]],
                            'size': s['size'],
                            'color': s.get('color', 0),
                            'bold': 'Bold' in s['font'],
                            'italic': ('Italic' in s['font']
                                       or 'Oblique' in s['font']),
                            'dir': [round(ldir[0], 4), round(ldir[1], 4)],
                            **({'spans': [s]} if typography else {}),
                        })
                for g in groups:
                    if not g['text'].strip():
                        continue
                    text = g['text'].strip()
                    m = MARKER.match(text)
                    # `gap` is this function's split threshold; the marker's
                    # whitespace gets its own name. Shadowing it once made
                    # every multi-span line after a list item raise on the
                    # wild corpus while the single-span fixtures stayed green.
                    marker, core, marker_gap = '', text, ''
                    if m:
                        marker = m.group(0).strip()
                        # The whitespace the source printed between marker
                        # and body, verbatim. retypeset re-emits it; until
                        # row 19 it wrote one space whatever the source had,
                        # and a two-space list body moved 3.06 pt left.
                        marker_gap = text[len(marker):m.end()]
                        core = text[m.end():].strip()
                    dm = DOTS.match(core)
                    dots, tail = None, ''
                    if dm:
                        core, dots, tail = (
                            dm.group(1).strip(), dm.group(2), dm.group(3).strip())
                    body_dx = None
                    if marker and core:
                        if pno not in raw_lines:
                            raw_lines[pno] = raw_char_lines(page)
                        body_dx = body_offset(g, marker, marker_gap, core,
                                              raw_lines[pno])
                    seg = {
                        'id': sid, 'page': pno,
                        'bbox': [round(v, 2) for v in g['bbox']],
                        'origin': [round(g['origin'][0], 2),
                                   round(g['origin'][1], 2)],
                        'size': round(g['size'], 2),
                        # sRGB int; retypeset re-applies it. Losing this is
                        # how white-on-dark headings turn black-on-dark —
                        # a defect the ink-density gate cannot see.
                        'color': int(g.get('color', 0)),
                        'bold': g['bold'], 'italic': g['italic'],
                        # Line direction: retypeset rotates the run about
                        # this origin. Without it a 90-degree side label is
                        # re-typeset flat across the page.
                        'dir': g.get('dir', [1.0, 0.0]),
                        'text': g['text'], 'marker': marker,
                        'gap': marker_gap, 'core': core,
                        **({'body_dx': body_dx} if body_dx is not None else {}),
                        'dots': dots or '', 'tail': tail,
                        'passthrough': bool(not core or PASS.match(core)),
                    }
                    if typography:
                        occurrence_id = f's{sid}'
                        candidates = {font_id: font_observations['fonts'][font_id]
                                      for font_id in font_observations['pages'][pno]}
                        runs = typo.source_runs(g['spans'], occurrence_id, candidates)
                        seg.update({'occurrence_id': occurrence_id, 'style_runs': runs})
                        for run in runs:
                            if run['unresolved']:
                                warnings.append({'kind': 'typography', 'id': sid,
                                                 'page': pno, 'text': g['text'],
                                                 'run_id': run['run_id'],
                                                 'why': ', '.join(run['unresolved'])})
                    if INNER_GAP.search(g['text']):
                        warnings.append({
                            'id': sid, 'page': pno,
                            'kind': 'inner-gap',
                            'text': g['text'][:80],
                            'why': 'run of >=6 spaces inside one span; '
                                   'likely a mid-line gap — use an override',
                        })
                    for kind, snippet in write_find_say_hits(g['text']):
                        warnings.append({
                            'id': sid, 'page': pno,
                            'kind': 'write-find-say',
                            'hit': kind,
                            'text': snippet,
                            'why': 'write/find/say candidate — confirm before '
                                   'translating (reader may have to write, '
                                   'hand over, or search for this)',
                        })
                    segments.append(seg)
                    sid += 1

    unextractable_pages = []
    for pno, page in enumerate(doc):
        if pno not in wanted:
            continue
        if any(s['page'] == pno for s in segments):
            continue
        if page_has_visible_content(page):
            unextractable_pages.append(pno)
            warnings.append({
                'page': pno,
                'kind': 'no-text-layer',
                'why': 'visible ink or an embedded image but no extractable '
                       'text — this looks scanned. Scans are out of scope with or '
                       'without an OCR layer; do not ship an untranslated file',
            })
    # Collected while `doc` is still open, so the source is opened once
    # instead of twice; the warnings are still appended in their original
    # place below, so the order of `result.warnings` is unchanged. The
    # `finally` is why this is safe: the second open it replaced had one, and
    # without it a raising image scan would leave the source handle open.
    try:
        image_warnings = image_region_warnings(doc, wanted)
    finally:
        doc.close()

    warnings.extend(merge_candidate_warnings(segments))
    warnings.extend(narrow_column_warnings(segments))
    warnings.extend(right_alignment_warnings(segments, obstacles))

    # Only the pages asked for are judged; the strip it compares against is
    # still of the whole document, which is what makes the verdicts the same.
    invisible = invisible_text_pages(src, pages=wanted)
    for pno, fraction in invisible:
        warnings.append({
            'page': pno,
            'kind': 'invisible-text',
            'fraction': fraction,
            'why': 'the text layer is invisible (an OCR layer over a scanned '
                   'image, or text hidden under an image): stripping it '
                   'changes nothing the reader sees. The visible words are '
                   'pixels; do not ship',
        })

    document = document_strings(src)
    warnings.extend(image_warnings)

    uniq = {}
    for s in segments:
        if not s['passthrough']:
            uniq[s['core']] = uniq.get(s['core'], 0) + 1
    # The document title and every outline entry are read by the window
    # caption, the bookmarks pane and every screen reader. They are cores.
    for extra in [document['title']] + document['outline']:
        if extra and not PASS.match(extra):
            uniq.setdefault(extra, 0)
            uniq[extra] += 1

    seg_path = os.path.join(outdir, 'segments.json')
    to_path = os.path.join(outdir, 'to_translate.json')
    widget_path = os.path.join(outdir, 'widget_text.json')
    widget_text = widget_text_scaffold(src)
    # The author fills widget_text.json in place and passes it back through
    # `pipeline.py init --widget-text`, which extracts again into the same
    # directory. A fresh scaffold written over it would lose every target.
    widget_kept = widget_text_is_authored(widget_path)
    segment_data = {'source': src, 'segments': segments, 'warnings': warnings,
                    'document': document, 'pages': sorted(wanted),
                    'geometry': {'space': GEOMETRY_SPACE,
                                 'rotated_pages': rotated_pages}}
    if typography:
        segment_data['typography'] = typography_data
        typography_data = typo.bind_extraction(segment_data)
        segment_data['typography'] = typography_data
    if not widget_kept:
        with open(widget_path, 'w', encoding='utf-8') as f:
            json.dump(widget_text, f, ensure_ascii=False, indent=1)
    with open(seg_path, 'w', encoding='utf-8') as f:
        json.dump(segment_data, f, ensure_ascii=False, indent=1)
    cores = [{'text': k, 'count': v} for k, v in uniq.items()]
    to_data = {'cores': cores}
    if typography:
        to_data.update(format='typography-1', segments='segments.json',
                       extraction_id=typography_data['extraction_id'])
    with open(to_path, 'w', encoding='utf-8') as f:
        json.dump(to_data, f, ensure_ascii=False, indent=1)
    return {
        'segments': segments,
        'warnings': warnings,
        'cores': cores,
        'document': document,
        'pages': sorted(wanted),
        'unextractable_pages': unextractable_pages,
        'invisible_text_pages': invisible,
        'segments_path': seg_path,
        'to_translate_path': to_path,
        'widget_text': widget_text,
        'widget_text_path': widget_path,
        'widget_text_kept': widget_kept,
        **({'typography': typography_data} if typography else {}),
    }


# What each warning kind asks of the author, printed once with a count.
# The old printout was one line per warning: 2,394 lines on a 126-page
# booklet, 1,093 of them write/find/say hits the skill said to confirm one
# at a time, and the one that mattered scrolled past (dev/wild/ANALYSIS.md
# section 4). Only inner-gap needs an entry each; the rest are decisions
# about a list. Every warning stays in segments.json.
WARNING_ASKS = [
    ('inner-gap', 'one "overrides" entry each, with explicit x per part'),
    ('narrow-column', 'one decision per stack: one merge, or one override with max_width'),
    ('right-aligned', 'a label column tucked against a rule or field: list those cores in "right"'),
    ('image-region', 'look at each; nothing here translates pixels — say what stays in the source language'),
    ('merge-candidate', 'accept in bulk with pipeline.py propose-merges, then delete the sibling-list entries'),
    ('write-find-say', 'confirm the list: each stays verbatim unless listed in allow_translate, and verify fails a dropped one'),
    ('no-text-layer', 'a refusal: the page is a scan'),
    ('invisible-text', 'a refusal: the words the reader sees are pixels'),
]
DEFAULT_MAX_PER_KIND = 10


def print_warning_digest(warnings, max_per_kind=DEFAULT_MAX_PER_KIND):
    """Per-kind counts and what each kind asks, then at most max_per_kind
    lines per kind. Nothing is dropped from the JSON."""
    by_kind = {}
    for w in warnings:
        by_kind.setdefault(w.get('kind') or 'other', []).append(w)
    asks = dict(WARNING_ASKS)
    order = [k for k, _ in WARNING_ASKS if k in by_kind]
    order += sorted(k for k in by_kind if k not in asks)
    log.info(f'WARNINGS: {len(warnings)} in {len(by_kind)} kind(s); every one is in '
          f'segments.json under "warnings" (--max-per-kind N lists more here):')
    width = max(len(k) for k in order)
    for k in order:
        log.info(f'  {k:{width}s} {len(by_kind[k]):5d}  {asks.get(k, "review")}')
    for k in order:
        items = by_kind[k]
        log.info(f'{k}:')
        for w in items[:max_per_kind]:
            preview = (w.get('text') or (w.get('lines') or [''])[0]
                       or w.get('why') or '')
            pid = w.get('id', ','.join(str(i) for i in w.get('ids', [])))
            log.info(f"  p{w['page']} seg {pid} [{k}]: {preview}")
        if len(items) > max_per_kind:
            log.info(f'  … {len(items) - max_per_kind} more {k} in segments.json')


def main(argv=None):
    with console():
        return _main(argv)


def _main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    from ._console import missing_option_value
    missing = missing_option_value(argv, ('--gap', '--outdir', '--pages', '--max-per-kind'))
    if missing:
        log.info(f'usage: {missing} requires a value')
        return 2
    src = argv[0]
    gap = float(_arg(argv, '--gap', 12.0))
    outdir = _arg(argv, '--outdir', '.')
    pages = _arg(argv, '--pages')
    max_per_kind = int(_arg(argv, '--max-per-kind', DEFAULT_MAX_PER_KIND))
    result = extract_segments(src, outdir=outdir, gap=gap, pages=pages,
                              typography='--typography' in argv)
    nseg = len(result['segments'])
    nuniq = len(result['cores'])
    nw = len(result['warnings'])
    nwidget = len(result['widget_text'])
    log.info(f'{nseg} segments, {nuniq} unique strings to translate, '
          f'{nw} warnings -> segments.json / to_translate.json')
    if result['widget_text_kept']:
        log.info(f'kept {result["widget_text_path"]}: it holds authored widget '
              f'text, so no fresh scaffold was written over it (delete it for one)')
    elif nwidget:
        log.info(f'{nwidget} field(s) carry widget text (tooltips, dropdown '
              f'labels, defaults) -> widget_text.json; author each "target" '
              f'and pass it to strip_text.py --widget-text')
    if result['warnings']:
        print_warning_digest(result['warnings'], max_per_kind)
    rc = 0
    if result.get('unextractable_pages'):
        pages = ', '.join(str(p) for p in result['unextractable_pages'])
        log.info(f'FAIL: pages with visible content but no extractable text: {pages}')
        log.info('  This looks scanned. The pipeline cannot translate images, and an '
              'OCR layer would not help (it is refused too); say so.')
        rc = 1
    if result.get('invisible_text_pages'):
        pages = ', '.join(f'{p} ({f:.1%} of text area changes)'
                          for p, f in result['invisible_text_pages'])
        log.info(f'FAIL: pages whose text layer is invisible: {pages}')
        log.info('  This looks like an OCR\'d scan (image + invisible OCR text layer). '
              'The words the reader sees are pixels; strip-and-retypeset would print '
              'the translation over them. This pipeline has no masking mode; do not ship.')
        rc = 1
    return rc


if __name__ == '__main__':
    raise SystemExit(main())
