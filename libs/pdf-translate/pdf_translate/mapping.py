"""One format reader for legacy mappings and bound typography occurrences."""
from copy import deepcopy
from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import re
import unicodedata

from . import han_forms
from .results import MappingError
from .typography import bind_extraction, canonical_digest


FORMAT = 'typography-1'
CLASSES = ('serif', 'sans')
ROLES = ('regular', 'bold', 'italic', 'bold_italic')


@dataclass(frozen=True)
class StyledRun:
    text: str
    source_runs: tuple[str, ...]
    font_class: str
    font_role: str


@dataclass(frozen=True)
class OccurrenceTarget:
    occurrence_id: str
    page: int
    source_text: str
    runs: tuple[StyledRun, ...]


@dataclass(frozen=True)
class MappingDocument:
    format: str
    legacy: dict | None = None
    lang: str = ''
    extraction_id: str = ''
    mapping_sha256: str = ''
    targets: tuple[OccurrenceTarget, ...] = ()
    document_targets: dict = field(default_factory=dict)
    font_sets: dict = field(default_factory=dict)
    allow_scale: dict = field(default_factory=dict)
    source_font_resolutions: tuple[dict, ...] = ()
    extraction: dict | None = None


def role_name(bold, italic):
    return 'bold_italic' if bold and italic else (
        'bold' if bold else ('italic' if italic else 'regular'))


def refuse(reason, detail, segment=None, run_id=None, occurrence_id=None):
    segment = segment or {}
    item = {'reason': reason, 'detail': detail,
            'occurrence_id': occurrence_id or segment.get('occurrence_id'),
            'run_id': run_id, 'page': segment.get('page'),
            'source_text': segment.get('text')}
    raise MappingError(detail, core=segment.get('text'),
                       refusals={'typography': [item]})


class _Pairs(list):
    """Distinguish JSON objects from arrays without dropping repeated keys."""


def _convert(value, strict):
    if isinstance(value, _Pairs):
        out = {}
        for key, item in value:
            if strict and key in out:
                refuse('invalid-style-reference', f'duplicate JSON key: {key}')
            out[key] = _convert(item, strict)
        return out
    if isinstance(value, list):
        return [_convert(item, strict) for item in value]
    if strict and isinstance(value, float) and not math.isfinite(value):
        refuse('invalid-style-reference', 'non-finite JSON number')
    return value


def _read_pairs(text):
    try:
        return json.loads(text, object_pairs_hook=_Pairs)
    except (ValueError, TypeError) as exc:
        refuse('invalid-style-reference', f'unreadable mapping JSON: {exc}')


def _decode_mapping(text):
    pairs = _read_pairs(text)
    if not isinstance(pairs, _Pairs):
        refuse('invalid-style-reference', 'mapping root must be an object')
    formats = [value for key, value in pairs if key == 'format']
    if not formats:
        return _convert(pairs, False), 'legacy'
    if len(formats) != 1 or formats[0] != FORMAT:
        refuse('invalid-style-reference', 'unknown or duplicate mapping format')
    return _convert(pairs, True), FORMAT


def _shape(obj, required, optional=(), where='object'):
    if not isinstance(obj, dict) or set(obj) - set(required) - set(optional) or set(required) - set(obj):
        refuse('invalid-style-reference', f'invalid fields in {where}')


def _list(value, where):
    if not isinstance(value, list):
        refuse('invalid-style-reference', f'{where} must be an array')
    return value


def _text(value, where):
    if not isinstance(value, str) or not value.strip():
        refuse('invalid-style-reference', f'{where} must be nonempty text')
    return value


def _geometry(value, length):
    return (isinstance(value, list) and len(value) == length and
            all(type(v) in (int, float) and math.isfinite(v) for v in value))


def _size_color(record):
    return (type(record.get('size')) in (int, float) and
            math.isfinite(record['size']) and record['size'] > 0 and
            type(record.get('color')) is int and 0 <= record['color'] <= 0xffffff)


def _index_extraction(extraction, expected):
    if not isinstance(extraction, dict) or not isinstance(extraction.get('typography'), dict):
        refuse('stale-extraction', 'typography extraction context is required')
    data = deepcopy(extraction)
    typo = data['typography']
    if type(typo.get('schema')) is not int:
        refuse('invalid-style-reference', 'extraction schema must be an integer')
    try:
        digest = bind_extraction(data)['extraction_id']
    except (KeyError, ValueError, TypeError):
        refuse('stale-extraction', 'typography extraction cannot be bound')
    if typo.get('schema') != 1 or digest != expected or digest != typo.get('extraction_id'):
        refuse('stale-extraction', 'mapping and extraction identities do not agree')
    if not isinstance(typo.get('fonts'), dict):
        refuse('invalid-style-reference', 'extraction has no font observations')
    for font_id, observed in typo['fonts'].items():
        if (not isinstance(font_id, str) or not re.fullmatch(r'f[0-9]+', font_id) or
                not isinstance(observed, dict) or observed.get('class') not in (*CLASSES, 'unknown') or
                any(observed.get(key) is not None and type(observed[key]) is not bool
                    for key in ('bold', 'italic'))):
            refuse('invalid-style-reference', 'invalid font observation')
    document = data.get('document')
    if (not isinstance(document, dict) or not isinstance(document.get('title'), str) or
            not isinstance(document.get('outline'), list) or
            any(not isinstance(text, str) for text in document['outline'])):
        refuse('invalid-style-reference', 'invalid document strings')
    options = typo.get('options')
    _shape(options, ('gap', 'pages'), where='extraction options')
    if type(options['gap']) not in (int, float) or not math.isfinite(options['gap']):
        refuse('invalid-style-reference', 'invalid extraction gap')
    if not re.fullmatch(r'[a-f0-9]{64}', str(typo.get('source_sha256', ''))):
        refuse('stale-extraction', 'extraction has no source digest')
    page_geometry = _list(typo.get('page_geometry'), 'page_geometry')
    for index, page in enumerate(page_geometry):
        if (not isinstance(page, dict) or type(page.get('page')) is not int or page['page'] != index or
                not _geometry(page.get('media_box'), 4) or
                not _geometry(page.get('crop_box'), 4) or
                not _geometry(page.get('effective_box'), 4) or
                type(page.get('rotation')) is not int):
            refuse('invalid-style-reference', 'invalid source page geometry')
    selected = _list(data.get('pages'), 'selected pages')
    if (any(type(p) is not int or p < 0 or p >= len(page_geometry) for p in selected)
            or selected != sorted(set(selected))):
        refuse('invalid-style-reference', 'invalid selected source pages')
    if (not isinstance(options['pages'], list) or
            any(type(p) is not int for p in options['pages']) or options['pages'] != selected):
        refuse('invalid-style-reference', 'extraction page selections disagree')
    for widget in _list(typo.get('fields'), 'field identities'):
        if (not isinstance(widget, dict) or type(widget.get('page')) is not int or
                not 0 <= widget['page'] < len(page_geometry) or
                not isinstance(widget.get('name'), str) or
                type(widget.get('type')) is not int or not _geometry(widget.get('rect'), 4)):
            refuse('invalid-style-reference', 'invalid field identity')
    segments = {}
    for segment in _list(data.get('segments'), 'segments'):
        if not isinstance(segment, dict):
            refuse('invalid-style-reference', 'segment must be an object')
        identity = segment.get('occurrence_id')
        if (not isinstance(identity, str) or not re.fullmatch(r's[0-9]+', identity) or
                type(segment.get('id')) is not int or identity != f"s{segment['id']}"):
            refuse('invalid-style-reference', 'invalid extraction occurrence ID')
        if identity in segments:
            refuse('duplicate-occurrence', 'duplicate extraction occurrence', segment)
        if (type(segment.get('page')) is not int or segment['page'] not in selected or
                not isinstance(segment.get('text'), str) or
                not _geometry(segment.get('bbox'), 4) or
                not _geometry(segment.get('origin'), 2) or
                not _geometry(segment.get('dir'), 2) or
                not _size_color(segment)):
            refuse('invalid-style-reference', 'invalid occurrence geometry/text', segment)
        offset, seen = 0, set()
        for run in _list(segment.get('style_runs'), 'source style runs'):
            if not isinstance(run, dict):
                refuse('invalid-style-reference', 'source run must be an object', segment)
            run_id = run.get('run_id')
            text = run.get('text')
            if (not isinstance(run_id, str) or not re.fullmatch(identity + r'/r[0-9]+', run_id) or
                    run_id in seen or not isinstance(text, str) or
                    run.get('offsets') != [offset, offset + len(text)] or
                    segment['text'][offset:offset + len(text)] != text or
                    not _geometry(run.get('origin'), 2) or not _geometry(run.get('bbox'), 4) or
                    not _size_color(run)):
                refuse('invalid-style-reference', 'invalid source run identity/offsets', segment)
            font_id = run.get('font_id')
            if font_id is not None and (not isinstance(font_id, str) or font_id not in typo['fonts']):
                refuse('invalid-style-reference', 'invalid source font reference', segment, run_id)
            offset += len(text)
            seen.add(run_id)
        if offset != len(segment['text']) or not seen:
            refuse('invalid-style-reference', 'source runs do not reconstruct occurrence', segment)
        segments[identity] = segment
    return data, segments


def _source_styles(conf, extraction):
    fonts = deepcopy(extraction['typography']['fonts'])
    resolutions, seen = [], set()
    for item in _list(conf.get('source_font_resolutions', []), 'source_font_resolutions'):
        _shape(item, ('font_id', 'class', 'bold', 'italic', 'reason'), where='font resolution')
        font_id = _text(item['font_id'], 'font_id')
        if font_id not in fonts or font_id in seen or item['class'] not in CLASSES:
            refuse('invalid-style-reference', 'foreign/duplicate font resolution or invalid class')
        if type(item['bold']) is not bool or type(item['italic']) is not bool:
            refuse('invalid-style-reference', 'font resolution flags must be booleans')
        _text(item['reason'], 'font resolution reason')
        observed = fonts[font_id]
        if not isinstance(observed, dict):
            refuse('invalid-style-reference', 'invalid font observation')
        unknown = False
        for key in ('class', 'bold', 'italic'):
            known = observed.get(key)
            if known is None or known == 'unknown':
                unknown = True
            elif known != item[key]:
                refuse('invalid-style-reference', f'font resolution conflicts with {key}')
        if not unknown:
            refuse('invalid-style-reference', 'font resolution must resolve unknown evidence')
        observed.update({key: item[key] for key in ('class', 'bold', 'italic')})
        resolutions.append(deepcopy(item))
        seen.add(font_id)
    return fonts, tuple(resolutions)


def check_plain_text(text, segment=None, run_id=None):
    """Conservative first-scope repertoire; unsupported clusters never normalize."""
    for ch in text:
        category = unicodedata.category(ch)
        name = unicodedata.name(ch, '')
        supported_letter = ('LATIN' in name or han_forms.is_cjk(ch) or ch == '々')
        if (ch == '‖' or category[0] in 'MC' or (category == 'So' and ord(ch) >= 0x1f000) or
                (category[0] == 'L' and not supported_letter) or
                (category[0] == 'N' and not (ch.isascii() or 0xff10 <= ord(ch) <= 0xff19))):
            refuse('unsupported-typography-construct',
                   f'unsupported script/cluster/control U+{ord(ch):04X}', segment, run_id)


def _font_sets(conf, mapping_dir):
    fonts = conf['font_sets']
    if not isinstance(fonts, dict) or set(fonts) - set(CLASSES):
        refuse('invalid-style-reference', 'font_sets requires serif/sans keys')
    normalized = {}
    for cls, roles in fonts.items():
        if not isinstance(roles, dict) or set(roles) - set(ROLES):
            refuse('invalid-style-reference', 'invalid font role keys')
        normalized[cls] = {}
        for role, value in roles.items():
            path = Path(_text(value, 'font path'))
            normalized[cls][role] = str(path if path.is_absolute() else mapping_dir / path)
    return normalized


def _targets(conf, segments, fonts, font_sets):
    targets, seen = [], set()
    for entry in _list(conf['targets'], 'targets'):
        _shape(entry, ('occurrence_id', 'runs'), where='target occurrence')
        identity = _text(entry['occurrence_id'], 'occurrence_id')
        if identity in seen:
            refuse('duplicate-occurrence', 'duplicate target occurrence', segments.get(identity),
                   occurrence_id=identity)
        if identity not in segments:
            refuse('unknown-occurrence', 'foreign target occurrence', occurrence_id=identity)
        seen.add(identity)
        segment = segments[identity]
        source = {r['run_id']: r for r in segment['style_runs']}
        check_plain_text(segment['text'], segment)
        represented, runs = set(), []
        for raw in _list(entry['runs'], 'target runs'):
            _shape(raw, ('text', 'source_runs'), where='target run')
            text = raw['text']
            if not isinstance(text, str) or not text.strip():
                refuse('unsupported-style-alignment', 'target run is empty', segment)
            check_plain_text(text, segment)
            references = _list(raw['source_runs'], 'source_runs')
            if (not references or any(not isinstance(r, str) for r in references) or
                    len(set(references)) != len(references)):
                refuse('invalid-style-reference', 'empty/duplicate/invalid source run references', segment)
            styles = set()
            for ref in references:
                if ref not in source:
                    refuse('invalid-style-reference', 'foreign source run', segment, ref)
                font = fonts.get(source[ref].get('font_id'))
                if (not isinstance(font, dict) or font.get('class') not in CLASSES or
                        type(font.get('bold')) is not bool or type(font.get('italic')) is not bool):
                    refuse('unresolved-source-style', 'source font/style is unresolved', segment, ref)
                styles.add((font['class'], role_name(font['bold'], font['italic'])))
                represented.add(ref)
            if len(styles) != 1:
                refuse('unsupported-style-alignment', 'target combines different source styles', segment)
            cls, role = styles.pop()
            if role not in font_sets.get(cls, {}):
                refuse('missing-font-role', f'missing {cls}/{role} target face', segment)
            runs.append(StyledRun(text, tuple(references), cls, role))
        required = {ref for ref, run in source.items() if run['text'].strip()}
        if not runs or required - represented:
            refuse('unsupported-style-alignment', 'meaningful source style run is omitted', segment)
        targets.append(OccurrenceTarget(identity, segment['page'], segment['text'], tuple(runs)))
    missing = segments.keys() - seen
    if missing:
        identity = sorted(missing)[0]
        refuse('unknown-occurrence', 'visible occurrence is missing', segments[identity])
    return tuple(targets)


def _scales(conf, segments):
    scales = {}
    for item in _list(conf.get('allow_scale', []), 'allow_scale'):
        _shape(item, ('occurrence_id', 'reason'), where='scale exception')
        identity = _text(item['occurrence_id'], 'scale occurrence_id')
        if identity not in segments:
            refuse('unknown-occurrence', 'foreign scale exception', occurrence_id=identity)
        if identity in scales:
            refuse('duplicate-occurrence', 'duplicate scale exception', segments[identity])
        scales[identity] = _text(item['reason'], 'scale exception reason')
    return scales


def parse_mapping(text, *, extraction=None, mapping_dir=None):
    conf, format_name = _decode_mapping(text)
    return _parse_mapping(conf, format_name, extraction=extraction, mapping_dir=mapping_dir)


def _parse_mapping(conf, format_name, *, extraction=None, mapping_dir=None):
    if format_name == 'legacy':
        return MappingDocument(format='legacy', legacy=conf, lang=conf.get('lang', ''))
    _shape(conf, ('format', 'extraction_id', 'lang', 'font_sets', 'targets', 'document_targets'),
           ('source_font_resolutions', 'allow_scale'), where='typography mapping')
    expected = conf['extraction_id']
    if not isinstance(expected, str) or not re.fullmatch(r'[a-f0-9]{64}', expected):
        refuse('stale-extraction', 'extraction_id must be lowercase SHA-256')
    lang = conf['lang']
    if (not isinstance(lang, str) or not han_forms.is_language_tag(lang) or
            han_forms.convention_for_lang(lang) in ('TC', 'KR')):
        refuse('unsupported-typography-construct', 'unsupported target language tag')
    data, segments = _index_extraction(extraction, expected)
    fonts, resolutions = _source_styles(conf, data)
    font_sets = _font_sets(conf, Path(mapping_dir or '.').resolve())
    targets = _targets(conf, segments, fonts, font_sets)
    scales = _scales(conf, segments)
    metadata = conf['document_targets']
    if not isinstance(metadata, dict):
        refuse('invalid-style-reference', 'document_targets must be an object')
    for key, value in metadata.items():
        _text(key, 'document source')
        _text(value, 'document target')
    document = data['document']
    from .extract_segments import PASS
    for value in [document.get('title', '')] + list(document.get('outline') or []):
        if value and not PASS.match(value) and value not in metadata:
            refuse('invalid-style-reference', f'document target missing: {value}')
    return MappingDocument(FORMAT, lang=lang, extraction_id=expected,
                           mapping_sha256=canonical_digest(conf), targets=targets,
                           document_targets=deepcopy(metadata), font_sets=font_sets,
                           allow_scale=scales, source_font_resolutions=resolutions,
                           extraction=data)


def load_mapping_data(path):
    """Read format and JSON once, including when a helper must refuse a format."""
    path = Path(path)
    try:
        text = path.read_text(encoding='utf-8-sig')
    except OSError as exc:
        refuse('invalid-style-reference', f'cannot read mapping: {exc}')
    return _decode_mapping(text)


def load_extraction(path, extraction_id):
    """Read and validate bound geometry for an unauthored scaffold."""
    try:
        data = _convert(_read_pairs(Path(path).read_text(encoding='utf-8-sig')), True)
    except OSError as exc:
        refuse('stale-extraction', f'cannot read extraction: {exc}')
    return _index_extraction(data, extraction_id)[0]


def load_mapping(path, segments_path=None):
    path = Path(path).resolve()
    conf, format_name = load_mapping_data(path)
    extraction = None
    if format_name == FORMAT:
        source = Path(segments_path) if segments_path is not None else path.parent / 'segments.json'
        try:
            extraction = _convert(_read_pairs(source.read_text(encoding='utf-8-sig')), True)
        except OSError as exc:
            refuse('stale-extraction', f'cannot read extraction: {exc}')
    return _parse_mapping(conf, format_name, extraction=extraction, mapping_dir=path.parent)


def charset_for(document, font_class, font_role):
    """Plain visible target characters belonging to exactly one selected face."""
    return {ch for target in document.targets for run in target.runs
            if (run.font_class, run.font_role) == (font_class, font_role)
            for ch in run.text}
