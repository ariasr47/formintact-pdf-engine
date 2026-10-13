#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 4 of pdf-translate: re-typeset translated text onto the stripped PDF.

Consumes segments.json (from extract_segments.py) + translations.json
(authored by you — format documented in references/translations-format.md)
and writes the translated PDF. All layout mechanics live here so every
document gets the same behavior:

- single-line segments -> TextWriter at the ORIGINAL baseline origin
- a list marker is re-emitted in Helvetica followed by the whitespace the
  source printed after it (segment "gap"; one space when a segments.json
  predates the key), and the body starts at the measured offset the
  source's own characters had (segment "body_dx"), because Helvetica's
  advance for the marker is only right for a Helvetica-metric source;
  without that key the body starts at Helvetica's width of marker + gap
- shrink-to-fit bounded by the nearest same-row obstacle (next segment or
  widget rect) so text never overlaps fields or neighboring cells
- dot leaders refilled anchored to the RIGHT at most the original run width
  (keeps leaders out of mid-line checkbox gaps), including on labels drawn
  by the Story engine (shaped scripts) or right-to-left
- '‖' in a translation splits a bold lead-in from a regular remainder
- declared merges rendered as re-flowed paragraphs via insert_htmlbox
  with auto-shrink (scale_low=0); a merge whose html is null is an
  accepted proposal nobody wrote yet, and FAILs like a null translation
- centered strings re-centered on the original bbox midpoint; strings in
  "right" re-anchored on the original bbox RIGHT edge, so a longer
  translation grows leftward instead of past the rule it sat against
- four font roles (regular, bold, italic, bold-italic) chosen from the
  source span's flags; a single-line target may carry inline <b>/<i>
  (or <strong>/<em>) and is then placed through the Story engine
- the compliance notice, if the job needs one, placed from "notices" into
  the box the author chose — the only text here that is not the
  translation of an existing string, and it goes through the same glyph
  check, canonical layer, scale report and placement gate as every run
- pass-through segments re-inserted verbatim in Helvetica
- every non-passthrough segment lacking a translation is reported; the run
  FAILS (exit 1) if any exist — missing text must never ship silently
- any run scaled below 0.7× FAILS unless its core (or merge first line) is
  in allow_scale — tiny type must not ship as a note
- opt in with capture_dir= (or PDF_TRANSLATE_CAPTURE_DIR) and a below-the-floor
  FAIL also writes a replayable evidence bundle; off by default and a capture
  problem never changes the refusal (capture.py)
- document metadata is retargeted: /Lang (and dc:language in XMP) from
  translations.json's "lang", /Title and every outline title from the
  mapping (both are cores), and the orphaned /StructTreeRoot is removed
  with /MarkInfo /Marked false, because the tags describe text that no
  longer exists
- every character of every placed run is checked against the exact font
  object that will draw it; a character the font lacks FAILS, because
  MuPDF substitutes a fallback face mid-string (or draws a box) and
  reports nothing
- RTL targets (Hebrew/Arabic) are written with right_to_left and wrapped in
  /ActualText so the logical string is in the text layer; layout stays LTR
  unless translations.json sets mirror: true (opt-in x-flip of text +
  field/link rects; graphics stay)
- runs whose target script needs shaping (Arabic-family, Indic, Thai, Lao,
  Khmer, Myanmar, Tibetan) are placed with the Story engine
  (insert_htmlbox, HarfBuzz) on the original baseline, at the original
  size and colour, then wrapped in /ActualText; TextWriter would draw
  them letter by letter (isolated Arabic forms, broken conjuncts)
- rotated lines (side labels, stamps) keep their angle: the run is written
  horizontally and morphed about its own origin by the segment's recorded
  direction, so origin, bbox and line direction match the source run. The
  width budget runs along that direction. The RTL mirror is skipped on a
  rotated run, its dot leaders are dropped (the tail after them is kept,
  and the result's warnings say so), and right, center, an override,
  inline markup or a merge asked of it is refused by name; a rotated run
  whose target also needs SHAPING is refused rather than drawn flat

Usage:
  python3 retypeset.py STRIPPED.pdf segments.json translations.json OUT.pdf
"""
import html as htmlmod
from dataclasses import dataclass
import json
import logging
import math
import os
import re
import sys
import tempfile
import time
import unicodedata
from pathlib import Path, PurePath

import pymupdf

from . import capture
from ._console import console, _arg, configure_cli_streams
from ._lang import write_language
from .results import (FontError, GlyphError, MappingError, PdfTranslateError,
                      PlacementError, RetypesetResult)
from .mapping import FORMAT, charset_for, load_mapping, refuse
from .strip_text import GEOMETRY_SPACE
from .source_retention import (attach as attach_source_retention,
                               prepare as prepare_source_retention,
                               replay_unmapped, verify_final as verify_retained_output,
                               RetentionCancelled, UNMAPPED)
from .typography import field_identities, page_geometry, source_digest, validate_font

# `python -m` runs this module as __main__; keep its log under console().
log = logging.getLogger('pdf_translate.retypeset')

SCALE_MIN = 0.7
# A notice the author did not size. Small enough for a footer line, big
# enough to read; `notices[].size` overrides it.
NOTICE_SIZE = 8.0
NOTICE_LH = 1.25
# Written beside the output on every successful build, so verify and a
# delivery can read what shrank without re-running retypeset. Pages are
# 0-based, the same as translations.json's merges[].page.
SCALE_REPORT = 'scale_report.json'
# Every refusal list the console prints stops here. The exception carries
# them whole, so this is a reading limit, not a data limit.
CONSOLE_LIST = 30
# Rebuilds allowed when the delivered file cannot vouch a retained run. Each
# one redraws one more run; past this the refusal stands (a rebuild costs a
# full placement pass).
_RETAINED_OUTPUT_RETRIES = 8


def more_not_shown(items, shown=CONSOLE_LIST):
    """`  … N more not shown` when a console list was cut, else nothing.

    The `scaled runs` listing names SCALE_REPORT at this point, because it
    writes that file on its next statement. A refusal cannot borrow the
    wording: it raises before `write_scale_report` is ever reached, and a
    report outliving a refused write is the failure
    `verify._remove_stale_report` exists to prevent - a missing report any
    consumer can detect, a stale one none can. So the count is stated and no
    file is promised.
    """
    extra = len(items) - shown
    return [f'  … {extra} more not shown'] if extra > 0 else []


def refusal_tail(*lists):
    """One line for the whole block when anything was cut, else nothing."""
    total = sum(max(len(x) - CONSOLE_LIST, 0) for x in lists)
    if not total:
        return []
    # Said once, not per list. The console is for a person; a consumer
    # calling run_retypeset already holds every entry, unsliced.
    return [f'  ({total} hidden in total — a caller using run_retypeset '
            f'gets every entry in refusals)']

# Sentinel: `scale_report=None` means WRITE NOTHING, which is a different
# instruction from "you did not say". Only the default writes the legacy
# fixed name beside the output.
_DEFAULT = object()
# A merge is drawn a hair under source size so the Story engine rarely has to
# shrink a paragraph that already fits its box. It is a fit allowance, not a
# free ride: it makes the delivered text smaller than the source, so it is
# part of the ratio the report carries (see merge_css_size).
MERGE_FIT = 0.98


def merge_css_size(size):
    """The size a merge is really drawn at, and its ratio to the source.

    The CSS carries one decimal, so the rounding is part of what the page
    shows: 9.0 becomes 8.8, not 8.82. Returning both keeps the string that
    is drawn and the ratio that is reported derived from one number.
    """
    css = round(size * MERGE_FIT, 1)
    return css, (css / size if size else 1.0)


# Hebrew, Arabic, Syriac, Thaana, Arabic supplement/presentation forms.
_RTL_RANGES = (
    (0x0590, 0x08FF),
    (0xFB1D, 0xFDFF),
    (0xFE70, 0xFEFC),
)


def seg_dir(seg):
    """Unit line direction of a segment; (1, 0) when absent or unusable."""
    d = seg.get('dir') or (1.0, 0.0)
    try:
        dx, dy = float(d[0]), float(d[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return 1.0, 0.0
    n = math.hypot(dx, dy)
    if n < 1e-9:
        return 1.0, 0.0
    return dx / n, dy / n


def is_rotated(dx, dy):
    return abs(dx - 1.0) > 1e-6 or abs(dy) > 1e-6


def rotation_morph(x, y, dx, dy):
    """(pivot, matrix) that turns a horizontal run into direction (dx, dy).

    Verified against extraction: text written horizontally at (x, y) and
    morphed this way comes back with the same origin, the same bbox and
    the same line dir as the source run it replaces.
    """
    return (pymupdf.Point(x, y), pymupdf.Matrix(dx, -dy, dy, dx, 0, 0))


def fitted_size(fs, width, room):
    """The size at which a run `width` points wide at `fs` fits `room`.

    Never larger than the source, and with no floor: until v65 every shrink
    was lifted to 4.0 pt, so a run from a source of 5.71 pt or less drew
    past its room onto its neighbour, the 0.7x gate saw the lifted ratio,
    and a source under 4 pt was drawn larger than it was (R-02). The ratio
    fs2 / fs is now what the page shows. A room of 1 pt or less is taken as
    1 pt, the guard the other budgets use, so a run never gets a size of 0.
    """
    if width <= room or width <= 0:
        return fs
    return fs * max(room, 1.0) / width


def unrotated_frame(page):
    """The page's rectangle in the space this module draws and measures in.

    page.rect is the page as a viewer shows it: 792 x 612 for a portrait
    page with /Rotate 90. segments.json, widget rects, TextWriter and
    insert_htmlbox all use the unrotated space instead, so every width
    budget and mirror pivot is measured against this frame. It equals
    page.rect when the page is not rotated.
    """
    return page.rect * page.derotation_matrix


def direction_limit(frame, seg, dx, dy, margin=16.0):
    """Room from the segment origin to the frame's edge ALONG its direction.

    right_limit's obstacle scan is a horizontal idea (same-row neighbours,
    widget rects to the right). Projecting every neighbour onto an
    arbitrary axis is a different problem, and a rotated run is nearly
    always a margin label or a stamp with nothing beyond it. The page edge
    is the budget; the 0.7x gate still refuses a translation that cannot
    fit in it. `frame` is unrotated_frame(page).
    """
    x, y = seg['origin']
    r = frame
    limits = []
    if dx > 1e-9:
        limits.append((r.x1 - margin - x) / dx)
    elif dx < -1e-9:
        limits.append((x - r.x0 - margin) / -dx)
    if dy > 1e-9:
        limits.append((r.y1 - margin - y) / dy)
    elif dy < -1e-9:
        limits.append((y - r.y0 - margin) / -dy)
    return max(1.0, min(limits)) if limits else 1.0


def is_rtl_text(text):
    """True if text contains Hebrew/Arabic-block letters."""
    for ch in text or '':
        o = ord(ch)
        for a, b in _RTL_RANGES:
            if a <= o <= b:
                return True
    return False


# Scripts whose letters must be shaped before they are drawn: joining
# (Arabic family), conjuncts and mark placement (Indic, Thai, Lao, Khmer,
# Myanmar, Tibetan). Hebrew only needs direction and stays on TextWriter.
_SHAPING_RANGES = (
    (0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF),   # Arabic
    (0xFB50, 0xFDFF), (0xFE70, 0xFEFC),                      # Arabic presentation forms
    (0x0700, 0x074F),                                        # Syriac
    (0x07C0, 0x07FF),                                        # N'Ko
    (0x0900, 0x0DFF),                                        # Devanagari .. Sinhala
    (0x0E00, 0x0EFF),                                        # Thai, Lao
    (0x0F00, 0x0FFF),                                        # Tibetan
    (0x1000, 0x109F),                                        # Myanmar
    (0x1780, 0x17FF),                                        # Khmer
)
# Measured on insert_htmlbox with line-height 1: a single line's baseline sits
# 0.8 x size below the rect top for every font tried, and the run starts at
# the rect's left edge. A rect 1.25 x size tall holds one line; a second line
# only fits once the engine has scaled below the 0.7x gate, which refuses.
SHAPED_BASELINE = 0.8
SHAPED_LINE = 1.25


def needs_shaping(text):
    """True if text contains letters of a script that must be shaped."""
    for ch in text or '':
        o = ord(ch)
        for a, b in _SHAPING_RANGES:
            if a <= o <= b:
                return True
    return False


def _family(bold, italic):
    return ('trbi' if bold else 'tri') if italic else ('trb' if bold else 'tr')


def place_story_line(page, x, baseline, inner_html, bold, italic, fs,
                     color_int, avail, css, arch, logical=None):
    """Draw one line through the Story engine at the ORIGINAL baseline.

    Used for two jobs a TextWriter cannot do: scripts that need shaping
    (joining, conjuncts) and single-line targets that carry inline <b>/<i>.
    inner_html is already-built markup, so callers escape their own text.
    """
    top = baseline - SHAPED_BASELINE * fs
    rect = pymupdf.Rect(x, top, x + max(avail, 1.0) + 1.0, top + SHAPED_LINE * fs)
    body = (f'<div style="font-family:{_family(bold, italic)}; '
            f'font-size:{fs:.2f}px; line-height:1; '
            f'color:#{color_int:06x}; margin:0; padding:0">{inner_html}</div>')
    _, scale = page.insert_htmlbox(rect, body, css=css, archive=arch, scale_low=0)
    if logical:
        wrap_last_stream_actualtext(page, logical)
    return scale


def place_shaped(page, x, baseline, text, bold, fs, color_int, avail, css,
                 arch, italic=False):
    """Draw one shaped run with the Story engine; returns the scale applied."""
    return place_story_line(page, x, baseline, story_text(text), bold,
                            italic, fs, color_int, avail, css, arch,
                            logical=text)


def _actualtext_bdc(text):
    raw = b'\xfe\xff' + (text or '').encode('utf-16-be')
    return b'/Span << /ActualText <' + raw.hex().encode('ascii') + b'> >> BDC\n'


def wrap_last_bt_actualtext(page, logical):
    """Mark the most recent content stream's BT..ET with /ActualText."""
    if not logical:
        return
    xrefs = page.get_contents()
    if not xrefs:
        return
    xref = xrefs[-1]
    data = page.parent.xref_stream(xref) or b''
    i = data.find(b'BT')
    if i < 0:
        return
    j = data.find(b'ET', i)
    if j < 0:
        return
    if b'/ActualText' in data[i:j]:
        return
    k = i + 2
    if k < len(data) and data[k:k + 1] in (b'\n', b'\r', b' '):
        k += 1
        if data[k - 1:k + 1] == b'\r\n':
            k += 1
    new = data[:k] + _actualtext_bdc(logical) + data[k:j] + b'EMC\n' + data[j:]
    page.parent.update_stream(xref, new)


def wrap_last_stream_actualtext(page, logical):
    """Mark the page's newest content stream with /ActualText.

    The Story engine (insert_htmlbox) draws into a Form XObject and appends
    a stream that only invokes it (q /fzFrmN Do Q); the glyphs it leaves in
    the text layer are presentation forms or glyph ids. Wrapping that
    invocation in a marked-content span makes extraction report the logical
    string instead, which is what the placement gate reads.
    """
    if not logical:
        return
    xrefs = page.get_contents()
    if not xrefs:
        return
    xref = xrefs[-1]
    data = page.parent.xref_stream(xref) or b''
    if b'/ActualText' in data:
        return
    tail = (chr(10) + 'EMC' + chr(10)).encode('ascii')
    page.parent.update_stream(xref, _actualtext_bdc(logical) + data + tail)


# Inline weight/style markup allowed in a SINGLE-LINE target, the way
# merges already allow it. Deliberately narrow: only these tags count as
# markup, so a translation that genuinely contains "<" is left alone.
INLINE_TAGS = re.compile(r'</?(?:b|i|em|strong)\s*/?>', re.I)


def has_inline_markup(text):
    return bool(INLINE_TAGS.search(text or ''))


def strip_inline_markup(text):
    return INLINE_TAGS.sub('', text or '')


def story_text(text):
    """`text` escaped for a Story text node, so it lands as written.

    MuPDF 1.28.2's Story engine decodes character references twice:
    `&amp;copy;` is drawn as ©, and `&amp;amp;` as &. So `&` is escaped
    twice and `<` and `>` once. tests.test_pipeline pins this with a round
    trip, so a MuPDF that decodes once fails loudly instead of drawing
    `&amp;` (R-06).
    """
    return htmlmod.escape(text or '', quote=False).replace('&amp;', '&amp;amp;')


def inline_markup_html(text):
    """`text` as HTML for the Story engine: the tags INLINE_TAGS names stay
    markup and everything between them is escaped, so a literal `<`, `&` or
    an unlisted tag reaches the page as the author wrote it. Sent raw, an
    address in angle brackets vanished, `&copy;` became ©, and `<span>` was
    swallowed (R-06)."""
    text = text or ''
    out, last = [], 0
    for m in INLINE_TAGS.finditer(text):
        out.append(story_text(text[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(story_text(text[last:]))
    return ''.join(out)


def _role_name(bold, italic):
    if bold and italic:
        return 'bold-italic'
    return 'bold' if bold else ('italic' if italic else 'regular')


def _font_label(font, labels):
    return labels.get(id(font), 'font')


def missing_glyphs(font, text):
    """Characters of text this font cannot draw.

    MuPDF does not report a missing glyph: it silently substitutes its own
    fallback face mid-string, and draws a box when the fallback has nothing
    either. Both look like a rendering choice, not a defect, so nothing
    downstream catches them — the ink gate barely moves and the text layer
    reads correctly. Control and format characters (soft hyphen, ZWJ,
    newlines) are not drawn and are not counted.
    """
    out = []
    seen = set()
    for ch in text or '':
        if ch in seen or ch in '\n\r\t':
            continue
        if unicodedata.category(ch) in ('Cc', 'Cf'):
            continue
        seen.add(ch)
        if not font.has_glyph(ord(ch)):
            out.append(ch)
    return out


# --------------------------------------------------------- document metadata
_DC_LANG = re.compile(r'(<dc:language>)(.*?)(</dc:language>)', re.S)
_RDF_LI = re.compile(r'(<rdf:li[^>]*>)(.*?)(</rdf:li>)', re.S)


def retarget_xmp(xml, lang):
    """Point every dc:language entry at the target tag. Returns (xml, changed).

    The Info dictionary and /Lang are rewritten from the mapping; XMP that
    still claims the source language contradicts both, and readers and
    catalogues believe XMP first. Everything else in the packet is left
    alone: it is the producer's, not ours.
    """
    if not xml or not lang:
        return xml, False
    changed = False

    def one(block):
        inner = block.group(2)
        if _RDF_LI.search(inner):
            new_inner = _RDF_LI.sub(lambda m: m.group(1) + lang + m.group(3),
                                    inner)
        else:
            new_inner = lang
        return block.group(1) + new_inner + block.group(3)

    out, n = _DC_LANG.subn(one, xml)
    changed = bool(n)
    return out, changed


def apply_document_metadata(doc, document, translations, lang):
    """Retarget /Lang, /Title and the outline; drop orphaned structure tags.

    None of this is page text, so nothing else in the pipeline touches it:
    an output otherwise keeps the source /Title in the window caption, the
    source /Lang for screen readers and hyphenation, and untranslated
    bookmarks. Structure tags describe text that no longer exists, so the
    tree is removed and /MarkInfo /Marked set false rather than left
    pointing at deleted content.
    """
    report = {'lang': None, 'title': None, 'outline': 0, 'xmp': False,
              'struct_tree_removed': False}
    document = document or {}

    if lang:
        # The whole tag, as the mapping spells it: PyMuPDF's set_language
        # keeps only part of it (es-US as es). The report is what the file
        # now holds, so the console line is what a reader will get.
        try:
            report['lang'] = write_language(doc, lang) or None
        except Exception:
            pass
        xml = doc.get_xml_metadata() or ''
        new_xml, changed = retarget_xmp(xml, lang)
        if changed:
            doc.set_xml_metadata(new_xml)
            report['xmp'] = True

    src_title = (document.get('title') or '').strip()
    new_title = translations.get(src_title) if src_title else None
    if new_title:
        meta = dict(doc.metadata or {})
        meta['title'] = new_title
        meta.pop('format', None)
        meta.pop('encryption', None)
        doc.set_metadata(meta)
        report['title'] = new_title

    toc = doc.get_toc(simple=True) or []
    if toc:
        changed = 0
        for entry in toc:
            title = str(entry[1])
            target = translations.get(title)
            if target:
                entry[1] = target
                changed += 1
        if changed:
            doc.set_toc(toc)
            report['outline'] = changed

    cat = doc.pdf_catalog()
    if cat:
        try:
            kind, _ = doc.xref_get_key(cat, 'StructTreeRoot')
            if kind != 'null':
                doc.xref_set_key(cat, 'StructTreeRoot', 'null')
                report['struct_tree_removed'] = True
            doc.xref_set_key(cat, 'MarkInfo', '<< /Marked false >>')
        except Exception:
            pass
    return report


# ---------------------------------------------------- canonical text layer
# MuPDF builds an embedded font's /ToUnicode by reverse-mapping the font's
# cmap. When several code points share one glyph it can report the wrong
# one: Arial's space glyph comes back as NBSP (U+00A0), its hyphen as a
# soft hyphen (U+00AD), and common kanji as CJK Compatibility Ideographs
# (立 as U+F9F7). The glyphs on the page are right; the text layer is not,
# so copy-paste, search and every string gate see characters nobody
# authored. After saving we rewrite /ToUnicode for the glyphs we actually
# placed, to the code points the author actually wrote.
_SUBSET_PREFIX = re.compile(r'^[A-Z]{6}\+')


def _font_key(name):
    return _SUBSET_PREFIX.sub('', str(name or '').lstrip('/')).replace(
        ' ', '').lower()


def ligature_gid_map(fontfile, chars):
    """{glyph id: text} for the ligatures and the single-substitution
    alternates this text could have produced.

    Alternates: Noto Sans JP swaps its digits for `locl` forms when
    HarfBuzz shapes a Latin run under the Japanese language tag (FL-150
    after "FL-", 11 in "11-inch"; digits inside a CJK run are untouched),
    and the alternate glyph has no cmap entry, so the layer said Ɍɐɋ for
    150 (measured on the FL-150 -> ja job, 2026-09-17). Each such glyph is
    mapped to its base glyph's character.

    The Story engine shapes Latin too: HarfBuzz applies `liga`/`clig` by
    default, and MuPDF 1.28 honours neither `font-variant-ligatures: none`
    nor `font-feature-settings` (measured, row 24), so "oficina" is drawn
    with the fi glyph and the layer says U+FB01 — or U+007F once the font
    is subsetted. The glyph is read from GSUB, not from cmap: pyftsubset
    keeps the substitution and the outline but drops U+FB01 from the cmap
    when the authored text never contained it, so cmap cannot find it.

    Only ligatures whose components are all authored are mapped, which
    keeps the override block to the handful a job can actually reach.
    """
    try:
        from fontTools.ttLib import TTFont
        font = TTFont(fontfile, lazy=True, fontNumber=0)
    except Exception:
        return {}
    try:
        return _ligatures(font, chars)
    except Exception:
        return {}
    finally:
        try:
            font.close()
        except Exception:
            pass


def _ligatures(font, chars):
    if 'GSUB' not in font:
        return {}
    lookups = font['GSUB'].table.LookupList.Lookup or []
    cmap = font.getBestCmap()
    lowest = {}
    for cp, name in cmap.items():
        if name not in lowest or cp < lowest[name]:
            lowest[name] = cp
    want = set(chars)
    out = {}
    for lookup in lookups:
        for sub in (getattr(lookup, 'SubTable', None) or []):
            # An extension lookup (type 7) wraps the real one.
            sub = getattr(sub, 'ExtSubTable', sub)
            # A single substitution (locl digits, vert forms, stylistic
            # sets) parks an authored character on an alternate glyph that
            # no code point names: map it back to the base's character. An
            # alternate that cmap already names is somebody's character
            # and is left alone.
            for base, alt in (getattr(sub, 'mapping', None) or {}).items():
                cp = lowest.get(base)
                if cp is None or chr(cp) not in want or alt in lowest:
                    continue
                try:
                    gid = font.getGlyphID(alt)
                except Exception:
                    continue
                prev = out.get(gid)
                if prev is None or (len(prev) == 1 and cp < ord(prev)):
                    out[gid] = chr(cp)
            for first, ligs in (getattr(sub, 'ligatures', None) or {}).items():
                for lig in ligs:
                    cps = [lowest.get(n)
                           for n in [first] + list(lig.Component)]
                    if any(c is None for c in cps):
                        continue
                    text = ''.join(chr(c) for c in cps)
                    if not set(text) <= want:
                        continue
                    try:
                        out[font.getGlyphID(lig.LigGlyph)] = text
                    except Exception:
                        continue
    return out


def authored_gid_map(fontfile, texts):
    """{glyph id: authored text} for every character placed with this font.

    Two authored characters can share one glyph (space and NBSP, hyphen
    and soft hyphen, 立 and its compatibility ideograph). The lower code
    point wins: in every drift pair the canonical character is the lower
    one, and picking it is what makes the layer canonical.

    A ligature glyph is nobody's character, so it is not reached by the
    loop below and keeps whatever the font said; those come from GSUB and
    map to more than one code point, which is why the values are strings.
    """
    try:
        font = pymupdf.Font(fontfile=fontfile)
    except Exception:
        return None, ''
    out = {}
    chars = set()
    for text in texts:
        for ch in text or '':
            chars.add(ch)
            cp = ord(ch)
            gid = font.has_glyph(cp)
            if not gid:
                continue
            if gid not in out or cp < ord(out[gid]):
                out[gid] = ch
    for gid, text in ligature_gid_map(fontfile, chars).items():
        out.setdefault(gid, text)
    return out, font.name


def _override_block(gidmap):
    lines = []
    items = sorted(gidmap.items())
    for i in range(0, len(items), 100):
        chunk = items[i:i + 100]
        lines.append(f'{len(chunk)} beginbfchar')
        for gid, text in chunk:
            # A bfchar destination may be several UTF-16BE code units, so
            # one entry can say "this glyph is f then i"; a bfrange cannot.
            lines.append(f'<{gid:04x}> <{_utf16be_hex(text)}>')
        lines.append('endbfchar')
    return ('\n' + '\n'.join(lines) + '\n').encode('ascii')


def _utf16be_hex(text):
    return text.encode('utf-16-be').hex()


def canonicalize_text_layer(path, fontfiles, texts, *, protected_fonts=()):
    """Rewrite /ToUnicode so the text layer reports the authored code points.

    A CMap is executed in order, so a bfchar block appended before endcmap
    overrides whatever an earlier bfrange said for the same code. Returns
    the number of font objects patched.
    """
    import pikepdf

    maps = {}
    for fontfile in fontfiles:
        face_texts = texts.get(fontfile, []) if isinstance(texts, dict) else texts
        gidmap, name = authored_gid_map(fontfile, face_texts)
        if not gidmap or not name:
            continue
        maps.setdefault(_font_key(name), {}).update(gidmap)
    if not maps:
        return 0
    patched = 0
    pdf = pikepdf.open(path, allow_overwriting_input=True)
    try:
        protected = set()
        for page_number, alias in protected_fonts:
            from .strip_text import resources_of
            font = resources_of(pdf.pages[page_number].obj).get('/Font', {}).get(alias)
            if font is None:
                raise MappingError('retained source font resource is missing')
            protected.add(font.objgen)
            protected.update(child.objgen for child in font.get('/DescendantFonts', []))
        for obj in pdf.objects:
            # pdf.objects yields every indirect object, including arrays,
            # numbers and strings. Asking one of those for a key raises,
            # and WHICH exception depends on the pikepdf version
            # (ValueError on 9.x, TypeError elsewhere) — so catch the
            # attempt, not a guessed type.
            try:
                if obj.get('/Type') != pikepdf.Name('/Font'):
                    continue
            except Exception:
                continue
            if obj.objgen in protected:
                continue
            gidmap = maps.get(_font_key(obj.get('/BaseFont')))
            tu = obj.get('/ToUnicode')
            if not gidmap or tu is None:
                continue
            data = bytes(tu.read_bytes())
            i = data.rfind(b'endcmap')
            if i < 0:
                continue
            obj.ToUnicode = pdf.make_stream(
                data[:i] + _override_block(gidmap) + data[i:])
            patched += 1
        if patched:
            pdf.save(path)
    finally:
        pdf.close()
    return patched


def _load_json(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


SCALE_REPORT_SCHEMA = 1


def write_scale_report(runs, path, *, typography=None):
    """The scaled-runs report, schema-versioned and written whole or not at all.

    The top level was a bare list. It is now
    `{"schema": 1, "version": ..., "runs": [...]}` — the same envelope every
    other machine-readable output carries, so a consumer can tell which build
    wrote a file it is holding. Broken deliberately now, before the product
    pins a version and builds goldens on the old shape;
    `verify.scale_report_for` reads both, because a report written by an
    older build must still verify.

    Written through a temp file and `os.replace`, like `verify.write_report`:
    a half-written report claiming a clean build is worse than none, which is
    the defect PR #7 exists to prevent. A failure is logged, never raised, and
    never changes the return code.
    """
    from . import __version__
    data = {'schema': SCALE_REPORT_SCHEMA, 'version': __version__, 'runs': list(runs)}
    if typography is not None:
        data.update(schema=2, typography=typography)
    text = json.dumps(data, ensure_ascii=False, indent=1)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix='.scale_report-', suffix='.tmp',
                                   dir=os.path.dirname(os.path.abspath(path)))
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        log.info(f'  (could not write {SCALE_REPORT}: {exc})')
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        # A stale report from an earlier build must not survive a failed
        # write and be read as this one's.
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass
        return False


def _same_path(first, second):
    try:
        return os.path.samefile(first, second)
    except OSError:
        return os.path.normcase(os.path.realpath(first)) == os.path.normcase(os.path.realpath(second))


def _legacy_fonts(conf, trf):
    """{role: path} for a legacy mapping's fonts, resolved as retypeset
    opens them. Mapping-relative paths work for both the direct command and
    rebuild. Old caller-relative mappings stay usable, but the fallback is
    made visible."""
    font_dir = os.path.dirname(os.path.abspath(trf))
    fonts = {}
    for role_name, path in conf['fonts'].items():
        if path and not os.path.isabs(path):
            local = os.path.join(font_dir, path)
            if not os.path.isfile(local) and os.path.isfile(path):
                log.info(f'NOTE font {role_name}: legacy caller-relative path {path}; '
                      'prefer a path relative to translations.json')
                local = os.path.abspath(path)
            path = local
        fonts[role_name] = path
    return fonts


def _refuse_output_aliases(out, report, inputs):
    """Refuse a legacy build whose output or scale report names an input.

    typography-1 refuses this before it builds; a legacy mapping used to go
    straight to the save. It then replaced translations.json or
    segments.json with PDF or report bytes and exited 0, or raised a bare
    ValueError for stripped.pdf (R-05). `inputs` is [(role, path)]; a path
    of None is skipped.
    """
    hits = []
    for writes, destination in (('output', out), ('scale report', report)):
        if not destination:
            continue
        for role, path in inputs:
            if path and _same_path(destination, path):
                hits.append({'writes': writes, 'path': destination,
                             'input': role})
    if report and _same_path(out, report):
        hits.append({'writes': 'output', 'path': out, 'input': 'scale report'})
    if not hits:
        return
    lines = [f'FAIL: {len(hits)} path(s) of this build would overwrite one of '
             f'its inputs; write them somewhere else:']
    lines += [f'  the {h["writes"]} {h["path"]} is the {h["input"]}'
              for h in hits]
    raise MappingError('an output path names an input of this build',
                       console_line='\n'.join(lines), exit_code=1,
                       refusals={'output_aliases': hits})


def _refuse_stale_geometry(segd, stripped):
    """Refuse a segments.json that records a rotated page in the old space.

    Up to v63, extract recorded a /Rotate page's text in the rotated space a
    viewer shows, while this module draws in the unrotated one, so such a
    file puts every run off the page or in the wrong place. A segments.json
    whose "geometry" names the unrotated space is current. Without the
    marker, a page with no /Rotate is the same in both spaces and is
    accepted; a rotated page that carries segments is refused. The console
    line is a documented, stable string (references/failure-modes.md §13):
    a consumer may match it.
    """
    if (segd.get('geometry') or {}).get('space') == GEOMETRY_SPACE:
        return
    pages = sorted({s['page'] for s in segd.get('segments', [])
                    if isinstance(s.get('page'), int)})
    if not pages:
        return
    with pymupdf.open(stripped) as doc:
        stale = [(p, doc[p].rotation) for p in pages
                 if 0 <= p < doc.page_count and doc[p].rotation]
    if not stale:
        return
    shown = ', '.join(f'p{p} /Rotate {r}' for p, r in stale[:CONSOLE_LIST])
    if len(stale) > CONSOLE_LIST:
        shown += ', …'   # the count says how many; refusals lists them all
    raise MappingError(
        f'stale extraction: {len(stale)} rotated page(s) in the old space',
        console_line=(f'FAIL: stale extraction: segments.json names no '
                      f'geometry space and {len(stale)} rotated page(s) '
                      f'carry segments ({shown}); it was extracted before '
                      f'v64 in the rotated space: run extract again.'),
        exit_code=1,
        refusals={'stale_extraction': [{'page': p, 'rotation': r}
                                       for p, r in stale]})


def _row_obstacles(seg, segs, drawn_boxes=None, *, left=False):
    """Same-row obstacles; measured rotated boxes use the growth anchor.

    A longer rotated run can enter this source box or arrive from a row
    above it. Classifying its *source* as left/right would lose that
    obstacle. Unmeasured neighbours keep the existing source-side rule.
    """
    drawn_boxes = drawn_boxes or {}
    y0, y1 = seg['bbox'][1], seg['bbox'][3]
    for other in segs:
        if other is seg:
            continue
        box = drawn_boxes.get(id(other), other['bbox'])
        if id(other) in drawn_boxes:
            source = other['bbox']
            if (min(seg['bbox'][2], source[2]) - max(seg['bbox'][0], source[0]) > 1 and
                    min(seg['bbox'][3], source[3]) - max(seg['bbox'][1], source[1]) > 1):
                # The rotated run already crossed this one in the source: a
                # diagonal watermark or a stamp printed over the text. That
                # overlap is the design, not room the translation took
                # (dogfood 2026-10-10, cms-1490s-en).
                continue
            ahead = box[0] < seg['bbox'][2] if left else box[2] > seg['origin'][0]
            same_row = box[3] > y0 and box[1] < y1
        else:
            ahead = (box[2] < seg['bbox'][0] + 1 if left
                     else box[0] > seg['bbox'][2] - 1)
            same_row = not (box[3] < y0 + 1 or box[1] > y1 - 1)
        if ahead and same_row:
            yield other, box


def _marker_bold(seg):
    """Whether a list marker is bold in its source: the line's majority weight, unless
    the line mixes weights and its recorded bold runs say otherwise for the marker."""
    marker = seg.get('marker') or ''
    runs = seg.get('bold_runs')
    if not marker or not runs or not (seg.get('text') or '').strip().startswith(marker):
        return bool(seg.get('bold'))
    return any(start <= 0 and end >= len(marker) for start, end in runs)


def _right_limit(page_rect, seg, segs, widgets, drawn_boxes=None):
    """The horizontal budget; ordinary neighbours retain their arithmetic."""
    x0 = seg['origin'][0]
    y0, y1 = seg['bbox'][1], seg['bbox'][3]
    lim = page_rect.width - 32
    for _, box in _row_obstacles(seg, segs, drawn_boxes):
        lim = min(lim, box[0])
    for rect in widgets:
        if rect.x0 > x0 + 1 and not (rect.y1 < y0 + 1 or rect.y0 > y1 - 1):
            if rect.x0 > seg['bbox'][2] - 2:
                lim = min(lim, rect.x0)
    return lim - 1.5


def _rotated_text_box(page, writer, morph):
    """Bounds of the fitted run as actually drawn, in unrotated page space.

    TextWriter.text_rect includes the font's global overhang, which can
    reserve tens of points never occupied by this text. Draw the already
    prepared writer on a temporary page instead; the final page reuses the
    same writer and morph. No PDF file or raster is needed.
    """
    frame = unrotated_frame(page)
    with pymupdf.open() as measured:
        probe = measured.new_page(width=frame.width, height=frame.height)
        probe.set_rotation(page.rotation)
        writer.write_text(probe, morph=morph)
        boxes = [pymupdf.Rect(char['bbox'])
                 for block in probe.get_text('rawdict', clip=pymupdf.INFINITE_RECT())['blocks']
                 for line in block.get('lines', [])
                 for span in line['spans'] for char in span['chars']
                 if char['c'].strip()]
    if not boxes:
        return None
    result = boxes[0]
    for box in boxes[1:]:
        result |= box
    return result


def _typography_failure(reason, detail, segment=None, run_id=None, error=MappingError, **attributes):
    segment = segment or {}
    item = {'reason': reason, 'detail': detail, 'occurrence_id': segment.get('occurrence_id'),
            'run_id': run_id, 'page': segment.get('page'), 'source_text': segment.get('text')}
    if error is FontError:
        attributes['reason'] = reason
    raise error(detail, refusals={'typography': [item]}, **attributes)


def _source_typography_supported(segment):
    first = segment['style_runs'][0]
    if (segment['dir'] != [1, 0] or segment.get('marker') or segment.get('dots') or
            segment.get('tail') or re.search(r'\s{6,}', segment['text']) or
            any(abs(run['size'] - first['size']) > 0.0001 or
                run['color'] != first['color'] or
                abs(run['origin'][1] - first['origin'][1]) > 0.0001
                for run in segment['style_runs'])):
        _typography_failure('unsupported-typography-construct',
                            'requires one horizontal baseline, size and color without synthesized chrome', segment)
    return first['origin'], first['size'], first['color']


def _glyph_inks(path, font, chars):
    """Actual selected glyph outlines in em units, with PDF downward-positive Y.

    MuPDF 1.28.2 glyph_bbox returned the whole-font bbox for f, j, A and space
    in both measured faces. BoundsPen measures each actual selected glyph.
    """
    from fontTools.ttLib import TTFont
    from fontTools.pens.boundsPen import BoundsPen
    inks, codes = {}, {}
    with TTFont(path) as face:
        glyphs, order = face.getGlyphSet(), face.getGlyphOrder()
        units = face['head'].unitsPerEm
        for char in sorted(chars):
            gid = font.has_glyph(ord(char), fallback=False)
            if gid in codes and codes[gid] != char:
                _typography_failure('unsupported-typography-construct',
                                    'distinct authored characters share a glyph; exact text cannot be attested')
            codes[gid] = char
            pen = BoundsPen(glyphs)
            glyphs[order[gid]].draw(pen)
            if pen.bounds is None:
                if not char.isspace():
                    _typography_failure('unsafe-typography-geometry', 'selected visible glyph has no measurable outline')
                inks[char] = None
            else:
                x0, y0, x1, y1 = pen.bounds
                inks[char] = (x0 / units, -y1 / units, x1 / units, -y0 / units)
    return inks


def _stroke_obstacles(page):
    """Conservative bounds of stroked edges; filled backgrounds are not barriers."""
    edges = []
    for drawing in page.get_drawings():
        if 's' not in drawing.get('type', '') or drawing.get('color') is None:
            continue
        half = max(float(drawing.get('width') or 0) / 2, 0.05)
        for item in drawing['items']:
            kind = item[0]
            if kind == 'l':
                points = [item[1], item[2]]
                pairs = [(points[0], points[1])]
            elif kind == 're':
                rect = item[1]
                pairs = [(rect.tl, rect.tr), (rect.tr, rect.br), (rect.br, rect.bl), (rect.bl, rect.tl)]
            else:
                # Curves/quad edges may enter a glyph box anywhere in their bounds.
                rect = pymupdf.Rect(drawing['rect'])
                edges.append(rect + (-half, -half, half, half))
                continue
            for start, end in pairs:
                rect = pymupdf.Rect(min(start.x, end.x), min(start.y, end.y),
                                    max(start.x, end.x), max(start.y, end.y))
                edges.append(rect + (-half, -half, half, half))
    return edges


def _legacy_form_obstacles(page, segments):
    """Source rules and empty closed controls in the existing page frame."""
    text_boxes = [pymupdf.Rect(segment['bbox']) for segment in segments]
    closed, underlines = [], []
    for drawing in page.get_drawings():
        if 's' not in drawing.get('type', '') or drawing.get('color') is None:
            continue
        half = max(float(drawing.get('width') or 0) / 2, .05)
        for item in drawing['items']:
            if item[0] == 're':
                rectangle = pymupdf.Rect(item[1])
                if not rectangle.is_empty and not any(rectangle.intersects(box) for box in text_boxes):
                    # A control's interior must stay empty as well as its edges.
                    closed.append(rectangle + (-half, -half, half, half))
            elif item[0] == 'l' and abs(item[1].y-item[2].y) <= 1e-4:
                left, right = sorted((item[1].x, item[2].x))
                if right > left:
                    underlines.append((left-half, right+half, item[1].y, half))
    return _stroke_obstacles(page) + closed, underlines


def _legacy_field_limit(segment, obstacles):
    """Reserve actual controls and a writing rule beside this source caption.

    A rule below the caption baseline, within half its source row height, is
    part of that row. The blank above its right-hand rule starts at the rule's
    actual X coordinate. No crop, form name or segment ID supplies a boundary.
    Rules already underneath the source label remain legitimate source ink.
    """
    source = pymupdf.Rect(segment['bbox'])
    origin, baseline = segment['origin']
    edges, underlines = obstacles
    limit = math.inf
    for rectangle in edges:
        if (rectangle.x0 >= source.x1-1e-4 and rectangle.x0 > origin and
                rectangle.y0 < source.y1 and rectangle.y1 > source.y0):
            limit = min(limit, rectangle.x0-1.5)
    for left, _, line_y, _ in underlines:
        if (left >= source.x1-1e-4 and left > origin and
                0 <= line_y-baseline <= source.height/2):
            limit = min(limit, left-1.5)
    return limit


def _legacy_retained_limit(segment, top, bottom, blocks):
    """Reserve vouched source ink inside a neighboring spaced-out text run.

    A retained row's segment can start left of an inline field while its
    suffix starts to the right. Its actual filled glyph bounds, rather than
    the merged segment's origin, supply that field's right-hand boundary.
    """
    source = pymupdf.Rect(segment['bbox'])
    origin = segment['origin'][0]
    limit = math.inf
    for block in blocks:
        for left, upper, _, lower in block['ink_boxes']:
            if (left >= source.x1-1e-4 and left > origin and
                    upper < bottom and lower > top):
                limit = min(limit, left)
    return limit


@dataclass(frozen=True)
class _TypographyPlacement:
    widths: tuple
    scale: float
    origin: tuple
    size: float
    color: int
    glyph_boxes: tuple


def _measure_typography(page, segment, target, font_for, inks_for, neighbors, widgets, strokes, allow_scale):
    (ox, baseline), size, color = _source_typography_supported(segment)
    frame = unrotated_frame(page)
    widths, boxes, x = [], [], 0.0
    for run in target.runs:
        key = (run.font_class, run.font_role)
        font, inks = font_for[key], inks_for[key]
        widths.append(font.text_length(run.text, fontsize=size))
        for char in run.text:
            ink = inks[char]
            if ink is not None:
                boxes.append((x + ink[0] * size, ink[1] * size,
                              x + ink[2] * size, ink[3] * size))
            x += font.glyph_advance(ord(char)) * size
    if not boxes:
        _typography_failure('unsafe-typography-geometry', 'occurrence has no measurable visible glyphs', segment)
    left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
    right, bottom = max(b[2] for b in boxes), max(b[3] for b in boxes)
    available = _right_limit(frame, segment, neighbors, widgets) - ox
    ratio = min(1.0, available / max(sum(widths), right))
    left_edge = frame.x0
    source_box = pymupdf.Rect(segment['bbox'])
    for rect in [pymupdf.Rect(s['bbox']) for s in neighbors if s is not segment] + list(widgets):
        if rect.x1 <= source_box.x0 and rect.y0 < source_box.y1 and rect.y1 > source_box.y0:
            left_edge = max(left_edge, rect.x1 + 1.5)
    if left < 0:
        ratio = min(ratio, (ox - left_edge) / -left)
    if top < 0:
        ratio = min(ratio, (baseline - frame.y0) / -top)
    if bottom > 0:
        ratio = min(ratio, (frame.y1 - baseline) / bottom)
    if not math.isfinite(ratio) or ratio <= 0:
        _typography_failure('unsafe-typography-geometry', 'glyphs cannot fit at the source baseline',
                            segment, error=PlacementError, page=segment['page'], key=target.occurrence_id)
    if ratio < SCALE_MIN and target.occurrence_id not in allow_scale:
        _typography_failure('below-scale-floor', f'occurrence needs {ratio:.4f} scale, below {SCALE_MIN}',
                            segment, error=PlacementError, page=segment['page'],
                            key=target.occurrence_id, scale=ratio)
    obstacles = [pymupdf.Rect(s['bbox']) for s in neighbors if s is not segment] + list(widgets) + strokes
    drawn_boxes = []
    for box in boxes:
        drawn = pymupdf.Rect(ox + box[0] * ratio, baseline + box[1] * ratio,
                             ox + box[2] * ratio, baseline + box[3] * ratio)
        drawn_boxes.append(drawn)
        if any(drawn.intersects(obstacle) for obstacle in obstacles):
            _typography_failure('unsafe-typography-geometry', 'glyph outline bounds intersect another source object',
                                segment, error=PlacementError, page=segment['page'], key=target.occurrence_id)
    return _TypographyPlacement(tuple(widths), ratio, (ox, baseline), size, color, tuple(drawn_boxes))


def _content_segment(segments, issue):
    """The occurrence a source-content issue is about: the smallest segment on
    its page whose box holds `issue.at`, where the text line holding the
    offending text starts. A whole-page issue, or text no segment holds,
    names only the page (R-42)."""
    if issue.at is not None:
        point = pymupdf.Point(issue.at)
        holding = [s for s in segments if s['page'] == issue.page and
                   (pymupdf.Rect(s['bbox']) + (-1, -1, 1, 1)).contains(point)]
        if holding:
            return min(holding, key=lambda s: pymupdf.Rect(s['bbox']).get_area())
    return {'page': issue.page}


def _run_typography(stripped, document, out, *, original, progress, cancel, scale_report):
    """Preflight every occurrence, then use the existing one-line TextWriter path."""
    data, expected = document.extraction, document.extraction['typography']
    try:
        current_digest = source_digest(original) if original else None
    except OSError:
        current_digest = None
    if current_digest != expected['source_sha256']:
        refuse('stale-extraction', 'original PDF is required and must match the extraction digest')
    from .typography_content import inspect_content
    for issue in inspect_content(original, source=True).issues:
        refuse('unsupported-typography-construct', issue.detail, _content_segment(data['segments'], issue))
    if data['pages'] != list(range(len(expected['page_geometry']))):
        refuse('unsupported-typography-construct', 'a whole-document build requires every source page')
    if any(w.get('kind') in ('no-text-layer', 'invisible-text') for w in data.get('warnings', [])):
        refuse('unsupported-typography-construct', 'source has unextractable or invisible page text')
    segments = {s['occurrence_id']: s for s in data['segments']}
    by_page = {}
    for segment in segments.values():
        _source_typography_supported(segment)
        by_page.setdefault(segment['page'], []).append(segment)
    with pymupdf.open(original) as source, pymupdf.open(stripped) as doc:
        if (page_geometry(source) != expected['page_geometry'] or
                page_geometry(doc) != expected['page_geometry'] or
                field_identities(source) != expected['fields'] or field_identities(doc) != expected['fields']):
            refuse('stale-extraction', 'source/stripped page geometry or field identities disagree')
        if any(p.rotation for p in source):
            refuse('unsupported-typography-construct', 'rotated source pages are outside the first scope')
        font_for, inks_for, observations, face_texts, names = {}, {}, {}, {}, {}
        for target in document.targets:
            segment = segments[target.occurrence_id]
            for run in target.runs:
                key = (run.font_class, run.font_role)
                if key in font_for:
                    continue
                path = document.font_sets[run.font_class][run.font_role]
                chars = charset_for(document, *key)
                try:
                    observed = validate_font(path, *key, chars)
                except FontError as exc:
                    kind = GlyphError if exc.reason == 'missing-glyph' else FontError
                    _typography_failure(exc.reason, exc.message, segment, run.source_runs[0],
                                        error=kind, face=path)
                font = pymupdf.Font(fontfile=path)
                name = _font_key(font.name)
                if name in names and names[name] != observed['sha256']:
                    _typography_failure('unresolved-target-font', 'selected font names identify different programs', segment)
                names[name] = observed['sha256']
                font_for[key] = font
                try:
                    inks_for[key] = _glyph_inks(path, font, chars)
                except MappingError as exc:
                    _typography_failure(exc.refusals['typography'][0]['reason'], exc.message,
                                        segment, run.source_runs[0])
                observations[key] = dict(observed, id='/'.join(key), path=path)
                face_texts[path] = [r.text for t in document.targets for r in t.runs
                                    if (r.font_class, r.font_role) == key]
        widgets = {p.number: [w.rect for w in p.widgets() or ()] for p in doc}
        strokes = {p.number: _stroke_obstacles(p) for p in doc}
        measurements, proposed = {}, {}
        for target in document.targets:
            segment = segments[target.occurrence_id]
            measurements[target.occurrence_id] = _measure_typography(
                doc[target.page], segment, target, font_for, inks_for, by_page[target.page],
                widgets[target.page], strokes[target.page], document.allow_scale)
            boxes = measurements[target.occurrence_id].glyph_boxes
            bounds = pymupdf.Rect(min(b.x0 for b in boxes), min(b.y0 for b in boxes),
                                  max(b.x1 for b in boxes), max(b.y1 for b in boxes))
            for other_id, other_bounds, other_boxes in proposed.get(target.page, []):
                if bounds.intersects(other_bounds) and any(a.intersects(b) for a in boxes for b in other_boxes):
                    _typography_failure('unsafe-typography-geometry',
                                        f'translated glyph bounds intersect occurrence {other_id}',
                                        segment, error=PlacementError, page=target.page, key=target.occurrence_id)
            proposed.setdefault(target.page, []).append((target.occurrence_id, bounds, boxes))
        record = {'schema': 1, 'source_sha256': current_digest, 'extraction_id': document.extraction_id,
                  'mapping_sha256': document.mapping_sha256, 'fonts': list(observations.values()),
                  'source_font_resolutions': list(document.source_font_resolutions), 'occurrences': []}
        scaled = []
        for page in doc:
            if cancel is not None and cancel():
                return RetypesetResult(output=None, pages=doc.page_count, cancelled=True)
            for target in (t for t in document.targets if t.page == page.number):
                segment = segments[target.occurrence_id]
                measured = measurements[target.occurrence_id]
                widths, ratio, origin = measured.widths, measured.scale, measured.origin
                size, color = measured.size, measured.color
                x, baseline = origin
                writer = pymupdf.TextWriter(page.rect)
                runs = []
                for run, width in zip(target.runs, widths):
                    key = (run.font_class, run.font_role)
                    writer.append((x, baseline), run.text, font=font_for[key], fontsize=size * ratio)
                    runs.append({'text': run.text, 'source_runs': list(run.source_runs),
                                 'class': run.font_class, 'role': run.font_role, 'font_id': '/'.join(key),
                                 'origin': [x, baseline], 'size': size * ratio, 'advance': width * ratio})
                    x += width * ratio
                writer.write_text(page, color=tuple(((color >> shift) & 255) / 255 for shift in (16, 8, 0)))
                record['occurrences'].append({'occurrence_id': target.occurrence_id, 'page': target.page,
                    'source_text': target.source_text, 'source_origin': list(origin), 'source_size': size,
                    'source_bbox': segment['bbox'], 'color': color, 'scale': ratio,
                    'scale_exception_applied': ratio < SCALE_MIN and target.occurrence_id in document.allow_scale,
                    'scale_reason': document.allow_scale.get(target.occurrence_id, ''), 'runs': runs})
                if ratio < 1:
                    scaled.append({'page': target.page, 'key': target.source_text,
                                   'occurrence_id': target.occurrence_id, 'ratio': round(ratio, 4)})
            if progress is not None:
                progress(page.number + 1, doc.page_count)
        apply_document_metadata(doc, data['document'], document.document_targets, document.lang)
        if cancel is not None and cancel():
            return RetypesetResult(output=None, pages=doc.page_count, cancelled=True)
        # Canonicalize a private new file; failed publication never overwrites a prior success.
        fd, temporary = tempfile.mkstemp(prefix='.typography-', suffix='.pdf', dir=Path(out).resolve().parent)
        os.close(fd)
        try:
            doc.ez_save(temporary)
            canonicalize_text_layer(temporary, list(face_texts), face_texts)
            record['output_sha256'] = source_digest(temporary)
            os.replace(temporary, out)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
        pages = doc.page_count
    report = str(Path(out).resolve().parent / SCALE_REPORT) if scale_report is _DEFAULT else scale_report
    if report and not write_scale_report(scaled, report, typography=record):
        report = ''
    log.info(f'saved {out}')
    return RetypesetResult(output=str(out), pages=pages, placed=len(document.targets),
                          scaled=tuple(scaled), scale_report_path=str(report or ''), typography=record)


def retypeset(stripped, segf, trf, out, *, original=None, capture_dir=None,
              capture_below=None):
    """Place translations onto a stripped PDF. Returns 0, or 1 if cores/overflow.

    The loud shape, unchanged: same signature, same printed bytes, same return
    code. `run_retypeset` is the one a service calls — it returns a
    `RetypesetResult` and raises, and its exceptions carry every refused core
    in full through `.refusals`, not the console's 40-character abbreviation.

    `capture_dir` is opt-in evidence collection for a below-the-floor scale
    refusal (`capture.py`) — off by default, and when off this function's
    console output and return code are exactly what they were before it
    existed.
    """
    with console():
        try:
            run_retypeset(stripped, segf, trf, out, original=original,
                          capture_dir=capture_dir, capture_below=capture_below)
        except PdfTranslateError as exc:
            log.info(exc.console_line)
            return exc.exit_code
    return 0


def run_retypeset(stripped, segf, trf, out, *, progress=None, cancel=None,
                  scale_report=_DEFAULT, resource_root=None, original=None,
                  capture_dir=None, capture_below=None, _unretained_ids=()):
    """Place translations onto a stripped PDF.

    Silent. Returns a RetypesetResult. Raises on refusal — `GlyphError` when
    the face cannot draw a character, `MappingError` for an unauthored core or
    a merge that does not match, `PlacementError` for a run that will not fit
    or a notice that cannot be placed. Every one carries `.refusals`: each
    refused item, by kind, with the core untruncated.

    `capture_dir` turns on evidence collection for a refusal caused
    specifically by a run scaling below `SCALE_MIN`: a self-contained,
    replayable bundle (source PDF, mapping, fonts, the structured refusal,
    the occurrence's page/position, and its box permission if any is on
    record) is written under a new subdirectory of `capture_dir`, named for
    the capture's timestamp. Unset — the default — nothing is written and
    nothing else about this call changes; `PDF_TRANSLATE_CAPTURE_DIR` is the
    same switch for a caller that cannot pass the keyword (see `capture.py`).
    A problem writing the bundle never changes the refusal that follows it.

    `progress(done, total)` is called once per unit of work. The total is
    **pages plus merge jobs**, because this runs two loops: a per-page pass and
    then a per-merge pass. A counter of pages alone reaches 100% and keeps
    going.

    `cancel()` is checked at the top of every unit in both loops. When it
    returns true the run stops and returns `RetypesetResult(cancelled=True,
    output=None)`. There is exactly one save, at the very end, so a cancelled
    run cannot leave a partial file — the absence is by construction, not by
    cleanup.

    `scale_report` is where the scaled-runs report is written. `None` means
    write nothing and return the runs in the result; the default is today's
    behaviour, a fixed name beside `out` — which two concurrent jobs writing
    to one directory would race for, so a service passes its own path.

    `resource_root` is the directory authored HTML resolves its resources
    against. It defaults to the **mapping's** directory, not the process's
    working directory: a service that runs from anywhere else was silently
    getting a substituted face.
    """
    resolved_capture = capture.resolve_capture_dir(capture_dir)
    resolved_below = capture.resolve_capture_below(capture_below)
    document = load_mapping(trf, segf)
    if document.format == FORMAT:
        protected = [stripped, segf, trf, original]
        protected.extend(path for roles in document.font_sets.values() for path in roles.values())
        report = (str(Path(out).resolve().parent / SCALE_REPORT)
                  if scale_report is _DEFAULT else scale_report)
        for destination in (out, report):
            if destination is not None and any(_same_path(destination, path) for path in protected if path):
                refuse('invalid-style-reference', 'output/report aliases a typography input')
        if report and _same_path(out, report):
            refuse('invalid-style-reference', 'PDF and report paths must differ')
        try:
            return _run_typography(stripped, document, out, original=original,
                                   progress=progress, cancel=cancel, scale_report=scale_report)
        except PdfTranslateError as exc:
            # Only a run that actually scaled below the floor is evidence for
            # B1; every other typography refusal (an impossible baseline, two
            # occurrences that collide, ...) is left alone.
            if resolved_capture and capture.is_below_floor(exc):
                capture.capture_refusal(
                    resolved_capture, exc, stripped=stripped, segf=segf, trf=trf,
                    out=out, original=original, fonts=document.font_sets,
                    mapping_format=document.format, legacy_conf=None,
                    scale_report=scale_report, resource_root=resource_root)
            raise
    conf = document.legacy
    fonts = _legacy_fonts(conf, trf)
    _refuse_output_aliases(
        out, (os.path.join(os.path.dirname(os.path.abspath(out)), SCALE_REPORT)
              if scale_report is _DEFAULT else scale_report),
        [('stripped PDF', stripped), ('segments', segf), ('mapping', trf),
         ('original PDF', original)]
        + [(f'{role_name} font', path) for role_name, path in fonts.items()])
    segd = _load_json(segf)
    _refuse_stale_geometry(segd, stripped)
    T = conf['translations']
    merges = conf.get('merges', [])
    center = set(conf.get('center', []))
    right = set(conf.get('right') or [])
    skip = set(conf.get('skip', []))
    allow_scale = set(conf.get('allow_scale') or [])
    # Cores a consumer could not place even as their own source text: a run
    # past the page margin, beside a neighbour that leaves no room, or in a
    # script the target face cannot draw. An occurrence whose translation is
    # its own source is not redrawn: its source glyphs are replayed as
    # printed (replay_unmapped), so one stuck run never costs the document.
    # A run whose glyphs cannot be vouched refuses by name (`source_glyphs`).
    source_glyphs = set(conf.get('source_glyphs') or [])
    overrides = conf.get('overrides', [])
    # One shrink factor per paragraph: the engine names each member line (and its
    # attached list marker) by segment id with the smallest ratio its paragraph
    # needed. A member never starts above it; its own fit may still go lower.
    scale_caps = {str(key): float(value)
                  for key, value in (conf.get('scale_caps') or {}).items()}
    notices = conf.get('notices') or []
    overflow = []
    scaled = []
    mirror = bool(conf.get('mirror'))

    def mirror_left(left, width, pw):
        if not mirror:
            return left
        return pw - left - width

    def consider_ratio(pno, key, ratio, opted=False):
        # Anything the fit or the engine shrank is reported, not only what
        # it shrank past the floor: the author cannot name a scaled run in
        # the delivery if nothing ever tells them (row 25).
        if ratio < 1.0:
            scaled.append({'page': pno, 'key': key or '', 'ratio': round(ratio, 4)})
        if ratio >= SCALE_MIN:
            return
        shown = (key or '')[:50]
        if opted or key in allow_scale:
            log.info(f'  note: p{pno} scaled to {ratio:.2f}x (allow_scale): {shown}')
            return
        overflow.append((pno, ratio, key))

    segments = segd['segments']
    by_page = {}
    for s in segments:
        by_page.setdefault(s['page'], []).append(s)

    merge_jobs = []
    # Each merge's box, kept as an obstacle for the runs around it: its lines
    # leave the page's segment list, and a neighbour in the next column must
    # not grow into the reflowed paragraph (dogfood 2026-10-10, vis-en-flu).
    merge_boxes = {}
    unauthored_merges = []
    # Horizontal-only features asked of a run drawn along its own direction:
    # (page, core, feature). Refused by name before anything is drawn.
    rotated_features = []
    for m in merges:
        if m.get('html') is None:
            # A merge proposal accepted in bulk but not yet written. Same
            # rule as a null translation: it must not ship silently.
            unauthored_merges.append(
                (m.get('page'), (m.get('lines') or [''])[0]))
            continue
        page_segs = by_page.get(m['page'], [])
        idxs, li = [], 0
        for i, s in enumerate(page_segs):
            if li < len(m['lines']) and s['text'].strip() == m['lines'][li].strip():
                idxs.append(i)
                li += 1
        if li != len(m['lines']):
            raise MappingError(
                'a declared merge does not match the page',
                console_line=(f"!! merge not matched p{m['page']}: "
                              f"{m['lines'][0][:60]} "
                              f"({li}/{len(m['lines'])})"),
                exit_code=1, core=m['lines'][0],
                refusals={'merges': [{'page': m['page'],
                                      'lines': list(m['lines']),
                                      'matched': li}]})
        for i in idxs:
            if is_rotated(*seg_dir(page_segs[i])):
                rotated_features.append((m['page'], page_segs[i]['core']
                                         or page_segs[i]['text'].strip(), 'merge'))
        r = pymupdf.Rect(min(page_segs[i]['bbox'][0] for i in idxs),
                         min(page_segs[i]['bbox'][1] for i in idxs),
                         max(page_segs[i]['bbox'][2] for i in idxs),
                         max(page_segs[i]['bbox'][3] for i in idxs))
        # An author may hand the merge a rect of their own (row 26): a
        # paragraph whose target needs one more line than the source could
        # otherwise only shrink, or be declined and hard-wrapped at the
        # source's breaks. Geometry must not choose this box — a box that
        # grows into a rule or a field is worse than a shrink — but the
        # author can see the page and choose.
        boxed = m.get('box') is not None
        if boxed:
            try:
                bx0, by0, bx1, by1 = (float(v) for v in m['box'])
            except (TypeError, ValueError):
                raise MappingError(
                    'a merge box is not four numbers',
                    console_line=(f"!! merge box p{m['page']} is not four "
                                  f"numbers [x0, y0, x1, y1]: {m['box']!r}"),
                    exit_code=1, core=m['lines'][0],
                    refusals={'merges': [{'page': m['page'],
                                          'box': m['box'],
                                          'why': 'not four numbers'}]})
            if bx1 <= bx0 or by1 <= by0:
                raise MappingError(
                    'a merge box is empty or inverted',
                    console_line=(f"!! merge box p{m['page']} is empty or "
                                  f"inverted: {m['box']!r}"),
                    exit_code=1, core=m['lines'][0],
                    refusals={'merges': [{'page': m['page'],
                                          'box': m['box'],
                                          'why': 'empty or inverted'}]})
            r = pymupdf.Rect(bx0, by0, bx1, by1)
        merge_boxes.setdefault(m['page'], []).append(
            {'bbox': [r.x0, r.y0, r.x1, r.y1], 'origin': [r.x0, r.y1], 'core': None,
             'text': ''})
        size = max(page_segs[i]['size'] for i in idxs)
        # Derive leading from the ORIGINAL baselines rather than assuming a
        # ratio: a flyer set 11pt on 16pt leading re-flows visibly compressed
        # if you hardcode 1.18, and the block no longer lines up with the rules
        # and headings around it.
        ys = sorted(page_segs[i]['origin'][1] for i in idxs)
        gaps = [b - a for a, b in zip(ys, ys[1:]) if b - a > 0.5]
        lh = round((sum(gaps) / len(gaps)) / size, 3) if gaps else 1.18
        color = m.get('color', page_segs[idxs[0]].get('color', 0))
        merge_key = (m.get('lines') or [m.get('html') or ''])[0]
        merge_opt = bool(m.get('allow_scale'))
        merge_jobs.append((m['page'], r, m['html'], m.get('align', 'left'),
                           size, lh, color, merge_key, merge_opt, boxed))
        for i in sorted(idxs, reverse=True):
            del page_segs[i]

    over_by_page = {}
    for o in overrides:
        over_by_page.setdefault(o['page'], []).append(o)

    def override_for(pno, text):
        """The override that replaces this whole segment, if any."""
        return next((o for o in over_by_page.get(pno, [])
                     if o['contains'] in text), None)

    missing = []
    for pno, segs in by_page.items():
        for seg in segs:
            text = seg['text'].strip()
            if text in skip:
                continue
            if T.get(seg['core']) is None and not seg['passthrough']:
                # An override replaces the segment's whole span with its
                # own parts, so the plain value is never drawn. Demanding
                # one anyway made the author write a phantom string that
                # exists only to satisfy this check and verify's (row 29).
                if override_for(pno, text):
                    continue
                missing.append((pno, seg['core']))
    # Accumulated rather than printed: the loud wrapper prints this block
    # verbatim, and a consumer gets the same refusals as data through the
    # exception's `refusals` — untruncated, unlike the console's summary.
    block = []
    if unauthored_merges:
        block.append(f'FAIL: {len(unauthored_merges)} merges with html null '
                     f'(accepted as proposals; author the paragraph or delete '
                     f'the entry):')
        for p, first in unauthored_merges[:CONSOLE_LIST]:
            block.append(f'  p{p}: {first[:70]}')
        block.extend(more_not_shown(unauthored_merges))
    if missing:
        block.append(f'FAIL: {len(missing)} untranslated segments:')
        for p, c in missing[:CONSOLE_LIST]:
            block.append(f'  p{p}: {c}')
        block.extend(more_not_shown(missing))
    if missing or unauthored_merges:
        block.extend(refusal_tail(missing, unauthored_merges))
        raise MappingError(
            f'{len(missing)} untranslated segment(s), '
            f'{len(unauthored_merges)} unauthored merge(s)',
            console_line='\n'.join(block), exit_code=1,
            core=(missing[0][1] if missing else None),
            refusals={
                'untranslated': [{'page': p, 'core': c} for p, c in missing],
                'unauthored_merges': [{'page': p, 'first_line': f}
                                      for p, f in unauthored_merges],
            })

    # A rotated run is written along its own direction. `right`, `center`,
    # an override's parts, inline markup and a merge's box are horizontal
    # ideas: on such a run they were ignored, misplaced or drawn as literal
    # tags. Each is the author's to take back, so it is refused by name.
    for pno, segs in by_page.items():
        for seg in segs:
            text = seg['text'].strip()
            if text in skip or not is_rotated(*seg_dir(seg)):
                continue
            core = seg['core'] or text
            if seg['core'] in right:
                rotated_features.append((pno, core, 'right'))
            if seg['core'] in center:
                rotated_features.append((pno, core, 'center'))
            if override_for(pno, text):
                rotated_features.append((pno, core, 'override'))
            jp = T.get(seg['core'])
            if jp and has_inline_markup(jp):
                rotated_features.append((pno, core, 'inline markup'))
    if rotated_features:
        block = [f'FAIL: {len(rotated_features)} horizontal-only feature(s) asked of '
                 f'rotated lines, which are drawn along their own direction; take '
                 f'each out of the mapping:']
        for p, c, feature in rotated_features[:CONSOLE_LIST]:
            block.append(f'  p{p}: {feature}: {c[:60]}')
        block.extend(more_not_shown(rotated_features))
        block.extend(refusal_tail(rotated_features))
        raise MappingError(
            f'{len(rotated_features)} horizontal-only feature(s) on rotated lines',
            console_line='\n'.join(block), exit_code=1, core=rotated_features[0][1],
            refusals={'rotated_features': [{'page': p, 'core': c, 'feature': f}
                                           for p, c, f in rotated_features]})

    # Four roles, each falling back to the nearest one the mapping named.
    # A document set in Times Italic used to come back upright Arial: the
    # extractor recorded italic and retypeset had nowhere to put it.
    f_regular = fonts['regular']
    f_bold = fonts.get('bold') or f_regular
    f_italic = fonts.get('italic') or f_regular
    f_bold_italic = fonts.get('bold_italic') or fonts.get('bold') or f_italic
    font_r = pymupdf.Font(fontfile=f_regular)
    font_b = pymupdf.Font(fontfile=f_bold)
    font_i = pymupdf.Font(fontfile=f_italic)
    font_bi = pymupdf.Font(fontfile=f_bold_italic)
    helv = pymupdf.Font('helv')
    hebo = pymupdf.Font('hebo')
    heit = pymupdf.Font('heit')
    hebi = pymupdf.Font('hebi')

    # Which non-regular roles are the regular face wearing another name.
    # The fallback itself is right — a job with one face must still build —
    # but it is silent, so an author can ask for bold, get regular, and
    # report a faithful page (row 31, measured on canary run 3).
    alias_roles = {name for name, path in (('bold', f_bold),
                                           ('italic', f_italic),
                                           ('bold-italic', f_bold_italic))
                   if _same_path(path, f_regular)}
    role_asks = {}

    def note_role(name, key):
        if name in alias_roles:
            role_asks.setdefault(name, []).append(str(key or '?')[:40])

    def note_markup(html, key):
        """The Story engine picks its face from <b>/<i>, not from role()."""
        if re.search(r'<(b|strong)\b', html or '', re.I):
            note_role('bold', key)
        if re.search(r'<(i|em)\b', html or '', re.I):
            note_role('italic', key)

    def role(bold, italic, key=None):
        note_role(_role_name(bold, italic), key)
        if bold and italic:
            return font_bi
        if bold:
            return font_b
        if italic:
            return font_i
        return font_r

    def helv_role(bold, italic):
        if bold and italic:
            return hebi
        if bold:
            return hebo
        if italic:
            return heit
        return helv

    doc = pymupdf.open(stripped)

    # Legacy identity translations can retain the source text-show operator,
    # but only after its page, origin, text, and resource are vouched against
    # the immutable original. Retained segments remain in ``segs`` as layout
    # obstacles; only their redraw is omitted.
    retained_ids = set()
    retained_blocks = {}
    source_inks = {}
    # Identity occurrences the reader could not vouch: redrawn, and reported.
    unretained = []
    if original:
        try:
            ink_segments = [segment for segment in segments
                if not (segment.get('marker') or segment.get('dots') or segment.get('tail') or
                        is_rotated(*seg_dir(segment)) or conf.get('mirror') or
                        segment['core'] in conf.get('center', []) or segment['core'] in conf.get('right', []) or
                        segment['text'].strip() in conf.get('skip', []) or
                        any(merge.get('page') == segment['page'] and segment['text'].strip() in merge.get('lines', [])
                            for merge in conf.get('merges', [])) or
                        any(override.get('page') == segment['page'] and override.get('contains', '') in segment['text']
                            for override in conf.get('overrides', []))) and
                T.get(segment['core']) is not None and T.get(segment['core']) != segment['core'] and
                not (has_inline_markup(T[segment['core']]) or needs_shaping(T[segment['core']]) or is_rtl_text(T[segment['core']]))]
            retained_blocks = prepare_source_retention(
                original, doc, segments, T, cancel=cancel,
                legacy_conf=conf, ink_segments=ink_segments, ink_envelopes=source_inks,
                unretained=unretained, exclude_ids=frozenset(_unretained_ids))
            retained_ids = {id(block['segment']) for blocks in retained_blocks.values()
                            for block in blocks}
            attach_source_retention(doc, retained_blocks)
        except RetentionCancelled:
            pages = len(doc)
            doc.close()
            return RetypesetResult(output=None, cancelled=True, pages=pages, placed=0)
        except Exception:
            doc.close()
            raise

    bad_notices = []
    for i, n in enumerate(notices):
        text = n.get('text')
        if text is None or not str(text).strip():
            bad_notices.append((i, 'text is null or empty — a notice nobody '
                                   'can read is not a notice'))
            continue
        pno = n.get('page')
        if not isinstance(pno, int) or not 0 <= pno < doc.page_count:
            bad_notices.append((i, f'page {pno!r} is not a page of this '
                                   f'document (0-{doc.page_count - 1})'))
            continue
        try:
            bx0, by0, bx1, by1 = (float(v) for v in n.get('box'))
        except (TypeError, ValueError):
            bad_notices.append((i, f'box is not four numbers '
                                   f'[x0, y0, x1, y1]: {n.get("box")!r}'))
            continue
        if bx1 <= bx0 or by1 <= by0:
            bad_notices.append((i, f'box is empty or inverted: '
                                   f'{n.get("box")!r}'))
    if bad_notices:
        block = [f'FAIL: {len(bad_notices)} notice(s) this file cannot place:']
        for i, why in bad_notices[:30]:
            block.append(f'  notices[{i}]: {why}')
        doc.close()
        raise PlacementError(
            f'{len(bad_notices)} notice(s) cannot be placed',
            console_line='\n'.join(block), exit_code=1,
            refusals={'notices': [{'index': i, 'why': w}
                                  for i, w in bad_notices]})

    widg = {p.number: [w.rect for w in p.widgets()] for p in doc}
    # Budgets and mirror pivots use the unrotated frame, the space
    # segments.json and every drawing call share; page.rect is the rotated
    # view and has the wrong width on a /Rotate 90 or 270 page.
    frames = {p.number: unrotated_frame(p) for p in doc}
    form_obstacles = {p.number: _legacy_form_obstacles(
        p, by_page.get(p.number, []) + merge_boxes.get(p.number, [])) for p in doc}
    ink_cache = {}
    font_paths = {id(font_r): f_regular, id(font_b): f_bold,
                  id(font_i): f_italic, id(font_bi): f_bold_italic}
    # Authored HTML resolves its resources here. Defaulting to the
    # mapping's directory rather than the process's cwd is what makes a
    # service that runs from anywhere else get the face it named.
    arch = pymupdf.Archive(resource_root or
                           os.path.dirname(os.path.abspath(trf)) or '.')
    # CSS URLs need quoted, escaped paths: spaces otherwise cause silent
    # font substitution, and Windows separators are interpreted as escapes.
    def css_url(path):
        escaped = re.sub(r'[\x00-\x1f\x7f"\\]',
                         lambda m: f'\\{ord(m[0]):x} ', PurePath(path).as_posix())
        return f'"{escaped}"'

    css_regular = css_url(f_regular)
    css_bold = css_url(f_bold)
    css_italic = css_url(f_italic)
    css_bold_italic = css_url(f_bold_italic)
    css = (f"@font-face {{font-family: tr; src: url({css_regular});}}"
           f"@font-face {{font-family: trb; src: url({css_bold});}}"
           f"@font-face {{font-family: tri; src: url({css_italic});}}"
           f"@font-face {{font-family: trbi; src: url({css_bold_italic});}}"
           "body {font-family: tr; margin: 0; padding: 0;}"
           "b, strong {font-family: trb; font-weight: normal;}"
           "i, em {font-family: tri; font-style: normal;}"
           "b i, b em, i b, em b, strong i, strong em "
           "{font-family: trbi; font-weight: normal; font-style: normal;}")

    drawn_rotated_boxes = {}
    blocked_rotated_neighbours = []

    def note_blocked_neighbour(pno, seg, other):
        item = (pno, seg['core'], other['core'])
        if item not in blocked_rotated_neighbours:
            blocked_rotated_neighbours.append(item)

    def record_blocked_neighbours(pno, seg, segs, *, left=False):
        anchor = seg['bbox'][2] if left else seg['origin'][0]
        for other, box in _row_obstacles(seg, segs, drawn_rotated_boxes, left=left):
            if id(other) not in drawn_rotated_boxes:
                continue
            no_room = box[2] + 1.5 >= anchor if left else box[0] - 1.5 <= anchor
            if no_room:
                note_blocked_neighbour(pno, seg, other)

    def right_limit(pno, seg, segs):
        segs = segs + merge_boxes.get(pno, [])
        limit = _right_limit(frames[pno], seg, segs, widg.get(pno, []),
                             drawn_rotated_boxes)
        if limit <= seg['origin'][0]:
            record_blocked_neighbours(pno, seg, segs)
        return limit

    def left_limit(pno, seg, segs, *, measured=True):
        """How far back a RIGHT-anchored run may grow (row 28).

        A `right` core keeps the source's right edge and grows leftward, so
        its width budget is the room on its left — the nearest same-row
        obstacle's right edge, or the page margin — not the room on its
        right, which is where the field it labels usually sits.
        """
        segs = segs + merge_boxes.get(pno, [])
        y0, y1 = seg['bbox'][1], seg['bbox'][3]
        lim = 32.0
        for _, box in _row_obstacles(seg, segs,
                                     drawn_rotated_boxes if measured else None, left=True):
            lim = max(lim, box[2])
        for wr in widg.get(pno, []):
            if wr.x1 < seg['bbox'][2] - 1 and not (
                    wr.y1 < y0 + 1 or wr.y0 > y1 - 1):
                if wr.x1 < seg['bbox'][0] + 2:
                    lim = max(lim, wr.x1)
        limit = lim + 1.5
        if measured and limit >= seg['bbox'][2]:
            record_blocked_neighbours(pno, seg, segs, left=True)
        return limit

    def centered_room(pno, seg, segs, room):
        """Constrain both halves about the existing center, for rotated ink."""
        cx = (seg['bbox'][0] + seg['bbox'][2]) / 2
        y0, y1 = seg['bbox'][1], seg['bbox'][3]
        for other in segs:
            box = drawn_rotated_boxes.get(id(other))
            if box is None or box[3] <= y0 or box[1] >= y1:
                continue
            if box[0] >= cx:
                # The existing center path clamps its left edge at ox-200.
                # Both that placement and exact centering must fit.
                available = min(2 * (box[0] - 1.5 - cx),
                                box[0] - 1.5 - (seg['origin'][0] - 200))
            elif box[2] <= cx:
                available = 2 * (cx - box[2] - 1.5)
            else:
                available = 0.0
            room = min(room, available)
            if available <= 0:
                note_blocked_neighbour(pno, seg, other)
        return room

    rotated_shaped = []
    leaders_dropped = []
    # Pass-through runs with characters the source cannot map to Unicode
    # (U+FFFD). They are never redrawn from that text; see replay_unmapped.
    unmapped_jobs = {}
    unmapped_dropped = []
    unmapped_kept = 0
    source_glyph_jobs = {}
    source_glyphs_refused = []
    glyph_misses = []
    seen_glyph_miss = set()

    def check_glyphs(pno, font, text, key, label):
        for ch in missing_glyphs(font, text):
            sig = (pno, label, ch)
            if sig in seen_glyph_miss:
                continue
            seen_glyph_miss.add(sig)
            glyph_misses.append((pno, label, ch, key))

    # Two loops, one budget. `total` counts pages AND merge jobs, because a
    # counter of pages alone reaches 100% and then keeps working.
    _total = len(doc) + len(merge_jobs)
    _done = 0

    def _tick():
        nonlocal _done
        _done += 1
        if progress is not None:
            progress(_done, _total)

    def _cancelled():
        return cancel is not None and cancel()

    for pno, page in enumerate(doc):
        if _cancelled():
            npages = len(doc)
            doc.close()
            return RetypesetResult(output=None, cancelled=True,
                                   pages=npages, placed=0)
        segs = by_page.get(pno, [])
        frame = frames[pno]
        # One TextWriter per distinct source color. A single black writer is
        # how white-on-dark headings silently become black-on-dark: the ink
        # gate barely moves because the band was already dark.
        writers = {}
        rtl_jobs = []
        shaped_jobs = []
        # One writer per rotated run: a morph rotates a whole TextWriter
        # about a single pivot, and each run must turn about its own origin.
        rot_jobs = []
        inline_jobs = []

        def place_rotated(px, py, parts_fs, cint, dx, dy, rtl=False,
                          logical=''):
            rtw = pymupdf.TextWriter(page.rect)
            xx = px
            for item in parts_fs:
                t, f, fsz = item[:3]
                # A fourth element is an explicit advance (a list marker
                # whose body starts at the measured source offset).
                adv = item[3] if len(item) > 3 else f.text_length(t, fsz)
                rtw.append((xx, py), t, font=f, fontsize=fsz,
                           right_to_left=rtl)
                xx += adv
            morph = rotation_morph(px, py, dx, dy)
            box = _rotated_text_box(page, rtw, morph)
            if box is not None:
                drawn_rotated_boxes[id(seg)] = box
            rot_jobs.append((rtw, cint, morph, logical if rtl else ''))

        def W(cint):
            if cint not in writers:
                writers[cint] = pymupdf.TextWriter(page.rect)
            return writers[cint]

        def rgb_of(cint):
            return (((cint >> 16) & 255) / 255.0,
                    ((cint >> 8) & 255) / 255.0,
                    (cint & 255) / 255.0)

        # Plan every rotated run before allocating horizontal room. Stable
        # ordering preserves each group's order, and an all-horizontal page
        # follows its existing path unchanged. The actual drawing still
        # happens below, after all runs have been planned.
        for seg in sorted(segs, key=lambda s: not is_rotated(*seg_dir(s))):
            tw = W(int(seg.get('color', 0)))
            text = seg['text'].strip()
            if text in skip:
                continue
            if id(seg) in retained_ids:
                continue
            ov = override_for(pno, text)
            if (seg['core'] in source_glyphs and T.get(seg['core']) == seg['core']
                    and not ov):
                source_glyph_jobs.setdefault(pno, []).append(seg)
                continue
            fs = seg['size']
            cap = scale_caps.get(str(seg.get('id')))
            ox, oy = seg['origin']
            dx, dy = seg_dir(seg)
            rot = is_rotated(dx, dy)
            if ov:
                for part in ov['parts']:
                    t = part['text']
                    f = role(part.get('bold'), part.get('italic'),
                             ov.get('contains') or t)
                    x = part.get('x', ox)
                    check_glyphs(pno, f, t, ov.get('contains') or t,
                                 _role_name(part.get('bold'),
                                            part.get('italic')))
                    w = f.text_length(t, fs)
                    maxw = part.get('max_width', 10_000)
                    fs2 = fitted_size(fs, w, maxw)
                    if fs and fs2 < fs:
                        consider_ratio(pno, ov.get('contains') or t, fs2 / fs)
                    if needs_shaping(t):
                        if rot:
                            rotated_shaped.append((pno, t))
                            continue
                        avail = maxw if maxw < 5000 else frame.width - 32 - x
                        shaped_jobs.append((mirror_left(x, w, frame.width), oy, t,
                                            bool(part.get('bold')), fs, int(seg.get('color', 0)),
                                            avail, ov.get('contains') or t,
                                            bool(part.get('italic'))))
                    elif rot:
                        place_rotated(x, oy, [(t, f, fs2)],
                                      int(seg.get('color', 0)), dx, dy,
                                      rtl=is_rtl_text(t), logical=t)
                    elif is_rtl_text(t):
                        rtl_jobs.append((x, oy, t, f, fs2, int(seg.get('color', 0)), t))
                    else:
                        tw.append((mirror_left(x, f.text_length(t, fs2),
                                               frame.width), oy),
                                  t, font=f, fontsize=fs2)
                continue
            core, marker, dots, tail = (
                seg['core'], seg['marker'], seg['dots'], seg['tail'])
            # The whitespace the source printed after the marker (row 19).
            # A segments.json from before the key gets one space, which is
            # what every build wrote until then: a stale work directory
            # must rebuild, not crash.
            gap = seg.get('gap')
            if gap is None:
                gap = ' '
            mk = marker + gap
            # The marker's own weight: a line that mixes weights records its bold
            # runs, so a bold "(a)" before a regular sentence stays bold, and a
            # regular one before a bold lead stays regular (W-4 Step 2, 2026-10-13).
            mbold = _marker_bold(seg)
            # Row 23: where the body's first glyph was in the source, measured
            # from its characters. The marker is drawn in Helvetica whatever
            # the source font, so Helvetica's advance is right only for a
            # Helvetica-metric source. An old segments.json has no key and
            # keeps the row-19 placement. Scales with the run when it shrinks.
            body_dx = seg.get('body_dx')

            def marker_adv(mfont, fsz):
                if body_dx is not None:
                    return body_dx * (fsz / fs if fs else 1.0)
                return mfont.text_length(mk, fsz)

            def part_adv(i, t, f, fsz):
                if i == 0 and marker:
                    return marker_adv(f, fsz)
                return f.text_length(t, fsz)
            jp = T.get(core)
            if jp is None:  # passthrough: an untranslated core refused above
                raw = seg['text'].rstrip()
                if UNMAPPED in raw:
                    # Drawn from U+FFFD this is a row of replacement boxes, or
                    # a glyph miss in a face without one (dogfood 2026-10-10,
                    # Form 1040 of 1913 and DS-64). Its source glyphs are
                    # replayed after the page loop, or it is reported.
                    unmapped_jobs.setdefault(pno, []).append(seg)
                    continue
                f = helv_role(seg['bold'], seg.get('italic'))
                label = 'Helvetica (pass-through)'
                # Base-14 Helvetica has no ▲ ► ✓ ☐ and the like. A pass-through run
                # Helvetica cannot draw is set in the job's own role face instead,
                # which may carry those symbols; check_glyphs still refuses when
                # neither face has them (dogfood 2026-10-10, six government forms).
                if any(not ch.isspace() and not f.has_glyph(ord(ch)) for ch in raw):
                    f = role(seg['bold'], seg.get('italic'))
                    label = 'role face (pass-through)'
                check_glyphs(pno, f, raw, raw, label)
                if rot:
                    place_rotated(ox, oy, [(raw, f, fs)],
                                  int(seg.get('color', 0)), dx, dy)
                    continue
                psize = fs * cap if cap else fs
                tw.append((mirror_left(ox, f.text_length(raw, psize), frame.width),
                           oy), raw, font=f, fontsize=psize)
                continue
            if has_inline_markup(jp) and not rot:
                # Mixed weights inside one line: the Story engine resolves
                # <b>/<i> against the four font roles, the way merges do.
                plain = strip_inline_markup(jp).replace('‖', '')
                check_glyphs(pno, font_r, plain, core, 'regular')
                if re.search(r'<(b|strong)\b', jp, re.I):
                    check_glyphs(pno, font_b, plain, core, 'bold')
                if re.search(r'<(i|em)\b', jp, re.I):
                    check_glyphs(pno, font_i, plain, core, 'italic')
                bx = ox
                imarker = None
                if marker:
                    # Drawn after the body, at the body's own scale: a marker kept
                    # at full size beside a shrunk body looks like a second size.
                    # The body keeps its source start, so it still lines up with
                    # the item's continuation lines.
                    mfont = helv_role(mbold, seg.get('italic'))
                    imarker = (ox, mk, mfont)
                    bx += marker_adv(mfont, fs)
                # The drawn rules and controls beside the source caption bound
                # this line as they bound a plain one: a writing cell whose
                # widget covers only its bottom is still the reader's to write in
                # (W-4 Step 4(a)/(b), 2026-10-13).
                limit = right_limit(pno, seg, segs)
                if not mirror:
                    limit = min(limit, _legacy_field_limit(seg, form_obstacles[pno]))
                avail = limit - bx
                # A line that mixes weights (`bold_runs`) is drawn regular under its
                # markup: the <b> runs carry its bold, so a mostly bold source line
                # keeps its regular words regular.
                inline_jobs.append((bx, oy, inline_markup_html(jp.replace('‖', '')),
                                    bool(seg['bold']) and not seg.get('bold_runs'),
                                    bool(seg.get('italic')),
                                    fs * cap if cap else fs, int(seg.get('color', 0)),
                                    avail, core, plain, imarker, cap or 1.0))
                continue
            parts = []
            if marker:
                mk_font = helv_role(mbold, seg.get('italic'))
                check_glyphs(pno, mk_font, mk, core,
                             'Helvetica (list marker)')
                parts.append((mk, mk_font))
            if '‖' in jp:
                bp, rp = jp.split('‖', 1)
                parts.append((bp, role(True, seg.get('italic'), core)))
                parts.append((rp, role(False, seg.get('italic'), core)))
            else:
                parts.append((jp, role(seg['bold'], seg.get('italic'), core)))
            for t, f in parts:
                check_glyphs(pno, f, t, core, _font_label(f, {
                    id(font_r): 'regular', id(font_b): 'bold',
                    id(font_i): 'italic', id(font_bi): 'bold-italic'}))
            if rot and (dots or tail):
                # Dot leaders are refilled only along a horizontal line. On a
                # rotated one they are dropped, but the tail after them (the
                # currency sign extract records) is content and is kept.
                if tail:
                    check_glyphs(pno, helv, tail, core, 'Helvetica (tail)')
                    parts.append((' ' + tail, helv))
                if dots:
                    leaders_dropped.append((pno, core))
            wsum = sum(part_adv(i, t, f, fs) for i, (t, f) in enumerate(parts))
            rtl_body = is_rtl_text(jp)
            shaped = needs_shaping(jp)
            def leader_geometry():
                end = seg['bbox'][2] - (
                    helv.text_length(tail, fs) + 2 if tail else 0)
                return end, end - ox - 8

            def fill_leaders(label_end, cur_end):
                """Refill dots from the label's end to the original right edge.

                Anchored right and never longer than the source run, so
                leaders cannot invade a mid-line checkbox gap.
                """
                dw = helv.text_length('.', fs)
                dstart = max(label_end + 1, cur_end - len(dots) * dw)
                n = max(0, int((cur_end - dstart) / dw))
                if n:
                    tw.append((dstart, oy), '.' * n, font=helv, fontsize=fs)
                if tail:
                    check_glyphs(pno, helv, tail, core, 'Helvetica (tail)')
                    tw.append((cur_end + 2, oy), tail, font=helv, fontsize=fs)

            if dots and (shaped or rtl_body) and not mirror and not rot:
                # The label goes through the Story engine (shaped) or a
                # right-to-left TextWriter; the leaders are still ordinary
                # Helvetica dots, so they are refilled here instead of
                # being dropped with the whole dot path.
                cur_end, label_max = leader_geometry()
                body = jp.replace('‖', '')
                cint = int(seg.get('color', 0))
                body_font = role(seg['bold'], seg.get('italic'), core)
                bx = ox
                if marker:
                    mfont = helv_role(mbold, seg.get('italic'))
                    tw.append((bx, oy), mk, font=mfont, fontsize=fs)
                    bx += marker_adv(mfont, fs)
                room = max(label_max - (bx - ox), 1.0)
                if shaped:
                    # Shaping usually narrows a run (ligatures, conjuncts),
                    # so the unshaped measurement is a safe upper bound for
                    # the box; anything wider is scaled by the engine and
                    # caught by the 0.7x gate.
                    width = min(room, body_font.text_length(body, fs))
                    shaped_jobs.append((bx, oy, body, bool(seg['bold']), fs,
                                        cint, width, core,
                                        bool(seg.get('italic'))))
                else:
                    w = body_font.text_length(body, fs)
                    fs2 = fitted_size(fs, w, room)
                    if fs and fs2 < fs:
                        consider_ratio(pno, core, fs2 / fs)
                    check_glyphs(pno, body_font, body, core,
                                 _role_name(seg['bold'], seg.get('italic')))
                    rtl_jobs.append((bx, oy, body, body_font, fs2, cint, body))
                    width = body_font.text_length(body, fs2)
                fill_leaders(bx + width, cur_end)
                continue

            if dots and not rtl_body and not shaped and not mirror and not rot:
                cur_end, label_max = leader_geometry()
                fs2 = fitted_size(fs, wsum, label_max)
                if fs and fs2 < fs:
                    consider_ratio(pno, core, fs2 / fs)
                x = ox
                for i, (t, f) in enumerate(parts):
                    tw.append((x, oy), t, font=f, fontsize=fs2)
                    x += part_adv(i, t, f, fs2)
                fill_leaders(x, cur_end)
                continue
            if rot:
                maxw = direction_limit(frame, seg, dx, dy)
            elif core in center:
                # Keep legacy page/widget/source budgets, then reserve the
                # measured rotated neighbours on either side of the center.
                maxw = (seg['bbox'][2] - left_limit(pno, seg, segs, measured=False)
                        if core in right else
                        _right_limit(frame, seg, segs + merge_boxes.get(pno, []),
                                     widg.get(pno, [])) - ox)
                maxw = centered_room(pno, seg, segs, maxw)
            elif core in right:
                # Measured against the room it will occupy, not the room to
                # the right of an origin it is not going to keep (row 28).
                maxw = seg['bbox'][2] - left_limit(pno, seg, segs)
            else:
                maxw = right_limit(pno, seg, segs) - ox
            if not (rot or mirror or core in center or core in right or shaped or rtl_body):
                field_room = _legacy_field_limit(seg, form_obstacles[pno])-ox
                if field_room <= 0:
                    doc.close()
                    raise PlacementError('no room beside source field', page=pno, key=core, scale=0,
                        refusals={'overflow': [{'page': pno, 'scale': 0, 'core': core}]})
                maxw = min(maxw, field_room)
            fs2 = fitted_size(fs, wsum, maxw)
            if not (rot or mirror or core in center or core in right or shaped or rtl_body) and wsum > 0:
                # fitted_size's legacy one-point minimum must not enlarge the
                # actual room reserved beside a field.
                fs2 = min(fs2, fs * maxw / wsum)
            if id(seg) in source_inks and all(id(font) in font_paths for _, font in parts):
                target_top, target_bottom, target_right, target_offset = 0., 0., 0., 0.
                for target_text, font in parts:
                    missing = set(target_text) - ink_cache.get(id(font), {}).keys()
                    if missing:
                        ink_cache.setdefault(id(font), {}).update(_glyph_inks(font_paths[id(font)], font, missing))
                    for char in target_text:
                        ink = ink_cache[id(font)][char]
                        if ink is not None:
                            target_top = min(target_top, ink[1])
                            target_bottom = max(target_bottom, ink[3])
                            target_right = max(target_right, target_offset+ink[2])
                        target_offset += font.text_length(char, 1)
                source_top, _ = source_inks[id(seg)]
                if target_top < 0:
                    fs2 = min(fs2, (oy-source_top) / -target_top)
                if not (rot or mirror or core in center or core in right or shaped or rtl_body) and wsum > 0:
                    limit = min(_legacy_field_limit(seg, form_obstacles[pno]),
                        _legacy_retained_limit(seg, oy+target_top*fs2,
                            oy+target_bottom*fs2, retained_blocks.get(pno, ())))
                    if math.isfinite(limit) and target_right > 0:
                        # A glyph can paint beyond its advance. Fit the actual
                        # rightmost outline, including every preceding advance.
                        # Native float32 positions can round a boundary fit
                        # outward. Reserve a few coordinate ulps before painting.
                        reserve = max(1e-4, abs(limit)*2**-20)
                        fs2 = min(fs2, (limit-ox-reserve) / target_right)
                if fs2 <= 0:
                    doc.close()
                    raise PlacementError('no room above source baseline', page=pno, key=core, scale=0,
                        refusals={'overflow': [{'page': pno, 'scale': 0, 'core': core}]})
            if cap and not shaped:
                fs2 = min(fs2, fs * cap)
            if fs and fs2 < fs and not shaped:
                consider_ratio(pno, core, fs2 / fs)
            x = ox
            run_w = sum(part_adv(i, t, f, fs2) for i, (t, f) in enumerate(parts))
            if core in center and not rot:
                cx = (seg['bbox'][0] + seg['bbox'][2]) / 2
                x = max(ox - 200, cx - run_w / 2)
            elif core in right and not rot:
                # Anchor the run's RIGHT edge where the source's was. A
                # longer translation grows leftward instead of over the
                # rule the column was tucked against.
                x = max(0.0, seg['bbox'][2] - run_w)
            if not rot:
                x = mirror_left(x, run_w, frame.width)
            if rot:
                if shaped:
                    # Drawing a shaped script flat on a rotated label is the
                    # exact silent defect this program refuses to ship.
                    rotated_shaped.append((pno, core))
                    continue
                place_rotated(x, oy, [(t, f, fs2, part_adv(i, t, f, fs2))
                                      for i, (t, f) in enumerate(parts)],
                              int(seg.get('color', 0)), dx, dy,
                              rtl=rtl_body, logical=jp.replace('‖', ''))
                continue
            if shaped:
                # Marker stays a TextWriter run in Helvetica; the body is
                # shaped at the ORIGINAL size and the engine's own scale
                # feeds the overflow gate.
                bx = x
                if marker:
                    mfont = helv_role(mbold, seg.get('italic'))
                    tw.append((bx, oy), mk, font=mfont, fontsize=fs)
                    bx += marker_adv(mfont, fs)
                shaped_jobs.append((bx, oy, jp.replace('‖', ''), bool(seg['bold']), fs,
                                    int(seg.get('color', 0)), maxw - (bx - x), core,
                                    bool(seg.get('italic'))))
                continue
            if rtl_body:
                logical = jp.replace('‖', '')
                rtl_jobs.append((x, oy, logical,
                                 role(seg['bold'], seg.get('italic'), core), fs2,
                                 int(seg.get('color', 0)), logical))
            else:
                for i, (t, f) in enumerate(parts):
                    tw.append((x, oy), t, font=f, fontsize=fs2)
                    x += part_adv(i, t, f, fs2)
        for cint, w in writers.items():
            w.write_text(page, color=rgb_of(cint))
        for (rtw, cint, morph, logical) in rot_jobs:
            rtw.write_text(page, color=rgb_of(cint), morph=morph)
            if logical:
                wrap_last_bt_actualtext(page, logical)
        for (rx, ry, t, fnt, fsz, cint, logical) in rtl_jobs:
            rtw = pymupdf.TextWriter(page.rect)
            rtw.append((rx, ry), t, font=fnt, fontsize=fsz, right_to_left=True)
            rtw.write_text(page, color=rgb_of(cint))
            wrap_last_bt_actualtext(page, logical)
        for (ix, iy, body, ibold, iital, ifs, icint, iavail, ikey,
             iplain, imarker, icap) in inline_jobs:
            note_markup(body, ikey)
            scale = place_story_line(page, ix, iy, body, ibold, iital, ifs,
                                     icint, iavail, css, arch)
            consider_ratio(pno, ikey, scale * icap)
            if imarker:
                mx, mtext, mfont = imarker
                mtw = pymupdf.TextWriter(page.rect)
                mtw.append((mx, iy), mtext, font=mfont, fontsize=ifs * scale)
                mtw.write_text(page, color=rgb_of(icint))
        for (sx, sy, text, bold, fsz, cint, avail, key, ital) in shaped_jobs:
            scale = place_shaped(page, sx, sy, text, bold, fsz, cint, avail,
                                 css, arch, italic=ital)
            consider_ratio(pno, key, scale)
        _tick()

    if unmapped_jobs:
        if original:
            with pymupdf.open(original) as source_doc:
                for pno, jobs in sorted(unmapped_jobs.items()):
                    kept, refused = replay_unmapped(source_doc, doc[pno], pno, jobs)
                    unmapped_kept += len(kept)
                    unmapped_dropped.extend(refused)
        else:
            unmapped_dropped.extend(
                {'page': pno, 'segment_id': seg.get('id'), 'core': seg.get('core'),
                 'reason': 'no-original'}
                for pno, jobs in sorted(unmapped_jobs.items()) for seg in jobs)

    if source_glyph_jobs:
        if original:
            with pymupdf.open(original) as source_doc:
                for pno, jobs in sorted(source_glyph_jobs.items()):
                    _, refused = replay_unmapped(source_doc, doc[pno], pno, jobs)
                    source_glyphs_refused.extend(refused)
        else:
            source_glyphs_refused.extend(
                {'page': pno, 'segment_id': seg.get('id'), 'core': seg.get('core'),
                 'reason': 'no-original'}
                for pno, jobs in sorted(source_glyph_jobs.items()) for seg in jobs)

    for (pno, r, html, align, size, lh, color, merge_key, merge_opt,
         boxed) in merge_jobs:
        if _cancelled():
            npages = len(doc)
            doc.close()
            return RetypesetResult(output=None, cancelled=True,
                                   pages=npages, placed=0)
        plain_html = re.sub(r'<[^>]+>', '', html or '')
        note_markup(html, merge_key)
        check_glyphs(pno, font_r, plain_html, merge_key, 'regular')
        if re.search(r'<(b|strong)\b', html or '', re.I):
            check_glyphs(pno, font_b, plain_html, merge_key, 'bold')
        pw = frames[pno].width
        if mirror:
            r = pymupdf.Rect(pw - r.x1, r.y0, pw - r.x0, r.y1)
            align = {'left': 'right', 'right': 'left'}.get(align, align)
        # A union bbox is a measurement of ink and runs tight, so it gets a
        # little slop; an authored box is an instruction and is used as
        # given.
        rect = (r if boxed
                else pymupdf.Rect(r.x0, r.y0 - 0.5, r.x1 + 1.5, r.y1 + 1.5))
        hexcol = f'#{color:06x}'
        css_size, fit_ratio = merge_css_size(size)
        h = (f'<div style="font-size:{css_size:.1f}px; line-height:{lh}; '
             f'color:{hexcol}; text-align:{align};">{html}</div>')
        _, scale = doc[pno].insert_htmlbox(rect, h, css=css, archive=arch, scale_low=0)
        # Both shrinks land on the page, so both belong in the ratio: the fit
        # allowance above and whatever the engine took on top of it.
        consider_ratio(pno, merge_key, scale * fit_ratio, opted=merge_opt)
        plain = re.sub(r'<[^>]+>', '', html)
        if is_rtl_text(plain) or needs_shaping(plain):
            wrap_last_stream_actualtext(doc[pno], plain)
        _tick()

    # The notice compliance.md requires is the one thing the author has to
    # add that is not the translation of an existing string. Four of five
    # canary runs wrote their own script for it, each re-deriving fonts,
    # placement and the canonical-layer rewrite by hand (lane B, P7). It
    # goes through everything a merge goes through; only the rect is the
    # author's rather than the source's, because only the author can see
    # what the page has room for.
    for i, n in enumerate(notices):
        pno = n['page']
        parts = [p for p in str(n['text']).split('‖')]
        size = float(n.get('size') or NOTICE_SIZE)
        key = f'notices[{i}]'
        plain = ' '.join(p.strip() for p in parts if p.strip())
        check_glyphs(pno, font_r, plain, key, 'regular')
        if n.get('bold_lead'):
            note_role('bold', key)
            check_glyphs(pno, font_b, parts[0], key, 'bold')
            body = (f'<b>{story_text(parts[0].strip())}</b> '
                    + ' '.join(story_text(p.strip())
                               for p in parts[1:] if p.strip()))
        else:
            body = ' '.join(story_text(p.strip())
                            for p in parts if p.strip())
        r = pymupdf.Rect(*(float(v) for v in n['box']))
        if mirror:
            pw = frames[pno].width
            r = pymupdf.Rect(pw - r.x1, r.y0, pw - r.x0, r.y1)
        h = (f'<div style="font-size:{size:.1f}px; line-height:{NOTICE_LH}; '
             f'color:#000000; text-align:left;">{body}</div>')
        _, scale = doc[pno].insert_htmlbox(r, h, css=css, archive=arch,
                                           scale_low=0)
        consider_ratio(pno, key, scale)
        if is_rtl_text(plain) or needs_shaping(plain):
            wrap_last_stream_actualtext(doc[pno], plain)
    if notices:
        log.info(f'notices: placed {len(notices)} on '
              f'{len({n["page"] for n in notices})} page(s); look at the '
              f'render — no gate judges where a notice sits')

    # One build can fail for several reasons at once. The console prints them
    # all, in this order, exactly as it always has; the exception carries all
    # of them as data so a consumer fixes the mapping in one pass instead of
    # rebuilding to discover the next reason.
    block = []
    if overflow:
        block.append(f'FAIL: {len(overflow)} segments scaled below '
                     f'{SCALE_MIN}x (shorten the translation or add '
                     f'allow_scale):')
        for p, ratio, c in overflow[:30]:
            block.append(f'  p{p} {ratio:.2f}x: {c}')
        block.extend(more_not_shown(overflow))
    if glyph_misses:
        block.append(f'FAIL: {len(glyph_misses)} character(s) the chosen font '
                     f'cannot draw. MuPDF substitutes a fallback face '
                     f'mid-string or draws a box and reports nothing; pick a '
                     f'font that covers the target script '
                     f'(references/fonts.md):')
        for pno, label, ch, key in glyph_misses[:30]:
            name = unicodedata.name(ch, '?')
            # The console keeps its 40-character abbreviation — this line is
            # read by a person and the whole core would drown it. The
            # structured refusal below carries the key in full, because a
            # consumer matches it against its own translation map and two
            # cores can share a 40-character prefix.
            block.append(f'  p{pno} [{label}] U+{ord(ch):04X} {name} ({ch}) '
                         f'in: {(key or "")[:40]}')
        block.extend(more_not_shown(glyph_misses))
    if rotated_shaped:
        block.append(f'FAIL: {len(rotated_shaped)} rotated segments whose '
                     f'target needs shaping. The Story engine places shaped '
                     f'runs upright; drawing them flat on a rotated label '
                     f'would ship confidently wrong text:')
        for p, c in rotated_shaped[:30]:
            block.append(f'  p{p}: {c}')
        block.extend(more_not_shown(rotated_shaped))
    if blocked_rotated_neighbours:
        block.append(f'FAIL: {len(blocked_rotated_neighbours)} horizontal run(s) '
                     'have no room beside a drawn rotated neighbour; shorten '
                     'the neighbour translation (allow_scale cannot free the anchor):')
        for p, core, neighbour in blocked_rotated_neighbours[:CONSOLE_LIST]:
            block.append(f'  p{p} {core}: rotated neighbour {neighbour}')
        block.extend(more_not_shown(blocked_rotated_neighbours))
    if source_glyphs_refused:
        block.append(f'FAIL: {len(source_glyphs_refused)} source_glyphs run(s) whose '
                     'source glyphs cannot be vouched as plain filled outlines:')
        for row in source_glyphs_refused[:CONSOLE_LIST]:
            block.append(f"  p{row['page']} {row['reason']}: {str(row['core'])[:60]}")
        block.extend(more_not_shown(source_glyphs_refused))
    block.extend(refusal_tail(overflow, glyph_misses, rotated_shaped,
                              blocked_rotated_neighbours, source_glyphs_refused))
    if (overflow or rotated_shaped or glyph_misses or blocked_rotated_neighbours or
            source_glyphs_refused):
        doc.close()
        refusals = {
            # Always empty here: an untranslated core refused before drawing.
            # The key stays, so every refusal from this site has one shape.
            'untranslated': [],
            'overflow': [{'page': p, 'scale': r, 'core': c}
                         for p, r, c in overflow],
            'glyph_misses': [{'page': p, 'role': label, 'char': ch,
                              'codepoint': f'U+{ord(ch):04X}',
                              'name': unicodedata.name(ch, '?'),
                              'core': key or ''}
                             for p, label, ch, key in glyph_misses],
            'rotated_shaped': [{'page': p, 'core': c}
                               for p, c in rotated_shaped],
        }
        if blocked_rotated_neighbours:
            refusals['rotated_neighbours'] = [
                {'page': p, 'core': c, 'neighbour': n}
                for p, c, n in blocked_rotated_neighbours]
        if source_glyphs_refused:
            refusals['source_glyphs'] = [
                {'page': row['page'], 'core': row['core'], 'reason': row['reason']}
                for row in source_glyphs_refused]
        summary = ', '.join(
            f'{len(v)} {k.replace("_", " ")}' for k, v in refusals.items() if v)
        # Precedence names the exception; `refusals` carries every kind. A
        # glyph the face cannot draw is first because it is the one a
        # consumer cannot fix by editing wording.
        if glyph_misses:
            pno, label, ch, key = glyph_misses[0]
            raise GlyphError(summary, console_line='\n'.join(block),
                             exit_code=1, page=pno, char=ch, face=label,
                             refusals=refusals)
        first = (overflow[0] if overflow else None)
        unscaled = (rotated_shaped[0] if rotated_shaped
                    else blocked_rotated_neighbours[0][:2] if blocked_rotated_neighbours
                    else (source_glyphs_refused[0]['page'], source_glyphs_refused[0]['core'])
                    if source_glyphs_refused else None)
        placement_exc = PlacementError(
            summary, console_line='\n'.join(block), exit_code=1,
            page=(first[0] if first else unscaled[0]),
            key=(first[2] if first else unscaled[1]),
            scale=(first[1] if first else None), refusals=refusals)
        # Only `overflow` is "a run scaled below the floor" — a rotated run
        # that needs shaping refuses for an unrelated reason and is not B1
        # evidence, even though it can share this raise site.
        if overflow and resolved_capture:
            capture.capture_refusal(
                resolved_capture, placement_exc, stripped=stripped, segf=segf,
                trf=trf, out=out, original=original, fonts=fonts,
                mapping_format=document.format, legacy_conf=conf,
                scale_report=scale_report, resource_root=resource_root)
        raise placement_exc

    if mirror:
        for page in doc:
            pw = frames[page.number].width
            for w in list(page.widgets() or []):
                r = w.rect
                w.rect = pymupdf.Rect(pw - r.x1, r.y0, pw - r.x0, r.y1)
                w.update()
            for lnk in list(page.get_links() or []):
                # get_links reports 'from' in the rotated view and
                # insert_link reads it unrotated: on a /Rotate page the
                # round trip alone moved the link.
                r = pymupdf.Rect(lnk['from']) * page.derotation_matrix
                flipped = dict(lnk)
                flipped['from'] = pymupdf.Rect(pw - r.x1, r.y0, pw - r.x0, r.y1)
                page.delete_link(lnk)
                page.insert_link(flipped)

    meta_report = apply_document_metadata(
        doc, segd.get('document'), T, conf.get('lang'))
    if meta_report['title']:
        log.info(f'metadata: /Title -> {meta_report["title"][:60]}')
    if meta_report['outline']:
        log.info(f'metadata: {meta_report["outline"]} outline title(s) translated')
    if meta_report['lang']:
        log.info(f'metadata: /Lang -> {meta_report["lang"]}'
              + (' (and dc:language)' if meta_report['xmp'] else ''))
    if meta_report['struct_tree_removed']:
        log.info('metadata: removed the orphaned /StructTreeRoot and set '
              '/MarkInfo /Marked false — the tags described text that no '
              'longer exists. Tell the user the file is no longer tagged.')

    for name in ('bold', 'italic', 'bold-italic'):
        asks = role_asks.get(name)
        if not asks:
            continue
        shown = ', '.join(dict.fromkeys(asks))
        log.info(f'font roles: "{name}" resolved to the regular face; '
              f'{len(asks)} run(s) asked for it ({shown[:100]}'
              f'{"…" if len(shown) > 100 else ""}). Name it in the delivery '
              f'or give the role its own file.')

    if scaled:
        log.info(f'scaled runs ({len(scaled)}) — reword them or name them in '
              f'the delivery:')
        for r in sorted(scaled, key=lambda r: (r['page'], r['ratio']))[:30]:
            log.info(f'  p{r["page"]} {r["ratio"]:.2f}x: {r["key"][:60]}')
        if len(scaled) > 30:
            log.info(f'  … {len(scaled) - 30} more in {SCALE_REPORT}')
    # A fixed name beside `out` is what two concurrent jobs writing into one
    # directory raced for. The default keeps that path so no CLI moves; a
    # service passes its own, or None to write nothing and read the runs off
    # the result instead.
    if scale_report is _DEFAULT:
        report = os.path.join(os.path.dirname(os.path.abspath(out)),
                              SCALE_REPORT)
    else:
        report = scale_report
    if report and not retained_blocks:
        write_scale_report(sorted(scaled, key=lambda r: (r['page'], r['key'])),
                           report)
    if resolved_capture and resolved_below is not None:
        # Nothing refused: this build is about to succeed. A consumer whose
        # floor sits above SCALE_MIN reverts these cores itself afterwards,
        # so the case that costs them never reaches the refusal path.
        capture.capture_near_floor(
            resolved_capture, scaled, resolved_below, stripped=stripped,
            segf=segf, trf=trf, out=out, original=original, fonts=fonts,
            mapping_format='legacy', legacy_conf=conf, scale_report=report,
            resource_root=resource_root)

    # The glyphs are right either way; this is the text layer.
    placed = [t for t in T.values() if isinstance(t, str)]
    placed.extend(re.sub(r'<[^>]+>', '', m.get('html') or '') for m in merges)
    for o in overrides:
        placed.extend(part.get('text') or '' for part in o.get('parts') or [])
    placed.extend(str(n.get('text') or '').replace('‖', ' ') for n in notices)
    for seg in segments:
        placed.extend(((seg.get('marker') or '')
                       + (' ' if seg.get('gap') is None else seg['gap']),
                       seg.get('tail') or ''))
        if seg.get('passthrough'):
            placed.append(seg.get('text') or '')
    # Every role face that can have drawn a run (R-04). Only regular and
    # bold used to be rewritten, so an italic cut of Arial or Times, whose
    # space and NBSP share a glyph, reported NBSP for every space.
    fontfiles = list(dict.fromkeys([f_regular, f_bold, f_italic, f_bold_italic]))
    pending = None
    retry_ids = ()
    try:
        save_path = out
        if retained_blocks:
            descriptor, pending = tempfile.mkstemp(
                prefix='.pdf-translate-retained-', suffix='.pdf',
                dir=os.path.dirname(os.path.abspath(out)))
            os.close(descriptor)
            save_path = pending
        try:
            doc.ez_save(save_path)
        finally:
            doc.close()
        if retained_blocks:
            n = canonicalize_text_layer(save_path, fontfiles, placed,
                protected_fonts=tuple((page_number, block['alias'])
                    for page_number, blocks in retained_blocks.items() for block in blocks))
        else:
            n = canonicalize_text_layer(save_path, fontfiles, placed)
        if retained_blocks:
            try:
                verify_retained_output(save_path, retained_blocks, cancel=cancel)
            except MappingError as exc:
                # The delivered file cannot vouch one retained run (another
                # draw covers it). Rebuild with that run redrawn instead of
                # failing the paid job (a-job-always-delivers); the check
                # above still guards every run that stays retained.
                rows = (exc.refusals or {}).get('source_retention') or [{}]
                failed_id = rows[0].get('segment_id')
                if (rows[0].get('reason') != 'retained-output' or failed_id is None or
                        failed_id in _unretained_ids or
                        len(_unretained_ids) >= _RETAINED_OUTPUT_RETRIES):
                    raise
                retry_ids = (*_unretained_ids, failed_id)
            else:
                os.replace(save_path, out)
                pending = None
    except RetentionCancelled:
        return RetypesetResult(output=None, cancelled=True,
                              pages=len(segd.get('pages') or []) or len(by_page), placed=0)
    finally:
        if pending is not None:
            os.unlink(pending)
    if retry_ids:
        return run_retypeset(stripped, segf, trf, out, progress=progress, cancel=cancel,
                             scale_report=scale_report, resource_root=resource_root,
                             original=original, capture_dir=capture_dir,
                             capture_below=capture_below, _unretained_ids=retry_ids)
    # A refused retained candidate must leave the report describing the
    # previous published PDF intact, just as it leaves that PDF intact.
    if report and retained_blocks:
        write_scale_report(sorted(scaled, key=lambda r: (r['page'], r['key'])),
                           report)
    if n:
        log.info(f'canonical text layer: rewrote /ToUnicode on {n} font object(s)')

    if leaders_dropped:
        log.info(f'NOTE: {len(leaders_dropped)} rotated line(s) drawn without their '
                 f'dot leaders, which are drawn only on horizontal lines; the text '
                 f'after them is kept:')
        for p, c in leaders_dropped[:CONSOLE_LIST]:
            log.info(f'  p{p}: {c[:60]}')
        for line in more_not_shown(leaders_dropped):
            log.info(line)
    if unretained:
        log.info(f'NOTE: {len(unretained)} unchanged run(s) redrawn because their source '
                 f'text could not be vouched for retention:')
        for row in unretained[:CONSOLE_LIST]:
            log.info(f"  p{row['page']}: {str(row['core'])[:60]} ({row['reason']})")
        for line in more_not_shown(unretained):
            log.info(line)
    if unmapped_kept:
        log.info(f'NOTE: {unmapped_kept} run(s) with no Unicode mapping drawn from their '
                 f'source glyphs')
    if unmapped_dropped:
        log.info(f'NOTE: {len(unmapped_dropped)} run(s) with no Unicode mapping not drawn: '
                 f'their source glyphs cannot be vouched and U+FFFD is never drawn:')
        for row in unmapped_dropped[:CONSOLE_LIST]:
            log.info(f"  p{row['page']}: {str(row['core'])[:60]} ({row['reason']})")
        for line in more_not_shown(unmapped_dropped):
            log.info(line)
    log.info(f'saved {out}')
    return RetypesetResult(
        output=out, pages=len(segd.get('pages') or []) or len(by_page),
        placed=len(placed),
        scaled=tuple(sorted(scaled, key=lambda r: (r['page'], r['key']))),
        cancelled=False,
        scale_report_path=(report or ''),
        warnings=tuple({'kind': 'leaders_dropped', 'page': p, 'core': c}
                       for p, c in leaders_dropped) +
                 tuple(dict(row, kind='source_not_retained') for row in unretained) +
                 tuple(dict(row, kind='unmapped_text_not_drawn') for row in unmapped_dropped))


def main(argv=None):
    # The envelope is needed here even though `retypeset()` opens its own:
    # the `elapsed` line is emitted AFTER that one closes, and without this it
    # is silently dropped. `console()` is re-entrant, so the nested use costs
    # nothing and the inner lines are not doubled.
    with console():
        return _main(argv)


def _main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    from ._console import missing_option_value
    missing = missing_option_value(
        argv, ('--original', '--capture-dir', '--capture-below'))
    if missing:
        log.info(f'usage: {missing} requires a value')
        return 2
    stripped, segf, trf, out = argv[0], argv[1], argv[2], argv[3]
    t0 = time.perf_counter()
    original = _arg(argv, '--original')
    # `--capture-dir` opts into a below-the-floor refusal bundle (capture.py);
    # PDF_TRANSLATE_CAPTURE_DIR is the same switch for an invocation that
    # cannot pass a flag. Neither is set here, so `retypeset()` resolves the
    # environment variable itself when this stays None.
    capture_dir = _arg(argv, '--capture-dir')
    # `--capture-below RATIO` widens it to a build that SUCCEEDS: a caller
    # whose own floor sits above SCALE_MIN reverts those cores itself, so
    # the case that costs them never reaches the refusal path at all.
    capture_below = _arg(argv, '--capture-below')
    rc = retypeset(stripped, segf, trf, out, original=original,
                   capture_dir=capture_dir, capture_below=capture_below)
    log.info(f'elapsed {time.perf_counter()-t0:.2f}s')
    return rc


if __name__ == '__main__':
    configure_cli_streams()
    raise SystemExit(main())
