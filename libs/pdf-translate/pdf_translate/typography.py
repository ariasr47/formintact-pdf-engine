"""Font evidence and source identity for the opt-in typography format.

Names associate an extracted span with actual PDF resources; they do not
classify arbitrary fonts. An ambiguous association remains unresolved.
"""
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
from struct import error as struct_error

from fontTools.ttLib import TTFont, TTLibError
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import DecomposingRecordingPen
import pymupdf

from .results import FontError


class _GlyphProgram:
    """An actually loaded font program and its normalized used-glyph data."""
    def __init__(self, data, stack):
        self.font = stack.enter_context(TTFont(io.BytesIO(data)))
        self.drawer = pymupdf.Font(fontbuffer=data)
        self.observed = _program_observation(data)
        self.glyphs = self.font.getGlyphSet()
        self.order = self.font.getGlyphOrder()
        self.units = self.font['head'].unitsPerEm
        self.cache = {}

    def glyph(self, gid):
        if gid not in self.cache:
            name = self.order[gid]
            pen = DecomposingRecordingPen(self.glyphs)
            self.glyphs[name].draw(pen)
            bounds = BoundsPen(self.glyphs)
            self.glyphs[name].draw(bounds)
            def normalize(value):
                if isinstance(value, (tuple, list)):
                    return tuple(normalize(item) for item in value)
                if isinstance(value, (int, float)):
                    return round(value / self.units, 7)
                return value
            advance = self.font['hmtx'].metrics[name][0] / self.units
            ink = None
            if bounds.bounds is not None:
                x0, y0, x1, y1 = bounds.bounds
                ink = (x0 / self.units, -y1 / self.units, x1 / self.units, -y0 / self.units)
            self.cache[gid] = ((normalize(pen.value), round(advance, 7)), ink)
        return self.cache[gid]


_STANDARD = {
    'Helvetica': ('sans', False, False),
    'Helvetica-Bold': ('sans', True, False),
    'Helvetica-Oblique': ('sans', False, True),
    'Helvetica-BoldOblique': ('sans', True, True),
    'Times-Roman': ('serif', False, False),
    'Times-Bold': ('serif', True, False),
    'Times-Italic': ('serif', False, True),
    'Times-BoldItalic': ('serif', True, True),
}


def canonical_digest(value):
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def source_digest(path):
    with open(path, 'rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _unsubset(name):
    return re.sub(r'^[A-Z]{6}\+', '', name)


# Weight words in a font name's style part. Black, Heavy and Demi faces print
# heavier than Bold, yet carry neither the bold flag nor 'Bold' in the name
# (Arial-Black: flags 0), so they were drawn regular: the CMS-1696 title and
# section headings (dogfood 2026-10-11). Abbreviations are the foundry ones
# (HelveticaNeueLTStd-Bd/-Hv, NHaasGroteskDSPro-95Blk).
_HEAVY_WORDS = frozenset({'black', 'heavy', 'bd', 'blk', 'hv', 'hvy'})
# An explicit lighter weight in the name outranks a numeric weight class.
# Medium is about 500 and stays regular.
_LIGHT_WORDS = frozenset({'thin', 'hairline', 'extralight', 'ultralight', 'light',
                          'lt', 'book', 'bk', 'regular', 'reg', 'rg', 'roman',
                          'normal', 'medium', 'md', 'med'})
_NAME_WORD = re.compile(r'[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+')
# A content stream that sets text render mode 2 (fill, then stroke).
_FILL_STROKE = re.compile(rb'(?<![\w.\-])2\s+Tr(?![A-Za-z])')
# A stroke this wide against the font size thickens the letters visibly;
# a hairline outline does not.
_SYNTHETIC_STROKE = 0.015


def _style_words(name):
    """The lowercase words of a font name's style part: after the first '-' or
    ',' when there is one, else every word after the first (the family)."""
    name = _unsubset(name or '')
    parts = re.split(r'[-,]', name, maxsplit=1)
    if len(parts) == 2:
        words = _NAME_WORD.findall(parts[1])
    else:
        words = _NAME_WORD.findall(name)[1:]
    return [word.lower() for word in words]


def name_weight(name):
    """True: the name says bold or heavier; False: it names a lighter weight;
    None: it says nothing about weight."""
    if 'Bold' in (name or ''):
        return True
    words = _style_words(name)
    if any(word.endswith('bold') for word in words) or _HEAVY_WORDS & set(words):
        return True
    if 'demi' in words and 'light' not in words:
        return True
    if _LIGHT_WORDS & set(words):
        return False
    return None


def _program_weight(doc, xref):
    """usWeightClass of the embedded program, else the descriptor's FontWeight."""
    try:
        _, _, _, program = doc.extract_font(xref)
    except (ValueError, RuntimeError):
        program = b''
    if program:
        try:
            with TTFont(io.BytesIO(program), lazy=True) as font:
                if 'OS/2' in font:
                    return int(font['OS/2'].usWeightClass)
        except (TTLibError, KeyError, ValueError, OSError, AssertionError, struct_error):
            pass
    descriptor = None
    try:
        kind, value = doc.xref_get_key(xref, 'FontDescriptor')
        if kind == 'xref':
            descriptor = int(value.split()[0])
        else:
            kind, value = doc.xref_get_key(xref, 'DescendantFonts')
            found = re.search(r'(\d+) 0 R', value) if kind == 'array' else None
            if found:
                kind, value = doc.xref_get_key(int(found.group(1)), 'FontDescriptor')
                if kind == 'xref':
                    descriptor = int(value.split()[0])
        if descriptor:
            kind, value = doc.xref_get_key(descriptor, 'FontWeight')
            if kind in ('int', 'float'):
                return float(value)
    except (ValueError, RuntimeError):
        pass
    return None


def majority_bold(parts):
    """A line's weight from its (text, bold) parts: bold when more than half of
    its visible characters are. A line bold for one word ("Caution: To claim
    ...") reads regular and says where its bold is in ``bold_runs``; drawn
    whole in bold it was wider and wrong (W-4, dogfood 2026-10-11)."""
    total = heavy = 0
    for text, bold in parts:
        count = sum(1 for ch in text if not ch.isspace())
        total += count
        heavy += count if bold else 0
    return 2 * heavy > total


class FontWeights:
    """Decides whether an extracted span prints bold, the same way everywhere.

    Bold when any of: MuPDF's bold flag (16, from the font's own style bits);
    a bold or heavier weight word in the font name (Bold, SemiBold, Black,
    Heavy, Demi, Bd, Blk, Hv); the embedded program's OS/2 usWeightClass, or
    the descriptor's FontWeight, at 600 or more, unless the name says a lighter
    weight; or synthetic bold, text drawn filled and stroked (render mode 2)
    with a stroke of at least 1.5 % of its size. Synthetic bold is read from
    the page's own content stream only (a Form XObject's is not searched).
    A whitespace-only span is never bold: its weight cannot be seen.
    """

    def __init__(self, doc):
        self.doc = doc
        self._xrefs = None
        self._names = {}
        self._stroked = {}

    def _font_xrefs(self):
        if self._xrefs is None:
            self._xrefs = {}
            for page in self.doc:
                for font in page.get_fonts(full=True):
                    self._xrefs.setdefault(_unsubset(font[3]), set()).add(font[0])
        return self._xrefs

    def name_bold(self, name):
        if name not in self._names:
            verdict = name_weight(name)
            if verdict is None:
                xrefs = self._font_xrefs()
                # MuPDF names a span's font up to the first space of its BaseFont
                # ('Weighty Regular' reads 'Weighty'); an exact name wins.
                found = xrefs.get(name) or {xref for base, refs in xrefs.items()
                                            if base.split(' ')[0] == name for xref in refs}
                weights = [_program_weight(self.doc, xref) for xref in sorted(found)]
                known = [weight for weight in weights if weight is not None]
                verdict = bool(known) and all(weight >= 600 for weight in known)
            self._names[name] = bool(verdict)
        return self._names[name]

    def _stroked_boxes(self, page):
        if page.number not in self._stroked:
            boxes = []
            try:
                contents = page.read_contents()
            except (ValueError, RuntimeError):
                contents = b''
            if _FILL_STROKE.search(contents or b''):
                traces = page.get_texttrace()
                filled = {(t['font'], tuple(t['chars'][0][2]))
                          for t in traces if t['type'] == 0 and t['chars']}
                for t in traces:
                    if (t['type'] == 1 and t['chars'] and t.get('linewidth')
                            and t['linewidth'] >= _SYNTHETIC_STROKE * t['size']
                            and (t['font'], tuple(t['chars'][0][2])) in filled):
                        boxes.append(pymupdf.Rect(t['bbox']))
            self._stroked[page.number] = boxes
        return self._stroked[page.number]

    def bold(self, span, page=None):
        text = span['text'] if 'text' in span else ''.join(
            c['c'] for c in span.get('chars', ()))
        if not text.strip():
            return False
        if span.get('flags', 0) & 16 or self.name_bold(span.get('font') or ''):
            return True
        if page is None:
            return False
        x0, y0, x1, y1 = span['bbox']
        middle = pymupdf.Point((x0 + x1) / 2, (y0 + y1) / 2)
        return any(box.contains(middle) for box in self._stroked_boxes(page))


def _program_observation(data):
    result = {'class': 'unknown', 'bold': None, 'italic': None,
              'evidence': {}, 'unresolved': [], 'aliases': []}
    try:
        with TTFont(io.BytesIO(data)) as font:
            names = font.get('name')
            if names:
                result['aliases'] = sorted({rec.toUnicode() for rec in names.names
                                            if rec.nameID == 6})
            os2, head, post = font.get('OS/2'), font.get('head'), font.get('post')
            if os2 is None or head is None or post is None:
                result['unresolved'].append('incomplete-font-metadata')
                return result
            panose = os2.panose
            evidence = {
                'family_class': os2.sFamilyClass,
                'panose': {'family_type': panose.bFamilyType,
                           'serif_style': panose.bSerifStyle},
                'weight': os2.usWeightClass, 'selection': os2.fsSelection,
                'mac_style': head.macStyle, 'italic_angle': post.italicAngle,
                'variable': 'fvar' in font,
            }
            result['evidence'] = evidence
            classes = set()
            family = (os2.sFamilyClass >> 8) & 255
            if family in (1, 2, 3, 4, 5, 7):
                classes.add('serif')
            elif family == 8:
                classes.add('sans')
            if panose.bFamilyType == 2:
                if 2 <= panose.bSerifStyle <= 10:
                    classes.add('serif')
                elif 11 <= panose.bSerifStyle <= 15:
                    classes.add('sans')
            if len(classes) == 1:
                result['class'] = classes.pop()
            else:
                result['unresolved'].append('unknown-or-conflicting-font-class')
            bold = bool(os2.fsSelection & 32)
            weight_bold = (True if os2.usWeightClass >= 700 else
                           False if os2.usWeightClass <= 500 else None)
            if bold == bool(head.macStyle & 1) and bold == weight_bold:
                result['bold'] = bold
            else:
                result['unresolved'].append('unknown-or-conflicting-weight')
            italic = bool(os2.fsSelection & (1 | 512))
            if italic == bool(head.macStyle & 2) == bool(post.italicAngle):
                result['italic'] = italic
            else:
                result['unresolved'].append('unknown-or-conflicting-slant')
    except (TTLibError, KeyError, ValueError, OSError) as exc:
        result['unresolved'].append('unreadable-font-program')
        result['evidence']['error'] = type(exc).__name__
    return result


def observe_fonts(doc):
    """All resource candidates, including fonts nested in Form XObjects."""
    fonts, pages = {}, {}
    for page in doc:
        resource_ids = []
        for xref, ext, kind, name, resource, encoding, referencer in page.get_fonts(full=True):
            font_id = f'f{xref}'
            resource_ids.append(font_id)
            if font_id in fonts:
                continue
            observation = {
                'xref': xref, 'name': name, 'kind': kind, 'extension': ext,
                'encoding': encoding, 'class': 'unknown', 'bold': None,
                'italic': None, 'sha256': None, 'aliases': [name],
                'evidence': {'resource': resource, 'referencer': referencer},
                'unresolved': [],
            }
            try:
                _, _, _, program = doc.extract_font(xref)
            except (ValueError, RuntimeError):
                program = b''
            if program:
                metadata = _program_observation(program)
                observation.update({key: metadata[key] for key in
                                    ('class', 'bold', 'italic', 'unresolved')})
                observation['aliases'] = sorted(set([name] + metadata['aliases']))
                observation['evidence'].update(metadata['evidence'])
                observation['sha256'] = hashlib.sha256(program).hexdigest()
            elif name in _STANDARD and kind == 'Type1':
                cls, bold, italic = _STANDARD[name]
                observation.update({'class': cls, 'bold': bold, 'italic': italic})
                observation['evidence']['identity'] = 'standard-face'
            else:
                observation['unresolved'].append('unsupported-font-identity')
            fonts[font_id] = observation
        pages[page.number] = tuple(dict.fromkeys(resource_ids))
    return {'fonts': fonts, 'pages': pages}


def inspect_font(path):
    """Inspect current bytes on every call; a pathname is not a font identity."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise FontError(f'cannot read target font: {exc}', face=str(path),
                        reason='unreadable-font') from exc
    observed = _program_observation(data)
    observed['sha256'] = hashlib.sha256(data).hexdigest()
    return observed


def validate_font(path, font_class, font_role, chars=()):
    """Require concrete, consistent font evidence and glyphs without fallback."""
    observed = inspect_font(path)
    reason = None
    if observed['evidence'].get('variable'):
        reason = 'uninstantiated-font'
    elif observed['unresolved']:
        reason = 'unresolved-target-font'
    elif observed['class'] != font_class:
        reason = 'wrong-font-class'
    elif (observed['bold'], observed['italic']) != (
            font_role in ('bold', 'bold_italic'), font_role in ('italic', 'bold_italic')):
        reason = 'wrong-font-role'
    if reason:
        raise FontError(f'{path}: {reason} for {font_class}/{font_role}',
                        face=str(path), reason=reason)
    if chars:
        import pymupdf
        try:
            font = pymupdf.Font(fontfile=str(path))
        except (RuntimeError, ValueError) as exc:
            raise FontError(f'cannot load target font: {exc}', face=str(path),
                            reason='unreadable-font') from exc
        missing = sorted(ord(ch) for ch in chars if not font.has_glyph(ord(ch), fallback=False))
        if missing:
            codes = ', '.join(f'U+{cp:04X}' for cp in missing)
            raise FontError(f'{path}: missing target glyphs {codes}', face=str(path),
                            reason='missing-glyph')
    return observed


def source_runs(group_spans, occurrence_id, font_observations):
    """Preserve each raw span; font_observations is this page's candidates."""
    runs, offset = [], 0
    for index, span in enumerate(group_spans):
        candidates = sorted(font_id for font_id, obs in font_observations.items()
                            if _unsubset(span['font']) in
                            {_unsubset(name) for name in obs['aliases']})
        font_id = candidates[0] if len(candidates) == 1 else None
        obs = font_observations.get(font_id) or {}
        text = span['text']
        run = {
            'run_id': f'{occurrence_id}/r{index}', 'font_id': font_id,
            'candidates': candidates, 'text': text,
            'offsets': [offset, offset + len(text)],
            'bbox': list(span['bbox']), 'origin': list(span['origin']),
            'size': span['size'], 'color': int(span.get('color', 0)),
            'class': obs.get('class', 'unknown'), 'bold': obs.get('bold'),
            'italic': obs.get('italic'),
            'evidence': {'span_font': span['font'], 'span_flags': span.get('flags'),
                         'span_alpha': span.get('alpha'), 'span_char_flags': span.get('char_flags'),
                         'font_observation': font_id},
            'unresolved': list(obs.get('unresolved', [])) if font_id else
                          ['ambiguous-font-resource' if candidates else 'missing-font-resource'],
        }
        runs.append(run)
        offset += len(text)
    return runs


def page_geometry(doc):
    return [{'page': page.number, 'media_box': list(page.mediabox),
             'crop_box': list(page.cropbox), 'rotation': page.rotation, 'effective_box': list(page.rect)}
            for page in doc]


def field_identities(doc):
    return [{'page': page.number, 'name': widget.field_name,
             'type': widget.field_type, 'rect': list(widget.rect)}
            for page in doc for widget in page.widgets() or ()]


def bind_extraction(payload):
    """Return a copied typography block with its recomputed identity.

    Location and diagnostics are deliberately excluded. Every persisted
    segment/font/style/geometry fact and document string participates.
    """
    typography = deepcopy(payload['typography'])
    typography.pop('extraction_id', None)
    bound = {'typography': typography, 'segments': payload['segments'],
             'document': payload['document'], 'pages': payload['pages']}
    typography['extraction_id'] = canonical_digest(bound)
    return typography
