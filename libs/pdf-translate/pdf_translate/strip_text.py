#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 1 of pdf-translate: prepare a PDF for text replacement.

Deletes ALL page text (BT..ET blocks) from content streams while leaving
vector graphics, images, and form-field widgets untouched, wraps surviving
content in q/Q (prevents graphics-state leakage into text you add later),
and removes the XFA layer if present (hybrid LiveCycle forms render the XFA
layer in Acrobat, hiding page edits).

Form XObjects are stripped recursively (an XObject inside an XObject, any
depth) and page /Resources are resolved through /Parent inheritance, because
letterheads and imposed pages hide text there.

Strip is also a gate. After saving, the stripped file is re-read with
annotation appearances excluded; if any page still has text the run FAILS,
lists page and text, and the stripped file is deleted. A stripped file that
still carries source text must never reach retypeset. Widget captions and
field values are appearance streams, not page text; they do not count.

Never use redaction annotations for text removal: they delete overlapping
form-field widgets.

Usage:
  python3 strip_text.py IN.pdf OUT.pdf [--hide-buttons name1,name2,...]
                                       [--captions captions.json]
                                       [--widget-text widget_text.json]
                                       [--keep-encryption]

--captions rewrites pushbutton /MK /CA strings in place (field count stays
exact). --hide-buttons sets the Hidden flag instead. Prefer captions when
the widget should remain as the UI chrome.

--widget-text rewrites the text that lives in the annotation dictionaries
and never reaches a content stream: /TU tooltips, choice /Opt display
strings and text-field /V /DV defaults. Export values are preserved (an
/Opt entry becomes [export, display]); a spec that would translate a
choice /V is refused, not ignored. extract_segments.py writes the
scaffold. Exit 2 on a mapping that is unauthored or wrong.

/Perms is always deleted and reported. /Perms /UR3 (Adobe Reader
extensions) and /Perms /DocMDP (certification) sign the bytes this script
rewrites, so both are invalid the moment text is stripped; leaving them is
what makes Acrobat announce "extended features are no longer available" or
"the document has been altered" over an otherwise correct file. A
certified source is flagged so the deliverable can say the certification
is gone. Signature fields themselves are left alone (field parity).

The output is unencrypted unless --keep-encryption, which re-applies the
source's permission bits with an EMPTY owner password — the original
cannot be recovered from the file, so those permissions are advisory. Say
so when you deliver.

Prints a JSON report to stdout: pages processed, XFA removed or not,
pushbuttons that have no /A action (dead if XFA was removed — decide
whether to hide them, rewrite captions, or leave them), Form XObjects that
contained text (also stripped, with nesting depth), rewritten widget text,
and leftover_text (empty on success). Exit 1 when leftover_text is not
empty.
"""
import io
import json
import logging
import os
import sys
import tempfile

import pikepdf
import pymupdf
from fontTools.pens.boundsPen import BoundsPen
from fontTools.ttLib import TTFont
from pikepdf import Name, parse_content_stream, unparse_content_stream

from ._pixels import INK_SKIP, changed_channels, dark_pixels


def encryption_report(pdf):
    """What the source's encryption and permissions are, for the recon note.

    An encrypted source comes out unencrypted unless you ask for
    --keep-encryption, and permissions are the issuer's decision, not
    ours: say what you changed.
    """
    if not pdf.is_encrypted:
        return {'encrypted': False}
    info = pdf.encryption
    return {
        'encrypted': True,
        'method': str(getattr(info, 'file_method', '')),
        'bits': getattr(info, 'bits', None),
        'empty_user_password': getattr(info, 'user_password', b'') == b'',
        'permissions': {k: bool(v) for k, v in zip(
            pikepdf.Permissions._fields, pdf.allow)},
    }


def remove_perms(pdf, report):
    """Delete /Perms and record what was there.

    /Perms /UR3 is Adobe's Reader-extensions (usage rights) signature and
    /Perms /DocMDP is a certification signature. Both sign the bytes we are
    about to rewrite, so both are invalid the moment text is stripped.
    Leaving them is what makes Acrobat announce "extended features are no
    longer available" or "the document has been altered" over a file that
    is otherwise correct. Signature FIELDS are left alone: removing a widget
    would break field parity.
    """
    perms = pdf.Root.get('/Perms')
    if perms is None:
        return
    keys = sorted(str(k) for k in perms.keys())
    report['perms_removed'] = keys
    report['certified'] = '/DocMDP' in keys
    del pdf.Root['/Perms']


# PDF 32000-1 Table 51: text showing, text positioning and text state.
TEXT_OPERATORS = frozenset({'Tj', 'TJ', "'", '"', 'Td', 'TD', 'Tm', 'T*',
                            'Tc', 'Tw', 'Tz', 'TL', 'Tf', 'Tr', 'Ts'})


def strip_ops(owner, pdf=None):
    """Remove every text object's text; keep its graphics state (R-01).

    BT/ET do not save and restore the graphics state: colour, gs, line
    width, cm and marked content set inside a text object stay in effect
    after ET. Dropping the whole object recoloured whatever was drawn next
    (arxiv's table shading turned black, N-400's rules turned white). So a
    text object loses BT, ET and its text operators, and everything else in
    it stays in place and in order. Returns (stream bytes, text objects).
    """
    ops = parse_content_stream(owner)
    out, depth, removed = [], 0, 0
    for operands, op in ops:
        o = str(op)
        if o == 'BT':
            depth += 1
            removed += 1
            continue
        if o == 'ET':
            depth = max(0, depth - 1)
            continue
        if depth and o in TEXT_OPERATORS:
            continue
        out.append((operands, op))
    return unparse_content_stream(out), removed


def _field_base(name):
    name = str(name)
    return name.split('[')[0] if name else name


def _objgen(obj):
    try:
        return obj.objgen
    except Exception:
        return None


def field_full_name(annot):
    """Fully qualified name of a field widget: every /T up the /Parent chain.

    The partial /T alone does not name a field. XFA-derived government forms
    repeat "Name[0]" under many parents and put "PDF417BarCode1[0]" on every
    page, each with its own value; keyed by the partial name, one field's
    widget text landed on all of them (review R-03). PDF 32000-1 §12.7.3.2.
    """
    parts, node, seen = [], annot, set()
    for _ in range(64):  # a malformed /Parent cycle ends here
        if node is None:
            break
        og = _objgen(node)
        if og and og != (0, 0):
            if og in seen:
                break
            seen.add(og)
        t = node.get('/T')
        if t is not None:
            parts.append(str(t))
        node = node.get('/Parent')
    return '.'.join(reversed(parts))


def resources_of(page_obj):
    """/Resources of a page, following /Parent inheritance.

    Some producers put /Resources on the /Pages node. pikepdf (QPDF) pushes
    inherited attributes onto the page when it opens a file, so the walk
    below is a safety net for readers that do not; the leftover-text gate
    after save is what actually proves nothing was missed.
    """
    node = page_obj
    seen = set()
    while node is not None:
        res = node.get('/Resources')
        if res is not None:
            return res
        key = _objgen(node)
        if key is not None:
            if key in seen:
                break
            seen.add(key)
        node = node.get('/Parent')
    return None


def strip_xobjects(res, pdf, page_index, report, seen, depth=1):
    """Strip BT..ET from every Form XObject reachable from res, recursively."""
    if res is None:
        return
    xobjs = res.get('/XObject')
    if xobjs is None:
        return
    # Sorted by name: pikepdf's dictionary iteration runs through a
    # hash-randomized structure, so the same file walks its XObjects in a
    # different order in each process. Nothing about WHAT is stripped
    # depends on that, but the report's order does — and so does which
    # name is recorded when two of them point at one object, since `seen`
    # skips whichever comes second. Sorting the finished report would fix
    # only the first half, so the traversal is what gets pinned.
    for name, xo in sorted(dict(xobjs).items()):
        key = _objgen(xo)
        if key is not None and key != (0, 0):
            if key in seen:
                continue
            seen.add(key)
        if xo.get('/Subtype') != Name('/Form'):
            continue
        xdata, xremoved = strip_ops(xo, pdf)
        if xremoved:
            xo.write(b'q\n' + xdata + b'\nQ\n')
            report['form_xobjects_stripped'].append(
                {'page': page_index, 'xobject': str(name), 'blocks': xremoved,
                 'depth': depth})
        strip_xobjects(xo.get('/Resources'), pdf, page_index, report, seen,
                       depth + 1)


def _annots_free_textpage(page):
    """A TextPage built from the page CONTENT only, widget appearances out.

    PyMuPDF sometimes hands back the raw wrapped object rather than an
    already-typed TextPage, so the four extractors below shared this
    four-line preamble verbatim.
    """
    dl = page.get_displaylist(annots=False)
    tp = dl.get_textpage()
    if not isinstance(tp, pymupdf.TextPage):
        tp = pymupdf.TextPage(tp)
    return tp


def page_text_without_annots(page):
    """Text of the page CONTENT only: annotation appearance streams excluded.

    get_text() includes widget appearances (captions, field values); those
    are not page text and must not count as leftovers.
    """
    tp = _annots_free_textpage(page)
    return tp.extractText()


# The space segments.json records geometry in, written under its "geometry"
# key. retypeset refuses a rotated page whose segments.json does not name it.
GEOMETRY_SPACE = 'unrotated'


def _to_unrotated(d, page):
    """Map a text dict from the page's rotated space to its unrotated one.

    A display list keeps the page's /Rotate, so its text page reports what a
    viewer shows: on a /Rotate 90 page, text drawn at PDF (60, 712) comes
    back at (712, 60) reading down. page.get_text sets the rotation to 0 for
    the call, and so do widget rects, get_drawings, TextWriter,
    insert_htmlbox and every verify reader. derotation_matrix maps one space
    to the other exactly; it is the identity when the page is not rotated,
    so nothing changes there. Mapping here, rather than zeroing /Rotate
    around get_displaylist the way get_text does, leaves the document
    untouched.
    """
    if not page.rotation:
        return d
    m = page.derotation_matrix
    turn = pymupdf.Matrix(m.a, m.b, m.c, m.d, 0, 0)

    def rect(r):
        return tuple(pymupdf.Rect(r) * m)

    def point(p):
        return tuple(pymupdf.Point(p) * m)

    frame = page.rect * m
    d['width'], d['height'] = frame.width, frame.height
    for block in d.get('blocks', []):
        block['bbox'] = rect(block['bbox'])
        if 'transform' in block:
            block['transform'] = tuple(pymupdf.Matrix(block['transform']) * m)
        for line in block.get('lines', []):
            line['bbox'] = rect(line['bbox'])
            line['dir'] = tuple(pymupdf.Point(line['dir']) * turn)
            for span in line.get('spans', []):
                span['bbox'] = rect(span['bbox'])
                span['origin'] = point(span['origin'])
                for char in span.get('chars', []):
                    char['bbox'] = rect(char['bbox'])
                    char['origin'] = point(char['origin'])
    return d


UNMAPPED = '�'


def _spans(data):
    return (span for block in data['blocks'] for line in block.get('lines', ())
            for span in line['spans'])


def _has_unmapped(data):
    return any(UNMAPPED in span['text'] for span in _spans(data))


def _glyph_draws_nothing(program, gid):
    """True when a font program's glyph has no outline; False when unsure."""
    try:
        font = TTFont(io.BytesIO(program), lazy=True)
        glyphs = font.getGlyphSet()
        pen = BoundsPen(glyphs)
        glyphs[font.getGlyphName(gid)].draw(pen)
        return pen.bounds is None
    except Exception:  # noqa: BLE001 -- an unreadable program stays unmapped
        return False


def _blank_unmapped(page, raw):
    """Turn each U+FFFD whose glyph draws nothing into a space, in place, in a
    rawdict. A glyph with no outline is white space, whatever its code says, and
    never a reason to keep a line in the source language: the JPL booklet ends 14
    lines with a font's `nonmarkingreturn` (2026-10-30). Any font of that name
    with a glyph at that id must agree; a glyph with ink stays unmapped.
    """
    programs = {}
    fonts = page.get_fonts()
    blank = set()
    for trace in page.get_texttrace():
        for ucs, gid, origin, _ in trace['chars']:
            if ucs != ord(UNMAPPED):
                continue
            matching = [font[0] for font in fonts
                        if font[3].partition('+')[2 if '+' in font[3] else 0] == trace['font']]
            for xref in matching:
                if xref not in programs:
                    programs[xref] = page.parent.extract_font(xref)[3]
            if matching and all(_glyph_draws_nothing(programs[xref], gid) for xref in matching):
                blank.add((round(origin[0], 1), round(origin[1], 1)))
    for span in _spans(raw):
        for char in span['chars']:
            if (char['c'] == UNMAPPED and
                    (round(char['origin'][0], 1), round(char['origin'][1], 1)) in blank):
                char['c'] = ' '
    return raw


def _copy_blanked_spans(data, raw):
    """Give a dict the characters of the rawdict of the same text page."""
    for span, source in zip(_spans(data), _spans(raw), strict=True):
        if UNMAPPED in span['text']:
            span['text'] = ''.join(char['c'] for char in source['chars'])


def page_textdict_without_annots(page):
    """get_text('dict') of the page CONTENT only: annotation appearances out.

    Same shape and coordinate space as page.get_text('dict'), minus the
    widget appearance streams. A text field's default value and a combo
    box's current choice are drawn by the widget, not by the page; letting
    them into extraction puts them in to_translate.json, where the author
    translates them and retypeset draws the translation *under* a widget
    that still shows the source value.
    """
    tp = _annots_free_textpage(page)
    data = tp.extractDICT()
    if _has_unmapped(data):
        _copy_blanked_spans(data, _blank_unmapped(page, tp.extractRAWDICT()))
    return _to_unrotated(data, page)


def page_rawdict_without_annots(page):
    """get_text('rawdict') of the page CONTENT only: per-character boxes and
    origins from the same annotation-free text page as the dict helper, in
    the same unrotated space. The extractor reads it for lines that carry a
    list marker (row 23)."""
    tp = _annots_free_textpage(page)
    data = tp.extractRAWDICT()
    if any(char['c'] == UNMAPPED for span in _spans(data) for char in span['chars']):
        _blank_unmapped(page, data)
    return _to_unrotated(data, page)


def leftover_page_text(path):
    """[(page_index, snippet)] for every page whose content still draws letters.

    Spans, not extractText(): MuPDF adds a tagged image's /ActualText to the plain
    text, and that is no text left on the page. NASA's Simple Machines worksheet
    (2026-10-30) describes its banner so, and the whole order failed on it.
    """
    doc = pymupdf.open(path)
    out = []
    try:
        for page in doc:
            text = ' '.join(' '.join(
                span['text'] for block in page_textdict_without_annots(page)['blocks']
                for line in block.get('lines', ()) for span in line['spans']).split())
            if text:
                out.append((page.number, text[:80]))
    finally:
        doc.close()
    return out


# Invisible-text oracle (goal 14). Real text changes the render when it is
# stripped: glyph ink covers at least ~10% of a span's bbox for any legible
# face. An OCR layer (render mode 3) or text hidden under an image changes
# nothing. 3% leaves headroom; both renders share every non-text pixel, so
# there is no anti-aliasing noise to absorb.
INVISIBLE_TEXT_MAX_CHANGED = 0.03
CHANGED_CHANNEL_DELTA = 16   # per-channel difference that counts as a change
# INK_SKIP, the floor below which a page is blank, is verify's own (_pixels).


def text_span_area(page):
    """Sum of text-span bbox areas (pt²) from page content, annotations excluded."""
    tp = _annots_free_textpage(page)
    area = 0.0
    for block in tp.extractDICT().get('blocks', []):
        for line in block.get('lines', []):
            for span in line.get('spans', []):
                if not span.get('text', '').strip():
                    continue
                x0, y0, x1, y1 = span['bbox']
                area += max(0.0, x1 - x0) * max(0.0, y1 - y0)
    return area


def _changed_pixels(a, b, n, delta=CHANGED_CHANNEL_DELTA, stop=None):
    """Approximate number of pixels whose colour differs between two renders.

    `stop(pixels)` may end the count early once it says True (see
    _pixels.changed_channels).
    """
    n = max(n, 1)
    channels_stop = None if stop is None else (lambda channels: stop(channels / n))
    return changed_channels(a, b, delta, stop=channels_stop) / n


def invisible_text_pages(src, pages=None):
    """[(page_index, changed_fraction)] for pages whose text is not what the reader sees.

    Strips a temporary copy and compares 72-dpi renders. If removing the
    text changes fewer than INVISIBLE_TEXT_MAX_CHANGED of the page's
    text-span area (in pixels), the text was invisible: an OCR layer over a
    scanned image, or text hidden under an image. Pages with negligible ink
    are skipped (a pale blank page is not a scan). If strip itself refuses
    the file, nothing is judged; that refusal already blocks the pipeline.

    `pages`, a collection of 0-based page indexes, limits which pages are
    judged; None judges them all. The strip is always of the whole
    document: text left anywhere refuses the file, and a form XObject two
    pages share is stripped once, so a partial strip could judge differently.
    """
    # A set, once: `in` on a one-shot iterable would use it up after one page.
    pages = None if pages is None else set(pages)
    with tempfile.TemporaryDirectory() as tmp:
        stripped = os.path.join(tmp, 'stripped.pdf')
        report = strip_text(src, stripped)
        if report['leftover_text'] or not os.path.isfile(stripped):
            return []
        o, s = pymupdf.open(src), pymupdf.open(stripped)
        out = []
        try:
            for i in range(min(len(o), len(s))):
                if pages is not None and i not in pages:
                    continue
                area = text_span_area(o[i])
                if area <= 0:
                    continue
                po, ps = o[i].get_pixmap(dpi=72), s[i].get_pixmap(dpi=72)
                if (po.width, po.height, po.n) != (ps.width, ps.height, ps.n):
                    continue
                bo, bs = bytes(po.samples), bytes(ps.samples)
                if dark_pixels(bo, po.n) < INK_SKIP:
                    continue
                # The count only grows, so once it reaches the threshold the
                # page cannot be flagged and the rest need not be counted.
                fraction = _changed_pixels(
                    bo, bs, po.n,
                    stop=lambda changed, area=area: changed / area >= INVISIBLE_TEXT_MAX_CHANGED) / area
                if fraction < INVISIBLE_TEXT_MAX_CHANGED:
                    out.append((i, round(fraction, 4)))
        finally:
            o.close()
            s.close()
        return out


# ---------------------------------------------------------------- widget text
# Widget text is not page text. /TU tooltips, /Opt choice labels and text
# /V /DV defaults live in the annotation dictionaries, never reach the
# content stream, and so are invisible to strip-and-retypeset. Without this
# channel a "fully translated" form still shows English on every hover and
# in every dropdown. Export values must survive: a choice /Opt entry becomes
# [export, display] so scripts, /V and submitted data keep working.


# Defined in .results with the rest of the refusal family; re-exported here so
# `except strip_text.WidgetTextError` keeps working for everything that
# catches it today. The name did not move, only its home.
from ._console import console, say
from .results import StripResult, WidgetTextError  # noqa: F401

log = logging.getLogger(__name__)


def _target_of(spec, what, field):
    """Author's target string from a scaffold entry or a bare string."""
    if spec is None:
        return None
    if isinstance(spec, str):
        return spec
    if isinstance(spec, dict):
        if 'target' not in spec:
            raise WidgetTextError(
                f'{field}: {what} entry has no "target" key')
        target = spec['target']
        if target is None:
            raise WidgetTextError(
                f'{field}: {what} target is null — author it, or delete the '
                f'key to leave the source text in place')
        if not str(target).strip():
            raise WidgetTextError(f'{field}: {what} target is empty')
        return str(target)
    raise WidgetTextError(f'{field}: {what} must be a string or an object')


def _option_pairs(spec, field):
    """[(export, target)] from either scaffold list or {export: display} dict."""
    pairs = []
    if isinstance(spec, dict):
        for export, target in spec.items():
            if target is None:
                raise WidgetTextError(
                    f'{field}: option "{export}" target is null — author it, '
                    f'or delete the key')
            pairs.append((str(export), str(target)))
        return pairs
    if isinstance(spec, list):
        for item in spec:
            if not isinstance(item, dict) or 'export' not in item:
                raise WidgetTextError(
                    f'{field}: each option needs an "export" key')
            pairs.append((str(item['export']),
                          _target_of(item, f'option {item["export"]}', field)))
        return pairs
    raise WidgetTextError(f'{field}: options must be an object or a list')


def _opt_export(entry):
    """Export value of one /Opt entry (string, or [export, display])."""
    if isinstance(entry, pikepdf.Array) or isinstance(entry, list):
        return str(entry[0]) if len(entry) else ''
    return str(entry)


def opt_exports(annot):
    """Ordered export values of a choice widget's /Opt, or None."""
    opt = annot.get('/Opt')
    if opt is None:
        return None
    return [_opt_export(e) for e in opt]


WIDGET_TEXT_KEYS = {'type', 'tooltip', 'options', 'value', 'default'}


def _pdf_str(obj):
    return str(obj) if obj is not None else None


def widget_text_scaffold(src):
    """{field: {...}} of every translatable annotation string in a PDF.

    Values are {"source": ..., "target": null} so the author can see what
    the string is and strip_text can tell "not authored yet" from "left in
    the source language on purpose" (delete the key for that). Pushbutton
    captions are the --captions channel and are not repeated here. Keys are
    fully qualified field names (field_full_name), so two fields that share
    a partial name each get their own entry.
    """
    out = {}
    pdf = pikepdf.open(src)
    try:
        for page in pdf.pages:
            for a in page.get('/Annots', []):
                ft = a.get('/FT')
                if ft is None or not str(a.get('/T', '')):
                    continue
                name = field_full_name(a)
                if name in out:
                    continue
                is_push = (ft == Name('/Btn')
                           and int(a.get('/Ff', 0) or 0) & (1 << 16))
                if is_push:
                    continue
                entry = {'type': str(ft)}
                tu = _pdf_str(a.get('/TU'))
                if tu and tu.strip():
                    entry['tooltip'] = {'source': tu, 'target': None}
                if ft == Name('/Ch'):
                    exports = opt_exports(a) or []
                    options = []
                    for export, raw in zip(exports, a.get('/Opt', [])):
                        display = (str(raw[1]) if (isinstance(raw, pikepdf.Array)
                                                   and len(raw) > 1)
                                   else export)
                        options.append({'export': export, 'source': display,
                                        'target': None})
                    if options:
                        entry['options'] = options
                elif ft == Name('/Tx'):
                    for key, pdfkey in (('value', '/V'), ('default', '/DV')):
                        val = _pdf_str(a.get(pdfkey))
                        if val and val.strip():
                            entry[key] = {'source': val, 'target': None}
                if len(entry) > 1:
                    out[name] = entry
    finally:
        pdf.close()
    return out


def widget_text_is_authored(path):
    """True when the file at `path` holds text an author wrote.

    A scaffold's only strings are each entry's `type` and each slot's
    `source` and `export`; every `target` is null. Anything else — a target,
    a string shorthand, a file that is not JSON — is someone's work, and
    extract must not write a fresh scaffold over it. A missing file is not.
    """
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    return not _scaffold_only(data)


def authored_widget_targets(entry):
    """{slot: target or None} for the slots one widget-text entry names.

    Slots are 'tooltip', 'value', 'default' and ('option', export). A null
    or unreadable target is None; a slot the entry leaves out is absent,
    which strip reads as "leave the source text in place". Lenient where
    strip refuses: verify reads a mapping to learn what was kept on
    purpose, not to apply it.
    """
    def one(spec):
        if isinstance(spec, str):
            return spec
        if isinstance(spec, dict) and isinstance(spec.get('target'), str):
            return spec['target']
        return None

    out = {}
    if not isinstance(entry, dict):
        return out
    for slot in ('tooltip', 'value', 'default'):
        if slot in entry:
            out[slot] = one(entry[slot])
    options = entry.get('options')
    if isinstance(options, dict):
        for export, target in options.items():
            out[('option', str(export))] = target if isinstance(target, str) else None
    elif isinstance(options, list):
        for item in options:
            if isinstance(item, dict) and 'export' in item:
                out[('option', str(item['export']))] = one(item)
    return out


def _scaffold_only(node, key=None):
    if isinstance(node, dict):
        return all(_scaffold_only(v, k) for k, v in node.items())
    if isinstance(node, list):
        return all(_scaffold_only(v, key) for v in node)
    return node is None or key in ('type', 'source', 'export')


def choice_exports(path):
    """{field name: [export values]} for every choice widget in a PDF.

    The export value is what a viewer submits and what /V holds; translating
    a dropdown must change only the display half of each /Opt entry. This is
    what verify's /Opt parity gate compares. Keys are fully qualified names;
    a second widget of the same field gets its own "(widget n)" key, so no
    widget's exports hide another's.
    """
    out = {}
    pdf = pikepdf.open(path)
    try:
        for page in pdf.pages:
            for a in page.get('/Annots', []):
                if a.get('/FT') != Name('/Ch') or not str(a.get('/T', '')):
                    continue
                exports = opt_exports(a)
                if exports is None:
                    continue
                name = key = field_full_name(a)
                n = 2
                while key in out:
                    key = f'{name} (widget {n})'
                    n += 1
                out[key] = exports
    finally:
        pdf.close()
    return out


def apply_widget_text(annot, spec, field, report):
    """Rewrite /TU, /Opt display halves and text /V /DV from one spec.

    Choice /V and /DV are export values once /Opt carries [export, display]
    pairs; translating them would break the submitted data, so a spec that
    asks for it is refused rather than quietly ignored.
    """
    if not isinstance(spec, dict):
        raise WidgetTextError(f'{field}: widget-text entry must be an object')
    unknown = sorted(set(spec) - WIDGET_TEXT_KEYS)
    if unknown:
        raise WidgetTextError(
            f'{field}: unknown widget-text keys {unknown} (expected '
            f'{sorted(WIDGET_TEXT_KEYS)})')
    changed = []
    is_choice = annot.get('/FT') == Name('/Ch')

    tooltip = _target_of(spec.get('tooltip'), 'tooltip', field)
    if tooltip is not None:
        annot.TU = pikepdf.String(tooltip)
        changed.append('tooltip')

    if 'options' in spec:
        if not is_choice:
            raise WidgetTextError(f'{field}: options on a non-choice field')
        exports = opt_exports(annot)
        if exports is None:
            raise WidgetTextError(f'{field}: field has no /Opt to translate')
        pairs = dict(_option_pairs(spec['options'], field))
        unknown = [e for e in pairs if e not in exports]
        if unknown:
            raise WidgetTextError(
                f'{field}: options {unknown} are not export values of this '
                f'field ({exports})')
        rebuilt = []
        for entry, export in zip(annot.Opt, exports):
            display = pairs.get(export)
            if display is None:
                rebuilt.append(entry)
                continue
            rebuilt.append(pikepdf.Array([pikepdf.String(export),
                                          pikepdf.String(display)]))
        annot.Opt = pikepdf.Array(rebuilt)
        changed.append('options')

    for key, pdfkey in (('value', '/V'), ('default', '/DV')):
        if key not in spec:
            continue
        if is_choice:
            raise WidgetTextError(
                f'{field}: {key} on a choice field is an export value; '
                f'translate "options" instead and leave {pdfkey} alone')
        target = _target_of(spec[key], key, field)
        if target is None:
            continue
        annot[Name(pdfkey)] = pikepdf.String(target)
        changed.append(key)

    # Same reason as captions: a stale appearance keeps drawing the source
    # string on top of the value we just rewrote. That is true of a value, a
    # default or the options, not of a tooltip (/TU is never drawn), and
    # never of a checkbox or radio button, whose /AP /N keys are the only
    # record of the on-state export name.
    redraws = bool({'value', 'default', 'options'} & set(changed))
    if redraws and '/AP' in annot and annot.get('/FT') != Name('/Btn'):
        del annot.AP
    if changed:
        report['rewritten_widget_text'].append(
            {'name': field, 'changed': changed})
    return bool(changed)


def resolve_widget_text_keys(pdf, widget_text):
    """{fully qualified field name: widget-text key} for this PDF.

    A key that is a field's full name applies to that field alone. A key
    that is a partial /T (or its base, without the [n] index), as mappings
    written before R-03 have, still applies when it names exactly one
    field; when it names several, the mapping is refused with every
    candidate named, rather than one field's text going onto all of them.
    """
    widgets = {}
    partial = {}
    for page in pdf.pages:
        for a in page.get('/Annots', []):
            t = str(a.get('/T', ''))
            if not t or a.get('/Subtype') != Name('/Widget'):
                continue  # a comment's /T is its author, not a field name
            name = field_full_name(a)
            widgets.setdefault(name, []).append(a)
            for alias in {t, _field_base(t)}:
                partial.setdefault(alias, set()).add(name)
    full = set(widgets)
    resolved, ambiguous = {}, {}
    for key in widget_text:
        if key in full:
            name = key
        elif len(partial.get(key, ())) == 1:
            name = next(iter(partial[key]))
        elif key in partial:
            ambiguous[key] = sorted(partial[key])
            continue
        else:
            continue  # unknown keys are reported after the pass
        if name in resolved:
            raise WidgetTextError(
                f'widget-text keys "{resolved[name]}" and "{key}" both name '
                f'field "{name}"; keep one')
        resolved[name] = key
    if ambiguous:
        detail = '; '.join(f'"{k}" -> {v}' for k, v in sorted(ambiguous.items()))
        raise WidgetTextError(
            f'widget-text key names more than one field; key each by its '
            f'fully qualified name: {detail}')
    for name, key in resolved.items():
        _refuse_divergent_widgets(key, name, widgets[name], widget_text[key])
    return resolved


_SPEC_PDF_KEYS = {'tooltip': '/TU', 'value': '/V', 'default': '/DV',
                  'options': '/Opt'}


def _refuse_divergent_widgets(key, name, widgets, spec):
    """Refuse one entry for widgets that share a full name but not their text.

    Distinct widget dictionaries can carry the same full name: a malformed
    form repeats a name, or a /T with a '.' in it spells the same string as
    a nested field. One translation for all of them would overwrite the
    others' source text, so the entry is refused instead.
    """
    if len(widgets) < 2 or not isinstance(spec, dict):
        return
    differ = [what for what, pdfkey in _SPEC_PDF_KEYS.items()
              if what in spec
              and len({repr(w.get(pdfkey)) for w in widgets}) > 1]
    if differ:
        raise WidgetTextError(
            f'widget-text key "{key}" names {len(widgets)} widgets that share '
            f'the full name "{name}" but differ in their source {differ}; '
            f'one entry would overwrite the others — delete the key to leave '
            f'them as they are')


def strip_text(src, dst, hide_buttons=None, captions=None, widget_text=None,
               keep_encryption=False):
    """Strip page text, wrap content in q/Q, remove XFA. Returns a report dict.

    hide_buttons: iterable of field names (base name, without [0] suffix).
    captions: dict of field name -> new /MK /CA caption (in-place rewrite).
    widget_text: dict of fully qualified field name -> {tooltip, options,
        value, default} (see apply_widget_text). Rewrites annotation text
        that never reaches the content stream. A partial name is accepted
        only when it names one field (resolve_widget_text_keys). Raises
        WidgetTextError on a mapping that is unauthored (null target),
        ambiguous, or would break export values.
    keep_encryption: re-encrypt the output with the source's permission
        bits. The original owner password cannot be recovered from the
        file, so the output gets an EMPTY owner password: the permissions
        are advisory again, and the deliverable has to say so.

    report['leftover_text'] lists pages whose content still had text after
    stripping (annotation appearances excluded). When it is not empty the
    output file is deleted: never hand retypeset a stripped file that still
    carries source text.
    """
    hide = set(hide_buttons or [])
    captions = captions or {}
    widget_text = widget_text or {}

    pdf = pikepdf.open(src)
    report = {'xfa_removed': False, 'pages': [], 'dead_buttons': [],
              'form_xobjects_stripped': [], 'hidden': [],
              'rewritten_captions': [], 'rewritten_widget_text': [],
              'perms_removed': [], 'certified': False,
              'encryption': encryption_report(pdf), 'reencrypted': False,
              'leftover_text': []}
    remove_perms(pdf, report)

    acro = pdf.Root.get('/AcroForm')
    if acro is not None and '/XFA' in acro:
        del acro.XFA
        report['xfa_removed'] = True

    try:
        spec_keys = resolve_widget_text_keys(pdf, widget_text)
    except WidgetTextError:
        pdf.close()
        raise

    seen_xobjects = set()
    seen_widget_text = set()
    for i, page in enumerate(pdf.pages):
        data, removed = strip_ops(page, pdf)
        page.Contents = pdf.make_stream(b'q\n' + data + b'\nQ\n')
        report['pages'].append({'page': i, 'text_blocks_removed': removed})
        strip_xobjects(resources_of(page.obj), pdf, i, report, seen_xobjects)
        for a in page.get('/Annots', []):
            t = str(a.get('/T', ''))
            base = _field_base(t)
            full_name = (field_full_name(a)
                         if t and a.get('/Subtype') == Name('/Widget') else '')
            spec_key = spec_keys.get(full_name)
            if spec_key is not None:
                if apply_widget_text(a, widget_text[spec_key], full_name,
                                     report):
                    seen_widget_text.add(spec_key)
            if a.get('/FT') != Name('/Btn'):
                continue
            ff = int(a.get('/Ff', 0) or 0)
            if not (ff & (1 << 16)):  # pushbutton flag
                continue
            if a.get('/A') is None:
                report['dead_buttons'].append({'page': i, 'name': t})
            cap_key = t if t in captions else (base if base in captions else None)
            if cap_key is not None:
                mk = a.get('/MK')
                if mk is None:
                    a.MK = pikepdf.Dictionary()
                    mk = a.MK
                mk.CA = pikepdf.String(str(captions[cap_key]))
                # Stale appearance streams keep drawing the source-language
                # caption on top of /CA; drop them so NeedAppearances rebuilds.
                if '/AP' in a:
                    del a.AP
                report['rewritten_captions'].append(t)
            if base in hide or t in hide:
                a.F = 2
                report['hidden'].append(t)

    unknown = sorted(set(widget_text) - seen_widget_text)
    if unknown:
        pdf.close()
        raise WidgetTextError(
            f'widget-text mapping names fields this PDF does not have: '
            f'{unknown}')
    if report['rewritten_widget_text']:
        # The appearances we dropped have to be rebuilt by the viewer.
        if acro is None:
            pdf.Root.AcroForm = pdf.make_indirect(pikepdf.Dictionary())
            acro = pdf.Root.AcroForm
        acro.NeedAppearances = True

    if keep_encryption and report['encryption'].get('encrypted'):
        pdf.save(dst, encryption=pikepdf.Encryption(
            owner='', user='', allow=pdf.allow))
        report['reencrypted'] = True
    else:
        pdf.save(dst)
    pdf.close()

    if report['reencrypted']:
        # Read back what the writer actually granted: modern encryption
        # revisions always allow accessibility extraction, so the applied
        # bits are not always the bits we asked for.
        back = pikepdf.open(dst)
        try:
            report['encryption_applied'] = {
                k: bool(v) for k, v in zip(pikepdf.Permissions._fields,
                                           back.allow)}
        finally:
            back.close()

    leftover = leftover_page_text(dst)
    report['leftover_text'] = [{'page': p, 'text': t} for p, t in leftover]
    if leftover:
        try:
            os.remove(dst)
        except OSError:
            pass
    return report


def run_strip(src, dst, hide_buttons=None, captions=None, widget_text=None,
              keep_encryption=False):
    """Strip every page's text. Silent. Returns a StripResult. Raises on refusal.

    `strip_text` was already the silent half of this pair — it returns a report
    dict and does not print; `main` is what prints. This is the same work with
    the family's shape on the way out, so a consumer learns one result type
    rather than one dict schema per stage. The dict is not going anywhere:
    `strip_text` keeps returning it, and the 1 capture block and every caller
    that reads `report['leftover_text']` are untouched.
    """
    report = strip_text(src, dst, hide_buttons=hide_buttons, captions=captions,
                        widget_text=widget_text,
                        keep_encryption=keep_encryption)
    encryption = report.get('encryption') or {}
    return StripResult(
        output=dst,
        pages=len(report.get('pages') or ()),
        xfa_removed=bool(report.get('xfa_removed')),
        perms_removed=', '.join(report.get('perms_removed') or ()),
        certified=bool(report.get('certified')),
        encrypted=bool(encryption.get('encrypted')),
        reencrypted=bool(report.get('reencrypted')),
        dead_buttons=tuple(report.get('dead_buttons') or ()),
        hidden=tuple(report.get('hidden') or ()),
        rewritten_captions=tuple(report.get('rewritten_captions') or ()),
        rewritten_widget_text=tuple(report.get('rewritten_widget_text') or ()),
        leftover_text=tuple(report.get('leftover_text') or ()),
    )


def _say(line):
    say(log, line)


def main(argv=None):
    with console():
        return _main(argv)


def _main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    src, dst = argv[0], argv[1]
    hide = []
    if '--hide-buttons' in argv:
        hide = [n for n in argv[argv.index('--hide-buttons') + 1].split(',') if n]
    captions = None
    if '--captions' in argv:
        with open(argv[argv.index('--captions') + 1], encoding='utf-8') as f:
            captions = json.load(f)
    widget_text = None
    if '--widget-text' in argv:
        with open(argv[argv.index('--widget-text') + 1], encoding='utf-8') as f:
            widget_text = json.load(f)
    try:
        report = strip_text(src, dst, hide_buttons=hide, captions=captions,
                            widget_text=widget_text,
                            keep_encryption='--keep-encryption' in argv)
    except WidgetTextError as exc:
        _say(f'FAIL widget text: {exc}')
        return 2
    _say(json.dumps(report, indent=2, ensure_ascii=False))
    if report.get('perms_removed'):
        _say(f"NOTE: deleted /Perms {report['perms_removed']} — those "
             f"signatures cover the bytes this script rewrites and cannot "
             f"survive editing.")
    if report.get('certified'):
        _say('WARNING: the source was a CERTIFIED document (/Perms /DocMDP). '
             'The translation is not certified. Say so when you deliver it.')
    if report['encryption'].get('encrypted') and not report.get('reencrypted'):
        _say('NOTE: the source was encrypted; the output is not. Pass '
             '--keep-encryption to re-apply its permission bits (with an '
             'empty owner password).')
    if report['leftover_text']:
        _say(f"FAIL: page text survived strip on {len(report['leftover_text'])} "
             f"page(s); {dst} was not written. Text hiding somewhere the "
             f"walker did not reach:")
        for item in report['leftover_text']:
            _say(f"  p{item['page']}: {item['text']}")
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
