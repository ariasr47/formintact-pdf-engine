"""Vouch complete source text objects before retaining them in legacy output.

MuPDF decodes the exact source font resource in an isolated page. The PDF
operator parser collects context; it never guesses Unicode from PDF strings.
Only complete, visible page-local objects are replayed. Unsupported objects
elsewhere on a page do not change a translated occurrence's rendering path.
"""
from __future__ import annotations

import hashlib
import io
import math
import unicodedata
from contextlib import ExitStack
from decimal import Decimal

import pikepdf
import pymupdf
from fontTools.ttLib import TTLibError

from .results import MappingError
from .strip_text import page_rawdict_without_annots, resources_of
from .typography_content import later_paint_intersects, page_drawing
from .typography import FontWeights, _GlyphProgram, majority_bold

_IDENTITY = (1., 0., 0., 1., 0., 0.)
_SHOW = frozenset({'Tj', 'TJ', "'", '"'})
_TEXT = frozenset({'BT', 'ET', 'Tf', 'Tm', 'Td', 'TD', 'Tj', 'TJ', 'Tr', 'Tc', 'Tw',
                   'Tz', 'TL', 'Ts', 'w'})
_DEFAULT_TEXT = {'Tc': 0., 'Tw': 0., 'Tz': 100., 'TL': 0., 'Tr': 0., 'Ts': 0.}
_NEUTRAL = b'BT 0 Tc 0 Tw 100 Tz 0 TL 0 Tr 0 Ts ET\n'
# Capitals whose outline top is the face's flat cap line: no apex, no round
# overshoot. Their vouched ink measures how high the face itself paints.
_FLAT_CAPS = frozenset(map(ord, 'BDEFHIJKLMNPRTUVWXYZ'))
_INTENTS = frozenset({'/RelativeColorimetric', '/AbsoluteColorimetric', '/Perceptual', '/Saturation'})


class RetentionCancelled(Exception):
    """Internal cancellation signal; no destination is published."""


def _refuse(reason, segment=None, **details):
    row = dict(details, reason=reason)
    if segment is not None:
        row.update(page=segment.get('page'), segment_id=segment.get('id'),
                   core=segment.get('core'), origin=segment.get('origin'))
    raise MappingError('source text retention cannot be vouched: ' + reason,
                       refusals={'source_retention': [row]})


# Everything this reader can raise while it vouches one page or occurrence.
# Cancellation is separate and always propagates.
_UNVOUCHED = (MappingError, ValueError, TypeError, IndexError, KeyError, AttributeError,
              ArithmeticError, RuntimeError, TTLibError, pikepdf.PdfError)


def _note_unretained(unretained, segment, exc):
    """Report an identity occurrence that keeps the redraw path, and why."""
    if unretained is None:
        return
    if isinstance(exc, str):
        reason = exc
    else:
        rows = (getattr(exc, 'refusals', None) or {}).get('source_retention') or [{}]
        reason = rows[0].get('reason') or (type(exc).__name__ if exc is not None else 'unvouched')
    unretained.append({'page': segment.get('page'), 'segment_id': segment.get('id'),
                       'core': segment.get('core'), 'reason': reason})


def _mostly_overlaps(first, second, share=.5):
    """True when two extracted runs share at least half the smaller box."""
    try:
        a, b = [float(v) for v in first['bbox']], [float(v) for v in second['bbox']]
    except (KeyError, TypeError, ValueError):
        return True
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return False
    smaller = min((a[2]-a[0]) * (a[3]-a[1]), (b[2]-b[0]) * (b[3]-b[1]))
    return smaller <= 0 or width * height >= share * smaller


def _cancel(cancel):
    if cancel is not None and cancel():
        raise RetentionCancelled


def _close(a, b, tolerance=.011):
    return len(a) == len(b) and all(math.isfinite(float(x)) and
        math.isfinite(float(y)) and abs(float(x)-float(y)) <= tolerance
        for x, y in zip(a, b))


def _same_glyphs(first, second):
    return (len(first) == len(second) and all(a[:2] == b[:2] and
            _close(a[2], b[2], 1e-4) and _close(a[3], b[3], 1e-4)
            for a, b in zip(first, second)))


def _compose(left, right):
    a, b, c, d, e, f = left
    g, h, i, j, k, l = right
    return (a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j,
            a*k+c*l+e, b*k+d*l+f)


def _geometry(page):
    # Both readers are MuPDF: PDF CropBox and MuPDF CropBox use different Y.
    return (tuple(page.mediabox), tuple(page.cropbox), page.rotation,
            page.parent.xref_get_key(page.xref, 'UserUnit'))


def _unrotated_transform(page):
    # MuPDF's page text observations use the unrotated crop frame. The public
    # transformation_matrix drops asymmetric crop offsets when Rotate != 0.
    # Observe the matching transform in memory and always restore the rotation;
    # this source document is never saved.
    rotation = page.rotation
    try:
        if rotation:
            page.set_rotation(0)
        return pymupdf.Matrix(page.transformation_matrix)
    finally:
        if rotation:
            page.set_rotation(rotation)


def _inverse(matrix):
    result = pymupdf.Matrix()
    if (not all(math.isfinite(value) for value in matrix) or
            result.invert(matrix) != 0 or not all(math.isfinite(value) for value in result)):
        return None
    return result


def _resource_signature(value, active=None, depth=0):
    """Complete semantic font graph, independent of xrefs and compression."""
    active = set() if active is None else active
    if depth > 40:
        raise ValueError('resource graph depth')
    if isinstance(value, (pikepdf.Dictionary, pikepdf.Array, pikepdf.Stream)):
        key = value.objgen if value.is_indirect else id(value)
        if key in active:
            raise ValueError('cyclic font resource')
        branch = active | {key}
        if isinstance(value, pikepdf.Array):
            return ('array', tuple(_resource_signature(v, branch, depth+1) for v in value))
        ignored = {'/Length', '/Filter', '/DecodeParms'} if isinstance(value, pikepdf.Stream) else set()
        entries = tuple((str(k), _resource_signature(v, branch, depth+1))
                        for k, v in sorted(value.items()) if str(k) not in ignored)
        if isinstance(value, pikepdf.Stream):
            return ('stream', entries, hashlib.sha256(value.read_bytes()).hexdigest())
        return ('dictionary', entries)
    if isinstance(value, pikepdf.String):
        return ('string', bytes(value))
    if isinstance(value, pikepdf.Name):
        return ('name', str(value))
    if isinstance(value, bool) or value is None:
        return ('literal', value)
    if isinstance(value, (int, float, Decimal)):
        return ('number', Decimal(str(value)).normalize())
    raise ValueError('unsupported font resource value')


def _mcid_scope(values):
    return (len(values) == 2 and isinstance(values[0], pikepdf.Name) and str(values[0]) != '/OC' and
            isinstance(values[1], pikepdf.Dictionary) and
            set(str(key) for key in values[1].keys()) == {'/MCID'} and
            isinstance(values[1]['/MCID'], int) and not isinstance(values[1]['/MCID'], bool) and
            values[1]['/MCID'] >= 0)


def _groups(page):
    """Collect complete BT groups with the state that precedes each group."""
    state = dict(_DEFAULT_TEXT, ctm=_IDENTITY, fill=b'0 g', unsafe=False,
                 clip=False, font=None, font_size=None, intent=None)
    stack, marked, groups, group = [], [], [], None
    for instruction in pikepdf.parse_content_stream(page):
        if not isinstance(instruction, pikepdf.ContentStreamInstruction):
            if group is not None:
                group['unsupported'] = True
                group['opaque_instruction'] = True
            continue  # An unrelated inline image paints, but changes no text state.
        values, op = instruction.operands, str(instruction.operator)
        if op == 'BT':
            if group is not None:
                raise ValueError('nested BT')
            group = {'instructions': [], 'state': dict(state), 'unsupported': any(marked),
                     'shows': 0, 'fonts': 0, 'matrices': 0, 'alias': None,
                     'text_clip': False, 'show_ready': True,
                     'inherited_marked': any(marked), 'opaque_instruction': False}
        if group is not None:
            group['instructions'].append(instruction)
            # ReportLab's final line advance moves the cursor without drawing.
            trailing_line_advance = op == 'T*' and not values and group['shows'] == 1
            if op not in _TEXT and not trailing_line_advance:
                group['unsupported'] = True
            if op in _SHOW:
                # Text clipping is accumulated at the show and applied at ET;
                # resetting Tr before ET does not restore the previous clip.
                group['text_clip'] |= 4 <= state['Tr'] <= 7
                group['show_ready'] &= group['fonts'] == 1 and group['matrices'] == 1
                group['show_state'] = dict(state)
                group['shows'] += 1
            if op == 'Tf':
                group['fonts'] += 1
                group['alias'] = str(values[0])
            # A sole Td/TD starts from BT's reset line matrix; its complete
            # absolute placement is verified by the same isolated probe.
            if op in ('Tm', 'Td', 'TD'):
                group['matrices'] += 1
        if op == 'q':
            stack.append(dict(state))
        elif op == 'Q':
            if not stack:
                raise ValueError('unbalanced Q')
            state = stack.pop()
        elif op in ('BDC', 'BMC'):
            marked.append(op != 'BDC' or not _mcid_scope(values))
        elif op == 'EMC':
            if not marked:
                raise ValueError('unbalanced marked content')
            marked.pop()
        elif op == 'cm':
            matrix = tuple(float(v) for v in values)
            if len(matrix) != 6 or not all(math.isfinite(v) for v in matrix):
                raise ValueError('invalid cm')
            state['ctm'] = _compose(state['ctm'], matrix)
        elif op in ('g', 'rg', 'k'):
            state['fill'] = pikepdf.unparse_content_stream([instruction])
        elif op == 'ri':
            if len(values) == 1 and isinstance(values[0], pikepdf.Name) and str(values[0]) in _INTENTS:
                state['intent'] = pikepdf.unparse_content_stream([instruction])
            else:
                state['unsafe'] = True
        elif op in ('cs', 'sc', 'scn', 'gs'):
            state['unsafe'] = True
        elif op in ('W', 'W*'):
            state['clip'] = True
        elif op in _DEFAULT_TEXT:
            state[op] = float(values[0])
        elif op == 'Tf':
            state['font'] = str(values[0])
            state['font_size'] = float(values[1])
        if op == 'ET':
            if group is None:
                raise ValueError('unmatched ET')
            group['bytes'] = pikepdf.unparse_content_stream(group['instructions'])
            groups.append(group)
            state['clip'] |= group['text_clip']
            group = None
    if group is not None or stack or marked:
        raise ValueError('unbalanced content')
    return groups


def _payload(group):
    state = group['state']
    matrix = (' '.join(format(v, '.12g') for v in state['ctm']) + ' cm\n').encode('ascii')
    text_state = '\n'.join(f'{state[op]:.12g} {op}' for op in _DEFAULT_TEXT).encode('ascii')
    inherited_font = (b'\n' + pikepdf.unparse_content_stream([
        pikepdf.ContentStreamInstruction([pikepdf.Name(state['font']),
            Decimal(str(state['font_size']))], pikepdf.Operator('Tf'))])
        if state['font'] is not None and state['font_size'] is not None else b'')
    return (b'q\n' + _NEUTRAL + matrix + state['fill'] + b'\n' + (state['intent'] or b'') +
            b'\nBT\n' + text_state +
            inherited_font + b'\nET\n' + group['bytes'] + b'\nQ\n')


def _encoded_show(instruction, resource):
    subtype = resource.get('/Subtype') if resource is not None else None
    if subtype == pikepdf.Name.Type0 and resource.get('/Encoding') == pikepdf.Name('/Identity-H'):
        unit = 2
    elif subtype in (pikepdf.Name.Type1, pikepdf.Name.TrueType):
        unit = 1
    else:
        raise ValueError('unsupported source code width')
    values = list(instruction.operands[0]) if str(instruction.operator) == 'TJ' else [instruction.operands[0]]
    count, leading = 0, 0.
    for value in values:
        if isinstance(value, pikepdf.String):
            if len(bytes(value)) % unit:
                raise ValueError('incomplete source code')
            count += len(bytes(value)) // unit
        elif (not isinstance(value, (int, float, Decimal)) or isinstance(value, bool) or
              not math.isfinite(float(value))):
            raise ValueError('unsupported source show operand')
        elif count == 0:
            leading += float(value)
    return count, leading


def _positioned_shows(group, source_page=None, page_transform=None):
    """Normalize explicit-position shows and pure MCID metadata, then vouch.

    Encoded shows and their real Tf size are unchanged. Td updates the text
    line matrix, independent of a preceding show's glyph advance. MCID-only
    tags are validated, but their IDs are never duplicated in appended text.
    """
    if ((group['shows'] <= 1 and not group['unsupported'] and group['fonts'] == 1 and
         group['show_ready']) or group.get('opaque_instruction') or
            group.get('inherited_marked')):
        return (group,)
    current = dict(group['state'])
    line, font, ready, positioned, tags = _IDENTITY, None, False, False, []
    glyphs, cursor = None, 0
    page_inverse, ctm_inverse = None, None
    if source_page is not None and page_transform is not None and group['shows'] > 1:
        _, traces = _observe_group(source_page, group)
        if all(trace['type'] == 0 and trace.get('wmode') == 0 and trace.get('opacity', 1) == 1
               for trace in traces):
            glyphs = [char for trace in sorted(traces, key=lambda trace: trace['seqno'])
                      for char in trace['chars']]
            page_inverse = _inverse(page_transform)
            ctm_inverse = _inverse(pymupdf.Matrix(group['state']['ctm']))
            if page_inverse is None or ctm_inverse is None:
                return (group,)
    if (current['font'] is not None and current['font_size'] is not None and
            math.isfinite(current['font_size']) and current['font_size'] > 0):
        font = pikepdf.ContentStreamInstruction(
            [pikepdf.Name(current['font']), Decimal(str(current['font_size']))], pikepdf.Operator('Tf'))
    flatness, width, parts = None, None, []
    for instruction in group['instructions']:
        values, op = instruction.operands, str(instruction.operator)
        if op in ('BT', 'ET'):
            continue
        if op == 'BDC':
            if not _mcid_scope(values):
                return (group,)
            tags.append(instruction)
        elif op == 'EMC':
            if not tags:
                return (group,)
            tags.pop()
        elif op == 'Tf':
            if len(values) != 2 or not math.isfinite(float(values[1])) or float(values[1]) <= 0:
                return (group,)
            font = instruction
            current['font'] = str(values[0])
            current['font_size'] = float(values[1])
        elif op == 'Tm':
            line = tuple(float(value) for value in values)
            if len(line) != 6 or not all(math.isfinite(value) for value in line):
                return (group,)
            ready = True
            positioned = True
        elif op in ('Td', 'TD'):
            if len(values) != 2 or not all(math.isfinite(float(value)) for value in values):
                return (group,)
            dx, dy = map(float, values)
            line = _compose(line, (1., 0., 0., 1., dx, dy))
            if op == 'TD':
                current['TL'] = -dy
            ready = True
            positioned = True
        elif op in _DEFAULT_TEXT:
            if len(values) != 1 or not math.isfinite(float(values[0])):
                return (group,)
            current[op] = float(values[0])
        elif op in ('g', 'rg', 'k'):
            if (len(values) != {'g': 1, 'rg': 3, 'k': 4}[op] or
                    not all(math.isfinite(float(value)) and 0 <= float(value) <= 1 for value in values)):
                return (group,)
            current['fill'] = pikepdf.unparse_content_stream([instruction])
        elif op == 'ri':
            if len(values) != 1 or not isinstance(values[0], pikepdf.Name) or str(values[0]) not in _INTENTS:
                return (group,)
            current['intent'] = pikepdf.unparse_content_stream([instruction])
        elif op in ('i', 'w'):
            if len(values) != 1 or not math.isfinite(float(values[0])) or float(values[0]) < 0:
                return (group,)
            if op == 'i':
                if float(values[0]) > 100:
                    return (group,)
                flatness = instruction
            else:
                width = instruction
        elif op in ('Tj', 'TJ'):
            if font is None:
                return (group,)
            selected, leading = None, 0.
            if glyphs is not None:
                try:
                    resource = resources_of(source_page.obj).get('/Font', {}).get(current['font'])
                    count, leading = _encoded_show(instruction, resource)
                except (ValueError, TypeError, IndexError):
                    return (group,)
                selected = glyphs[cursor:cursor+count]
                if len(selected) != count:
                    return (group,)
                cursor += count
            placement = line
            if (not ready or (selected and positioned and
                    all(current[key] == default for key, default in _DEFAULT_TEXT.items() if key != 'TL'))):
                if (not positioned or not selected or
                        any(current[key] != default for key, default in _DEFAULT_TEXT.items() if key != 'TL')):
                    return (group,)
                # Vouch the original glyph's actual baseline, including native
                # rounding accumulated through explicit relative moves. Keep
                # the encoded show and apply its leading TJ offset only once.
                point = pymupdf.Point(selected[0][2]) * page_inverse * ctm_inverse
                offset = -leading / 1000 * current['font_size']
                placement = (*line[:4], point.x-line[0]*offset, point.y-line[1]*offset)
                if not all(math.isfinite(value) for value in placement):
                    return (group,)
            matrix = (' '.join(format(value, '.12g') for value in placement) + ' Tm\n').encode('ascii')
            text = '\n'.join(f'{current[key]:.12g} {key}' for key in _DEFAULT_TEXT).encode('ascii')
            setup = [item for item in (flatness, width, font) if item is not None]
            data = (b'BT\n' + pikepdf.unparse_content_stream(setup) + b'\n' + text + b'\n' +
                    matrix + pikepdf.unparse_content_stream([instruction]) + b'\nET')
            # Reparse into normal instructions for the unchanged encoded-unit
            # and TJ-slice proofs. This is an owned metadata-only temporary.
            with pikepdf.new() as parsed:
                page = parsed.add_blank_page()
                page.Contents = parsed.make_stream(data)
                instructions = list(pikepdf.parse_content_stream(page))
            state = dict(group['state'], fill=current['fill'], intent=current['intent'])
            part = dict(group, instructions=instructions, bytes=data, state=state,
                        show_state=dict(current), alias=current['font'], fonts=1,
                        matrices=1, shows=1, show_ready=True, unsupported=False)
            if selected is not None:
                _, seen_traces = _observe_group(source_page, part)
                seen = [char for trace in sorted(seen_traces, key=lambda trace: trace['seqno'])
                        for char in trace['chars']]
                if not _same_glyphs(selected, seen):
                    return (group,)
            parts.append(part)
            ready = False
        else:
            return (group,)
    if tags or len(parts) != group['shows'] or (glyphs is not None and cursor != len(glyphs)):
        return (group,)
    return tuple(parts)


def _observe_group(source_page, group):
    with pikepdf.new() as probe:
        probe.pages.append(source_page)
        page = probe.pages[0]
        if '/Annots' in page:
            del page['/Annots']
        page.Contents = probe.make_stream(_payload(group))
        data = io.BytesIO()
        probe.save(data)
    with pymupdf.open(stream=data.getvalue(), filetype='pdf') as observed:
        spans = _spans(observed[0])
        traces, _, _ = page_drawing(observed, 0)
    return spans, traces


def _fixed_show(group, traces, resource, segment):
    """Account for every original encoded unit in its actual glyph order."""
    subtype = resource.get('/Subtype')
    if subtype == pikepdf.Name.Type0 and resource.get('/Encoding') == pikepdf.Name('/Identity-H'):
        unit = 2
    elif subtype in (pikepdf.Name.Type1, pikepdf.Name.TrueType):
        unit = 1
    else:
        _refuse('mixed-object', segment, detail='unsupported code widths')
    ordered = sorted(traces, key=lambda trace: trace['seqno'])
    if len(ordered) != 1 or ordered[0]['type'] != 0 or ordered[0].get('wmode') != 0:
        _refuse('mixed-object', segment, detail='ambiguous glyph order')
    show = next(item for item in group['instructions'] if str(item.operator) in _SHOW)
    op = str(show.operator)
    if op == 'TJ':
        array = list(show.operands[0])
    elif op == 'Tj':
        array = [show.operands[0]]
    else:
        _refuse('mixed-object', segment, detail='position-changing show')
    count = 0
    for item in array:
        if isinstance(item, pikepdf.String):
            if len(bytes(item)) % unit:
                _refuse('mixed-object', segment, detail='incomplete character code')
            count += len(bytes(item)) // unit
        elif not isinstance(item, (int, float, Decimal)) or isinstance(item, bool) or not math.isfinite(float(item)):
            _refuse('mixed-object', segment, detail='unsupported TJ operand')
    if count != len(ordered[0]['chars']):
        _refuse('mixed-object', segment, detail='codes and actual glyphs differ')
    return unit, array, show, ordered[0]


def _slice_group(source_page, group, span, traces, resource, segment):
    """Retain a full source span from a fixed-code, single-show object."""
    unit, array, show, trace = _fixed_show(group, traces, resource, segment)
    matches = _trace_windows(span, traces)
    if len(matches) != 1:
        _refuse('mixed-object', segment, detail='ambiguous glyph slice')
    _, start, end = matches[0]
    all_chars = trace['chars']
    selected, cursor = [], 0
    for item in array:
        if isinstance(item, pikepdf.String):
            codes = bytes(item)
            if len(codes) % unit:
                _refuse('mixed-object', segment, detail='incomplete character code')
            count = len(codes) // unit
            left, right = max(start-cursor, 0), min(end-cursor, count)
            if left < right:
                selected.append(pikepdf.String(codes[left*unit:right*unit]))
            cursor += count
        elif isinstance(item, (int, float, Decimal)) and not isinstance(item, bool):
            if start <= cursor < end:
                selected.append(item)  # Preserve kerning inside the selected span.
        else:
            _refuse('mixed-object', segment, detail='unsupported TJ operand')
    if cursor != len(all_chars):
        _refuse('mixed-object', segment, detail='codes and actual glyphs differ')
    def with_show(values):
        instructions = [pikepdf.ContentStreamInstruction([pikepdf.Array(values)], pikepdf.Operator('TJ'))
                        if item is show else item for item in group['instructions']]
        return dict(group, instructions=instructions, bytes=pikepdf.unparse_content_stream(instructions))
    sliced = with_show(selected)
    observed_spans, observed_traces = _observe_group(source_page, sliced)
    if len(observed_spans) != 1 or len(observed_traces) != 1:
        _refuse('mixed-object', segment, detail='slice did not produce one span')
    original_trace = _trace_slice(span, traces)[0][0]
    # _ink_boxes validates orthogonal axes before applying the baseline.
    matrix = _IDENTITY
    for item in group['instructions']:
        if str(item.operator) == 'Tm':
            matrix = tuple(float(value) for value in item.operands)
    a, b, c, d, _, _ = _compose(group['state']['ctm'], matrix)
    if a*d-b*c == 0 or not math.isclose(a*c+b*d, 0, abs_tol=1e-8):
        _refuse('mixed-object', segment, detail='nonconformal placement')
    dx, dy = original_trace['dir']
    vx = span['origin'][0] - observed_spans[0]['origin'][0]
    vy = span['origin'][1] - observed_spans[0]['origin'][1]
    if abs(vx*dy-vy*dx) > 1e-4:
        _refuse('mixed-object', segment, detail='slice changes baseline')
    adjustment = -(vx*dx+vy*dy) / original_trace['size'] * 1000
    sliced = with_show([Decimal(str(adjustment)), *selected])
    observed_spans, observed_traces = _observe_group(source_page, sliced)
    matches = _trace_slice(span, traces)
    actual = _trace_slice(observed_spans[0], observed_traces) if len(observed_spans) == 1 else []
    if (len(observed_spans) != 1 or not _same_span(span, observed_spans[0]) or
            len(matches) != 1 or len(actual) != 1 or not _same_glyphs(matches[0][1], actual[0][1])):
        deltas = [abs(float(a)-float(b)) for wanted, seen in zip(matches[0][1], actual[0][1])
                  for a, b in zip((*wanted[2], *wanted[3]), (*seen[2], *seen[3]))] if matches and actual else []
        _refuse('mixed-object', segment, detail='slice glyph geometry differs',
                span_equal=(len(observed_spans) == 1 and _same_span(span, observed_spans[0])),
                glyph_max_delta=max(deltas, default=None))
    return sliced, observed_spans[0], observed_traces, (start, end)


def _spans(page, weights=None):
    # `weights` (the source document's) adds each span's weight, decided the
    # way extraction decided the segment's; _bind_segment compares the two.
    spans = []
    for block_index, block in enumerate(page_rawdict_without_annots(page).get('blocks', [])):
        for line_index, line in enumerate(block.get('lines', [])):
            for span in line['spans']:
                spans.append(dict(span, text=''.join(c['c'] for c in span['chars']),
                                  direction=tuple(line['dir']), line_key=(block_index, line_index)))
                if weights is not None:
                    spans[-1]['heavy'] = weights.bold(spans[-1], page)
    return spans


def _same_span(first, second, *, allow_contextual_superscript=False):
    flags_match = first['flags'] == second['flags']
    if allow_contextual_superscript:
        # MuPDF derives this bit from the line's first character, separately
        # from intrinsic font flags. Neighboring translated runs can change
        # it without changing the retained font, size or glyph geometry.
        flags_match = not ((first['flags'] ^ second['flags']) & ~pymupdf.TEXT_FONT_SUPERSCRIPT)
    return (first['text'] == second['text'] and first['font'] == second['font'] and
            first['color'] == second['color'] and flags_match and
            _close((first['size'],), (second['size'],), 1e-5) and
            all(_close(first[key], second[key], 1e-4) for key in ('origin', 'bbox', 'direction')) and
            len(first['chars']) == len(second['chars']) and
            all(a['c'] == b['c'] and _close(a['origin'], b['origin'], 1e-4) and
                _close(a['bbox'], b['bbox'], 1e-4)
                for a, b in zip(first['chars'], second['chars'])))


def _span_slice(span, start, end):
    chars = span['chars'][start:end]
    return dict(span, chars=chars, text=''.join(char['c'] for char in chars),
                origin=chars[0]['origin'], bbox=(min(char['bbox'][0] for char in chars),
                min(char['bbox'][1] for char in chars), max(char['bbox'][2] for char in chars),
                max(char['bbox'][3] for char in chars)))


def _matching_slices(expected, spans):
    matches = []
    for span in spans:
        count = len(expected['chars'])
        for start in range(len(span['chars'])-count+1):
            if span['chars'][start]['c'] != expected['chars'][0]['c']:
                continue
            part = _span_slice(span, start, start+count)
            if _same_span(expected, part, allow_contextual_superscript=True):
                matches.append(part)
    return matches


def _source_parts(span, probes, segment):
    """Bind a merged MuPDF span to complete independently observed BT parts."""
    candidates = []
    for _, _, probe_spans, _ in probes:
        for observed in probe_spans:
            count = len(observed['chars'])
            if not count:
                continue
            for start in range(len(span['chars'])-count+1):
                if not _close(span['chars'][start]['origin'], observed['origin'], 1e-4):
                    continue
                part = _span_slice(span, start, start+count)
                if _same_span(part, observed):
                    candidates.append((start, start+count, part))
    covered = [0] * len(span['chars'])
    for start, end, _ in candidates:
        for index in range(start, end):
            covered[index] += 1
    if any(count != 1 and not (count == 0 and char.get('synthetic') and char['c'].isspace())
           for count, char in zip(covered, span['chars'])):
        _refuse('ambiguous-or-missing', segment, detail='text-object coverage')
    return [part for _, _, part in sorted(candidates, key=lambda row: row[:2])]


def _bind_segment(segment, spans, *, metrics=True):
    # Legacy extraction merges adjacent spans into one occurrence. Bind its
    # complete contiguous source parts, never choose a substring of a span.
    matches = []
    wanted = str(segment['text']).strip()
    for offset, first in enumerate(spans):
        if not _close(first['origin'], segment['origin']):
            continue
        parts, text = [], ''
        for span in spans[offset:]:
            if span['line_key'] != first['line_key']:
                break
            parts.append(span)
            text += span['text']
            if text.strip() == wanted:
                matches.append(tuple(parts))
            if len(text.strip()) > len(wanted):
                break
    if len(matches) != 1:
        _refuse('stale-occurrence', segment, matches=len(matches))
    parts = matches[0]
    if not metrics:
        return parts
    first = parts[0]
    box = (min(s['bbox'][0] for s in parts), min(s['bbox'][1] for s in parts),
           max(s['bbox'][2] for s in parts), max(s['bbox'][3] for s in parts))
    if (not _close(box, segment['bbox']) or
            not _close((first['size'],), (segment['size'],)) or
            (segment.get('font') is not None and first['font'] != segment['font']) or
            first['color'] != segment.get('color', 0) or
            not _close(first['direction'], segment.get('dir', (1., 0.))) or
            majority_bold((s['text'], s['heavy']) for s in parts) != bool(segment.get('bold')) or
            any('Italic' in s['font'] or 'Oblique' in s['font'] for s in parts) != bool(segment.get('italic'))):
        _refuse('stale-occurrence', segment)
    return parts


def _trace_windows(span, traces):
    # A TJ displacement can insert an extracted ASCII space without an
    # encoded unit or painted glyph. Bind the physical glyphs on both sides;
    # the full raw span, original TJ operands and glyph geometry are still
    # proved separately before retaining or publishing the source program.
    expected = [char for char in span['chars']
                if not (char['c'] == ' ' and char.get('synthetic'))]
    if not expected:
        return []
    matches = []
    for trace in traces:
        if trace['font'] != span['font']:
            continue
        chars = trace['chars']
        for offset in range(len(chars)-len(expected)+1):
            window = chars[offset:offset+len(expected)]
            if all(cp == ord(char['c']) and _close(origin, char['origin'], 1e-4)
                   for (cp, _, origin, _), char in zip(window, expected)):
                matches.append((trace, offset, offset+len(expected)))
    return matches


def _trace_slice(span, traces):
    return [(trace, trace['chars'][start:end]) for trace, start, end in _trace_windows(span, traces)]


class _ResolvedGlyphProgram:
    """Filled outlines from the font MuPDF resolved for this source resource.

    A missing embedded program does not justify selecting a font by name.
    Keep the exact resolved font before releasing its PDF descriptor. The
    owning font/path wrappers release their references; the document remains
    open throughout prepare(). No cross-document cache is retained.
    """
    def __init__(self, document, xref):
        native = pymupdf.mupdf
        pdf = pymupdf._as_pdf_document(document)
        resource = native.pdf_load_object(pdf, xref)
        descriptor = native.ll_pdf_load_font(pdf.m_internal, None, resource.m_internal)
        try:
            if not descriptor or not descriptor.font or descriptor.wmode != 0:
                raise ValueError('unresolved horizontal source font')
            self.font = native.FzFont(native.ll_fz_keep_font(descriptor.font))
        finally:
            if descriptor:
                native.ll_pdf_drop_font(descriptor)
        if not self.font.fz_font_ft_face():
            raise ValueError('source font has no scalable outline')
        self.cache = {}

    def glyph(self, gid):
        if gid not in self.cache:
            native = pymupdf.mupdf
            identity = native.FzMatrix()
            path = self.font.fz_outline_glyph(gid, identity)
            if not path:
                raise ValueError('source glyph has no path')
            rectangle = native.ll_fz_bound_path(path.m_internal, None, identity.internal())
            values = (rectangle.x0, -rectangle.y1, rectangle.x1, -rectangle.y0)
            if not all(math.isfinite(value) for value in values):
                raise ValueError('nonfinite source outline')
            # MuPDF returns an empty sentinel for a whitespace outline. Its
            # coordinates are finite, but its lower bounds exceed its uppers.
            ink = values if values[0] < values[2] and values[1] < values[3] else None
            self.cache[gid] = (None, ink)  # Advance is unused by _ink_boxes.
        return self.cache[gid]


def _source_program(document, resource, programs, stack):
    if (resource is None or not resource.is_indirect or resource.get('/Subtype') not in
            (pikepdf.Name.Type1, pikepdf.Name('/MMType1'), pikepdf.Name.TrueType, pikepdf.Name.Type0)):
        raise ValueError('unsupported source font resource')
    # Type3 requires a PDF resource stack and has no vouched scalable outline.
    # The explicitly accepted types above do not use the loader's null stack.
    xref = resource.objgen[0]
    if xref not in programs:
        data = document.extract_font(xref)[3]
        if data:
            try:
                programs[xref] = _GlyphProgram(data, stack)
            except TTLibError:
                programs[xref] = _ResolvedGlyphProgram(document, xref)
        else:
            programs[xref] = _ResolvedGlyphProgram(document, xref)
    return programs[xref]


def _outline_axes(group, trace, *, allow_reflection=True):
    matrix = _IDENTITY
    for instruction in group['instructions']:
        if str(instruction.operator) == 'Tm':
            matrix = tuple(float(v) for v in instruction.operands)
    a, b, c, d, _, _ = _compose(group['state']['ctm'], matrix)
    if (trace.get('wmode') != 0 or a*d-b*c == 0 or (a*d-b*c < 0 and not allow_reflection) or
            not math.isclose(a*c+b*d, 0, abs_tol=1e-8)):
        raise ValueError('unsupported outline transform')
    dx, dy = trace['dir']
    return dx, dy, math.copysign(math.hypot(c, d) / math.hypot(a, b), a*d-b*c)


def _ink_boxes(group, trace, chars, program, *, allow_reflection=True):
    """Vouch actual GID ink for orthogonal source axes, including reflection."""
    dx, dy, orientation = _outline_axes(group, trace, allow_reflection=allow_reflection)
    boxes = []
    for cp, gid, origin, _ in chars:
        _, ink = program.glyph(gid)
        if ink is None:
            if cp > 32:
                raise ValueError('visible glyph has no outline')
            continue
        corners = [(origin[0] + (dx*x-dy*y*orientation)*trace['size'],
                    origin[1] + (dy*x+dx*y*orientation)*trace['size'])
                   for x in (ink[0], ink[2]) for y in (ink[1], ink[3])]
        boxes.append((min(p[0] for p in corners), min(p[1] for p in corners),
                      max(p[0] for p in corners), max(p[1] for p in corners)))
    return tuple(boxes)


def _rectangle_union(rectangles):
    # A coordinate hull can equal MuPDF's special infinite-Rect sentinel even
    # when its members do not. Never use Rect.intersects on the hull itself.
    if not rectangles or any(not math.isfinite(value) for rect in rectangles for value in rect):
        return None
    return (min(rect.x0 for rect in rectangles), min(rect.y0 for rect in rectangles),
            max(rect.x1 for rect in rectangles), max(rect.y1 for rect in rectangles))


def _hulls_separate(first, second):
    # Unknown/nonfinite hulls must reach the unchanged exact glyph predicate.
    return (first is not None and second is not None and
            (first[0] >= second[2] or second[0] >= first[2] or
             first[1] >= second[3] or second[1] >= first[3]))


def _text_paints(traces, vouched_ink=None, *, paints=None):
    """Invocation-local broad phase; actual glyph intersections still decide."""
    result = []
    for trace in traces:
        if trace['type'] not in (0, 1) or trace.get('opacity', 1) <= 0:
            continue
        intervals = (vouched_ink or {}).get(id(trace), ())
        rectangles = tuple(pymupdf.Rect(box) for _, _, boxes in intervals for box in boxes)
        if any(not any(start <= index < end for start, end, _ in intervals)
               for index in range(len(trace['chars']))):
            # Trace boxes are advances and font metrics, not ink bounds. The
            # native paint log uses fz_bound_text with the actual fonts, glyphs,
            # full transform and stroke. Keep that conservative whole-paint
            # bound whenever any glyph interval has not been independently
            # vouched; never substitute an unknown glyph's nominal box.
            # ToUnicode may map a painted glyph to a space/control character;
            # Unicode whitespace is not evidence that its actual ink is empty.
            sequence = trace.get('seqno')
            if (paints is None or not isinstance(sequence, int) or not 0 <= sequence < len(paints)
                    or paints[sequence][0] not in ('fill-text', 'stroke-text')):
                _refuse('source-trace', detail='text paint bound cannot be identified')
            bound = pymupdf.Rect(paints[sequence][1])
            if bound.is_infinite or any(not math.isfinite(value) for value in bound):
                _refuse('source-trace', detail='text paint bound is unknown')
            # A finite empty native bound proves this paint has no ink. This
            # is different from inferring emptiness from its Unicode text.
            if not bound.is_empty:
                rectangles += (bound,)
        rectangles = tuple(rect for rect in rectangles if not rect.is_empty and not rect.is_infinite)
        result.append((trace.get('seqno', -1), _rectangle_union(rectangles), rectangles))
    return tuple(result)


def _visible(span, traces, paints, annotation_start, segment, *, ink_boxes=None, text_paints=None):
    matches = _trace_slice(span, traces)
    if len(matches) != 1:
        _refuse('source-trace', segment, matches=len(matches))
    trace, chars = matches[0]
    if trace['type'] != 0 or trace.get('opacity', 1) != 1:
        _refuse('unsupported-text-object', segment)
    boxes = ink_boxes if ink_boxes is not None else tuple(box for cp, _, _, box in chars if cp > 32)
    if any(later_paint_intersects(paints, trace.get('seqno'), pymupdf.Rect(box), annotation_start)
           for box in boxes):
        _refuse('covered-source', segment)
    source_rectangles = tuple(pymupdf.Rect(box) for box in boxes)
    source_rectangles = tuple(rect for rect in source_rectangles if not rect.is_empty and not rect.is_infinite)
    source_union = _rectangle_union(source_rectangles)
    for sequence, later_union, rectangles in (_text_paints(traces, paints=paints) if text_paints is None else text_paints):
        if (sequence <= trace.get('seqno', -1) or not source_rectangles or not rectangles or
                _hulls_separate(source_union, later_union)):
            continue
        if any(rect.intersects(other) for rect in source_rectangles for other in rectangles):
            _refuse('covered-source', segment, detail='later text intersects')
    return tuple((cp, gid, tuple(origin), tuple(box)) for cp, gid, origin, box in chars)


def _vouched_group_ink(source, source_fonts, probes, traces, programs, stack, cancel):
    """Optional finer bounds; every unknown interval keeps its native bound."""
    result = {}
    for _, group, spans, probe_traces in probes:
        _cancel(cancel)
        if (group['unsupported'] or group['shows'] != 1 or group['fonts'] != 1 or
                group['matrices'] != 1 or not group['show_ready'] or group['text_clip'] or
                group['state']['clip'] or group['state']['unsafe'] or
                any(group['show_state'][key] != default for key, default in _DEFAULT_TEXT.items() if key != 'TL')):
            continue
        resource = source_fonts.get(group['alias'])
        if resource is None:
            continue
        for span in spans:
            # Extraction can insert a synthetic space for a TJ positioning
            # gap. It has no encoded unit or painted glyph. Match the actual
            # glyph sequence on both sides; CP/GID/origin/bbox proof below
            # remains mandatory before any native paint bound is refined.
            physical = dict(span, chars=[char for char in span['chars']
                if not (char['c'] == ' ' and char.get('synthetic'))])
            if not physical['chars']:
                continue
            windows = _trace_windows(physical, traces)
            seen = _trace_slice(physical, probe_traces)
            if len(windows) != 1 or len(seen) != 1:
                continue
            trace, start, end = windows[0]
            chars = trace['chars'][start:end]
            if (trace['type'] != 0 or trace.get('opacity', 1) != 1 or
                    not _same_glyphs(chars, seen[0][1])):
                continue
            try:
                program = _source_program(source, resource, programs, stack)
                boxes = _ink_boxes(group, trace, chars, program)
            except Exception:
                continue
            intervals = result.setdefault(id(trace), [])
            if not any(start < old_end and old_start < end for old_start, old_end, _ in intervals):
                intervals.append((start, end, boxes))
    return result


def retainable_segments(segments, translations, legacy_conf=None):
    conf = legacy_conf or {}
    result = {}
    for segment in segments:
        core, page = segment.get('core'), int(segment['page'])
        target = translations.get(core)
        if not (target == core or (segment.get('passthrough') and target is None)):
            continue
        text = str(segment.get('text') or '').strip()
        # An unchanged core also leaves its extracted trailing dots unchanged.
        # Retain the complete source occurrence, including its original gaps
        # and dots; reconstructing it with target metrics can shift a suffix.
        if (not text or segment.get('marker') or segment.get('tail') or
                text in conf.get('skip', []) or core in conf.get('center', []) or
                core in conf.get('right', []) or conf.get('mirror') or
                any(merge.get('page') == page and text in merge.get('lines', [])
                    for merge in conf.get('merges', [])) or
                any(override.get('page') == page and override.get('contains', '') in text
                    for override in conf.get('overrides', []))):
            continue
        result.setdefault(page, []).append(segment)
    return result


def prepare(source_path, destination, segments, translations, *, cancel=None, legacy_conf=None,
            ink_segments=(), ink_envelopes=None, unretained=None, exclude_ids=frozenset()):
    """Vouch every retainable identity occurrence; return the retained blocks.

    An occurrence this reader cannot vouch is left out of the result (and
    appended to ``unretained`` when given), so the caller redraws it like any
    other run. Only inputs that contradict each other (page count, page
    geometry) still refuse the document. ``exclude_ids`` names segment ids a
    previous build's final check could not vouch; they are redrawn too.
    """
    by_page = retainable_segments(segments, translations, legacy_conf)
    if exclude_ids:
        for page_number in list(by_page):
            for segment in by_page[page_number]:
                if segment.get('id') in exclude_ids:
                    _note_unretained(unretained, segment, 'retained-output')
            by_page[page_number] = [segment for segment in by_page[page_number]
                                    if segment.get('id') not in exclude_ids]
            if not by_page[page_number]:
                del by_page[page_number]
    geometry_by_page = {}
    for segment in ink_segments:
        geometry_by_page.setdefault(int(segment['page']), []).append(segment)
    if not by_page and not geometry_by_page:
        return {}
    _cancel(cancel)
    retained = {}
    # A line of short letters only ("accuracy.") inks no higher than its
    # x-height, yet its face reserves the cap line there: the same face prints
    # capitals at that size elsewhere. Each face's cap height is measured from
    # its own vouched flat capitals, and an upright line's envelope rises to it
    # once the whole document has been read. A face never seen printing a flat
    # capital keeps its actual ink top.
    cap_heights = {}
    cap_lines = []
    with pikepdf.open(source_path) as source_pdf, pymupdf.open(source_path) as source, ExitStack() as stack:
        weights = FontWeights(source)
        programs = {}
        if source.page_count != destination.page_count:
            _refuse('page-count')
        # Parse the actual destination resources once, without a pathname import.
        with pikepdf.open(io.BytesIO(destination.tobytes())) as stripped_pdf:
            for page_number in sorted(set(by_page) | set(geometry_by_page)):
                page_segments = by_page.get(page_number, [])
                page_anchor = (page_segments or geometry_by_page[page_number])[0]
                _cancel(cancel)
                if _geometry(source[page_number]) != _geometry(destination[page_number]):
                    raise MappingError('source and stripped page geometry differs',
                        refusals={'stale_extraction': [{'page': page_number, 'reason': 'geometry'}]})
                try:
                    if source_pdf.pages[page_number].get('/UserUnit', 1) != 1:
                        _refuse('unsupported-page-units', page_anchor)
                    source_page = source_pdf.pages[page_number]
                    source_fonts = resources_of(source_page.obj).get('/Font', {})
                    destination_fonts = resources_of(stripped_pdf.pages[page_number].obj).get('/Font', {})
                    spans = _spans(source[page_number], weights)
                    traces, paints, annotation_start = page_drawing(source, page_number)
                    page_transform = _unrotated_transform(source[page_number])
                    try:
                        groups = [part for group in _groups(source_page) for part in
                                  _positioned_shows(group, source_page, page_transform)]
                    except (ValueError, pikepdf.PdfError) as exc:
                        _refuse('unreadable-content', page_anchor, detail=str(exc))
                    probes = []
                    for index, group in enumerate(groups):
                        _cancel(cancel)
                        # Unsupported groups may still identify a retained occurrence;
                        # probe them before selecting its explicit refusal reason.
                        probe_spans, probe_traces = _observe_group(source_page, group)
                        probes.append((index, group, probe_spans, probe_traces))
                    source_ink = _vouched_group_ink(source, source_fonts, probes, traces, programs, stack, cancel)
                    text_paints = _text_paints(traces, source_ink, paints=paints)
                except _UNVOUCHED as exc:
                    # This reader cannot vouch any source text on the page, so
                    # none of it is replayed. Every identity occurrence keeps the
                    # established redraw path and the optional ink envelope is
                    # skipped: one unreadable page must not fail a paid job
                    # (a-job-always-delivers).
                    for segment in page_segments:
                        _note_unretained(unretained, segment, exc)
                    continue

                def vouch(candidates):
                    """One complete proof over exactly ``candidates``.

                    A candidate this reader cannot vouch is collected, not
                    raised. The caller accepts only a pass with no failure, so
                    every retained block is proved against the final retained
                    set alone (its group slicing and source claims included).
                    """
                    blocks, failed, bound_spans = [], {}, []
                    for segment in candidates:
                        try:
                            parts = [part for span in _bind_segment(segment, spans, metrics=False)
                                     for part in _source_parts(span, probes, segment)]
                        except _UNVOUCHED as exc:
                            failed[id(segment)] = (segment, exc)
                            continue
                        bound_spans.extend((segment, part) for part in parts)
                    claimed = {}
                    for segment, span in bound_spans:
                        _cancel(cancel)
                        if id(segment) in failed:
                            continue
                        try:
                            block, claim = _vouch_span(segment, span, bound_spans, claimed)
                        except _UNVOUCHED as exc:
                            failed[id(segment)] = (segment, exc)
                            continue
                        claimed.setdefault(claim[0], []).append(claim[1:])
                        blocks.append(block)
                    return blocks, failed

                def _vouch_span(segment, span, bound_spans, claimed):
                    matches = [(index, group, probe_span, probe_traces)
                        for index, group, probe_spans, probe_traces in probes
                        for probe_span in probe_spans if _same_span(span, probe_span)]
                    if len(matches) != 1:
                        _refuse('ambiguous-or-missing', segment, matches=len(matches))
                    index, group, probe_span, probe_traces = matches[0]
                    group_spans = probes[index][2]
                    if group['shows'] != 1:
                        _refuse('multiple-show', segment)
                    if any(group['state'][op] != default for op, default in _DEFAULT_TEXT.items()
                           if op != 'TL'):
                        _refuse('inherited-state', segment)
                    if (group['unsupported'] or group['fonts'] != 1 or group['matrices'] != 1 or
                            not group['show_ready'] or group['text_clip'] or
                            group['state']['clip'] or group['state']['unsafe']):
                        _refuse('unsupported-text-object', segment)
                    if any(group['show_state'][op] != default for op, default in _DEFAULT_TEXT.items()
                           if op != 'TL'):
                        _refuse('unsupported-text-object', segment, detail='nondefault effective show state')
                    _bind_segment(segment, spans)
                    resource = source_fonts.get(group['alias'])
                    if resource is None:
                        _refuse('resource-collision', segment)
                    _, _, _, trace = _fixed_show(group, probe_traces, resource, segment)
                    windows = _trace_windows(probe_span, probe_traces)
                    if len(windows) != 1:
                        _refuse('mixed-object', segment, detail='ambiguous glyph slice')
                    _, start, end = windows[0]
                    if any(start < old_end and old_start < end
                           for old_start, old_end in claimed.get(index, [])):
                        _refuse('overlapping-source-claims', segment)
                    group_key = (index, None)
                    if any(sum(_same_span(candidate_span, observed_span)
                               for _, candidate_span in bound_spans) != 1
                           for observed_span in group_spans):
                        group, probe_span, probe_traces, interval = _slice_group(
                            source_page, group, span, probe_traces, resource, segment)
                        group_key = (index, interval)
                    else:
                        coverage = [0] * len(trace['chars'])
                        for observed_span in group_spans:
                            observed_windows = _trace_windows(observed_span, probes[index][3])
                            if len(observed_windows) != 1:
                                _refuse('mixed-object', segment, detail='unbound original show units')
                            _, first, last = observed_windows[0]
                            for position in range(first, last):
                                coverage[position] += 1
                        if any(count != 1 for count in coverage):
                            _refuse('mixed-object', segment, detail='unaccounted original show units')
                    source_trace_matches = _trace_slice(span, traces)
                    if len(source_trace_matches) != 1:
                        _refuse('source-trace', segment, matches=len(source_trace_matches))
                    source_trace, chars = source_trace_matches[0]
                    ink_boxes = None
                    # Require actual source ink for both observations. Splitting
                    # one original trace into independently retained shows can
                    # expose harmless overlaps between full-font boxes. Glyph
                    # identities, resources and geometry are still vouched.
                    # Trace boxes describe advances and font metrics, and do
                    # not bound unknown/sheared glyph ink. Refuse that gap.
                    try:
                        program = _source_program(source, resource, programs, stack)
                        ink_boxes = _ink_boxes(group, source_trace, chars, program)
                    except Exception:
                        _refuse('covered-source', segment, detail='glyph visibility cannot be proved')
                    source_chars = _visible(span, traces, paints, annotation_start, segment,
                                            ink_boxes=ink_boxes, text_paints=text_paints)
                    probe_chars = _trace_slice(probe_span, probe_traces)
                    if len(probe_chars) != 1 or not _same_glyphs(source_chars, tuple(
                            (cp, gid, tuple(origin), tuple(box)) for cp, gid, origin, box in probe_chars[0][1])):
                        _refuse('source-glyphs', segment)
                    alias = group['alias']
                    try:
                        identical = (alias in source_fonts and alias in destination_fonts and
                            _resource_signature(source_fonts[alias]) == _resource_signature(destination_fonts[alias]))
                    except (ValueError, pikepdf.PdfError) as exc:
                        _refuse('resource-collision', segment, font=alias, detail=str(exc))
                    if not identical:
                        _refuse('resource-collision', segment, font=alias)
                    return ({'bytes': _payload(group),
                        'group_index': group_key,
                        'segment': segment, 'span': span, 'glyphs': source_chars,
                        'geometry': _geometry(source[page_number]), 'alias': alias,
                        'ink_boxes': ink_boxes,
                        'font_resource': _resource_signature(source_fonts[alias])}, (index, start, end))

                # An identity occurrence this reader cannot vouch is not
                # retained; it keeps the established redraw path instead of
                # failing the whole paid job (a-job-always-delivers). Each
                # pass that is not accepted excludes at least one more
                # candidate, so this ends within len(page_segments) + 1 passes.
                # A retained run that a redrawn one would largely cover (a
                # duplicate or fake-bold occurrence) is redrawn too: the final
                # visibility check would otherwise refuse the delivered file.
                excluded = {}
                blocks = []
                for _ in range(len(page_segments) + 1):
                    candidates = [segment for segment in page_segments if id(segment) not in excluded]
                    blocks, failed = vouch(candidates)
                    if not failed:
                        failed = {id(block['segment']): (block['segment'], 'overlaps-redrawn-run')
                                  for block in blocks
                                  if any(_mostly_overlaps(block['segment'], other)
                                         for other, _ in excluded.values())}
                    if not failed:
                        break
                    excluded.update(failed)
                else:
                    blocks = []
                    excluded.update({id(segment): (segment, None) for segment in page_segments
                                     if id(segment) not in excluded})
                for segment, failure in excluded.values():
                    _note_unretained(unretained, segment, failure)
                if blocks:
                    retained.setdefault(page_number, []).extend(blocks)
                for segment in geometry_by_page.get(page_number, []):
                    _cancel(cancel)
                    boxes = []
                    lines = []
                    try:
                        parts = [part for span in _bind_segment(segment, spans)
                                 for part in _source_parts(span, probes, segment)]
                    except MappingError:
                        # This is an optional envelope for translated output,
                        # not permission to retain source operators. Unvouched
                        # formats keep the established translated-text path;
                        # required retention above still refuses every gap.
                        continue
                    for part in parts:
                        matches = [(group, seen) for _, group, seen_spans, _ in probes
                                   for seen in seen_spans if _same_span(part, seen)]
                        actual = _trace_slice(part, traces)
                        if len(matches) != 1 or len(actual) != 1:
                            boxes = []
                            break
                        group, _ = matches[0]
                        if (group['unsupported'] or not group['show_ready'] or group['shows'] != 1 or
                                group['fonts'] != 1 or group['matrices'] != 1 or group['text_clip'] or
                                group['state']['clip'] or group['state']['unsafe'] or
                                any(group['show_state'][op] != default for op, default in _DEFAULT_TEXT.items()
                                    if op != 'TL')):
                            boxes = []
                            break
                        resource = source_fonts.get(group['alias'])
                        try:
                            program = _source_program(source, resource, programs, stack)
                            trace, chars = actual[0]
                            # Reflected source ink can be retained exactly, but
                            # is outside the optional upright target budget.
                            ink = _ink_boxes(group, trace, chars, program, allow_reflection=False)
                            _visible(part, traces, paints, annotation_start, segment,
                                     ink_boxes=ink, text_paints=text_paints)
                            boxes.extend(ink)
                            dx, dy, orientation = _outline_axes(group, trace, allow_reflection=False)
                            size = trace['size']
                            face = (program, dx*orientation > 0)
                            if dy != 0 or not chars or size <= 0:
                                lines.append(None)
                                continue
                            for cp, gid, origin, _ in chars:
                                glyph_ink = program.glyph(gid)[1] if cp in _FLAT_CAPS else None
                                if glyph_ink is not None:
                                    # The capital's page-space top, as _ink_boxes maps it,
                                    # in source sizes above (negative) its own baseline.
                                    top = min(origin[1] + dx*y*orientation*size
                                              for y in (glyph_ink[1], glyph_ink[3]))
                                    cap_heights[face] = min(cap_heights.get(face, 0.),
                                                            (top - origin[1]) / size)
                            lines.append((face, chars[0][2][1], size))
                        except (TTLibError, ValueError, MappingError, IndexError, KeyError, RuntimeError):
                            boxes = []
                            break
                    if boxes and ink_envelopes is not None:
                        ink_envelopes[id(segment)] = (min(box[1] for box in boxes), max(box[3] for box in boxes))
                        cap_lines.append((segment, lines))
    if ink_envelopes is not None:
        for segment, lines in cap_lines:
            if not lines or any(line is None or line[0] not in cap_heights for line in lines):
                continue
            top, bottom = ink_envelopes[id(segment)]
            cap_top = min(baseline + size*cap_heights[face] for face, baseline, size in lines)
            ink_envelopes[id(segment)] = (min(top, cap_top), bottom)
    return retained


def attach(destination, retained):
    for page_number, blocks in retained.items():
        page = destination[page_number]
        existing = page.read_contents() or b''
        with pikepdf.open(io.BytesIO(destination.tobytes())) as parsed:
            _groups(parsed.pages[page_number])  # Require balanced graphics/text state.
        xref = destination.get_new_xref()
        destination.update_object(xref, '<<>>')
        unique = {block['group_index']: block['bytes'] for block in blocks}
        destination.update_stream(xref, b'q\n' + existing + b'\nQ\n' +
                                  b'\n'.join(unique.values()))
        page.set_contents(xref)


UNMAPPED = '\ufffd'
_OPAQUE = .999
_COVERED_SHARE = .25


def _rect_area(box):
    return max(0., box[2]-box[0]) * max(0., box[3]-box[1])


def _inside(inner, outer, tolerance=.5):
    return (inner[0] >= outer[0]-tolerance and inner[1] >= outer[1]-tolerance and
            inner[2] <= outer[2]+tolerance and inner[3] <= outer[3]+tolerance)


def _ll_rect(rect):
    return (rect.x0, rect.y0, rect.x1, rect.y1)


class _OutlineWalker(pymupdf.mupdf.FzPathWalker2):
    """Collect one path's operators, in the coordinates it was built in."""

    def __init__(self):
        super().__init__()
        self.use_virtual_moveto()
        self.use_virtual_lineto()
        self.use_virtual_curveto()
        self.use_virtual_closepath()
        self.ops = []

    def moveto(self, ctx, x, y):
        self.ops.append(('m', x, y))

    def lineto(self, ctx, x, y):
        self.ops.append(('l', x, y))

    def curveto(self, ctx, x1, y1, x2, y2, x3, y3):
        self.ops.append(('c', x1, y1, x2, y2, x3, y3))

    def closepath(self, ctx):
        self.ops.append(('h',))


class _GlyphLog(pymupdf.mupdf.FzDevice2):
    """Every glyph a page's own content paints inside ``areas``, as outlines.

    MuPDF interprets the page, so each glyph comes with the exact font
    program, matrix and fill colour the source used, whatever the text
    object looks like. A glyph is ``unsafe`` (and never replayed) when its
    painted shape is not just its filled outline: a soft mask, a blend mode
    or group alpha, a pattern tile, a clip that is not a box containing the
    glyph, a stroke, or a font without a scalable outline (Type 3).
    """

    def __init__(self, areas):
        super().__init__()
        for name in ('fill_text', 'stroke_text', 'clip_text', 'clip_stroke_text',
                     'ignore_text', 'fill_path', 'stroke_path', 'clip_path',
                     'clip_stroke_path', 'fill_shade', 'fill_image', 'fill_image_mask',
                     'clip_image_mask', 'pop_clip', 'begin_mask', 'end_mask',
                     'begin_group', 'end_group', 'begin_tile', 'end_tile'):
            getattr(self, 'use_virtual_' + name)()
        self.areas = areas
        self.glyphs, self.fills = [], []
        self.clips, self.groups = [], []
        self.masks = self.tiles = self.seq = 0
        self.failure = None

    def _guard(self, function, *args):
        # A Python exception must not cross MuPDF's C stack; record it and
        # let the caller refuse the page.
        try:
            function(*args)
        except Exception as exc:  # noqa: BLE001
            if self.failure is None:
                self.failure = exc

    def _state_unsafe(self):
        if self.masks:
            return 'soft-mask'
        if self.tiles:
            return 'pattern'
        if not all(self.groups):
            return 'blend'
        return None

    def _text(self, text, ctm, kind, colorspace=None, color=None, alpha=1.):
        mupdf = pymupdf.mupdf
        ctm = mupdf.FzMatrix(ctm)
        rgb, unsafe = (0., 0., 0.), self._state_unsafe()
        if kind == 'fill':
            if alpha < _OPAQUE:
                unsafe = unsafe or 'alpha'
            if colorspace is None:
                unsafe = unsafe or 'colour'
            else:
                rgb = tuple(mupdf.fz_convert_color(
                    mupdf.FzColorspace(mupdf.ll_fz_keep_colorspace(colorspace)), color,
                    mupdf.fz_device_rgb(), mupdf.FzColorspace(), mupdf.FzColorParams())[:3])
        span = text.head
        while span:
            wrapped = mupdf.FzTextSpan(span)
            font, trm = wrapped.font(), wrapped.trm()
            turned = mupdf.fz_concat(mupdf.FzMatrix(trm.a, trm.b, trm.c, trm.d, 0, 0), ctm)
            norm = math.hypot(turned.a, turned.b) or 1.
            writing = (turned.a / norm, turned.b / norm)
            for index in range(span.len):
                item = wrapped.items(index)
                point = mupdf.fz_transform_point(mupdf.fz_make_point(item.x, item.y), ctm)
                origin = (point.x, point.y)
                if not any(_inside((*origin, *origin), area, 0.) for area in self.areas):
                    continue
                glyph = {'ucs': item.ucs, 'gid': item.gid, 'origin': origin, 'kind': kind,
                         'seq': self.seq, 'rgb': rgb, 'ops': (), 'ink': None, 'unsafe': unsafe,
                         'dir': writing}
                if kind == 'stroke':
                    glyph['unsafe'] = 'stroked'
                if kind in ('fill', 'stroke') and item.gid >= 0:
                    matrix = mupdf.fz_concat(
                        mupdf.FzMatrix(trm.a, trm.b, trm.c, trm.d, item.x, item.y), ctm)
                    try:
                        path = mupdf.fz_outline_glyph(font, item.gid, matrix)
                    except Exception:  # noqa: BLE001 - no outline is reported below
                        path = None
                    if path is None or not path.m_internal:
                        glyph['unsafe'] = glyph['unsafe'] or 'no-outline'
                    else:
                        walker = _OutlineWalker()
                        mupdf.fz_walk_path(path, walker, walker.m_internal)
                        glyph['ops'] = tuple(walker.ops)
                        points = [(op[i], op[i+1]) for op in walker.ops
                                  for i in range(1, len(op), 2)]
                        if points:
                            glyph['ink'] = (min(p[0] for p in points), min(p[1] for p in points),
                                            max(p[0] for p in points), max(p[1] for p in points))
                if glyph['ink'] is not None and glyph['unsafe'] is None:
                    for clip in self.clips:
                        if clip is None or not _inside(glyph['ink'], clip):
                            glyph['unsafe'] = 'clipped'
                            break
                self.glyphs.append(glyph)
            span = span.next
        self.seq += 1

    def _fill(self, rect, alpha):
        if alpha >= _OPAQUE and not self.masks:
            self.fills.append((self.seq, rect))
        self.seq += 1

    def fill_text(self, ctx, text, ctm, colorspace, color, alpha, params):
        self._guard(self._text, text, ctm, 'fill', colorspace, color, alpha)

    def stroke_text(self, ctx, text, stroke, ctm, colorspace, color, alpha, params):
        self._guard(self._text, text, ctm, 'stroke')

    def ignore_text(self, ctx, text, ctm):
        self._guard(self._text, text, ctm, 'ignore')

    def clip_text(self, ctx, text, ctm, scissor):
        self.clips.append(None)

    def clip_stroke_text(self, ctx, text, stroke, ctm, scissor):
        self.clips.append(None)

    def fill_path(self, ctx, path, even_odd, ctm, colorspace, color, alpha, params):
        self._guard(lambda: self._fill(_ll_rect(pymupdf.mupdf.ll_fz_bound_path(path, None, ctm)), alpha))

    def stroke_path(self, ctx, path, stroke, ctm, colorspace, color, alpha, params):
        self.seq += 1

    def clip_path(self, ctx, path, even_odd, ctm, scissor):
        try:
            self.clips.append(_ll_rect(pymupdf.mupdf.ll_fz_bound_path(path, None, ctm)))
        except Exception:  # noqa: BLE001
            self.clips.append(None)

    def clip_stroke_path(self, ctx, path, stroke, ctm, scissor):
        self.clips.append(None)

    def fill_shade(self, ctx, shade, ctm, alpha, params):
        self._guard(lambda: self._fill(_ll_rect(pymupdf.mupdf.ll_fz_bound_shade(shade, ctm)), alpha))

    def fill_image(self, ctx, image, ctm, alpha, params):
        self._guard(lambda: self._fill(_ll_rect(pymupdf.mupdf.fz_transform_rect(
            pymupdf.mupdf.fz_make_rect(0, 0, 1, 1), pymupdf.mupdf.FzMatrix(ctm))), alpha))

    def fill_image_mask(self, ctx, image, ctm, colorspace, color, alpha, params):
        self._guard(lambda: self._fill(_ll_rect(pymupdf.mupdf.fz_transform_rect(
            pymupdf.mupdf.fz_make_rect(0, 0, 1, 1), pymupdf.mupdf.FzMatrix(ctm))), alpha))

    def clip_image_mask(self, ctx, image, ctm, scissor):
        self.clips.append(None)

    def pop_clip(self, ctx):
        if self.clips:
            self.clips.pop()

    def begin_mask(self, ctx, area, luminosity, colorspace, color, params):
        self.masks += 1
        self.clips.append(None)

    def end_mask(self, ctx, function):
        self.masks = max(0, self.masks - 1)

    def begin_group(self, ctx, area, colorspace, isolated, knockout, blendmode, alpha):
        self.groups.append(blendmode == 0 and alpha >= _OPAQUE)

    def end_group(self, ctx):
        if self.groups:
            self.groups.pop()

    def begin_tile(self, ctx, area, view, xstep, ystep, ctm, tile_id, doc_id):
        self.tiles += 1
        return 0

    def end_tile(self, ctx):
        self.tiles = max(0, self.tiles - 1)


def _blank(ch):
    return ch.isspace() or unicodedata.category(ch) == 'Cc'


def _replay_one(segment, glyphs, fills, area, turn):
    """The glyphs that draw ``segment``, or the reason they cannot be kept.

    A glyph belongs to the run when its origin is inside the run's box and on
    its baseline: tight leading puts the next line's baseline inside the box.
    Bound glyphs must then account for the run's characters exactly.
    """
    origin = pymupdf.Point(segment['origin']) * turn
    direction = pymupdf.Point(segment.get('dir') or (1., 0.)) * pymupdf.Matrix(
        turn.a, turn.b, turn.c, turn.d, 0, 0)
    length = math.hypot(direction.x, direction.y) or 1.
    across = max(.5, .4 * float(segment.get('size') or 0.))

    def on_baseline(point):
        dx, dy = point[0] - origin.x, point[1] - origin.y
        return abs(dx * direction.y - dy * direction.x) / length <= across

    # Where the run ends along its own direction. A glyph starts before that:
    # the next run's first glyph can start exactly at this box's far edge, as
    # when one word is split into two runs (dogfood 2026-10-10, opm-sf2809
    # `e` + `nroll ...`), and must not be bound to this one.
    box = pymupdf.Rect(segment['bbox']) * turn
    end = max(((x - origin.x) * direction.x + (y - origin.y) * direction.y) / length
              for x, y in ((box.x0, box.y0), (box.x1, box.y0), (box.x0, box.y1), (box.x1, box.y1)))

    def before_end(point):
        return ((point[0] - origin.x) * direction.x + (point[1] - origin.y) * direction.y) / length < end - .05

    # Written along the run: a glyph of another line that crosses this baseline
    # (a diagonal watermark over the text, cms-1490s-en) is not this run's.
    def along(glyph):
        dx, dy = glyph.get('dir') or (direction.x / length, direction.y / length)
        return (dx * direction.x + dy * direction.y) / length > .9

    bound = [glyph for glyph in glyphs if _inside((*glyph['origin'], *glyph['origin']), area, 0.) and
             on_baseline(glyph['origin']) and before_end(glyph['origin']) and along(glyph)]
    # A fill-and-stroke run (Tr 2) paints each glyph twice; its fill is the
    # glyph. A glyph that is only stroked keeps its own record and refuses.
    filled = {(glyph['gid'], round(glyph['origin'][0], 3), round(glyph['origin'][1], 3))
              for glyph in bound if glyph['kind'] == 'fill'}
    bound = [glyph for glyph in bound if not (
        glyph['kind'] == 'stroke' and
        (glyph['gid'], round(glyph['origin'][0], 3), round(glyph['origin'][1], 3)) in filled)]
    # A control character is matched like a space on both sides: a broken
    # ToUnicode maps a bullet box to U+0007 for the extractor while MuPDF reports
    # the same glyph as a space (dogfood 2026-10-10, vis-ja-flu `医療...`).
    expected = sorted(ch for ch in str(segment.get('text') or '') if not _blank(ch))
    seen = sorted(chr(glyph['ucs']) if 0 < glyph['ucs'] <= 0x10FFFF else UNMAPPED
                  for glyph in bound if not (0 < glyph['ucs'] <= 0x10FFFF and _blank(chr(glyph['ucs']))))
    if seen != expected:
        return None, 'unbound-glyphs'
    drawn = [glyph for glyph in bound if glyph['kind'] != 'ignore']
    for glyph in drawn:
        if glyph['unsafe']:
            return None, glyph['unsafe']
        ink = glyph['ink']
        if ink is None:
            continue
        for seq, rect in fills:
            if seq > glyph['seq']:
                overlap = (max(ink[0], rect[0]), max(ink[1], rect[1]),
                           min(ink[2], rect[2]), min(ink[3], rect[3]))
                if _rect_area(overlap) > _COVERED_SHARE * max(_rect_area(ink), 1e-9):
                    return None, 'covered-source'
    return drawn, None


def _format(value):
    return format(value, '.4f').rstrip('0').rstrip('.') or '0'


def replay_unmapped(source, target, page_number, segments):
    """Draw, as filled outlines, the source glyphs of runs that have no Unicode.

    A run the source font cannot map to Unicode extracts as U+FFFD. Redrawn
    from that text it becomes a row of replacement boxes, or a glyph miss in
    a face without one. Its exact source glyphs are painted instead, from
    the font program MuPDF used, at the source matrix and fill colour, over
    the stripped page ``target``. Returns ``(kept, refused)``: the ids of the segments
    drawn, and one row per segment whose glyphs cannot be vouched as plain
    filled outlines; that run is not drawn at all, and the caller reports it.
    """
    mupdf = pymupdf.mupdf
    refused = []
    if not segments:
        return set(), refused
    page = source[page_number]
    turn = page.rotation_matrix
    areas = [tuple(pymupdf.Rect(segment['bbox']) * turn + (-.5, -.5, .5, .5)) for segment in segments]
    log = _GlyphLog(areas)
    try:
        mupdf.fz_run_page_contents(page.this, log, mupdf.FzMatrix(), mupdf.FzCookie())
    except Exception as exc:  # noqa: BLE001
        log.failure = log.failure or exc
    if log.failure is not None:
        return set(), [{'page': page_number, 'segment_id': segment.get('id'),
                        'core': segment.get('core'), 'reason': 'unreadable-content'}
                       for segment in segments]
    shape = target.new_shape()
    to_pdf = shape.ipctm
    kept = set()
    for segment, area in zip(segments, areas):
        drawn, reason = _replay_one(segment, log.glyphs, log.fills, area, turn)
        if reason is not None:
            refused.append({'page': page_number, 'segment_id': segment.get('id'),
                            'core': segment.get('core'), 'reason': reason})
            continue
        for glyph in drawn:
            if not glyph['ops']:
                continue
            parts = []
            for op in glyph['ops']:
                if op[0] == 'h':
                    parts.append('h')
                    continue
                points = [pymupdf.Point(op[i], op[i+1]) * to_pdf for i in range(1, len(op), 2)]
                parts.append(' '.join(f'{_format(p.x)} {_format(p.y)}' for p in points) + ' ' + op[0])
            shape.draw_cont = '\n'.join(parts) + '\n'
            shape.finish(color=None, fill=glyph['rgb'], closePath=False)
        kept.add(id(segment))
    shape.commit(overlay=True)
    return kept, refused


def verify_final(path, retained, *, cancel=None):
    """Observe the actual post-canonicalization file before publishing it."""
    with pymupdf.open(path) as delivered, pikepdf.open(path) as parsed, ExitStack() as stack:
        programs = {}
        for page_number, blocks in retained.items():
            _cancel(cancel)
            if page_number >= delivered.page_count:
                _refuse('retained-output', blocks[0]['segment'], detail='missing page')
            # Annotation-free drawing may reload the page. Acquire the local
            # wrapper after that call so it does not keep the old native page
            # alive across reload_page().
            traces, paints, annotation_start = page_drawing(delivered, page_number)
            page = delivered[page_number]
            spans = _spans(page)
            fonts = resources_of(parsed.pages[page_number].obj).get('/Font', {})
            validated, vouched_ink = [], {}
            for block in blocks:
                _cancel(cancel)
                segment = block['segment']
                matches = _matching_slices(block['span'], spans)
                if _geometry(page) != block['geometry'] or len(matches) != 1:
                    _refuse('retained-output', segment, detail='geometry or occurrence',
                            matches=len(matches))
                try:
                    windows = _trace_windows(matches[0], traces)
                    if len(windows) != 1:
                        _refuse('retained-output', segment, detail='glyph occurrence')
                    trace, start, end = windows[0]
                    if not _same_glyphs(trace['chars'][start:end], block['glyphs']):
                        _refuse('retained-output', segment, detail='glyphs')
                    if (block['alias'] not in fonts or
                            _resource_signature(fonts[block['alias']]) != block['font_resource']):
                        _refuse('retained-output', segment, detail='font resource')
                    validated.append((block, matches[0]))
                    if block['ink_boxes'] is not None:
                        vouched_ink.setdefault(id(trace), []).append((start, end, block['ink_boxes']))
                except (MappingError, ValueError, pikepdf.PdfError) as exc:
                    _refuse('retained-output', segment, detail=str(exc))
            # Only glyph intervals already proved against the actual delivered
            # file may use source ink in place of conservative font boxes.
            # Unknown/unretained text keeps its full boxes and drawing order.
            source_page = parsed.pages[page_number]
            transform = _unrotated_transform(page)
            groups = [part for group in _groups(source_page) for part in
                      _positioned_shows(group, source_page, transform)]
            probes = []
            for index, group in enumerate(groups):
                _cancel(cancel)
                observed, observed_traces = _observe_group(source_page, group)
                probes.append((index, group, observed, observed_traces))
            other_ink = _vouched_group_ink(delivered, fonts, probes, traces, programs, stack, cancel)
            for identity, intervals in other_ink.items():
                existing = vouched_ink.setdefault(identity, [])
                for start, end, boxes in intervals:
                    if not any(start < last and first < end for first, last, _ in existing):
                        existing.append((start, end, boxes))
            text_paints = _text_paints(traces, vouched_ink, paints=paints)
            for block, span in validated:
                _cancel(cancel)
                try:
                    _visible(span, traces, paints, annotation_start, block['segment'],
                             ink_boxes=block['ink_boxes'], text_paints=text_paints)
                except (MappingError, ValueError, pikepdf.PdfError) as exc:
                    _refuse('retained-output', block['segment'], detail=str(exc))
