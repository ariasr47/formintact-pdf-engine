#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hot-path wrappers around the stage scripts.

The deterministic pipeline is a few seconds. Wall-clock is the translation
and visual loop. Use `rebuild` after the first extract instead of re-running
strip / extract / prepare_font / compare every round.

Usage:
  python3 pipeline.py init ORIGINAL.pdf --work DIR [--captions captions.json]
                                                  [--widget-text wt.json]
                                                  [--keep-encryption]
                                                  [--pages 1-10]
      strip + extract into DIR (stripped.pdf, segments.json,
      to_translate.json, widget_text.json). Run it once bare to get the
      widget_text.json scaffold, author the targets, then run it again with
      --widget-text to apply tooltips, dropdown labels and defaults.
      Extract never writes a fresh scaffold over a widget_text.json that
      holds authored text; delete the file to get a new one.

  python3 pipeline.py from-cores --work DIR [--force]
      scaffold DIR/translations.json from DIR/to_translate.json cores.
      Values are JSON null (retypeset still FAILs until you author them).
      Refuses to overwrite unless --force. No model, no auto-merge.

  python3 pipeline.py propose-merges --work DIR [--accept] [--min-lines 2]
      turn the extractor's wrapped-paragraph candidates into ready-to-edit
      "merges" entries with html: null. Writes DIR/merges_proposed.json;
      --accept also folds them into DIR/translations.json. Nothing merges
      without this command, and a null html still FAILs retypeset until
      you author the paragraph. Paragraph mode for manuals and brochures.

  python3 pipeline.py merge-mappings OUT.json IN.json [IN.json ...]
      combine mappings authored per page range into one. Conflicting
      values for the same core are reported and nothing is written unless
      --last-wins.

  python3 pipeline.py qa --work DIR [--glossary glossary.csv] [--strict]
      linguistic QA on DIR/translations.json before you build: numbers and
      dates that moved, untranslated lines, inconsistent variants, brackets,
      punctuation parity, spacing, expansion band. Advisory; --strict makes
      warnings fail too.

  python3 pipeline.py rebuild --work DIR ORIGINAL.pdf OUT.pdf [verify flags...]
      retypeset DIR/stripped.pdf + DIR/segments.json + DIR/translations.json
      then verify ORIGINAL.pdf OUT.pdf with any extra verify flags, against
      DIR/translations.json and DIR/segments.json unless --translations or
      --segments is forwarded (the mapping is always supplied)
      Invalidates the default and selected verification reports before the
      attempt. An older PDF may remain after failure; check this run's result.

  python3 pipeline.py render ORIGINAL.pdf TRANSLATED.pdf renders/ [--dpi 110]
      every page of both files; exits 1 when the page counts differ.

  python3 pipeline.py review --work DIR [--ingest review.json] [--notes NOTES.md]
      the second-reader step. Bare, it writes DIR/review_pairs.md (every
      core, merge, override and notice in one place) and
      DIR/review_prompt.md (the MQM prompt with this job's identity filled
      in). With --ingest it reads the reviser's verdict back, reports every
      finding, and appends the accepted terminology ones to
      DIR/glossary.csv so the next job of this class is gated on them.
      No model is called: the reviser is a person, or one you run yourself.

  python3 pipeline.py finish ORIGINAL.pdf OUT.pdf FONT.ttf FINAL.pdf HTML
                             --work DIR [--no-review]
      field_fonts + compare (delivery only). Refuses while DIR/review.json
      is absent or any finding is still open; --no-review delivers anyway
      and marks the delivery. Writes review_state.json beside FINAL.pdf on
      every run, refused or not. Exits 1, with every file written, when
      FINAL.pdf's page count differs from the original's.

  python3 pipeline.py bilingual ORIGINAL.pdf FINAL.pdf BOTH.pdf
      optional reading copy with source and target pages interleaved.
      Refuses a fillable input unless --reading-copy (duplicate field
      names fill together).
"""
import json
import logging
import os
import sys
import time
from dataclasses import replace

from .extract_segments import main as extract_main
from .field_fonts import field_fonts
from .compare import compare
from .render_pages import page_count_mismatch, render_pages
from .retypeset import retypeset
from .strip_text import strip_text, WidgetTextError
from .bilingual import main as bilingual_main
from .qa_check import main as qa_main
from ._console import console, _arg
from .review import run_review
from .mapping import FORMAT, load_extraction, load_mapping, load_mapping_data, refuse
from .results import MappingError, PdfTranslateError
from .verify import verify as verify, main as verify_main

log = logging.getLogger(__name__)

# Everything here emits through the package logger; nothing prints. The
# handler that carries those lines to stdout lives in `_console.py`, shared by
# every CLI entry point, because `pipeline.py` runs the other stages
# in-process and a second handler would print every line twice. `console()` is
# re-entrant for exactly that reason.


def cmd_init(argv):
    src = argv[0]
    work = _arg(argv, '--work', '.')
    os.makedirs(work, exist_ok=True)
    captions = None
    if '--captions' in argv:
        with open(argv[argv.index('--captions') + 1], encoding='utf-8') as f:
            captions = json.load(f)
    widget_text = None
    if '--widget-text' in argv:
        with open(argv[argv.index('--widget-text') + 1], encoding='utf-8') as f:
            widget_text = json.load(f)
    stripped = os.path.join(work, 'stripped.pdf')
    t0 = time.perf_counter()
    try:
        report = strip_text(src, stripped, captions=captions,
                            widget_text=widget_text,
                            keep_encryption='--keep-encryption' in argv)
    except WidgetTextError as exc:
        log.info(f'FAIL widget text: {exc}')
        return 2
    log.info(f'strip: xfa_removed={report.get("xfa_removed")} '
          f'dead_buttons={len(report.get("dead_buttons") or [])}')
    if report.get('perms_removed'):
        log.info(f'strip: deleted /Perms {report["perms_removed"]}')
    if report.get('certified'):
        log.info('WARNING: the source was CERTIFIED (/Perms /DocMDP); the '
              'translation is not. Say so when you deliver it.')
    if report.get('encryption', {}).get('encrypted') and not report.get('reencrypted'):
        log.info('NOTE: the source was encrypted; the output is not '
              '(--keep-encryption re-applies its permission bits).')
    leftover = report.get('leftover_text') or []
    if leftover:
        log.info(f'FAIL: page text survived strip on {len(leftover)} page(s); '
              f'{stripped} was not written:')
        for item in leftover:
            log.info(f"  p{item['page']}: {item['text']}")
        return 1
    extract_args = [src, '--outdir', work]
    if '--typography' in argv:
        extract_args.append('--typography')
    if '--max-per-kind' in argv:
        extract_args += ['--max-per-kind', argv[argv.index('--max-per-kind') + 1]]
    if '--pages' in argv:
        extract_args += ['--pages', argv[argv.index('--pages') + 1]]
    rc = extract_main(extract_args)
    log.info(f'elapsed {time.perf_counter()-t0:.2f}s -> {work}')
    return rc


def scaffold_from_cores(to_translate_path, out_path, force=False):
    """See `_scaffold_from_cores`. Loud: prints and returns an int.

    A library-level entry point — tests/test_pipeline.py captures stdout
    around a direct call to it, so it keeps printing. The envelope is
    re-entrant, so calling it from `main` does not double a line.
    """
    with console():
        try:
            return _scaffold_from_cores(to_translate_path, out_path, force=force)
        except MappingError as exc:
            log.info(exc.console_line)
            return 2


def _scaffold_from_cores(to_translate_path, out_path, force=False):
    """Write translations.json with a null for every core. Returns 0, or 2."""
    if not os.path.isfile(to_translate_path):
        log.info(f'from-cores: missing {to_translate_path}')
        return 2
    if os.path.isfile(out_path) and not force:
        log.info(f'from-cores: {out_path} exists (pass --force to overwrite)')
        return 2
    data, format_name = load_mapping_data(to_translate_path)
    seg_path = os.path.join(os.path.dirname(os.path.abspath(to_translate_path)), 'segments.json')
    if format_name == FORMAT:
        if set(data) != {'format', 'segments', 'extraction_id', 'cores'} or data['segments'] != 'segments.json':
            refuse('stale-extraction', 'from-cores requires the adjacent bound segments.json reference')
        extraction = load_extraction(seg_path, data['extraction_id'])
        document = extraction['document']
        metadata = [document.get('title', '')] + list(document.get('outline') or [])
        conf = {'format': FORMAT, 'extraction_id': data['extraction_id'], 'lang': '', 'font_sets': {},
                'source_font_resolutions': [], 'allow_scale': [],
                'document_targets': {text: None for text in metadata if text},
                'targets': [{'occurrence_id': s['occurrence_id'], 'runs': []} for s in extraction['segments']]}
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(conf, f, ensure_ascii=False, indent=1)
            f.write('\n')
        log.info(f'from-cores: {len(conf["targets"])} occurrences -> {out_path} (unauthored typography template)')
        return 0
    if os.path.isfile(seg_path):
        with open(seg_path, encoding='utf-8') as f:
            extraction = json.load(f)
        if isinstance(extraction, dict) and 'typography' in extraction:
            refuse('stale-extraction', 'from-cores: typography extraction reference is missing')
    cores = data.get('cores') or []
    translations = {}
    for c in cores:
        text = (c.get('text') if isinstance(c, dict) else None) or ''
        if text:
            translations[text] = None
    conf = {
        'fonts': {'regular': 'font-sub.ttf', 'bold': 'font-sub.ttf',
                  'italic': 'font-sub.ttf', 'bold_italic': 'font-sub.ttf'},
        # BCP-47 tag of the TARGET language, e.g. "es-MX". retypeset writes
        # it to /Lang and to dc:language; verify REVIEWs a mapping without
        # one, because the output otherwise tells screen readers it is
        # still in the source language.
        'lang': None,
        'translations': translations,
        'merges': [],
        'overrides': [],
        'center': [],
        'right': [],
        'skip': [],
        'allow_scale': [],
    }
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(conf, f, ensure_ascii=False, indent=1)
        f.write('\n')
    log.info(f'from-cores: {len(translations)} cores -> {out_path} '
          f'(values are null — author them; do not ship)')
    return 0


def cmd_from_cores(argv):
    work = _arg(argv, '--work', '.')
    force = '--force' in argv
    to_path = os.path.join(work, 'to_translate.json')
    out_path = os.path.join(work, 'translations.json')
    return scaffold_from_cores(to_path, out_path, force=force)


def propose_merges(work, accept=False, min_lines=2):
    """See `_propose_merges`. Loud: prints and returns an int.

    A library-level entry point — tests/test_pipeline.py captures stdout
    around a direct call to it, so it keeps printing. The envelope is
    re-entrant, so calling it from `main` does not double a line.
    """
    with console():
        try:
            return _propose_merges(work, accept=accept, min_lines=min_lines)
        except MappingError as exc:
            log.info(exc.console_line)
            return 2


def _propose_merges(work, accept=False, min_lines=2):
    """Shape the extractor's merge candidates as editable merges entries.

    A wrapped paragraph must be translated as one unit and re-flowed;
    translating each visual line on its own is how a brochure turns to
    fragments. Geometry can propose those groups but must never apply
    them — sibling list items share a column and read exactly like a
    wrapped paragraph. So: propose in bulk, accept in bulk, author each
    html. A proposal with html null still fails retypeset.
    """
    seg_path = os.path.join(work, 'segments.json')
    if not os.path.isfile(seg_path):
        log.info(f'propose-merges: missing {seg_path}')
        return 2
    with open(seg_path, encoding='utf-8') as f:
        data = json.load(f)
    tr_path = os.path.join(work, 'translations.json')
    conf = None
    if os.path.isfile(tr_path):
        conf, format_name = load_mapping_data(tr_path)
        if format_name != 'legacy':
            refuse('unsupported-typography-construct', 'propose-merges cannot preserve typography occurrences')
    if 'typography' in data:
        refuse('unsupported-typography-construct', 'propose-merges cannot preserve typography occurrences')
    by_id = {s['id']: s for s in data.get('segments') or []}
    proposals = []
    for w in data.get('warnings') or []:
        ids = w.get('ids') or []
        # Candidates carry kind "merge-candidate" since row 22; a
        # segments.json from an earlier extractor has none. Both propose:
        # a stale work directory must not lose its paragraphs.
        if w.get('kind') not in (None, 'merge-candidate', 'narrow-column'):
            continue
        if len(ids) < min_lines:
            continue
        segs = [by_id[i] for i in ids if i in by_id]
        if len(segs) < min_lines:
            continue
        proposals.append({
            'page': w.get('page', segs[0]['page']),
            'lines': [s['text'].strip() for s in segs],
            'html': None,
            'align': 'left',
            # Null means "re-flow into the union of these lines". A rect
            # here is the author's decision and nothing else's: look at the
            # page first (row 26).
            'box': None,
            'why': w.get('why', ''),
        })
    out_path = os.path.join(work, 'merges_proposed.json')
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({'merges': proposals}, f, ensure_ascii=False, indent=1)
    log.info(f'propose-merges: {len(proposals)} candidate(s) -> {out_path} '
          f'(html is null — author each, or delete the entry; box is null '
          f'— set one only if you looked at the page)')
    if not accept:
        log.info('propose-merges: nothing changed. Re-run with --accept to fold '
              'these into translations.json.')
        return 0

    tr_path = os.path.join(work, 'translations.json')
    if not os.path.isfile(tr_path):
        log.info(f'propose-merges: missing {tr_path} (run from-cores first)')
        return 2
    existing = {tuple(m.get('lines') or []) for m in conf.get('merges') or []}
    merges = list(conf.get('merges') or [])
    added = 0
    for p in proposals:
        key = tuple(p['lines'])
        if key in existing:
            continue
        merges.append({k: p[k] for k in ('page', 'lines', 'html', 'align')})
        existing.add(key)
        added += 1
    conf['merges'] = merges
    with open(tr_path, 'w', encoding='utf-8') as f:
        json.dump(conf, f, ensure_ascii=False, indent=1)
        f.write('\n')
    log.info(f'propose-merges: added {added} merge(s) to {tr_path}; author every '
          f'html (null still FAILs retypeset)')
    return 0


def cmd_propose_merges(argv):
    work = _arg(argv, '--work', '.')
    min_lines = int(_arg(argv, '--min-lines', 2))
    return propose_merges(work, accept='--accept' in argv,
                          min_lines=min_lines)


def merge_mappings(out_path, paths, last_wins=False):
    """See `_merge_mappings`. Loud: prints and returns an int.

    A library-level entry point — tests/test_pipeline.py captures stdout
    around a direct call to it, so it keeps printing. The envelope is
    re-entrant, so calling it from `main` does not double a line.
    """
    with console():
        try:
            return _merge_mappings(out_path, paths, last_wins=last_wins)
        except MappingError as exc:
            log.info(exc.console_line)
            return 2


def _merge_mappings(out_path, paths, last_wins=False):
    """Combine per-page-range mappings into one. Conflicts stop the write."""
    merged = None
    conflicts = []
    for path in paths:
        conf, format_name = load_mapping_data(path)
        if format_name != 'legacy':
            refuse('unsupported-typography-construct', 'merge-mappings cannot preserve typography occurrences')
        if merged is None:
            merged = json.loads(json.dumps(conf))
            continue
        for core, target in (conf.get('translations') or {}).items():
            have = merged['translations'].get(core, KeyError)
            if have is not KeyError and have != target and target is not None:
                if have is None or last_wins:
                    merged['translations'][core] = target
                else:
                    conflicts.append((core, have, target, path))
            elif have is KeyError:
                merged['translations'][core] = target
        for key in ('merges', 'overrides'):
            merged.setdefault(key, [])
            merged[key].extend(conf.get(key) or [])
        for key in ('center', 'right', 'skip', 'allow_scale',
                    'allow_translate'):
            if conf.get(key):
                merged[key] = sorted(set(merged.get(key) or [])
                                     | set(conf[key]))
    if merged is None:
        log.info('merge-mappings: no inputs')
        return 2
    if conflicts:
        log.info(f'merge-mappings: {len(conflicts)} conflicting core(s); '
              f'nothing written (pass --last-wins to take the later file):')
        for core, a, b, path in conflicts[:20]:
            log.info(f'  {core[:50]!r}: {a!r} vs {b!r} (from {path})')
        return 1
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
        f.write('\n')
    total = len(merged.get('translations') or {})
    log.info(f'merge-mappings: {total} cores -> {out_path}')
    return 0


def cmd_merge_mappings(argv):
    rest = [a for a in argv if a != '--last-wins']
    if len(rest) < 2:
        log.info('merge-mappings: OUT.json IN.json [IN.json ...]')
        return 2
    return merge_mappings(rest[0], rest[1:], last_wins='--last-wins' in argv)


def cmd_qa(argv):
    work = _arg(argv, '--work', '.')
    rest = [a for a in argv if a not in ('--work', work)]
    tr = os.path.join(work, 'translations.json')
    segs = os.path.join(work, 'segments.json')
    if not os.path.isfile(tr):
        log.info(f'qa: missing {tr}')
        return 2
    args = [tr]
    if os.path.isfile(segs):
        args += ['--segments', segs]
    # --glossary resolved against the working directory while --segments was
    # joined to --work: an asymmetry that made the two-command story
    # (`review --ingest` writes work/glossary.csv, `qa --work … --glossary
    # glossary.csv` reads it back) need a path dance. A bare name that exists
    # under work now resolves there; an explicit path that resolves is left
    # exactly as the caller wrote it.
    if '--glossary' in rest:
        gi = rest.index('--glossary')
        if gi + 1 < len(rest):
            value = rest[gi + 1]
            joined = os.path.join(work, value)
            if not os.path.exists(value) and os.path.isfile(joined):
                rest = rest[:gi + 1] + [joined] + rest[gi + 2:]
    return qa_main(args + rest)


def _prepare_rebuild_reports(paths, protected):
    """Invalidate only recognized verification reports, before any stage runs.

    Preflight every path before removing any: --report is caller-controlled,
    and an unreadable/unrelated file must never be treated as our disposable
    output. The content check also protects fonts before mapping can load.
    """
    from .retypeset import _same_path
    from .verify import _remove_stale_report

    for path in paths:
        if any(_same_path(path, source) for source in protected):
            log.info(f'rebuild: report path must not overwrite an input or output: {path}')
            return False
        if not os.path.lexists(path):
            continue
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = None
        if not (isinstance(data, dict) and data.get('schema') == 1
                and isinstance(data.get('gates'), list)
                and isinstance(data.get('original'), str)
                and isinstance(data.get('output'), str)
                and type(data.get('exit_code')) is int):
            log.info(f'rebuild: refusing to replace an unrecognized verification report: {path}')
            return False
    for path in paths:
        if not _remove_stale_report(path):
            log.info('rebuild: cannot invalidate previous verification; rebuild not started')
            return False
    return True


def cmd_rebuild(argv):
    if '--work' not in argv:
        log.info('rebuild requires --work DIR')
        return 2
    wi = argv.index('--work')
    work = argv[wi + 1]
    rest = argv[:wi] + argv[wi + 2:]
    if len(rest) < 2:
        log.info('rebuild: ORIGINAL.pdf OUT.pdf [verify flags...]')
        return 2
    orig, out, extra = rest[0], rest[1], rest[2:]
    orig = os.path.abspath(orig)
    out = os.path.abspath(out)
    work = os.path.abspath(work)
    stripped = os.path.join(work, 'stripped.pdf')
    segs = os.path.join(work, 'segments.json')
    tr = os.path.join(work, 'translations.json')
    t0 = time.perf_counter()
    default_report = os.path.join(work, 'verify_report.json')
    report = extra[extra.index('--report') + 1] if '--report' in extra else default_report
    value_flags = ('--translations', '--segments', '--original', '--source-words-from', '--reference-fonts',
                   '--widget-text')
    inputs = [orig, stripped, segs, tr]
    inputs.extend(value for flag, value in zip(extra, extra[1:]) if flag in value_flags)
    # verify reads only `--flag value`, but `--flag=value` still names a file
    # the caller meant as an input, and OUT must not replace it either.
    inputs.extend(arg.split('=', 1)[1] for arg in extra
                  if any(arg.startswith(flag + '=') for flag in value_flags))
    # R-05: checked here, before anything is written, for every input;
    # retypeset checks the original, mapping and segments again, and the
    # fonts only there.
    from .retypeset import _same_path
    if any(_same_path(out, path) for path in inputs):
        log.info(f'rebuild: output path must not overwrite an input: {out}')
        return 2
    protected = [out] + inputs
    # The work directory's default report must not claim success after the
    # caller switches to a custom report. Other custom paths are caller-owned.
    reports = list(dict.fromkeys((default_report, os.path.abspath(report))))
    if not _prepare_rebuild_reports(reports, protected):
        return 2
    mapping = load_mapping(tr, segs)
    typography = mapping.format == FORMAT
    if typography:
        for arg in extra:
            if any(arg == flag or arg.startswith(flag + '=') for flag in ('--translations', '--segments', '--original')):
                log.info('rebuild: typography source/mapping/segments are fixed; conflicting forwarded flags are not allowed')
                return 2
        extra += ['--translations', tr, '--segments', segs]
    else:
        # A06: verify against the mapping this rebuild built from, so empty
        # targets, placement, override markers and identifiers are checked on
        # every default run. A caller's own --translations / --segments are
        # honoured as given; the mapping itself is always supplied, and the
        # default segments come only with the default mapping. Only separated
        # tokens count here. Equals forms are forwarded too, but verify now
        # refuses them instead of silently ignoring them.
        if '--translations' not in extra:
            extra += ['--translations', tr]
            if '--segments' not in extra:
                extra += ['--segments', segs]
    # The original goes to every job: a typography build needs it, and a
    # legacy one records it in a capture bundle, which without it says
    # `source_pdf: null` (R-40). Legacy drawing never reads it.
    rc = retypeset(stripped, segs, tr, out, original=orig)
    if rc != 0:
        log.info(f'elapsed {time.perf_counter()-t0:.2f}s (retypeset failed)')
        return rc
    # Retypeset resolves font paths beside the mapping. Leave the caller's
    # directory intact so all command-line verification paths keep meaning.
    if '--report' not in extra:
        extra = extra + ['--report', default_report]
    rc = verify_main([orig, out] + extra)
    log.info(f'elapsed {time.perf_counter()-t0:.2f}s')
    return rc


def cmd_render(argv):
    dpi = int(_arg(argv, '--dpi', 110))
    orig, trans, outdir = argv[0], argv[1], argv[2]
    t0 = time.perf_counter()
    render_pages(orig, trans, outdir, dpi=dpi)
    log.info(f'elapsed {time.perf_counter()-t0:.2f}s')
    return 1 if page_count_mismatch(orig, trans) else 0


def _finding_line(severity, kind, core, detail):
    """qa_check.main's column shape, reused verbatim so a reader who knows
    one command can read the other."""
    return f'{severity:5} {kind:13} {core[:48]!r}: {detail}'


def cmd_review(argv):
    if '--work' not in argv:
        log.info('review requires --work DIR')
        return 2
    work = argv[argv.index('--work') + 1]
    ingest = _arg(argv, '--ingest')
    notes = _arg(argv, '--notes')

    t0 = time.perf_counter()
    verdict = run_review(work, ingest=ingest, notes=notes)
    for err in verdict.errors:
        log.info(f'review: {err}')
    if verdict.errors and not verdict.findings:
        log.info(f'elapsed {time.perf_counter()-t0:.2f}s')
        return verdict.exit_code

    tr_path = os.path.join(work, 'translations.json')
    if os.path.isfile(tr_path):
        mapping = load_mapping(tr_path)
        tr = mapping.legacy or {}
        notices = len(tr.get('notices') or [])
        if mapping.format == FORMAT:
            log.info(f'review: {len(mapping.targets)} occurrences')
        else:
            log.info(f'review: {len(tr.get("translations") or {})} cores, '
                 f'{len(tr.get("merges") or [])} merges, '
                 f'{len(tr.get("overrides") or [])} overrides, '
                 f'{notices} notice{"" if notices == 1 else "s"}')
    for path in verdict.wrote:
        log.info(f'review: wrote {path}')

    if not verdict.present:
        log.info('review: no review.json yet. finish will refuse.')
    else:
        who = verdict.reviewer or {}
        qualified = ('qualified in pair' if who.get('qualified_in_pair')
                     else 'not qualified in pair')
        log.info(f'review: reviewer {who.get("name", "?")} '
                 f'({who.get("kind", "?")}, {qualified})')
        counts = verdict.counts
        log.info(f'review: {len(verdict.findings)} finding(s) — '
                 f'{counts["critical"]} critical, {counts["major"]} major, '
                 f'{counts["minor"]} minor')
        log.info(f'review: {counts["accepted"]} accepted, '
                 f'{counts["rejected"]} rejected, {counts["open"]} open')
        for core, note, resolved in verdict.migrations:
            log.info(f'review: migrated resolution {note[:60]!r} → '
                     f'{resolved}; prose kept in resolution_note')
        for f in verdict.open_findings:
            log.info(_finding_line('OPEN', f.category, f.core, f.detail))
        if verdict.termbase_added:
            log.info(f'review: {len(verdict.termbase_added)} accepted '
                     f'terminology finding(s) → '
                     f'{os.path.join(work, "glossary.csv")}')
        if verdict.termbase_skipped:
            log.info(f'review: {len(verdict.termbase_skipped)} skipped — no '
                     f'term named; add "term" and "term_target"')
        for term, existing, new in verdict.conflicts:
            log.info(f'review: CONFLICT {term!r} is already {existing!r}; '
                     f'{new!r} was not written')
        if verdict.open_findings:
            log.info(f'review: {len(verdict.open_findings)} open finding(s). '
                     f'finish will refuse until each is accepted or rejected.')
    log.info(f'elapsed {time.perf_counter()-t0:.2f}s')
    return verdict.exit_code


def cmd_finish(argv):
    # --work and --no-review come out of argv before the five positionals,
    # exactly as cmd_rebuild does.
    no_review = '--no-review' in argv
    argv = [a for a in argv if a != '--no-review']
    work = None
    if '--work' in argv:
        wi = argv.index('--work')
        work = argv[wi + 1]
        argv = argv[:wi] + argv[wi + 2:]
    if len(argv) < 5:
        log.info('finish: ORIGINAL.pdf OUT.pdf FONT.ttf FINAL.pdf HTML '
                 '--work DIR [--no-review]')
        return 2
    orig, out, font, final, html = argv[0], argv[1], argv[2], argv[3], argv[4]

    if work is None:
        # Ruling 1: inferring the work directory from FINAL.pdf was rejected
        # — the FL-150 delivery went to Downloads/ while the job lived
        # elsewhere, so the gate would refuse a reviewed job and be switched
        # off. The flag is explicit or there is no gate.
        log.info('finish: --work DIR is required — it is where review.json '
                 'lives, and finish cannot check a review it cannot find.')
        return 2

    t0 = time.perf_counter()
    # Read-only: packaging never rewrites the reviser's inputs, and never
    # appends to the termbase behind `review --ingest`'s back.
    verdict = run_review(work, ingest='review.json', generate=False)
    if no_review:
        verdict = replace(verdict, no_review=True)

    if verdict.blocks_delivery and not no_review:
        if verdict.errors:
            log.info('finish: review could not be accepted:')
            for error in verdict.errors:
                log.info(f'  {error}')
            log.info(f'finish: correct the errors, then run `pipeline.py review '
                     f'--work {work} --ingest review.json` before retrying.')
        elif not verdict.present:
            log.info(f'finish: no {os.path.join(work, "review.json")} — no '
                     f'second reader has checked this translation.')
            log.info(f'finish: run `pipeline.py review --work {work}`, have '
                     f'it reviewed, then `review --work {work} --ingest '
                     f'review.json`.')
        else:
            log.info(f'finish: {len(verdict.open_findings)} finding(s) are '
                     f'still open; each must be accepted or rejected.')
            for f in verdict.open_findings:
                log.info(_finding_line('OPEN', f.category, f.core, f.detail))
        log.info('finish: --no-review delivers anyway and marks the delivery.')
        _write_review_state(final, verdict)
        log.info(f'elapsed {time.perf_counter()-t0:.2f}s')
        return 2

    if verdict.review_line:
        log.info(verdict.review_line)

    rc = field_fonts(out, font, final)
    if rc != 0:
        _write_review_state(final, verdict)
        return rc
    # A02: compare returns 1 when the delivery's page count differs from the
    # original's. Everything is still written (finish packages, it does not
    # withhold); the exit code is what stops it reading as a clean delivery.
    rc = compare(orig, final, html)
    _write_review_state(final, verdict)
    log.info(f'elapsed {time.perf_counter()-t0:.2f}s')
    return rc


def _write_review_state(final, verdict):
    """The record beside the delivery, written on EVERY run, refused or not.

    R5 says a non-build stage never withholds a document, so for the product
    the load-bearing part of this loop is the record, not the refusal: a
    consumer that never reads stdout can still surface the REVIEW line.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(final)),
                        'review_state.json')
    try:
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(verdict.to_dict(), f, ensure_ascii=False, indent=1)
            f.write('\n')
    except OSError as exc:
        log.info(f'finish: (could not write {path}: {exc})')


def main(argv=None):
    with console():
        try:
            return _main(argv)
        except PdfTranslateError as exc:
            log.info(exc.console_line)
            return exc.exit_code


def _main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        log.info(__doc__)
        return 2
    from ._console import missing_option_value
    missing = missing_option_value(argv, ('--work', '--captions', '--widget-text', '--pages', '--max-per-kind',
        '--min-lines', '--glossary', '--ingest', '--notes', '--dpi', '--report', '--fill-text', '--translations',
        '--segments', '--allow', '--min-ink', '--source-regex', '--source-words-from', '--allow-extra-prefix', '--reference-fonts'))
    if missing:
        log.info(f'usage: {missing} requires a value')
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == 'review':
        return cmd_review(rest)
    if cmd == 'init':
        return cmd_init(rest)
    if cmd == 'from-cores':
        return cmd_from_cores(rest)
    if cmd == 'propose-merges':
        return cmd_propose_merges(rest)
    if cmd == 'merge-mappings':
        return cmd_merge_mappings(rest)
    if cmd == 'qa':
        return cmd_qa(rest)
    if cmd == 'rebuild':
        return cmd_rebuild(rest)
    if cmd == 'render':
        return cmd_render(rest)
    if cmd == 'finish':
        return cmd_finish(rest)
    if cmd == 'bilingual':
        return bilingual_main(rest)
    log.info(f'unknown command {cmd}')
    log.info(__doc__)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
