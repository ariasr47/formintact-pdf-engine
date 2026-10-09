#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Self-contained evidence bundles for a below-the-floor scale refusal.

B1 (wrapping a translation onto a second line) cannot be planned because
nobody can measure how much it would recover: every attempt to obtain a
real failed job came back empty, because a refusal prints a message and
exits — nothing ever wrote the inputs anywhere
(``docs/reviews/2026-09-19-b1-recovery-probe.md``). This module is the fix:
when :mod:`pdf_translate.retypeset` is about to raise because a run scaled
below :data:`pdf_translate.retypeset.SCALE_MIN`, it hands the exception and
the job's paths to :func:`capture_refusal`, which writes everything a later
session would need to reproduce the refusal, unasked, to a directory the
caller names.

Three rules shape every function here:

1. **Opt-in, default off.** Copying a customer's source PDF to disk is a
   side effect nobody should get by surprise. Nothing in this module runs
   unless a caller names a directory, either through ``capture_dir=`` on
   :func:`pdf_translate.run_retypeset` or through the
   ``PDF_TRANSLATE_CAPTURE_DIR`` environment variable (see
   :func:`resolve_capture_dir`).
2. **Never change the build's outcome.** :func:`capture_refusal` catches
   everything — a full disk, a bad path, a permissions error, a bug in this
   module — and only logs it. The caller always re-raises the *original*
   exception, unmodified, whether or not the bundle was written.
3. **Record unknown as unknown.** A below-floor occurrence's permitted box
   is only ever taken from an existing authored source (a legacy merge's
   ``box``); when no such record exists, the bundle says so explicitly
   rather than inventing one — B1's design forbids inferring a box the
   caller never drew.
"""
import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

#: The opt-in switch for callers that cannot thread a keyword argument
#: through to :func:`pdf_translate.run_retypeset` — a CLI wrapper, or a
#: caller further up the stack such as ``pipeline.py``. Unset (or empty)
#: means capture stays off; an explicit ``capture_dir=`` argument always
#: wins over it.
ENV_VAR = 'PDF_TRANSLATE_CAPTURE_DIR'

#: The same switch for the threshold. A consumer that cannot thread a
#: keyword through — ``pipeline.py`` calls the loud ``retypeset()`` — sets
#: this instead.
BELOW_ENV_VAR = 'PDF_TRANSLATE_CAPTURE_BELOW'

SCHEMA = 1


def resolve_capture_below(capture_below, environ=None):
    """The ratio at or under which a SUCCESSFUL build still captures.

    ``None`` — the default — means capture only on our own refusal, which is
    today's behaviour and the only behaviour before this existed.

    It exists because our floor is not everyone's. A consumer holding 0.75
    app-side watches a build succeed at 0.72, reverts the core itself, and
    ships that paragraph in the source language; nothing refused, so a hook
    keyed to a refusal never saw the case that cost them. The band between
    their floor and ours is invisible from here unless they name it.

    Unreadable text in the environment is ignored rather than raised: this
    switches on evidence collection, and failing a build over a malformed
    evidence setting would be worse than not collecting.
    """
    if capture_below is not None:
        return float(capture_below)
    raw = (environ if environ is not None else os.environ).get(BELOW_ENV_VAR)
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def near_floor_runs(runs, threshold):
    """Every scaled run at or under `threshold`, one per core, worst kept.

    One bundle per job, keyed by core. A core can be scaled more than once
    within a single pass, and the intermediate attempts are the caller's
    business; the fact worth keeping is whether that core could be made to
    fit at all, which is the worst ratio it reached.
    """
    worst = {}
    for run in runs:
        try:
            ratio = float(run.get('ratio'))
        except (TypeError, ValueError):
            continue
        if ratio > threshold:
            continue
        key = (run.get('page'), run.get('key'))
        if key not in worst or ratio < worst[key]['ratio']:
            worst[key] = {'page': run.get('page'), 'key': run.get('key'),
                          'ratio': ratio}
    return [worst[k] for k in sorted(worst, key=lambda k: (k[0] is None, k))]


def resolve_capture_dir(capture_dir):
    """What directory (if any) capture should write to.

    The explicit argument wins. ``None`` falls back to the environment
    variable, and an unset or empty variable means capture stays off —
    the return value is ``None`` either way, so a caller can test it with
    a plain ``if``.
    """
    if capture_dir is not None:
        return capture_dir
    return os.environ.get(ENV_VAR) or None


def is_below_floor(exc):
    """True when a refusal is exactly "a run scaled below the floor".

    Both retypeset paths can raise :class:`~pdf_translate.results.PlacementError`
    for reasons that have nothing to do with the floor (an impossible
    baseline, two occurrences whose boxes collide, a rotated run that needs
    shaping). Only two shapes count here: the legacy path's ``overflow``
    refusal list, and the typography path's ``below-scale-floor`` reason —
    the two sites in ``retypeset.py`` that exist because a run's required
    scale fell under ``SCALE_MIN``.
    """
    refusals = getattr(exc, 'refusals', None) or {}
    if refusals.get('overflow'):
        return True
    return any(item.get('reason') == 'below-scale-floor'
              for item in refusals.get('typography', []))


def capture_refusal(capture_dir, exc, *, stripped, segf, trf, out,
                    original=None, fonts=None, mapping_format=None,
                    legacy_conf=None, scale_report=None, resource_root=None):
    """Best-effort: write a replayable bundle for `exc` under `capture_dir`.

    Never raises. A problem writing the bundle — disk full, a bad path, a
    permissions error, anything at all — is logged and swallowed; the
    caller always re-raises `exc` itself afterward, so a capture failure
    can never mask or replace the real refusal, and can never turn it into
    a success.

    Returns the bundle's directory (a `Path`) on success, or `None`.
    """
    try:
        return _write_bundle(
            capture_dir, exc, stripped=stripped, segf=segf, trf=trf, out=out,
            original=original, fonts=fonts, mapping_format=mapping_format,
            legacy_conf=legacy_conf, scale_report=scale_report,
            resource_root=resource_root)
    except Exception:
        # A single pre-formatted argument, no keywords: the repo's logger
        # calls are one-argument-only (tests/test_import_surface.py
        # LoggerCallShapeTests) so a %-style or exc_info record can never
        # silently vanish. The traceback text is folded into the message
        # itself instead of relying on exc_info.
        import traceback
        log.warning(f'capture: could not write a refusal bundle to '
                   f'{capture_dir!r} (the original refusal is unaffected): '
                   f'{traceback.format_exc()}')
        return None


def capture_near_floor(capture_dir, runs, threshold, *, stripped, segf, trf,
                       out, original=None, fonts=None, mapping_format=None,
                       legacy_conf=None, scale_report=None, resource_root=None):
    """Best-effort: one bundle for a SUCCESSFUL build that scaled near the floor.

    Same guarantees as :func:`capture_refusal` and for the same reason: this
    writes evidence, and evidence collection must never decide whether a
    build succeeds. Never raises; returns the bundle directory or ``None``.

    Returns ``None`` without writing when nothing reached the threshold, so
    a caller can leave it switched on permanently and pay nothing on the
    jobs that fit.
    """
    try:
        near = near_floor_runs(runs, threshold)
        if not near:
            return None
        return _write_bundle(
            capture_dir, None, stripped=stripped, segf=segf, trf=trf, out=out,
            original=original, fonts=fonts, mapping_format=mapping_format,
            legacy_conf=legacy_conf, scale_report=scale_report,
            resource_root=resource_root, near=near, threshold=threshold)
    except Exception:
        import traceback
        log.warning(f'capture: could not write a near-floor bundle to '
                   f'{capture_dir!r} (the build itself is unaffected): '
                   f'{traceback.format_exc()}')
        return None


def _case_id():
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    return f'{stamp}-{uuid.uuid4().hex[:8]}'


# A segments.json that will not parse. Distinct from every value json.loads
# can return, `null` included.
_UNREAD = object()


def _read_segments(segf):
    """(parsed segments.json, None), or (_UNREAD, why it could not be read).

    _UNREAD rather than None: `null` is valid JSON, so a file that parses
    cleanly to None is a parsed document, not a failed read, and the two must
    not answer the same question the same way.

    Read once per bundle: `_write_bundle`'s `described` closure runs per
    occurrence and would otherwise reparse this file for every one of them.

    It lives out here, rather than inline in `_write_bundle`, so that an
    `except ... as` binding cannot land in the same scope as the refusal that
    caller passed in. It did once: the handler was named `exc`, Python unbinds
    that name when the handler exits, and every later use of the refusal
    raised UnboundLocalError — so a bundle that should have recorded an
    unknown position was abandoned instead.
    """
    try:
        return json.loads(Path(segf).read_text(encoding='utf-8-sig')), None
    except (OSError, ValueError) as read_error:
        return _UNREAD, f'could not read segments.json to resolve position: {read_error}'


def _write_bundle(capture_dir, exc, *, stripped, segf, trf, out, original,
                  fonts, mapping_format, legacy_conf, scale_report,
                  resource_root, near=None, threshold=None):
    """One bundle. `exc` is a refusal; `near` is a successful build's runs.

    Exactly one of the two is given. Everything else - the copied inputs,
    the rewritten mapping, the fonts - is identical, because what makes a
    bundle replayable does not depend on which side of our floor it fell.
    """
    from . import __version__

    root = Path(capture_dir)
    root.mkdir(parents=True, exist_ok=True)
    case_dir = root / _case_id()
    case_dir.mkdir(parents=True)

    files = {}
    if original is not None and os.path.isfile(original):
        shutil.copy2(original, case_dir / 'source.pdf')
        files['source_pdf'] = 'source.pdf'
    else:
        files['source_pdf'] = None
    shutil.copy2(stripped, case_dir / 'stripped.pdf')
    files['stripped_pdf'] = 'stripped.pdf'
    shutil.copy2(segf, case_dir / 'segments.json')
    files['segments_json'] = 'segments.json'
    segments, segments_note = _read_segments(segf)

    font_map, font_files = _copy_fonts(case_dir, fonts or {})
    files['fonts'] = font_files
    mapping_rel = _copy_mapping(case_dir, trf, font_map)
    files['mapping_json'] = mapping_rel

    def described(page, key, scale):
        position, position_note = _locate_position(
            segments, segments_note, page, key)
        box_permitted, box_note = _locate_box_permission(
            mapping_format, legacy_conf, page, key)
        return {'page': page, 'key': key, 'scale': scale,
                'position': position, 'position_note': position_note,
                'box_permitted': box_permitted,
                'box_permitted_note': box_note}

    if near is None:
        occurrence = described(getattr(exc, 'page', None),
                               getattr(exc, 'key', None),
                               getattr(exc, 'scale', None))
        occurrences = None
    else:
        occurrence = None
        occurrences = [described(r['page'], r['key'], r['ratio']) for r in near]

    manifest = {
        'schema': SCHEMA,
        'captured_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'library_version': __version__,
        'mapping_format': mapping_format,
        'command': {
            'stage': 'retypeset',
            'given': {
                'stripped': str(stripped), 'segments': str(segf),
                'mapping': str(trf), 'output': str(out),
                'original': (str(original) if original is not None else None),
                'scale_report': (str(scale_report)
                                 if isinstance(scale_report, (str, os.PathLike))
                                 else None),
                'resource_root': (str(resource_root)
                                  if resource_root is not None else None),
            },
        },
        'kind': 'refusal' if near is None else 'near-floor',
        'refusal': exc.to_dict() if exc is not None else None,
        'capture_below': threshold,
        'occurrence': occurrence,
        'occurrences': occurrences,
        'files': files,
        'replay': (
            "run_retypeset(case_dir / files['stripped_pdf'], "
            "case_dir / files['segments_json'], "
            "case_dir / files['mapping_json'], <a fresh out.pdf>, "
            "original=(case_dir / files['source_pdf']) if files['source_pdf'] "
            "else None) reproduces this job. A 'refusal' bundle raises the "
            "same refusal; a 'near-floor' bundle succeeds and reports the "
            "same ratios, which is the point - nothing refused, and the "
            "caller's own floor is what the core fell under."
        ),
    }
    (case_dir / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding='utf-8')
    return case_dir


def _sanitize(name):
    keep = '-_.() abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    return ''.join(c if c in keep else '_' for c in name) or 'font'


def _copy_fonts(case_dir, fonts):
    """Copy every distinct font file the job used into `case_dir/fonts/`.

    `fonts` is either the legacy flat `{role: path}` map or the typography
    `{class: {role: path}}` map. Returns `(logical name -> path on disk,
    logical name -> bundle-relative path)`, the first for rewriting the
    copied mapping's own font references, the second for the manifest.
    """
    flat = {}
    for key, value in fonts.items():
        if isinstance(value, dict):
            for role, path in value.items():
                flat[f'{key}/{role}'] = path
        else:
            flat[key] = value

    dest_dir = case_dir / 'fonts'
    by_source, font_map, font_files = {}, {}, {}
    for logical, path in flat.items():
        if not path or not os.path.isfile(path):
            continue
        real = os.path.realpath(path)
        if real not in by_source:
            base = _sanitize(f'{logical}-{Path(path).name}')
            dest = dest_dir / base
            n = 1
            while dest.exists():
                dest = dest_dir / _sanitize(f'{logical}-{n}-{Path(path).name}')
                n += 1
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            by_source[real] = dest
        rel = str(by_source[real].relative_to(case_dir)).replace(os.sep, '/')
        font_map[logical] = rel
        font_files[logical] = rel
    return font_map, font_files


def _copy_mapping(case_dir, trf, font_map):
    """Copy the authored mapping, rewriting its font paths to the bundle's own copies.

    Both mapping formats resolve a relative font path against the mapping
    file's own directory (``retypeset.py`` for legacy, ``mapping.py``'s
    ``_font_sets`` for typography-1), so a bundle-relative path here keeps
    the mapping usable wherever the bundle is later opened, not only next
    to the job it was captured from.
    """
    dest = case_dir / 'translations.json'
    try:
        conf = json.loads(Path(trf).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        shutil.copy2(trf, dest)
        return 'translations.json'
    if isinstance(conf.get('fonts'), dict):
        for role, rel in conf['fonts'].items():
            if role in font_map:
                conf['fonts'][role] = font_map[role]
    if isinstance(conf.get('font_sets'), dict):
        for cls, roles in conf['font_sets'].items():
            if not isinstance(roles, dict):
                continue
            for role in list(roles):
                logical = f'{cls}/{role}'
                if logical in font_map:
                    roles[role] = font_map[logical]
    dest.write_text(json.dumps(conf, ensure_ascii=False, indent=1), encoding='utf-8')
    return 'translations.json'


def _locate_position(data, read_note, page, key):
    """Best-effort geometry for the refused occurrence, from parsed segments.json.

    Matches on `occurrence_id` (typography) or `core`/stripped `text`
    (legacy, including a merge's first line). Honest about a miss: a
    caller reads `position_note`, not a guess. `data` is _UNREAD, with
    `read_note` saying why, when the caller could not parse the file. A file
    that parsed to `null` is not that case and takes the ordinary path below,
    exactly as it did when this function opened the file itself.
    """
    if data is _UNREAD:
        return None, read_note
    key_stripped = (key or '').strip()
    for seg in data.get('segments', []):
        if seg.get('page') != page:
            continue
        if (seg.get('occurrence_id') == key or seg.get('core') == key or
                (seg.get('text') or '').strip() == key_stripped):
            return {'origin': seg.get('origin'), 'bbox': seg.get('bbox')}, None
    return None, f'no segment on page {page!r} matched key {key!r} in segments.json'


def _locate_box_permission(mapping_format, legacy_conf, page, key):
    """Whether the author permitted a fixed box for this occurrence, or unknown.

    B1's approved design (``docs/design/B1-line-wrapping/README.md``)
    requires explicit caller permission for wrapping; nothing here may
    invent one. The typography-1 mapping format has no per-occurrence box
    field at all, so it is always unknown there. The legacy format's only
    caller-authored fixed box today is a declared merge's `box`; when this
    occurrence is not part of one, permission is unknown, not absent.
    """
    if mapping_format != 'legacy':
        return None, ('the typography-1 mapping format has no per-occurrence '
                      'box-permission field yet; recording unknown rather '
                      'than inferring one')
    key_stripped = (key or '').strip()
    for merge in (legacy_conf or {}).get('merges') or []:
        first_line = ((merge.get('lines') or [''])[0] or '').strip()
        if merge.get('page') == page and first_line == key_stripped:
            box = merge.get('box')
            if box is not None:
                return box, "the matching merge's caller-authored box"
            return None, ('a merge exists for this occurrence but the '
                          'caller authored no box for it')
    return None, ('no caller-authored merge or box exists for this '
                  'occurrence in the mapping; recording unknown rather '
                  'than inferring one')
