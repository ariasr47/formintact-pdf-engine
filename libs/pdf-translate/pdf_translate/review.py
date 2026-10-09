#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The reviser loop: the two files a second reader needs, and their verdict.

No gate can see a wrong term of art. The first FL-150 to Japanese job exited
verify 0 and qa_check with 0 errors while carrying 世帯主 for "head of
household" — a status a married Japanese reader takes to mean themselves,
where the US filing status is for the unmarried. The page was right and the
string was present; only a second reader found it.

This module is that second reader's plumbing. It writes the pairs file and the
prompt, ingests the review.json that comes back, and grows a per-class termbase
from the accepted terminology findings so the next job of that class is gated
on what this one learned.

It prints nothing, raises nothing and exits nothing, exactly like
qa_check.run_qa and verify.run_verify. It returns a ReviewVerdict; the caller
decides what that means for a console.

Nothing here calls a model. The reviser is a person, or a model the operator
runs — outside this pipeline, in a session of their own.
"""
import csv
import html
import json
import os
from dataclasses import dataclass

from .mapping import FORMAT, MappingDocument, load_mapping
from .results import MappingError

SCHEMA = 1

RESOLUTIONS = ('accepted', 'rejected', 'open')

CATEGORIES = ('terminology', 'accuracy', 'linguistic-conventions',
              'style', 'locale-conventions', 'audience-appropriateness')

SEVERITIES = ('critical', 'major', 'minor')

# Every field a finding must carry. `suggestion` may be null; `term`,
# `term_target` and `resolution_note` are optional by design.
REQUIRED_FINDING_KEYS = ('core', 'target', 'category', 'subtype',
                         'severity', 'detail', 'resolution')

# notices[].text uses this as a line break, so a notice reads as it will be
# drawn rather than as one run-on line.
NOTICE_BREAK = '\u2016'


def leading_token(value):
    """The enum token at the head of a resolution, or '' if there is none.

    `references/review.md` §4 has specified the enum since before anything
    read the field, so the one real review.json in this repo stores prose:
    'accepted, applied in v2 (2026-09-17)', 'rejected on measurement: the
    label ends at 259.5 pt', 'accepted with a different fix: 月給／週給／時給'.
    A strict parser would refuse all 31 findings on the only job that exists.

    Three of those four shapes carry a qualifier before the colon, so the
    token is the first WORD of the trimmed head, not the whole head: stopping
    at the colon would put 'rejected on measurement' outside the enum and
    refuse a finding the file resolved perfectly clearly.
    """
    head = str(value).split(',')[0].split(':')[0].strip().casefold()
    return head.split()[0] if head.split() else ''


@dataclass(frozen=True)
class ReviewFinding:
    """One reviser finding.

    `resolution` is the enum; `resolution_note` is the prose that used to live
    in it — on the FL-150 job the rejections are the most valuable text in the
    file, so it is kept whole, never truncated to the token parsed out of it.

    `term`/`term_target` are the termbase pair a terminology finding may carry.
    They are None when the reviser did not name one, which is reported and
    skipped — never guessed. `core` is the mapping key, and on the FL-150 job
    six of the eleven accepted terminology findings are keyed to a whole
    sentence ("Utilities (gas, electric, water, trash)" — the term is
    Utilities), so the core cannot stand in for the term.
    """
    core: str
    target: str
    category: str
    subtype: str
    severity: str
    detail: str
    suggestion: str | None = None
    resolution: str = 'open'
    resolution_note: str = ''
    term: str | None = None
    term_target: str | None = None
    migrated: bool = False
    occurrence_id: str | None = None
    source_runs: tuple | None = None

    def to_dict(self):
        result = {'core': self.core, 'target': self.target,
                'category': self.category, 'subtype': self.subtype,
                'severity': self.severity, 'detail': self.detail,
                'suggestion': self.suggestion,
                'resolution': self.resolution,
                'resolution_note': self.resolution_note,
                'term': self.term, 'term_target': self.term_target,
                'migrated': self.migrated}
        if self.occurrence_id is not None:
            result['occurrence_id'] = self.occurrence_id
        if self.source_runs is not None:
            result['source_runs'] = list(self.source_runs)
        return result


@dataclass(frozen=True)
class ReviewVerdict:
    """Structured result of the review stage. Does not print or exit."""
    work: str
    findings: tuple = ()
    reviewer: dict | None = None
    document: dict | None = None
    present: bool = False
    no_review: bool = False
    wrote: tuple = ()
    termbase_added: tuple = ()
    termbase_skipped: tuple = ()
    conflicts: tuple = ()
    errors: tuple = ()
    migrations: tuple = ()

    @property
    def open_findings(self):
        return tuple(f for f in self.findings if f.resolution == 'open')

    @property
    def counts(self):
        out = {k: 0 for k in SEVERITIES + RESOLUTIONS}
        for f in self.findings:
            out[f.severity] = out.get(f.severity, 0) + 1
            out[f.resolution] = out.get(f.resolution, 0) + 1
        return out

    @property
    def blocks_delivery(self):
        """A review must be present, valid and fully resolved to attest delivery."""
        return bool(self.errors) or (not self.present) or bool(self.open_findings)

    @property
    def exit_code(self):
        if self.errors:
            return 2
        return 1 if self.open_findings else 0

    @property
    def review_line(self):
        """The one line a delivery built without a reviser must carry."""
        if self.no_review:
            return ('REVIEW no reviser: this delivery was built with '
                    '--no-review. No second reader has checked its '
                    'terminology; a wrong term of art passes every gate.')
        if self.errors:
            return ('REVIEW invalid review: errors prevent this review from '
                    'attesting delivery; correct the reported errors and retry.')
        if not self.present:
            return ('REVIEW no reviser: no review.json on record for this '
                    'job.')
        if self.open_findings:
            return (f'REVIEW {len(self.open_findings)} finding(s) still open; '
                    'each must be accepted or rejected.')
        return ''

    def to_dict(self):
        from . import __version__
        return {'schema': SCHEMA, 'version': __version__,
                'work': self.work, 'present': self.present,
                'no_review': self.no_review,
                'blocks_delivery': self.blocks_delivery,
                'exit_code': self.exit_code,
                'review_line': self.review_line,
                'counts': self.counts,
                'reviewer': self.reviewer, 'document': self.document,
                'findings': [f.to_dict() for f in self.findings],
                'termbase_added': [list(p) for p in self.termbase_added],
                'termbase_skipped': list(self.termbase_skipped),
                'conflicts': [list(c) for c in self.conflicts],
                'errors': list(self.errors)}


def load_review(path):
    """(doc, [error]) from a review.json. Never raises on a malformed file."""
    try:
        with open(path, encoding='utf-8-sig') as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        return {}, [f'no review.json at {path}']
    except (OSError, ValueError) as exc:
        return {}, [f'{path} is not readable JSON: {exc}']
    if not isinstance(doc, dict):
        return {}, [f'{path} is not a JSON object']
    return doc, []


def validate(doc, mapping=None):
    """([ReviewFinding], [error]) — enforces the enums, migrates prose.

    A violation produces an error and drops that finding. Nothing is guessed:
    a resolution whose leading token is outside the enum is an error, because
    a wrong guess here decides whether a document is delivered.
    """
    errors = []
    if mapping is not None and mapping.format == FORMAT:
        expected = {'extraction_id': mapping.extraction_id, 'mapping_sha256': mapping.mapping_sha256}
        if doc.get('typography') != expected:
            return [], ['review typography binding is missing or differs from the current extraction/mapping']
    if not isinstance(doc.get('reviewer'), dict):
        errors.append('review.json has no "reviewer" block; a review with no '
                      'named reviewer is not a review')
    raw = doc.get('findings')
    if not isinstance(raw, list):
        errors.append('review.json has no "findings" list')
        return [], errors

    findings = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            errors.append(f'finding {i} is not an object')
            continue
        missing = [k for k in REQUIRED_FINDING_KEYS if k not in item]
        if missing:
            errors.append(f'finding {i} is missing {", ".join(missing)}')
            continue
        occurrence = item.get('occurrence_id')
        source_runs = item.get('source_runs')
        if (('occurrence_id' in item and (not isinstance(occurrence, str) or not occurrence)) or
                ('source_runs' in item and (not isinstance(source_runs, list) or not source_runs or
                 not all(isinstance(s, str) and s for s in source_runs) or len(set(source_runs)) != len(source_runs)))):
            errors.append(f'finding {i}: invalid occurrence_id or source_runs')
            continue
        if mapping is not None and mapping.format == FORMAT:
            if not isinstance(item['core'], str) or not isinstance(item['target'], str):
                errors.append(f'finding {i}: core and target must be plain strings')
                continue
            candidates = [t for t in mapping.targets if t.source_text == item['core']]
            metadata = (occurrence is None and source_runs is None and
                        isinstance(item['core'], str) and item['core'] in mapping.document_targets and
                        item['target'] == mapping.document_targets[item['core']])
            if (not metadata and (len(candidates) > 1 or item['core'] in mapping.document_targets) and
                    (occurrence is None or source_runs is None)):
                errors.append(f'finding {i}: shared core requires occurrence_id and source_runs for page text')
                continue
            if occurrence is not None:
                candidates = [t for t in candidates if t.occurrence_id == occurrence]
            if not metadata and (len(candidates) != 1 or item['target'] != ''.join(r.text for r in candidates[0].runs) or
                    (source_runs is not None and not set(source_runs).issubset(
                        {s for r in candidates[0].runs for s in r.source_runs}))):
                errors.append(f'finding {i}: core, target or source_runs do not match the named occurrence')
                continue
        if item['category'] not in CATEGORIES:
            errors.append(f'finding {i}: category {item["category"]!r} is not '
                          f'one of {"|".join(CATEGORIES)}')
            continue
        if item['severity'] not in SEVERITIES:
            errors.append(f'finding {i}: severity {item["severity"]!r} is not '
                          f'one of {"|".join(SEVERITIES)}')
            continue

        raw_res = item['resolution']
        note = str(item.get('resolution_note', '') or '')
        migrated = False
        if raw_res in RESOLUTIONS:
            resolution = raw_res
        else:
            token = leading_token(raw_res)
            if token not in RESOLUTIONS:
                errors.append(
                    f'finding {i}: resolution {str(raw_res)[:40]!r} does not '
                    f'begin with one of {"|".join(RESOLUTIONS)}')
                continue
            resolution = token
            migrated = True
            note = note or str(raw_res)

        findings.append(ReviewFinding(
            core=item['core'], target=item['target'],
            category=item['category'], subtype=item['subtype'],
            severity=item['severity'], detail=item['detail'],
            suggestion=item.get('suggestion'),
            resolution=resolution, resolution_note=note,
            term=item.get('term') or None,
            term_target=item.get('term_target') or None,
            migrated=migrated, occurrence_id=occurrence,
            source_runs=tuple(source_runs) if source_runs is not None else None))
    return findings, errors


def _pages_one_based(n):
    """The mapping stores 0-based pages; a reviser reads a printed form."""
    return int(n) + 1


def review_pairs_md(translations, segments, notes):
    """The file a second reader actually reads: every pair, in one place."""
    if isinstance(translations, MappingDocument):
        return _typography_pairs_md(translations, notes)
    mapping = translations.get('translations') or {}
    merges = translations.get('merges') or []
    overrides = translations.get('overrides') or []
    notices = translations.get('notices') or []
    lang = translations.get('lang') or '?'

    out = []
    out.append('# Review pairs')
    out.append('')
    out.append(f'Target language: `{lang}`.')
    out.append(f'{len(mapping)} cores, {len(merges)} merges, '
               f'{len(overrides)} overrides, {len(notices)} notice'
               f'{"" if len(notices) == 1 else "s"}.')
    out.append('')
    out.append('Page numbers here are **1-based**, as printed on the form. '
               'The mapping stores them 0-based; do not quote a mapping index '
               'back into a finding.')
    out.append('')
    out.append('Findings key on the **source string exactly as it appears '
               'below** — same case, same punctuation, same trailing space. '
               'It is the mapping key, and `review --ingest` matches on it.')
    out.append('')
    out.append('`<br>` in a target is a **line break the renderer draws**, '
               'not a character in the translation. Do not report it as an '
               'error, and do not include it in a `term_target`.')
    out.append('')

    if notes:
        out.append('## Identity')
        out.append('')
        out.append('From the job\'s NOTES.md. Read it before judging register.')
        out.append('')
        out.append('```')
        out.append(notes.strip())
        out.append('```')
        out.append('')

    out.append('## Cores')
    out.append('')
    out.append('| source | target |')
    out.append('| --- | --- |')
    for core, target in mapping.items():
        out.append(f'| {_cell(core)} | {_cell(target)} |')
    out.append('')

    if merges:
        out.append('## Merges')
        out.append('')
        out.append('Source lines joined into one flowed paragraph. The source '
                   'column is the original line break, kept.')
        out.append('')
        for m in merges:
            page = _pages_one_based(m.get('page', 0))
            lines = ' / '.join(m.get('lines') or [])
            out.append(f'- **page {page}** — {_cell(lines)}')
            out.append(f'  - → {_cell(m.get("html"))}')
        out.append('')

    if overrides:
        out.append('## Overrides')
        out.append('')
        out.append('A positioned multi-part placement: the parts are drawn at '
                   'the x positions given, on one line, in x order. This is '
                   'not a core kept in the source language.')
        out.append('')
        for o in overrides:
            page = _pages_one_based(o.get('page', 0))
            parts = sorted(o.get('parts') or [], key=lambda p: p.get('x', 0))
            drawn = '  '.join(str(p.get('text', '')) for p in parts)
            out.append(f'- **page {page}**, at `{_cell(o.get("contains"))}`')
            out.append(f'  - → {_cell(drawn)}')
        out.append('')

    if notices:
        out.append('## Notices')
        out.append('')
        out.append('Text this translation ADDS to the page. It is not in the '
                   'source; judge it as authored text.')
        out.append('')
        for n in notices:
            page = _pages_one_based(n.get('page', 0))
            text = str(n.get('text', '')).replace(NOTICE_BREAK, '\n  ')
            out.append(f'- **page {page}**, {n.get("size", "?")} pt')
            out.append(f'  {text}')
        out.append('')

    return '\n'.join(out) + '\n'


def _plain_cell(value):
    # Entity escaping preserves literal markup; Markdown punctuation must not
    # acquire formatting or split a table. Never change the authored mapping.
    value = html.escape(str(value), quote=False).replace('\n', ' ')
    for char in ('\\', '|', '`', '*', '_', '[', ']'):
        value = value.replace(char, '\\' + char)
    return value


def _typography_pairs_md(mapping, notes):
    segments = {s['occurrence_id']: s for s in mapping.extraction['segments']}
    out = ['# Review pairs', '', f'Target language: `{mapping.lang}`.', '',
           f'{len(mapping.targets)} occurrences. Page numbers below are 1-based.',
           'Each numbered run is literal text followed by its class/role and source-run references.',
           'Report the exact occurrence_id, source_runs, full source and full target. HTML is literal text.', '',
           f'Extraction: `{mapping.extraction_id}`', f'Mapping: `{mapping.mapping_sha256}`', '']
    if notes:
        out.extend(['## Identity', '', _plain_cell(notes), ''])
    out.extend(['| occurrence | page; origin | source | target | ordered runs |',
                '| --- | --- | --- | --- | --- |'])
    for target in mapping.targets:
        origin = segments[target.occurrence_id]['style_runs'][0]['origin']
        runs = '; '.join(f'{i + 1}: {_plain_cell(r.text)} ({r.font_class}/{r.font_role}; '
                         f'{", ".join(r.source_runs)})' for i, r in enumerate(target.runs))
        out.append(f'| {target.occurrence_id} | {target.page + 1}; {origin} | {_plain_cell(target.source_text)} | '
                   f'{_plain_cell("".join(r.text for r in target.runs))} | {runs} |')
    if mapping.document_targets:
        out.extend(['', '## Document metadata', '',
                    'Metadata findings use their exact source/target without page occurrence IDs. '
                    'Page findings sharing that source must include occurrence_id and source_runs.', '',
                    '| source | target |', '| --- | --- |'])
        out.extend(f'| {_plain_cell(s)} | {_plain_cell(t)} |' for s, t in mapping.document_targets.items())
    if mapping.source_font_resolutions:
        out.extend(['', '## Caller-authored source font resolutions', ''])
        out.extend(f'- {r["font_id"]}: {_plain_cell(r["reason"])}' for r in mapping.source_font_resolutions)
    return '\n'.join(out) + '\n'


def _cell(value):
    """A markdown table cell that cannot break the table.

    The break marker is a layout instruction, not a character in the
    translation — a reviser who reads it as text reports it as an error. It
    renders as <br> here because a real newline would break the table row.
    """
    if value is None:
        return '_(none)_'
    return (str(value).replace('|', '\\|')
            .replace(NOTICE_BREAK, '<br>').replace('\n', ' '))


# The reference template of references/review.md §3, with its slots named.
# Kept here verbatim so the generated prompt and the documented one cannot
# drift apart silently; the test diffs the MQM sentence against the reference.
PROMPT = """You are revising a translation of a {document_class} issued by \
{issuer}, from {source_language} into {target_language}, register: {register}.
A published translation of this same document {parallel}.

For each segment in {pairs}, report every error you find. Use the MQM
categories: terminology, accuracy (mistranslation, omission, addition,
untranslated), linguistic conventions (grammar, spelling, punctuation),
style, locale conventions (number, date, currency, address format),
audience appropriateness. Severity: critical (changes what the reader
must do), major, minor.

Do not rewrite anything you cannot justify. If a term is a form name,
statute, case number or anything the reader must write down, hand over or
search for, it is CORRECT for it to remain in {source_language}: do not
report it.

For a terminology finding, also return `term` and `term_target`: the
shortest source phrase that is wrong and the established rendering that
replaces it, not the whole segment. A finding without them cannot enter a
termbase, and will be reported as skipped rather than guessed at.

Return JSON only, matching this schema:
{schema}
"""

SCHEMA_BLOCK = """{
  "document": {"class": ..., "issuer": ..., "parallel_text": ...,
               "pair": "en->ja", "register": ...},
  "reviewer": {"kind": "human|model", "name": ..., "qualified_in_pair": true},
  "findings": [
    {"core": "source string exactly as it appears in the pairs file",
     "target": "the translation reviewed",
     "category": "terminology|accuracy|linguistic-conventions|style|"
                 "locale-conventions|audience-appropriateness",
     "subtype": "mistranslation|omission|addition|untranslated|...",
     "severity": "critical|major|minor",
     "detail": "what is wrong",
     "suggestion": "proposed target, or null",
     "resolution": "accepted|rejected|open",
     "resolution_note": "why, in prose — optional but valuable",
     "term": "terminology findings only: the short source term, or null",
     "term_target": "terminology findings only: its rendering, or null"}
  ],
  "summary": {"critical": 0, "major": 0, "minor": 0,
              "verdict": "ship|revise|do-not-ship", "remaining_risks": ...}
}"""


def _pair_languages(pair):
    """('en', 'ja') from 'en->ja'; the whole string twice if it has no arrow."""
    for sep in ('->', '→', '-->'):
        if sep in str(pair):
            a, b = str(pair).split(sep, 1)
            return a.strip(), b.strip()
    return str(pair), str(pair)


def review_prompt_md(document, pairs_path, mapping=None):
    """The prompt a first-pass model reviser is handed, slots filled."""
    document = document or {}
    source, target = _pair_languages(document.get('pair') or '?->?')
    parallel = document.get('parallel_text')
    if parallel is None:
        parallel_text = 'does not exist, or was not searched'
    else:
        parallel_text = f'exists: {parallel}'
    schema = SCHEMA_BLOCK
    if mapping is not None and mapping.format == FORMAT:
        binding = json.dumps({'extraction_id': mapping.extraction_id, 'mapping_sha256': mapping.mapping_sha256})
        schema = schema.replace('{\n', '{\n  "typography": ' + binding + ',\n', 1)
        schema = schema.replace('{"core":', '{"occurrence_id": "s0", "source_runs": ["s0/r0"],\n     "core":')
    return PROMPT.format(
        document_class=document.get('class') or '(class not stated)',
        issuer=document.get('issuer') or '(issuer not stated)',
        source_language=source, target_language=target,
        register=document.get('register') or '(register not stated)',
        parallel=parallel_text, pairs=pairs_path,
        schema=schema)


def termbase_rows(findings):
    """([(src, tgt)], [skipped core]) from the accepted terminology findings.

    A finding without BOTH `term` and `term_target` is skipped and reported.
    The source side is the column qa_check --glossary matches on, and diffing
    `target` against `suggestion` recovers only the target side, so there is
    nothing to infer the source side from that would be safe to gate on.
    """
    rows, skipped = [], []
    for f in findings:
        if f.category != 'terminology' or f.resolution != 'accepted':
            continue
        if f.term and f.term_target:
            rows.append((f.term, f.term_target))
        else:
            skipped.append(f.core)
    return rows, skipped


def append_termbase(path, rows):
    """([added], [(term, existing, new)]) — idempotent, never overwrites.

    Writes the two-column shape qa_check.load_glossary parses. A pair already
    present is not written twice; a source term already mapped to a DIFFERENT
    target is a conflict, reported and never written — whoever ran --ingest
    last does not get to decide which rendering gates the next job.
    """
    existing = {}
    if os.path.isfile(path):
        with open(path, encoding='utf-8-sig', newline='') as fh:
            for row in csv.reader(fh):
                if len(row) >= 2 and row[0].strip() and row[1].strip():
                    existing.setdefault(row[0].strip(), row[1].strip())

    added, conflicts = [], []
    for src, tgt in rows:
        src, tgt = str(src).strip(), str(tgt).strip()
        if not src or not tgt:
            continue
        if src in existing:
            if existing[src] != tgt:
                conflicts.append((src, existing[src], tgt))
            continue
        existing[src] = tgt
        added.append((src, tgt))

    if added:
        new_file = not os.path.isfile(path)
        # newline='' so csv writes \r\n itself and nothing doubles it; utf-8
        # without a BOM, which load_glossary's utf-8-sig reads either way.
        with open(path, 'a', encoding='utf-8', newline='') as fh:
            writer = csv.writer(fh)
            if new_file:
                writer.writerow(['source term', 'target term'])
            for pair in added:
                writer.writerow(list(pair))
    return added, conflicts


def run_review(work, ingest=None, notes=None, generate=True):
    """The whole stage. Reads, writes up to three files, returns a verdict.

    Prints nothing, raises nothing, exits nothing. A missing work directory is
    an error in the verdict, not an exception, and is never created here: this
    stage runs against a job someone else built.

    `generate=False` makes it read-only — no pairs file, no prompt, no
    termbase append. That is how `finish` asks "what is the review state of
    this job?" without a packaging step quietly rewriting the reviser's inputs
    underneath them.
    """
    errors, wrote = [], []
    if not os.path.isdir(work):
        return ReviewVerdict(work=work,
                             errors=(f'no such work directory: {work}',))

    tr_path = os.path.join(work, 'translations.json')
    translations, mapping = {}, None
    if os.path.isfile(tr_path):
        try:
            mapping = load_mapping(tr_path)
            translations = mapping.legacy if mapping.format != FORMAT else mapping
        except MappingError as exc:
            return ReviewVerdict(work=work, errors=(str(exc),))
        except (OSError, ValueError) as exc:
            errors.append(f'translations.json is not readable JSON: {exc}')
    else:
        errors.append(f'no translations.json in {work}')

    notes_text = ''
    notes_path = os.path.join(work, notes or 'NOTES.md')
    if os.path.isfile(notes_path):
        with open(notes_path, encoding='utf-8-sig') as fh:
            notes_text = fh.read()

    segments = None
    seg_path = os.path.join(work, 'segments.json')
    if os.path.isfile(seg_path):
        try:
            with open(seg_path, encoding='utf-8-sig') as fh:
                segments = json.load(fh)
        except (OSError, ValueError):
            segments = None

    if translations and generate:
        pairs_path = os.path.join(work, 'review_pairs.md')
        with open(pairs_path, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(review_pairs_md(translations, segments, notes_text))
        wrote.append(pairs_path)

    findings, reviewer, document = [], None, None
    present = False
    migrations = []
    added, skipped, conflicts = [], [], []

    if ingest:
        ing_path = ingest if os.path.isabs(ingest) else os.path.join(work, ingest)
        doc, load_errors = load_review(ing_path)
        errors.extend(load_errors)
        if not load_errors:
            present = True
            reviewer = doc.get('reviewer')
            document = doc.get('document')
            findings, validation_errors = validate(doc, mapping)
            errors.extend(validation_errors)
            if mapping is not None and mapping.format == FORMAT and validation_errors:
                findings = []
            migrations = [(f.core, f.resolution_note, f.resolution)
                          for f in findings if f.migrated]
            rows, skipped = termbase_rows(findings)
            if rows and generate:
                glossary = os.path.join(work, 'glossary.csv')
                added, conflicts = append_termbase(glossary, rows)
                if added:
                    wrote.append(glossary)

    if generate:
        prompt_doc = (document or (translations.get('document') if isinstance(translations, dict) else None))
        prompt_path = os.path.join(work, 'review_prompt.md')
        with open(prompt_path, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(review_prompt_md(prompt_doc, 'review_pairs.md', mapping))
        wrote.append(prompt_path)

    return ReviewVerdict(
        work=work, findings=tuple(findings), reviewer=reviewer,
        document=document, present=present, wrote=tuple(wrote),
        termbase_added=tuple(added), termbase_skipped=tuple(skipped),
        conflicts=tuple(conflicts), errors=tuple(errors),
        migrations=tuple(migrations))
