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
