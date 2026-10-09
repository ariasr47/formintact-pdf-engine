#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 5b of pdf-translate: linguistic QA on the mapping, before you build.

verify.py checks the FILE: fields, layout, placement, the text layer. Nothing
there can read. This reads translations.json against the source strings and
reports the defect classes a human reviser would catch first — the ones that
survive every structural gate because the page still looks perfect:

  numbers        a figure or amount that vanished or changed between source
                 and target. The commonest serious error in form
                 translation, and completely invisible to layout gates.
  dates          a date whose day, month or year changed or vanished
                 (error), or one reordered for the target locale (warning:
                 confirm the document is not instructing a filing format
                 the reader has to copy).
  untranslated   target identical to source, on a core that is not a
                 write/find/say identifier. Sometimes right (a form number);
                 usually a line the author skipped.
  inconsistent   two source strings that differ only in case or punctuation
                 mapped to different targets, or one source string whose
                 variants disagree. A reader sees the same label named two
                 ways.
  brackets       a bracket or quote pair the source balanced and the target
                 did not.
  punctuation    trailing ':' / '.' / '?' present on one side only. On a form
                 the colon is what tells the reader a field follows.
  spacing        doubled spaces, or space before ,.;:!?
  length         growth outside the W3C/IBM expansion band for a string that
                 short, or a target under 40% of the source with no
                 spaceless-script reason. Both mean the layout you are about
                 to build is wrong, or content was dropped.
  glossary       (with --glossary) a job termbase entry whose source term is
                 in the string and whose target term is not.

None of this judges register or terminology: no script can tell 養子支援 from
養育費. It narrows what the human reader has to look for. It is advisory by
design — findings are severity 'error' (exit 1) or 'warn' (exit 0, listed);
--strict fails on both.

Usage:
  python3 qa_check.py translations.json [--segments segments.json]
                                        [--glossary glossary.csv] [--strict]
                                        [--json findings.json]

glossary.csv is a per-job termbase (the issuer's published terms, or the
client's), two columns: source term, target term. It is NEVER shipped with
this skill — a public skill cannot maintain glossaries for every pair and
register, and the identity record already tells the author to look the
issuer's own terms up.
"""
import csv
import json
import logging
import os
import re
import sys
import unicodedata
from dataclasses import dataclass

from ._console import console, say, _arg
from .extract_segments import write_find_say_hits
from .mapping import FORMAT, load_mapping
from .verify import SPACELESS_SCRIPTS, dominant_script, strip_inline_markup

log = logging.getLogger(__name__)

# W3C i18n / IBM guidance: the shorter the string, the more room it needs.
# (max source length, allowed growth factor). Forms are short labels, so
# the flat "EN->DE grows 30%" rule of thumb is wrong exactly where it
# matters most.
EXPANSION_BAND = (
    (10, 3.0),
    (20, 2.0),
    (30, 1.8),
    (50, 1.6),
    (70, 1.4),
    (10 ** 9, 1.3),
)
SHRINK_FLOOR = 0.4

NUMBER = re.compile(r'\d[\d.,  ٫٬/:\-]*\d|\d')
# A date is three groups joined by . / or -. Reordering them is a locale
# adaptation, not a lost figure, so dates are compared as a set of parts and
# a reorder is a warning to confirm -- not a number error.
DATE = re.compile(r'\b(\d{1,4})[./\-](\d{1,2})[./\-](\d{1,4})\b')
PAIRS = (('(', ')'), ('[', ']'), ('{', '}'),
         ('«', '»'), ('“', '”'), ('‘', '’'))
DOUBLE_SPACE = re.compile(r'\S {2,}\S')
SPACE_BEFORE_PUNCT = re.compile(r'\s+[,.;:!?](?:\s|$)')
TRAILING = ':.?!;'


def expansion_limit(n):
    for upto, factor in EXPANSION_BAND:
        if n <= upto:
            return factor
    return EXPANSION_BAND[-1][1]


def date_parts(text):
    """[(sorted parts, raw)] for every date-shaped group in the string."""
    out = []
    for m in DATE.finditer(text or ''):
        parts = tuple(sorted((g.lstrip('0') or '0') for g in m.groups()))
        out.append((parts, m.group(0)))
    return out


def strip_dates(text):
    return DATE.sub(' ', text or '')


def number_tokens(text):
    """Digit groups, with grouping and decimal separators removed.

    12,500.00 and 12.500,00 are the same amount written two ways; a digit
    that disappears is not.
    """
    out = []
    for m in NUMBER.finditer(text or ''):
        digits = re.sub(r'\D', '', m.group(0))
        if digits:
            out.append(digits.lstrip('0') or '0')
    return sorted(out)


def unbalanced_pairs(text):
    """[(open, close)] pairs this string opens and closes a different number of times."""
    bad = []
    for a, b in PAIRS:
        if a == b:
            continue
        if (text or '').count(a) != (text or '').count(b):
            bad.append((a, b))
    for q in ('"',):
        if (text or '').count(q) % 2:
            bad.append((q, q))
    return bad


def normalize_key(text):
    """Case-folded, punctuation-stripped, whitespace-collapsed."""
    stripped = ''.join(
        ch for ch in unicodedata.normalize('NFKC', text or '')
        if not unicodedata.category(ch).startswith('P'))
    return ' '.join(stripped.casefold().split())


def _finding(kind, severity, core, detail):
    return {'kind': kind, 'severity': severity, 'core': core, 'detail': detail}


def check_pair(core, target, identifiers, target_script, *, plain=False):
    """Findings for one source/target pair."""
    out = []
    tgt = target if plain else strip_inline_markup(target).replace('‖', '')

    src_dates, tgt_dates = date_parts(core), date_parts(tgt)
    src_dparts = sorted(p for p, _ in src_dates)
    tgt_dparts = sorted(p for p, _ in tgt_dates)
    if src_dparts != tgt_dparts:
        out.append(_finding(
            'dates', 'error', core,
            f'source dates {[r for _, r in src_dates]}, target dates '
            f'{[r for _, r in tgt_dates]} — a day, month or year changed '
            f'or vanished'))
    elif src_dates and [r for _, r in src_dates] != [r for _, r in tgt_dates]:
        out.append(_finding(
            'dates', 'warn', core,
            f'{[r for _, r in src_dates]} was rewritten as '
            f'{[r for _, r in tgt_dates]} — confirm that is this locale\'s '
            f'order, and that the document is not instructing a filing '
            f'format the reader must copy'))

    src_nums = number_tokens(strip_dates(core))
    tgt_nums = number_tokens(strip_dates(tgt))
    if src_nums != tgt_nums:
        lost = [n for n in src_nums if n not in tgt_nums]
        added = [n for n in tgt_nums if n not in src_nums]
        out.append(_finding(
            'numbers', 'error', core,
            f'source has {src_nums}, target has {tgt_nums}'
            + (f'; lost {lost}' if lost else '')
            + (f'; added {added}' if added else '')))

    if tgt.strip() == core.strip() and core not in identifiers:
        out.append(_finding(
            'untranslated', 'warn', core,
            'target is identical to the source and this core is not a '
            'write/find/say identifier — translate it, or map it to itself '
            'deliberately and record why'))

    src_bad = unbalanced_pairs(core)
    for pair in unbalanced_pairs(tgt):
        if pair not in src_bad:
            out.append(_finding(
                'brackets', 'error', core,
                f'target does not close {pair[0]}{pair[1]}'))

    src_tail = core.rstrip()[-1:] if core.rstrip() else ''
    tgt_tail = tgt.rstrip()[-1:] if tgt.rstrip() else ''
    if (src_tail in TRAILING or tgt_tail in TRAILING) and src_tail != tgt_tail:
        out.append(_finding(
            'punctuation', 'warn', core,
            f'source ends {src_tail!r}, target ends {tgt_tail!r}'))

    if DOUBLE_SPACE.search(tgt):
        out.append(_finding('spacing', 'warn', core,
                            'doubled space inside the target'))
    if SPACE_BEFORE_PUNCT.search(tgt) and not SPACE_BEFORE_PUNCT.search(core):
        out.append(_finding('spacing', 'warn', core,
                            'space before punctuation the source did not have'))

    n = len(core.strip())
    if n:
        ratio = len(tgt.strip()) / n
        limit = expansion_limit(n)
        if ratio > limit:
            out.append(_finding(
                'length', 'warn', core,
                f'target is {ratio:.1f}x the source; {n}-character strings '
                f'are expected to grow at most {limit:.1f}x (W3C/IBM). '
                f'Either it will not fit, or it says more than the source'))
        elif (ratio < SHRINK_FLOOR
              and target_script not in SPACELESS_SCRIPTS):
            out.append(_finding(
                'length', 'warn', core,
                f'target is {ratio:.1f}x the source — check nothing was '
                f'dropped'))
    return out


def consistency_findings(translations):
    """Source strings that differ only in case or punctuation, mapped apart."""
    groups = {}
    records = ((core, target, {}) for core, target in translations.items()) if isinstance(translations, dict) else translations
    for core, target, context in records:
        if target is None:
            continue
        groups.setdefault(normalize_key(core), []).append((core, target, context))
    out = []
    for _, members in sorted(groups.items()):
        targets = {normalize_key(t) for _, t, _ in members}
        if len(members) > 1 and len(targets) > 1:
            shown = '; '.join(f'{c!r} -> {t!r}' for c, t, _ in members[:4])
            for core, _, context in (members if members[0][2] else members[:1]):
                out.append(dict(_finding(
                    'inconsistent', 'warn', core,
                    f'these differ only in case or punctuation but are '
                    f'translated differently: {shown}'), **context))
    return out


def load_glossary(path):
    """[(source term, target term)] from a two-column CSV. Header optional."""
    terms = []
    with open(path, encoding='utf-8-sig', newline='') as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            src, tgt = row[0].strip(), row[1].strip()
            if not src or not tgt:
                continue
            if src.lower() in ('source', 'source term', 'term') and not terms:
                continue
            terms.append((src, tgt))
    return terms


def glossary_findings(translations, terms, *, plain=False):
    out = []
    for core, target in translations.items():
        if target is None:
            continue
        low_core = core.casefold()
        low_tgt = (target if plain else strip_inline_markup(target)).casefold()
        for src_term, tgt_term in terms:
            if src_term.casefold() in low_core and tgt_term.casefold() not in low_tgt:
                out.append(_finding(
                    'glossary', 'error', core,
                    f'the job termbase maps {src_term!r} to {tgt_term!r}, '
                    f'which is not in this target'))
    return out


def identifier_cores(conf, segments):
    """Cores the write/find/say rule says may legitimately stay verbatim."""
    allowed = set(conf.get('allow_translate') or [])
    out = set(allowed)
    for seg in (segments or []):
        text = seg.get('text') or ''
        if write_find_say_hits(text):
            out.add(text.strip())
    # A translation core used to be scanned twice — once folded into the
    # segment texts above, once here — to add its stripped and unstripped
    # forms. One scan, both forms, same set.
    for core in (conf.get('translations') or {}):
        if write_find_say_hits(core):
            out.add(core.strip())
            out.add(core)
    return out


def _typography_findings(document, glossary_path):
    records = [(t.source_text, ''.join(r.text for r in t.runs),
                {'occurrence_id': t.occurrence_id, 'page': t.page,
                 'source_runs': list(dict.fromkeys(s for r in t.runs for s in r.source_runs))})
               for t in document.targets]
    records.extend((source, target, {'where': 'document-metadata', 'occurrence_id': None,
                                     'page': None, 'source_runs': []})
                   for source, target in document.document_targets.items())
    script = dominant_script('\n'.join(target for _, target, _ in records)) or 'Latin'
    terms = load_glossary(glossary_path) if glossary_path else []
    findings = []
    for core, target, context in records:
        identifiers = {core} if write_find_say_hits(core) else set()
        checks = check_pair(core, target, identifiers, script, plain=True)
        checks.extend(glossary_findings({core: target}, terms, plain=True))
        findings.extend(dict(f, **context) for f in checks)
    findings.extend(consistency_findings(records))
    findings.sort(key=lambda f: (0 if f['severity'] == 'error' else 1, f['kind'], f['core'], f['occurrence_id'] or ''))
    return findings


def qa_check(translations_path, segments_path=None, glossary_path=None):
    """Returns a list of findings. Reads files only; changes nothing."""
    document = load_mapping(translations_path, segments_path)
    if document.format == FORMAT:
        return _typography_findings(document, glossary_path)
    conf = document.legacy
    segments = None
    if segments_path and os.path.isfile(segments_path):
        with open(segments_path, encoding='utf-8') as f:
            data = json.load(f)
        segments = data.get('segments') if isinstance(data, dict) else data

    translations = {k: v for k, v in (conf.get('translations') or {}).items()
                    if v is not None}
    skip = set(conf.get('skip') or [])
    translations = {k: v for k, v in translations.items() if k not in skip}
    identifiers = identifier_cores(conf, segments)
    target_script = dominant_script('\n'.join(translations.values())) or 'Latin'

    findings = []
    for core, target in translations.items():
        findings.extend(check_pair(core, str(target), identifiers,
                                   target_script))
    findings.extend(consistency_findings(translations))
    if glossary_path:
        findings.extend(glossary_findings(translations,
                                          load_glossary(glossary_path)))
    order = {'error': 0, 'warn': 1}
    findings.sort(key=lambda f: (order.get(f['severity'], 2), f['kind'],
                                 f['core']))
    return findings


@dataclass(frozen=True)
class QAVerdict:
    """Structured linguistic-QA result. Does not print or exit."""
    findings: list

    @property
    def error_count(self):
        return sum(1 for f in self.findings if f['severity'] == 'error')

    @property
    def warn_count(self):
        return sum(1 for f in self.findings if f['severity'] != 'error')

    @property
    def exit_code(self):
        return 1 if self.error_count else 0

    @property
    def ok(self):
        return self.exit_code == 0


def run_qa(translations_path, segments_path=None, glossary_path=None):
    """Run linguistic QA. Returns a QAVerdict; does not print or exit."""
    return QAVerdict(findings=qa_check(
        translations_path, segments_path, glossary_path))


def _say(line):
    say(log, line)


def main(argv=None):
    with console():
        from .results import PdfTranslateError
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
    missing = missing_option_value(argv, ('--segments', '--glossary', '--json'))
    if missing:
        log.info(f'usage: {missing} requires a value')
        return 2
    translations = argv[0]
    segments = _arg(argv, '--segments')
    if not segments:
        candidate = os.path.join(
            os.path.dirname(os.path.abspath(translations)), 'segments.json')
        segments = candidate if os.path.isfile(candidate) else None
    findings = qa_check(translations, segments, _arg(argv, '--glossary'))

    out_path = _arg(argv, '--json')
    if out_path:
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump({'findings': findings}, f, ensure_ascii=False, indent=1)

    errors = [f for f in findings if f['severity'] == 'error']
    warns = [f for f in findings if f['severity'] != 'error']
    for f in findings[:200]:
        _say(f"{f['severity'].upper():5} {f['kind']:13} {f['core'][:48]!r}: "
             f"{f['detail']}")
    _say(f'qa_check: {len(errors)} error(s), {len(warns)} warning(s) over '
         f'{translations}')
    if not findings:
        _say('qa_check: nothing to report. This does NOT mean the wording is '
             'right — no script can judge that. A human still reads it.')
    if errors or ('--strict' in argv and warns):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
