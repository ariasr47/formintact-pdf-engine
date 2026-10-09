#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 5 of pdf-translate: automated gates. Run after EVERY build; exit 0 = pass.

1. field parity   set of (fully-qualified name, type) identical to original;
                  extra fields allowed only with --allow-extra-prefix
2. fill roundtrip a text value in the TARGET script + a checkbox survive
                  save/reopen (skipped if the PDF has no fields)
3. ink density    per-page dark-pixel ratio vs original within [--min-ink, hard
                  ceiling 3x] — catches invisible glyphs and blank regions
4. leak scan      keyed to the SOURCE document's script (detected from its
                  text layer; Han + kana count as one family, CJK). When the
                  output is in another script: three or more consecutive
                  source-script words (>= 4 letters for Latin, >= 2 otherwise),
                  or a run of six or more characters of a spaceless script,
                  is untranslated running text and fails; shorter leftovers
                  are reported for review only, because official form names
                  (Schedule C, Form W-2), statutes and proper nouns are
                  supposed to survive translation intact. When source and
                  output share a space-delimited script the scan uses the
                  document's own words (--source-words-from, or harvested
                  from the original automatically). A shared spaceless
                  family (zh->ja) is judged by gate 21 when the mapping's
                  lang names the target; else one REVIEW line.
                  A run that equals the ORIGINAL's /Title is kept, not
                  counted: the compliance notice names the form it
                  translates, in the source language, and the reader has
                  to find that form. So is a run equal to a multi-word
                  --allow entry, which matches a whole run and never its
                  words. A longer run that merely contains either is
                  still a leak; kept runs are printed as a note.
5. text layer     a page with visible ink or an embedded image but no
                  extractable text is treated as a scan and fails (scans are out of
                  scope; an OCR layer is refused by gate 11).
                  Ink ratio is skipped when the original has negligible ink,
                  instead of failing a pale/blank page with ratio 0.00.
6. placement      if --translations is given, every non-passthrough target
                  string (length >= 2) must appear in the output text
                  layer, character for character apart from wrapping
                  whitespace (a re-flowed merge wraps, so the layer has a
                  newline where the paragraph has a space). retypeset
                  canonicalizes /ToUnicode, so no NBSP or hyphen folding
                  is applied here any more.
                  Omit the flag: this gate does not run. Does not judge
                  whether a target is the right term.
7. button chrome  with --translations: a visible original pushbutton caption
                  still in the output (get_text() sees /AP) must be skip'd or
                  rewritten (--captions). Otherwise FAIL and list the caption.
                  Omit the flag: this gate does not run. Field count stays
                  the field-parity gate — do not add widgets.
8. identifiers    with --translations: quoted and form-name write/find/say
                  spans from the original page (shipped write_find_say_hits)
                  must appear in the output text layer unless listed in
                  allow_translate. Missing ones FAIL and are listed.
                  Omit the flag: this gate does not run. url-or-email is not
                  a hard fail.
9. empty targets  with --translations: a non-skip, non-null translations
                  value (also merge html / override parts) whose
                  NBSP-normalized strip is empty FAILs and names the core.
                  Omit the flag: this gate does not run. Length-1 non-empty
                  placement skip is unchanged. JSON null is retypeset's job.
10. caption width with --translations: output pushbutton /MK /CA wider
                  than the widget rect (helv text_length, pad 2 pt each
                  side) FAILs listing field name and caption. Omit the
                  flag: this gate does not run. Measures output /CA only.
11. invisible text a page of the ORIGINAL whose text is not what the reader
                  sees (stripping it changes under 3% of the text-span
                  area in pixels: an OCR layer over a scan, or text under
                  an image) FAILs. Always runs. Refusal, not a fix.
17. shaped marks  with --translations: every target whose script needs
                  shaping (Arabic family, Indic, Thai, Lao, Khmer, Myanmar,
                  Tibetan) must appear in an /ActualText span. retypeset
                  marks every Story-engine run that way, so a shaped target
                  that is missing was drawn glyph by glyph. This is the
                  Indic check: a broken conjunct leaves no code-point tell,
                  but who drew the run does.
12. unshaped Arabic an output page whose Arabic letters are all isolated
                  presentation forms (three or more joining letters, no
                  initial or medial form) was drawn letter by letter and
                  FAILs. Reads the glyph forms, not /ActualText. Silent
                  when the page has no Arabic presentation forms.
18. conjunct shaping every embedded face on a page that draws a conjunct-
                  forming script (Devanagari, Bengali, Tamil, Khmer, Myanmar)
                  is probed: that script's cluster string, rendered off the
                  extracted font program glyph by glyph and again through
                  the Story engine, must lose glyphs (क्षत्रिय 8 -> 4). No
                  loss means the face has no usable GSUB and every conjunct
                  drawn with it is broken: FAIL. A face that lacks the probe
                  glyphs is REVIEW (cannot attest; prepare_font adds them).
                  Thai, Lao and Hebrew niqqud have no count tell and are
                  REVIEW, never PASS; Arabic stays with gate 12. Always
                  runs; silent when no such script is on the page.
13. override markers with --translations: an override replaces its whole
                  span, so its parts must keep the source segment's list
                  marker (d.) and tail ($). Needs segments.json: --segments
                  PATH, else the file beside translations.json; without
                  one the gate is SKIPped, not failed.
14. /Opt parity   a choice field's export values (the /Opt entry, or its
                  first element once entries are [export, display]) must be
                  byte-identical to the original's, in the same order.
                  Translating a dropdown changes the display half only;
                  changing an export breaks /V and everything submitted.
                  Always runs, like field parity. Silent with no choice
                  fields.
22. widget text   a tooltip (/TU), dropdown display label or text-field
                  value or default that has a letter in it and reads the
                  same in the output as in the original is REVIEW: it never
                  reaches a content stream, so the leak scan cannot see it.
                  Kept on purpose, and silent, when the widget-text mapping
                  deletes the field's key, leaves the slot out, or gives a
                  target equal to its source. The mapping is --widget-text
                  PATH, else the widget_text.json beside --translations; a
                  null target is not a decision. Always runs; silent when
                  no field carries such text.
16. document meta with --translations: /Lang must match the mapping's
                  "lang", a translated /Title and translated outline titles
                  must actually be in the output, and the output must not
                  carry the orphaned /StructTreeRoot (its tags describe
                  stripped text). No "lang" in the mapping is a REVIEW
                  line, not a failure.
15. canonical text the output text layer must report the code points
                  somebody actually wrote. NBSP, soft hyphen, U+2010/U+2011
                  and CJK Compatibility Ideographs that are in neither the
                  original nor the authored mapping are ToUnicode drift and
                  FAIL: the page looks right while the words in it cannot
                  be searched or copied. Always runs.
19. kinsoku       no drawn line of CJK text begins with a character JIS X
                  4051 / JLREQ forbids at line start, or ends with one it
                  forbids at line end (half-width forms included). Lines
                  are grouped into stacks by geometry, not MuPDF's blocks,
                  so a stack's first line is never a line-start violation
                  and its last never a line-end one; a single-character
                  line and a marker that begins two or more lines are
                  exempt. REVIEW, never FAIL: a form label can look like a
                  hand-split line. Always runs; silent when no page has an
                  eligible stack.
20. han forms     on a page that draws Japanese or Chinese text, every
                  embedded face is rendered off its font program and
                  compared, pixel for pixel, with the Noto reference of the
                  language's convention (lang: ja -> Japanese; zh, zh-Hans
                  -> Simplified Chinese) and with the other convention's,
                  both instanced at the face's weight. Own reference <= 0.02
                  and the other >= 0.10 on two of 直 骨 海: PASS; the
                  reverse: FAIL. No lang, no reference for the convention
                  (zh-Hant, ko), a face without the probe glyphs, a family
                  that is not Noto, or no reference directory: REVIEW,
                  never a guess. Only the faces that drew CJK glyphs on
                  the page are judged; an embedded face that drew none
                  neither fails nor attests. A non-CJK /Lang with no
                  mapping lang is REVIEW. Silent when no page draws CJK
                  text or lang is not a CJK language. --reference-fonts
                  DIR names the references (default: tests/fonts of a
                  checkout).
21. leak-cjk      when source and output share the spaceless CJK family and
                  the mapping's lang names a Han convention (ja, zh-Hans,
                  zh-Hant), every drawn line is scanned for characters that
                  cannot belong to that target — kana in a Chinese one, Han
                  outside its repertoire (cp932 / GB 2312 / Big5), NFKC
                  first. Six or more in a line FAIL (an echoed sentence),
                  one to five REVIEW (a name in kana, a rare character —
                  allowlist with --allow). The source's convention is read
                  the same way; Traditional Chinese -> Japanese is not
                  separable and keeps the REVIEW line. Kept runs and the
                  original's /Title are skipped.

These gates catch structural failures. They do NOT catch visual defects —
after they pass you still render pages side-by-side and look at them.

Usage:
  python3 verify.py ORIGINAL.pdf TRANSLATED.pdf \
      [--fill-text "value in target script"] [--allow WORD,"Multi Word Name",...] \
      [--min-ink 0.4] [--source-regex "[A-Za-z]{4,}"] \
      [--source-words-from segments.json] [--allow-extra-prefix tr_] \
      [--translations translations.json] [--segments segments.json] \
      [--widget-text widget_text.json] [--report verify_report.json]
      [--fail-on-review]
"""
import json
import logging
import os
import re
import stat
import sys
import tempfile
import time
import unicodedata
from dataclasses import dataclass

import pymupdf

from ._console import console, _arg, configure_cli_streams
from ._lang import declared_language, same_language
from ._pixels import INK_SKIP, VISIBLE_INK, dark_pixels
from .extract_segments import write_find_say_hits
from .strip_text import choice_exports, invisible_text_pages

# Every gate line goes through here. `verify()` attaches the console handler
# and the bytes are what `print` wrote; `run_verify()` attaches nothing and is
# silent without touching the process's stdout.
# `python -m` runs this module as __main__; keep its log under console().
log = logging.getLogger('pdf_translate.verify')
from . import cjk_tell, han_forms, shaping_probe

# Unicode letter ranges per script (goal 15). A range table, not an ICU
# dependency: the scan only needs to tell the source script from the target
# one. Han, hiragana and katakana are one family because Japanese mixes them.
SCRIPT_RANGES = {
    'Latin': ((0x41, 0x5A), (0x61, 0x7A), (0xAA, 0xAA), (0xBA, 0xBA), (0xC0, 0x24F),
              (0x1E00, 0x1EFF), (0x2C60, 0x2C7F), (0xA720, 0xA7FF),
              (0xFF21, 0xFF3A), (0xFF41, 0xFF5A)),
    'Greek': ((0x370, 0x3FF), (0x1F00, 0x1FFF)),
    'Cyrillic': ((0x400, 0x52F), (0x2DE0, 0x2DFF), (0xA640, 0xA69F)),
    'Armenian': ((0x530, 0x58F),),
    'Hebrew': ((0x590, 0x5FF), (0xFB1D, 0xFB4F)),
    'Arabic': ((0x600, 0x6FF), (0x750, 0x77F), (0x8A0, 0x8FF), (0xFB50, 0xFDFF),
               (0xFE70, 0xFEFF)),
    'Devanagari': ((0x900, 0x97F), (0xA8E0, 0xA8FF)),
    'Bengali': ((0x980, 0x9FF),),
    'Gurmukhi': ((0xA00, 0xA7F),),
    'Gujarati': ((0xA80, 0xAFF),),
    'Tamil': ((0xB80, 0xBFF),),
    'Telugu': ((0xC00, 0xC7F),),
    'Kannada': ((0xC80, 0xCFF),),
    'Malayalam': ((0xD00, 0xD7F),),
    'Sinhala': ((0xD80, 0xDFF),),
    'Thai': ((0xE00, 0xE7F),),
    'Lao': ((0xE80, 0xEFF),),
    'Tibetan': ((0xF00, 0xFFF),),
    'Myanmar': ((0x1000, 0x109F),),
    'Georgian': ((0x10A0, 0x10FF),),
    'Khmer': ((0x1780, 0x17FF),),
    'Hangul': ((0x1100, 0x11FF), (0x3130, 0x318F), (0xA960, 0xA97F), (0xAC00, 0xD7FF)),
    'CJK': ((0x3040, 0x30FF), (0x31F0, 0x31FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
            (0xF900, 0xFAFF), (0xFF66, 0xFF9F), (0x20000, 0x2FA1F)),
}
# No word spaces: "three words" is approximated by six consecutive characters.
SPACELESS_SCRIPTS = {'CJK', 'Thai', 'Lao', 'Khmer', 'Myanmar', 'Tibetan'}
# JIS X 4051 / W3C JLREQ ("Requirements for Japanese Text Layout", Appendix A
# character classes) line-breaking prohibitions, transcribed from those
# public references and from nothing else. Chinese layout (GB/T 15834)
# forbids the same punctuation at the same positions, so the gate judges
# every CJK line, not only Japanese ones. The Story engine breaks by UAX #14
# and never violates these (measured: dev/probes/cjk_kinsoku_probe.py); a
# target split by hand across source lines can.
# Not covered: cl-12 prefix abbreviations (￥＄＃№, line-end) and cl-13 postfix
# abbreviations (％‰℃°′″, line-start).
#
# Characters that may not BEGIN a line:
KINSOKU_LINE_START = frozenset(
    # cl-02 closing brackets 」』）］｝〉》】〕〙〗’”｠⦆ and half-width ｣
    '\u300d\u300f\uff09\uff3d\uff5d\u3009\u300b\u3011\u3015\u3019\u3017'
    '\u2019\u201d\uff60\uff63\u2986'
    # cl-03 hyphens ‐ 〜 ゠ – and the full-width tilde ～ Windows writes for 〜
    '\u2010\u301c\u30a0\u2013\uff5e'
    # cl-04 dividing punctuation ？！‼⁇⁈⁉
    '\uff1f\uff01\u203c\u2047\u2048\u2049'
    # cl-05 middle dots ・：； and half-width ･
    '\u30fb\uff1a\uff1b\uff65'
    # cl-06 full stops 。． and half-width ｡
    '\u3002\uff0e\uff61'
    # cl-07 commas 、， and half-width ､
    '\u3001\uff0c\uff64'
    # cl-09 iteration marks 々〻ゝゞヽヾ
    '\u3005\u303b\u309d\u309e\u30fd\u30fe'
    # cl-10 prolonged sound mark ー and half-width ｰ
    '\u30fc\uff70'
    # cl-11 small kana ぁぃぅぇぉっゃゅょゎゕゖ, ァィゥェォッャュョヮヵヶ, half-width ｧ..ｯ
    '\u3041\u3043\u3045\u3047\u3049\u3063\u3083\u3085\u3087\u308e\u3095\u3096'
    '\u30a1\u30a3\u30a5\u30a7\u30a9\u30c3\u30e3\u30e5\u30e7\u30ee\u30f5\u30f6'
    '\uff67\uff68\uff69\uff6a\uff6b\uff6c\uff6d\uff6e\uff6f'
    # cl-11 small katakana extension ㇰ..ㇿ (Ainu), counted as CJK letters by SCRIPT_RANGES
    '\u31f0\u31f1\u31f2\u31f3\u31f4\u31f5\u31f6\u31f7\u31f8\u31f9\u31fa\u31fb\u31fc\u31fd\u31fe\u31ff'
)
# ... and that may not END one: cl-01 opening brackets 「『（［｛〈《【〔〘〖‘“｟⦅
# and half-width ｢
KINSOKU_LINE_END = frozenset(
    '\u300c\u300e\uff08\uff3b\uff5b\u3008\u300a\u3010\u3014\u3018\u3016'
    '\u2018\u201c\uff5f\uff62\u2985'
)
# The bullets a document sets on purpose at the start of two or more lines
# of a list. Only these can be a marker; a full stop that begins two lines
# of a stack is two hand-split breaks, not a list.
KINSOKU_LIST_MARKERS = frozenset('\u30fb\uff65')   # ・ ･
SPACELESS_RUN_CHARS = 6
# Latin keeps today's 4-letter word floor; other scripts have short real words.
MIN_WORD_LETTERS = {'Latin': 4}


@dataclass(frozen=True)
class Finding:
    """Where a gate outcome points: a 1-based page or None, the thing it
    names (field, font, script, token, …) and the run or detail, untruncated."""
    page: int | None
    where: str
    text: str

    def to_dict(self):
        return {'page': self.page, 'where': self.where, 'text': self.text}


@dataclass(frozen=True)
class GateResult:
    """One named gate: status is PASS, FAIL, SKIP, or REVIEW."""
    name: str
    status: str
    message: str = ''
    findings: tuple = ()

    def to_dict(self):
        return {'name': self.name, 'status': self.status, 'message': self.message,
                'findings': [f.to_dict() for f in self.findings]}


# Every name a GateResult can carry, in the order the gates run. A gate
# that prints nothing for a job (no fields, no Arabic, no --translations)
# records nothing either: the verdict mirrors the console, line for line.
GATE_NAMES = (
    'field-parity', 'opt-export-parity', 'widget-text', 'fill-roundtrip', 'page-parity',
    'extractable-text', 'ink-ratio', 'visible-text', 'canonical-text',
    'arabic-letterforms', 'conjunct-shaping', 'kinsoku', 'han-forms',
    'leak-scan', 'leak-cjk', 'leak-running', 'leak-isolated',
    'empty-targets', 'placement', 'shaped-actualtext',
    'button-captions', 'caption-width', 'override-markers',
    'metadata', 'metadata-lang', 'scaled-runs', 'identifiers', 'typography',
)


@dataclass(frozen=True)
class VerifyVerdict:
    """Structured result of the structural gates. Does not print or exit."""
    exit_code: int
    gates: tuple
    original: str = ''
    output: str = ''
    fail_on_review: bool = False

    @property
    def ok(self):
        return self.exit_code == 0

    def to_dict(self):
        from . import __version__
        return {'schema': 1, 'version': __version__,
                'original': self.original, 'output': self.output,
                'exit_code': self.exit_code,
                'fail_on_review': self.fail_on_review,
                'gates': [g.to_dict() for g in self.gates]}


def script_of(ch):
    """Script name for a letter; None for digits, punctuation, unknown scripts."""
    if not unicodedata.category(ch).startswith('L'):
        return None
    o = ord(ch)
    for name, ranges in SCRIPT_RANGES.items():
        for a, b in ranges:
            if a <= o <= b:
                return name
    return None


def dominant_script(text):
    """The script most of the letters in text belong to, or None if no letters."""
    counts = {}
    for ch in text or '':
        name = script_of(ch)
        if name:
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)


def _letter_bag(line):
    return tuple(sorted(ch for ch in line if unicodedata.category(ch).startswith('L')))


def target_script(out_text, source_text):
    """Dominant script of the output, ignoring lines that also occur in the source.

    Leaks are source text, so a large leftover would make the output's
    dominant script look like the source's and hide itself. Lines that occur
    in the source, verbatim or as the same bag of letters (the text layer of
    a re-typeset RTL line can come back in another order), are set aside
    before judging what the target is.
    """
    src_norm = ' '.join(normalize_ws_nbsp(source_text or '').split())
    src_bags = set()
    for line in (source_text or '').splitlines():
        bag = _letter_bag(line)
        if len(bag) >= 2:
            src_bags.add(bag)
    keep = []
    for line in (out_text or '').splitlines():
        norm = ' '.join(normalize_ws_nbsp(line).split())
        if not norm or norm in src_norm:
            continue
        bag = _letter_bag(line)
        if len(bag) >= 2 and bag in src_bags:
            continue
        keep.append(line)
    return dominant_script(chr(10).join(keep))


def script_class(script):
    """Regex character class matching one letter of the script."""
    parts = []
    for a, b in SCRIPT_RANGES[script]:
        parts.append(chr(a) if a == b else f'{chr(a)}-{chr(b)}')
    return '[' + ''.join(parts) + ']'


def source_words_from_text(text, allow, script='Latin'):
    """Unique source words of the given script from text (lowercased)."""
    if script == 'Latin':
        words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'\-]{3,}", text or '')}
    else:
        minlen = MIN_WORD_LETTERS.get(script, 2)
        words = {w.lower() for w in re.findall(script_class(script) + '{%d,}' % minlen, text or '')}
    return words - set(allow)


WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")
RUN = re.compile(r"[A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){2,}")
# VISIBLE_INK and INK_SKIP, the dark-pixel floors at 72 dpi, live in
# _pixels with the count they apply to; extract and strip use the same ones.


def source_words_from_segments(path, allow, script='Latin'):
    """Unique source words of the document's script from its segments."""
    with open(path, encoding='utf-8') as f:
        segs = json.load(f)['segments']
    return source_words_from_text('\n'.join(s['text'] for s in segs), allow, script)


# Fold table for comparisons where the SOURCE side can drift and we cannot
# rewrite it: identifier spans read out of the original, and authored values
# that are whitespace. MuPDF builds ToUnicode by reverse-mapping the font
# cmap, so a source PDF may report NBSP for space, U+00AD or U+2010/U+2011
# for hyphen, whatever the author typed (audit finding H4). Our OWN output no
# longer needs this: retypeset rewrites /ToUnicode to the authored code
# points and the canonical-text-layer gate fails what is left, so the
# placement gate compares without folding.
_PLACEMENT_FOLD = str.maketrans({
    '\xa0': ' ',
    '\u202f': ' ',
    '\u2007': ' ',
    '\u2010': '-',
    '\u2011': '-',
    '\u00ad': '-',
})


def normalize_ws_nbsp(text):
    """NBSP/figure spaces → ASCII space; U+2010/U+2011/U+00AD → ASCII hyphen.

    For comparisons against text read out of the ORIGINAL, whose drift is
    not ours to rewrite. Placement compares with normalize_ws instead.
    """
    if not text:
        return ''
    return text.translate(_PLACEMENT_FOLD)


# Wrapping whitespace only: newline, carriage return, tab, runs of ASCII
# spaces. Deliberately NOT NBSP or the other exotic spaces — drift is the
# canonical-text-layer gate's business, and folding it here is what used to
# hide it.
_WRAP_WS = re.compile(r'[ \t\r\n]+')


def normalize_ws(text):
    """Collapse wrapping whitespace; fold nothing else.

    A merge is re-flowed by the Story engine, so a paragraph that wraps
    comes back from get_text() with a newline where the authored string
    has a space. Comparing those verbatim fails a build whose paragraph is
    placed perfectly — the gate would be the bug. Every other character
    still has to match what was authored.
    """
    return _WRAP_WS.sub(' ', text or '').strip()


# Code points that a reverse-mapped ToUnicode reports for a glyph the author
# spelled with something else: NBSP for space, soft hyphen and U+2010/U+2011
# for '-', CJK Compatibility Ideographs for common kanji (立 as U+F9F7).
DRIFT_RANGES = (
    (0x00A0, 0x00A0), (0x00AD, 0x00AD), (0x202F, 0x202F), (0x2007, 0x2007),
    (0x2010, 0x2011), (0xF900, 0xFAFF), (0x2F800, 0x2FA1F),
    # Story-engine ligatures (row 24): U+FB01 when the font carries the
    # ligature's code point, U+007F when a subset dropped it. Either one
    # means "oficina" is not in the layer and nobody can search for it.
    (0x007F, 0x007F), (0xFB00, 0xFB06),
)


def drifted_characters(out_text, authored_text):
    """[(char, count)] drift-prone code points nobody authored.

    A character that is in the original, or in an authored string, is the
    author's business and passes. What is left came from the text layer
    itself, and means retypeset's /ToUnicode rewrite did not reach that
    font: the page looks right and every string in it is a character the
    reader cannot search for or copy.
    """
    ok = set(authored_text or '')
    counts = {}
    for ch in out_text or '':
        if ch in ok:
            continue
        o = ord(ch)
        if any(a <= o <= b for a, b in DRIFT_RANGES):
            counts[ch] = counts.get(ch, 0) + 1
    return sorted(counts.items())


# retypeset writes this beside its output: every run it shipped below
# source size, so the author can name them without re-running the build
# (row 25). Absent means a build from before it existed, not a pass.
SCALE_REPORT = 'scale_report.json'


def scale_report_for(judged):
    """The scale report beside `judged`, [] if empty, None if absent.

    Reads **both** shapes. v58 and later write
    `{"schema": 1, "version": ..., "runs": [...]}`; every build before that
    wrote a bare list. A report written by an older build must still verify —
    that is the same promise the SKIP status already makes for a build from
    before the report existed at all — so the envelope is a break in what is
    written, not in what can be read.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(judged)),
                        SCALE_REPORT)
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(data, dict):
        if type(data.get('schema')) is not int or data['schema'] not in (1, 2):
            return None
        runs = data.get('runs')
        return runs if isinstance(runs, list) else None
    return data if isinstance(data, list) else None


# Same narrow inline markup retypeset accepts in a single-line target.
INLINE_TAGS = re.compile(r'</?(?:b|i|em|strong)\s*/?>', re.I)


def strip_inline_markup(text):
    return INLINE_TAGS.sub('', text or '')


def collect_translation_targets(conf, exclude_cores=None):
    """Authored strings that should land in get_text() (not skip / not passthrough).

    translations.json values are already the non-passthrough mapping. '‖' is a
    retypeset weight split, not a glyph, so each side is searched separately.
    Merges and overrides are also placed text.
    """
    from .mapping import MappingDocument
    if isinstance(conf, MappingDocument):
        if conf.format == 'typography-1':
            return [''.join(run.text for run in target.runs) for target in conf.targets]
        conf = conf.legacy
    skip = set(conf.get('skip') or [])
    exclude = set(exclude_cores or [])
    targets = []
    for core, tgt in (conf.get('translations') or {}).items():
        if core in skip or core in exclude or tgt is None:
            continue
        for part in strip_inline_markup(str(tgt)).split('‖'):
            targets.append(part)
    for merge in conf.get('merges') or []:
        text = _merge_plain_text(merge.get('html'))
        if text:
            targets.append(text)
    for override in conf.get('overrides') or []:
        for part in override.get('parts') or []:
            t = part.get('text')
            if t:
                targets.append(t)
    # A compliance notice is placed text like any other (P7): if the author
    # wrote it and retypeset drew it, the gate wants it in the layer.
    for notice in conf.get('notices') or []:
        for part in str(notice.get('text') or '').split('‖'):
            if part.strip():
                targets.append(part.strip())
    return targets


def load_widget_text_mapping(widget_text, translations):
    """(mapping or None, where it came from or why it was not used).

    `widget_text` is a path or an already-loaded dict; without one, the
    `widget_text.json` beside `translations` is read, as segments.json is.
    """
    if isinstance(widget_text, dict):
        return widget_text, 'the widget-text mapping passed in'
    path = widget_text
    if path is None and translations:
        beside = os.path.join(os.path.dirname(os.path.abspath(translations)),
                              'widget_text.json')
        path = beside if os.path.isfile(beside) else None
    if path is None:
        return None, 'no widget-text mapping was read'
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        return None, f'{path} was not read: {exc}'
    if not isinstance(data, dict):
        return None, f'{path} is not a JSON object'
    return data, path


def widget_text_left(orig, trans, mapping=None):
    """(checked, [(field, label, text)]): how many of the original's widget
    strings have a letter in them, and those the output still shows in the
    source language without having been kept on purpose.

    A tooltip, a dropdown's display label or a text field's value or
    default counts when the output's string equals the original's and has
    a letter in it. The widget-text mapping marks what was kept on purpose:
    a field whose key was deleted, a slot the entry leaves out, or a target
    equal to its source (data returned as itself). A null target is not a
    decision, so it does not excuse anything. Raises WidgetTextError when
    the mapping's keys cannot be resolved against the original.
    """
    import pikepdf
    from .strip_text import (authored_widget_targets, resolve_widget_text_keys,
                             widget_text_scaffold)
    before, after = widget_text_scaffold(orig), widget_text_scaffold(trans)
    names = None
    if mapping is not None:
        with pikepdf.open(orig) as pdf:
            names = resolve_widget_text_keys(pdf, mapping)

    def has_letter(text):
        return any(ch.isalpha() for ch in text)

    checked, left = 0, []
    for field, entry in before.items():
        texts = [entry[s]['source'] for s in ('tooltip', 'value', 'default') if s in entry]
        texts += [o['source'] for o in entry.get('options', [])]
        checked += sum(1 for text in texts if has_letter(text))
        now = after.get(field)
        if now is None:
            continue   # field parity reports a missing field
        unchanged = []
        for slot in ('tooltip', 'value', 'default'):
            if slot in entry and (now.get(slot) or {}).get('source') == entry[slot]['source']:
                unchanged.append((slot, slot, entry[slot]['source']))
        shown = {o['export']: o['source'] for o in now.get('options', [])}
        for option in entry.get('options', []):
            if shown.get(option['export']) == option['source']:
                unchanged.append((('option', option['export']),
                                  f'option {option["export"]}', option['source']))
        unchanged = [u for u in unchanged if has_letter(u[2])]
        if not unchanged:
            continue
        if names is not None and field not in names:
            continue   # the key was deleted: left in place on purpose
        targets = (authored_widget_targets(mapping[names[field]])
                   if names is not None else None)
        for slot, label, text in unchanged:
            if targets is not None and (slot not in targets or targets[slot] == text):
                continue
            left.append((field, label, text))
    return checked, left


def document_metadata_misses(odoc, jdoc, conf):
    """[(what, detail)] document-level strings the translation left behind.

    /Lang, /Title and the outline are not page text, so nothing in the
    strip-and-retypeset path touches them. They are what the window
    caption, the bookmarks pane and every screen reader read.
    """
    from .mapping import MappingDocument
    if isinstance(conf, MappingDocument) and conf.format == 'typography-1':
        misses = []
        got = declared_language(jdoc)
        if got.lower() != conf.lang.lower():
            misses.append(('lang', f'expected {conf.lang}; output declares {got or "nothing"}'))
        title = (odoc.metadata or {}).get('title') or ''
        if title in conf.document_targets and (jdoc.metadata or {}).get('title') != conf.document_targets[title]:
            misses.append(('title', 'document title does not match its authored target'))
        wanted = [[entry[0], conf.document_targets.get(entry[1], entry[1]), entry[2]]
                  for entry in odoc.get_toc(simple=True)]
        if jdoc.get_toc(simple=True) != wanted:
            misses.append(('outline', 'bookmarks do not match their authored targets'))
        if jdoc.xref_get_key(jdoc.pdf_catalog(), 'StructTreeRoot')[0] != 'null':
            misses.append(('struct-tree', 'orphaned source structure tree remains'))
        return misses
    T = conf.get('translations') or {}
    misses = []

    want_lang = (conf.get('lang') or '').strip()
    got_lang = declared_language(jdoc)
    # The same language, either way round: the region is ignored, and a
    # script counts only when both tags declare one (zh-Hans is not
    # zh-Hant). v79 passed `es` against `es-US` only because PyMuPDF
    # shortened the stored tag.
    if want_lang:
        if not got_lang or not same_language(got_lang, want_lang):
            misses.append(('lang', f'translations.json asks for '
                                   f'"{want_lang}", output declares '
                                   f'"{got_lang or "nothing"}"'))

    otitle = ((odoc.metadata or {}).get('title') or '').strip()
    jtitle = ((jdoc.metadata or {}).get('title') or '').strip()
    if otitle and T.get(otitle) and jtitle == otitle:
        misses.append(('title', f'/Title is still the source string: '
                                f'{otitle[:60]}'))

    for entry in jdoc.get_toc(simple=True) or []:
        title = str(entry[1]) if len(entry) > 1 else ''
        if title and T.get(title):
            misses.append(('outline', f'bookmark still reads: {title[:60]}'))

    try:
        cat = jdoc.pdf_catalog()
        if cat and jdoc.xref_get_key(cat, 'StructTreeRoot')[0] != 'null':
            misses.append((
                'struct-tree',
                'the output still carries /StructTreeRoot; those tags '
                'describe text that was stripped, so assistive technology '
                'reads a structure that no longer matches the page'))
    except Exception:
        pass
    return misses


def _authored_text_is_empty(raw):
    """True when normalize + strip leaves nothing. Length-1 'Z' is not empty."""
    if raw is None:
        return False
    plain = strip_inline_markup(str(raw))
    return len(normalize_ws_nbsp(plain).strip()) == 0


def _merge_plain_text(html):
    text = html or ''
    text = re.sub(r'<br\s*/?>', ' ', text, flags=re.I)
    return re.sub(r'<[^>]+>', '', text)


def empty_translation_targets(conf):
    """Labels for authored empty/whitespace values (not skip, not null).

    Returns cores / merge first lines / override contains so FAIL can name
    them. Whitespace includes NBSP. Skip entries are omitted even if the
    mapped string is empty.
    """
    skip = set(conf.get('skip') or [])
    empty = []
    seen = set()

    def add(label):
        if label and label not in seen:
            seen.add(label)
            empty.append(label)

    for core, tgt in (conf.get('translations') or {}).items():
        if core in skip or tgt is None:
            continue
        if _authored_text_is_empty(tgt):
            add(core)
    for merge in conf.get('merges') or []:
        if _authored_text_is_empty(_merge_plain_text(merge.get('html'))):
            lines = merge.get('lines') or []
            add(lines[0] if lines else '(merge)')
    for override in conf.get('overrides') or []:
        for part in override.get('parts') or []:
            t = part.get('text')
            if t is None:
                continue
            if _authored_text_is_empty(t):
                add(override.get('contains') or '(override)')
    return empty


_ACTUALTEXT_HEX = re.compile(rb'/ActualText\s*<\s*([0-9A-Fa-f]+)\s*>')


def extract_actualtext(page):
    """Logical strings from /ActualText marked content on the page."""
    data = page.read_contents() or b''
    out = []
    for m in _ACTUALTEXT_HEX.finditer(data):
        raw = bytes.fromhex(m.group(1).decode('ascii'))
        if raw.startswith(b'\xfe\xff'):
            out.append(raw[2:].decode('utf-16-be', 'replace'))
        elif raw.startswith(b'\xff\xfe'):
            out.append(raw[2:].decode('utf-16-le', 'replace'))
        else:
            try:
                out.append(raw.decode('utf-16-be', 'replace'))
            except Exception:
                continue
    return out


def page_search_text(page):
    """get_text() plus ActualText — RTL logical order lives in the latter."""
    parts = [page.get_text() or '']
    parts.extend(extract_actualtext(page))
    return '\n'.join(parts)


# Scripts whose letters must be shaped before they are drawn. Same table
# retypeset uses to decide what goes through the Story engine.
_SHAPING_RANGES = (
    (0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF), (0xFE70, 0xFEFC),
    (0x0700, 0x074F), (0x07C0, 0x07FF),
    (0x0900, 0x0DFF),
    (0x0E00, 0x0EFF), (0x0F00, 0x0FFF),
    (0x1000, 0x109F), (0x1780, 0x17FF),
)


def needs_shaping(text):
    for ch in text or '':
        o = ord(ch)
        for a, b in _SHAPING_RANGES:
            if a <= o <= b:
                return True
    return False


def unmarked_shaped_targets(actual_texts, targets):
    """Shaped-script targets that no /ActualText span carries.

    Arabic has a glyph-form tell — isolated presentation forms mean the
    run was drawn letter by letter. Devanagari and Thai have none: the
    shaper writes glyph ids, so a broken conjunct and a correct one look
    the same in the text layer. What IS deterministic is who drew the run.
    retypeset places every shaping-script run through the Story engine and
    marks it with /ActualText; a run that reached the page any other way
    was drawn glyph by glyph, and that is the defect. Cheaper and more
    certain than comparing renders against a reference.
    """
    hay = '\n'.join(actual_texts)
    missing, seen = [], set()
    for raw in targets:
        text = normalize_ws(raw)
        if len(text) < 2 or text in seen or not needs_shaping(text):
            continue
        seen.add(text)
        if text not in hay:
            missing.append(raw)
    return missing


def missing_translation_targets(page_text, targets):
    """Return targets of length ≥ 2 absent from the output text layer.

    Wrapping whitespace is collapsed on both sides, because a re-flowed
    merge wraps and the layer then holds a newline where the paragraph
    holds a space. Nothing else is folded: retypeset canonicalizes
    /ToUnicode to the authored code points, so an authored NBSP must land
    as an NBSP, and folding that here would hide exactly the drift the
    canonical-text-layer gate exists for.

    A target in a spaceless script (CJK, Thai, Khmer, ...) wraps with no
    space at the break, so the layer holds a newline where the paragraph
    holds nothing; for those the wrapping whitespace is removed on both
    sides instead of collapsed. A Latin target still needs its spaces.
    Measured on the FL-150 -> ja job (2026-09-17): every wrapped merge and
    the notice came back "missing" from a correct file.
    """
    hay = normalize_ws(page_text)
    hay_joined = None
    missing = []
    seen = set()
    for raw in targets:
        nt = normalize_ws(raw)
        if len(nt) < 2:
            continue
        if nt in seen:
            continue
        seen.add(nt)
        if nt in hay:
            continue
        if _has_spaceless_script(nt):
            if hay_joined is None:
                hay_joined = _WRAP_WS.sub('', page_text or '')
            if _WRAP_WS.sub('', raw) in hay_joined:
                continue
        missing.append(raw)
    return missing


def _has_spaceless_script(text):
    """True if any letter of text belongs to a script written without spaces."""
    return any(script_of(ch) in SPACELESS_SCRIPTS for ch in text)


PUSHBUTTON = 1 << 16


def pushbutton_captions(doc):
    """Pushbutton captions (/MK /CA) keyed by field name.

    Hidden is an annotation /F bit, not field flags — not filtered here:
    the ORIGINAL's captions are all wanted. hidden_pushbuttons() is the
    filter the two gates apply to the output.
    """
    caps = {}
    for page in doc:
        for w in page.widgets() or []:
            if not (w.field_flags & PUSHBUTTON):
                continue
            cap = (w.button_caption or '').strip()
            if cap:
                caps[w.field_name] = cap
    return caps


def _annot_hidden(doc, widget):
    """The Hidden bit of the widget ANNOTATION's /F flags (not field flags)."""
    try:
        kind, val = doc.xref_get_key(widget.xref, 'F')
    except Exception:
        return False
    if kind != 'int':
        return False
    try:
        return bool(int(val) & 2)
    except (TypeError, ValueError):
        return False


def hidden_pushbuttons(doc):
    """Field names of pushbuttons whose annotation flags say Hidden.

    A hidden widget draws nothing: no caption on the page, no width to
    fit. --hide-buttons sets the bit at strip time; the chrome and
    caption-width gates leave those buttons alone (FL-150's four dead XFA
    buttons were hidden that way and FAILed both gates, 2026-09-17).
    """
    names = set()
    for page in doc:
        for w in page.widgets() or []:
            if not (w.field_flags & PUSHBUTTON):
                continue
            if _annot_hidden(doc, w):
                names.add(w.field_name)
    return names


def leftover_button_captions(orig_caps, out_caps, out_text, skip=None, hidden=None):
    """Original pushbutton captions still drawing, and not skip / not rewritten.

    get_text() includes widget appearance streams, so a leftover /AP of
    'Print' is visible here even after strip removed page text. Rewritten
    CA plus dropped /AP removes it. skip is the explicit leave-chrome path;
    hidden names the output pushbuttons whose annotation is Hidden, which
    draw nothing and are not leftover.
    """
    skip = set(skip or [])
    hidden = set(hidden or [])
    hay = normalize_ws_nbsp(out_text)
    leftover = []
    for name, cap in orig_caps.items():
        nt = normalize_ws_nbsp(cap)
        if len(nt) < 2:
            continue
        if cap in skip or nt in skip:
            continue
        if name in hidden:
            continue
        out_n = normalize_ws_nbsp(out_caps.get(name) or '')
        rewritten = bool(out_n) and out_n != nt
        still_in_text = nt in hay
        if rewritten and not still_in_text:
            continue
        leftover.append((name, cap))
    return leftover


def caption_overflows(caption, rect_width, fs):
    """True if Helvetica caption is wider than rect_width minus 2 pt pad each side."""
    cap = (caption or '').strip()
    if not cap:
        return False
    width = pymupdf.Font('helv').text_length(cap, fontsize=fs)
    return width > rect_width - 4


def caption_fontsize(widget):
    """widget.text_fontsize if > 0, else max(4, rect.height - 4)."""
    fs = getattr(widget, 'text_fontsize', 0) or 0
    try:
        fs = float(fs)
    except (TypeError, ValueError):
        fs = 0.0
    if fs > 0:
        return fs
    return max(4.0, float(widget.rect.height) - 4.0)


def overflowing_button_captions(doc):
    """Output pushbuttons whose /CA does not fit the widget rect.

    A Hidden pushbutton draws no caption, so it has nothing to fit.
    """
    hits = []
    for page in doc:
        for w in page.widgets() or []:
            if not (w.field_flags & PUSHBUTTON):
                continue
            if _annot_hidden(doc, w):
                continue
            cap = (w.button_caption or '').strip()
            if not cap:
                continue
            if caption_overflows(cap, w.rect.width, caption_fontsize(w)):
                hits.append((w.field_name, cap))
    return hits


IDENTIFIER_KINDS = ('quoted', 'form-name')


def collect_identifier_spans(orig_doc):
    """Unique quoted / form-name write/find/say spans from original pages.

    Uses shipped extract_segments.write_find_say_hits — do not copy its regexes.
    url-or-email hits are not collected (not a hard fail this sitting).
    """
    spans = []
    seen = set()
    for page in orig_doc:
        for kind, snippet in write_find_say_hits(page.get_text() or ''):
            if kind not in IDENTIFIER_KINDS:
                continue
            nt = normalize_ws_nbsp(snippet)
            if len(nt) < 2 or nt in seen:
                continue
            seen.add(nt)
            spans.append(snippet)
    return spans


def missing_identifier_spans(page_text, spans, allow_translate=None):
    """Return identifier spans absent from page_text, minus allow_translate.

    Same NBSP/hyphen normalize as placement. Omit allow_translate (or [])
    means every span is required.
    """
    allow = {normalize_ws_nbsp(s) for s in (allow_translate or []) if s}
    hay = normalize_ws_nbsp(page_text)
    missing = []
    for raw in spans:
        nt = normalize_ws_nbsp(raw)
        if nt in allow:
            continue
        if nt not in hay:
            missing.append(raw)
    return missing


def override_marker_misses(conf, segments):
    """[(contains, token)] for override parts that drop a source marker or tail.

    An override replaces the whole span, so the list marker ('d.') and the
    tail after the dot leaders ('$') only survive if some part carries them.
    Overrides whose `contains` matches no segment are not this gate's job.
    """
    by_page = {}
    for seg in segments or []:
        by_page.setdefault(seg.get('page'), []).append(seg)
    misses = []
    for ov in conf.get('overrides') or []:
        contains = ov.get('contains') or ''
        if not contains:
            continue
        parts_text = normalize_ws_nbsp(' '.join(
            str(part.get('text') or '') for part in (ov.get('parts') or [])))
        for seg in by_page.get(ov.get('page'), []):
            if contains not in seg.get('text', ''):
                continue
            for token in ((seg.get('marker') or '').strip(), (seg.get('tail') or '').strip()):
                if token and token not in parts_text and (contains, token) not in misses:
                    misses.append((contains, token))
    return misses


def load_segments_file(translations_path, segments_path=None):
    """Whole segments.json: --segments, else the file beside the mapping."""
    path = segments_path
    if not path and translations_path:
        candidate = os.path.join(os.path.dirname(os.path.abspath(translations_path)),
                                 'segments.json')
        if os.path.isfile(candidate):
            path = candidate
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def document_only_cores(segfile):
    """Cores that are document metadata and never page text.

    The document /Title and the outline titles are cores, so from-cores
    scaffolds them and the author translates them — but they are drawn by
    the window caption and the bookmarks pane, not by a content stream.
    The placement gate must not demand them in the text layer.
    """
    if not isinstance(segfile, dict):
        return set()
    doc = segfile.get('document') or {}
    candidates = {(doc.get('title') or '').strip()}
    candidates.update(str(t).strip() for t in (doc.get('outline') or []))
    candidates.discard('')
    on_page = {s.get('core') for s in (segfile.get('segments') or [])}
    return {c for c in candidates if c not in on_page}


def _run_key(text, script='Latin'):
    """A run's form for keep-list comparison: letters only, lowercased.

    Spaceless scripts compare with whitespace removed. Quotes, commas and
    case around a quoted title must not matter; a missing or extra word
    must.

    Tokens below the branch's own word floor are dropped from BOTH sides
    (row 27). The scan cannot see them in a run — `FL` and `100` in
    `FL-100 Petition—Marriage/Domestic Partnership` are two letters and a
    number, so the run it builds is `Petition Marriage Domestic
    Partnership` — and a title key that still carried them could never
    equal it. A word the scan can see is still a word: a run missing one
    of those is not the title.
    """
    text = normalize_ws_nbsp(text).lower()
    if script in SPACELESS_SCRIPTS:
        return re.sub(r'\s+', '', text)
    minlen = MIN_WORD_LETTERS.get(script, 2)
    return ' '.join(w for w in re.findall(r"[^\W\d_]+", text)
                    if len(w) >= minlen)


def _kept(run, keep, kept, script='Latin'):
    """True when run equals a keep phrase (the original /Title, or a
    multi-word --allow entry). Records it in kept for the report."""
    if not keep:
        return False
    key = _run_key(run, script)
    if not key:
        return False
    for phrase in keep:
        if _run_key(phrase, script) == key:
            if kept is not None:
                kept.append(run.strip()[:70])
            return True
    return False


def scan_leaks(text, allow, source_words=None, src_re=None, script='Latin',
               keep=None, kept=None):
    """Split surviving source-script tokens into running vs isolated.

    script is the SOURCE document's script. When source_words is provided
    (same-script pairs), both buckets use THIS document's own source words so
    the translation itself is not flagged. Otherwise runs of the source
    script are the leaks: words for space-delimited scripts, character runs
    for spaceless ones. Latin behaviour is unchanged from before goal 15.
    """
    running, isolated = [], []
    if script and script != 'Latin':
        return _scan_leaks_script(text, allow, script, source_words, src_re,
                                  keep=keep, kept=kept)
    if source_words:
        tokens = [m.group(0) for m in WORD.finditer(text)]
        i = 0
        while i < len(tokens):
            low = tokens[i].lower()
            if low in source_words and low not in allow:
                j = i
                seq = []
                while j < len(tokens):
                    lj = tokens[j].lower()
                    if lj in source_words and lj not in allow:
                        seq.append(tokens[j])
                        j += 1
                    else:
                        break
                if _kept(' '.join(seq), keep, kept, script):
                    pass   # the quoted title, or an allowlisted phrase
                elif len(seq) >= 3:
                    running.append(' '.join(seq)[:70])
                else:
                    isolated.extend(seq)
                i = j
            else:
                i += 1
        return running, isolated

    for phrase in RUN.findall(text):
        if _kept(phrase, keep, kept, script):
            continue
        words = re.findall(r"[A-Za-z']+", phrase)
        if sum(1 for w in words if w.lower() not in allow) >= 3:
            running.append(phrase.strip()[:70])
    masked = RUN.sub(' ', text)
    if src_re is not None:
        for word in src_re.findall(masked):
            if word.lower() not in allow:
                isolated.append(word)
    return running, isolated


def _scan_leaks_script(text, allow, script, source_words=None, src_re=None,
                       keep=None, kept=None):
    """scan_leaks for a non-Latin source script."""
    running, isolated = [], []
    cls = script_class(script)
    if script in SPACELESS_SCRIPTS:
        for m in re.finditer(cls + '+', text):
            run = m.group(0)
            if run.lower() in allow or _kept(run, keep, kept, script):
                continue
            if len(run) >= SPACELESS_RUN_CHARS:
                running.append(run[:70])
            else:
                isolated.append(run)
        return running, isolated
    minlen = MIN_WORD_LETTERS.get(script, 2)
    word = cls + '{%d,}' % minlen
    if source_words:
        tokens = re.findall(cls + '+', text)
        i = 0
        while i < len(tokens):
            if tokens[i].lower() in source_words and tokens[i].lower() not in allow:
                j = i
                seq = []
                while j < len(tokens) and tokens[j].lower() in source_words \
                        and tokens[j].lower() not in allow:
                    seq.append(tokens[j])
                    j += 1
                if _kept(' '.join(seq), keep, kept, script):
                    pass   # the quoted title, or an allowlisted phrase
                elif len(seq) >= 3:
                    running.append(' '.join(seq)[:70])
                else:
                    isolated.extend(seq)
                i = j
            else:
                i += 1
        return running, isolated
    run_re = re.compile(word + r'(?:\s+' + word + r'){2,}')
    for phrase in run_re.findall(text):
        if _kept(phrase, keep, kept, script):
            continue
        words = re.findall(cls + '+', phrase)
        if sum(1 for w in words if w.lower() not in allow) >= 3:
            running.append(phrase.strip()[:70])
    masked = run_re.sub(' ', text)
    iso_re = src_re if src_re is not None else re.compile(word)
    for w in iso_re.findall(masked):
        if w.lower() not in allow:
            isolated.append(w)
    return running, isolated


# Arabic Presentation Forms-B: four-form groups (isolated, final, initial,
# medial) start at these code points. Initial and medial forms only exist
# when a shaper joined the letters; a run drawn letter by letter shows the
# isolated forms only.
_AR_GROUPS = (0xFE89, 0xFE8F, 0xFE95, 0xFE99, 0xFE9D, 0xFEA1, 0xFEA5, 0xFEB1,
              0xFEB5, 0xFEB9, 0xFEBD, 0xFEC1, 0xFEC5, 0xFEC9, 0xFECD, 0xFED1,
              0xFED5, 0xFED9, 0xFEDD, 0xFEE1, 0xFEE5, 0xFEE9, 0xFEF1)
ARABIC_ISOLATED_JOINING = set(_AR_GROUPS)
ARABIC_CONNECTED_FORMS = {b + 2 for b in _AR_GROUPS} | {b + 3 for b in _AR_GROUPS}


def unshaped_arabic_pages(doc):
    """([(page_index, isolated_count)], saw_forms) from the drawn glyph forms."""
    flags = pymupdf.TEXTFLAGS_RAWDICT | pymupdf.TEXT_IGNORE_ACTUALTEXT
    flagged, saw = [], False
    for page in doc:
        iso = conn = 0
        for block in page.get_text('rawdict', flags=flags)['blocks']:
            for line in block.get('lines', []):
                for span in line['spans']:
                    for c in span['chars']:
                        o = ord(c['c'])
                        if o in ARABIC_CONNECTED_FORMS:
                            conn += 1
                        elif o in ARABIC_ISOLATED_JOINING:
                            iso += 1
        if iso or conn:
            saw = True
        if conn == 0 and iso >= 3:
            flagged.append((page.number, iso))
    return flagged, saw


def conjunct_shaping_report(doc):
    """(lines, status, findings) for gate 18: probe every embedded face that draws a
    conjunct-forming script in the output.

    The text layer cannot show a broken conjunct (the shaper writes glyph
    ids), but the face can be asked directly: extract the embedded font
    program, render the script's probe cluster off it glyph by glyph and
    through the Story engine, and compare the counts (shaping_probe). Each
    (script, font) pair is probed once and reported once with the pages it
    draws. status is FAIL if any probe failed, else REVIEW if anything could
    not be attested, else PASS, else None when no such script is present.
    """
    fonts = {}       # xref -> (name, ext, program bytes or None)
    judged = {}      # (script, xref) -> [ProbeResult, pages]
    unattested = {}  # script -> pages with no face carrying the probe
    reviews = {}     # script -> pages (blind or unmeasured scripts)
    for page in doc:
        scripts = shaping_probe.scripts_in(page_search_text(page))
        for script in sorted(scripts):
            if shaping_probe.review_reason(script):
                reviews.setdefault(script, []).append(page.number + 1)
                continue
            probe = shaping_probe.PROBE_FOR.get(script)
            if probe is None:
                continue
            attested = False
            for entry in page.get_fonts(full=True):
                xref = entry[0]
                if xref not in fonts:
                    try:
                        info = doc.extract_font(xref)
                        fonts[xref] = (info[0], info[1], info[3] or None)
                    except Exception:
                        fonts[xref] = (str(entry[3]), '', None)
                name, ext, program = fonts[xref]
                if not program:
                    continue
                key = (script, xref)
                if key not in judged:
                    try:
                        results = shaping_probe.probe_font_bytes(program, ext, [script])
                        result = results[0] if results else None
                    except Exception as exc:
                        result = shaping_probe.review_result(
                            script, f'could not load the embedded font {name}: {exc}')
                    judged[key] = [result, []]
                result, pages = judged[key]
                if result is None or result.status == 'SKIP':
                    continue
                attested = True
                pages.append(page.number + 1)
            if not attested:
                unattested.setdefault(script, []).append(page.number + 1)

    def pages_of(nums):
        return 'page ' + ', '.join(str(n) for n in sorted(set(nums)))

    lines, statuses, findings = [], [], []
    for (script, xref), (result, pages) in sorted(judged.items()):
        if result is None or result.status == 'SKIP' or not pages:
            continue
        line = f'{result.line()} [{pages_of(pages)}]'
        if result.status == 'FAIL':
            line += ' — every conjunct drawn with this face is broken; do not ship'
        lines.append(line)
        statuses.append(result.status)
        face = result.expected_font or fonts[xref][0]
        findings.extend(Finding(p, face, result.line()) for p in sorted(set(pages)))
    for script, pages in sorted(unattested.items()):
        probe = shaping_probe.PROBE_FOR[script]
        lines.append(f'REVIEW conjunct shaping {script}: cannot attest [{pages_of(pages)}]: '
                     f'no embedded face carries the probe "{probe.text}"; rebuild the subset '
                     f'with prepare_font.py, which adds the probe glyphs, or check a render '
                     f'with a reader of the script')
        statuses.append('REVIEW')
        findings.extend(Finding(p, script, f'cannot attest: no embedded face carries the probe "{probe.text}"')
                        for p in sorted(set(pages)))
    for script, pages in sorted(reviews.items()):
        reason = shaping_probe.review_reason(script)
        lines.append(f'REVIEW conjunct shaping {script}: {reason} [{pages_of(pages)}]')
        statuses.append('REVIEW')
        findings.extend(Finding(p, script, reason) for p in sorted(set(pages)))
    if 'FAIL' in statuses:
        status = 'FAIL'
    elif 'REVIEW' in statuses:
        status = 'REVIEW'
    elif 'PASS' in statuses:
        status = 'PASS'
    else:
        status = None
    return lines, status, findings


def _has_cjk(text):
    return any(script_of(ch) == 'CJK' for ch in text)


def _page_lines(page):
    """Non-empty text lines of a page, top to bottom: (text, x0, y0, x1, y1)."""
    out = []
    for block in page.get_text('dict').get('blocks', []):
        for ln in block.get('lines', []):
            text = ''.join(sp.get('text', '') for sp in ln.get('spans', ())).strip()
            if text:
                x0, y0, x1, y1 = ln['bbox']
                out.append((text, x0, y0, x1, y1))
    out.sort(key=lambda t: (t[2], t[1]))
    return out


# A line sits under another when the two overlap horizontally by at least
# half the narrower width and the gap between their boxes is at most
# STACK_GAP times the lower line's height — leading of about two and a
# half line heights (three times the font size) (measured: hand-split
# lines at 1.4x, 1.6x, 2.4x and 3.0x stay stacked, 4.0x does not). MuPDF's
# own blocks split at about 1.6x, which ordinary body copy exceeds, so
# they are not trusted for this.
STACK_GAP = 1.5


def _stacks(lines):
    """Group lines into stacks: runs of lines set one under the other."""
    stacks = []
    for line in lines:
        _, x0, y0, x1, y1 = line
        h = y1 - y0
        for stack in stacks:
            _, px0, py0, px1, py1 = stack[-1]
            overlap = min(x1, px1) - max(x0, px0)
            if (overlap >= 0.5 * min(x1 - x0, px1 - px0)
                    and -0.5 * h <= y0 - py1 <= STACK_GAP * h):
                stack.append(line)
                break
        else:
            stacks.append([line])
    return stacks


def kinsoku_report(doc):
    """(lines, status, findings) for gate 19: no drawn line of CJK text begins
    with a character JIS X 4051 / JLREQ forbids at line start, or ends with
    one it forbids at line end. REVIEW, never FAIL.

    Judged on the lines MuPDF reads back from each page, grouped into stacks
    by geometry (_stacks); a stack is eligible when one of its lines carries
    a CJK letter. In an eligible stack a line that begins with closing
    punctuation, a small kana or the prolonged sound mark is a violation
    when a line sits above it — the break before it was a choice — and one
    that ends with an opening bracket when a line sits below it. Two shapes
    a form sets on purpose are values, not breaks, and are exempt: a line
    that is a single character (ー for "none", a bracket after a field, a
    lone 「), and a list bullet (KINSOKU_LIST_MARKERS: ・ ･) that begins
    two or more lines of the stack, each followed by more text. The Story
    engine breaks by UAX #14 and never violates these
    (dev/probes/cjk_kinsoku_probe.py);
    a target split by hand across source lines can — and so can a form label
    that merely looks like one, which is why this is REVIEW. Horizontal text
    only: vertical setting puts every glyph on its own line. status is
    REVIEW if any line violates, PASS if lines were judged and none did,
    None when no page has an eligible stack.
    """
    findings, judged = [], 0
    for page in doc:
        for stack in _stacks(_page_lines(page)):
            texts = [t for t, *_ in stack]
            if not any(_has_cjk(t) for t in texts):
                continue
            firsts = [t[0] for t in texts if len(t) > 1]
            markers = {c for c in firsts if c in KINSOKU_LIST_MARKERS and firsts.count(c) >= 2}
            last = len(texts) - 1
            for i, text in enumerate(texts):
                judged += 1
                if len(text) == 1:
                    continue
                if i < last and text[-1] in KINSOKU_LINE_END:
                    findings.append(Finding(page.number + 1, 'line-end', text))
                if i > 0 and text[0] in KINSOKU_LINE_START and text[0] not in markers:
                    findings.append(Finding(page.number + 1, 'line-start', text))
    if not judged:
        return [], None, []
    if findings:
        lines = [f'REVIEW kinsoku: {len(findings)} line(s) of CJK text break a line-breaking '
                 f'rule (JIS X 4051 / JLREQ): closing punctuation, a small kana or the prolonged '
                 f'sound mark begins a line, or an opening bracket ends one. A target split by '
                 f'hand across source lines does this — declare the paragraph as a merge; a form '
                 f'label can look the same, so check the lines:']
        for f in findings[:20]:
            lines.append(f'   p{f.page} {f.where}: {f.text[:60]}')
        return lines, 'REVIEW', findings
    return [f'PASS kinsoku: {judged} CJK line(s) break within the rules'], 'PASS', []


def cjk_tell_report(doc, convention, keep, kept, allow, source=None):
    """(lines, status, findings) for gate 21: every drawn line of the output
    that carries characters which cannot belong to the target convention
    (cjk_tell.tells_in). Kept runs (the original's /Title, multi-word --allow
    phrases) are skipped; single --allow tokens are removed before counting.
    A line with cjk_tell.LINE_FAIL or more tells is FAIL (an echoed sentence
    carries 9–56), one to five REVIEW (a name in kana, a rare character);
    status FAIL > REVIEW > PASS; one Finding(page, 'line', text) per line.
    source is the source's convention, named in the FAIL line.

    A CJK sentence has no spaces, so one --allow token can be a whole line:
    allowed is allowed, but never silently — a line whose tells an --allow
    token reduced joins the kept-runs note and the PASS line counts it. A line
    of Han both languages share (住所氏名, 令和七年) is invisible to the tell,
    and the PASS line says so: --translations and the visual pass remain the
    check for those."""
    name = cjk_tell.REPERTOIRE[convention][0]
    source_name = (cjk_tell.REPERTOIRE[source][0] if source in cjk_tell.REPERTOIRE
                   else 'source-language')
    hits, judged, reduced = [], 0, 0
    for page in doc:
        for text, *_ in _page_lines(page):
            if _kept(text, keep, kept, 'CJK'):
                continue
            judged += 1
            stripped = cjk_tell.strip_allowed(text, allow)
            tells = cjk_tell.tells_in(stripped, convention)
            if stripped != text and len(tells) < len(cjk_tell.tells_in(text, convention)):
                if kept is not None:
                    kept.append(text.strip()[:70])
                reduced += 1
            if tells:
                hits.append((page.number + 1, text, tells))
    blind = 'a line of Han both languages share is invisible to the tell'
    if not hits:
        line = (f'PASS leak scan (CJK tell): {judged} line(s) hold only characters a {name} '
                f'target can carry')
        if reduced:
            line += f' ({reduced} line(s) reduced by --allow)'
        return [f'{line}; {blind}'], 'PASS', []
    worst = max(len(t) for _, _, t in hits)
    if worst >= cjk_tell.LINE_FAIL:
        head = (f'FAIL leak scan (CJK tell): {len(hits)} line(s) carry characters that cannot '
                f'belong to a {name} target — untranslated {source_name} text, or a proper noun '
                f'to allowlist with --allow:')
        status = 'FAIL'
    else:
        head = (f'REVIEW leak scan (CJK tell): {len(hits)} line(s) carry a few characters that '
                f'cannot belong to a {name} target (a name in kana, a rare character — allowlist '
                f'with --allow):')
        status = 'REVIEW'
    lines = [head]
    for pno, text, tells in hits[:20]:
        lines.append(f'   p{pno}: {text[:60]}  [{len(tells)} tells: {"".join(tells)[:12]}]')
    return lines, status, [Finding(pno, 'line', text) for pno, text, _ in hits]


def _cjk_drawing_fonts(page):
    """(draws, keys): whether the page draws CJK glyphs at all, and the
    han_forms.font_key of every font that drew one, from the spans MuPDF
    gives back — the only witness of who drew what. A span with no font name
    contributes the key 'an unnamed font', so it is never silently dropped."""
    draws, keys = False, set()
    for block in page.get_text('rawdict').get('blocks', []):
        for line in block.get('lines', []):
            for span in line.get('spans', []):
                if any(han_forms.is_cjk(c['c']) for c in span.get('chars', ()) if c.get('c')):
                    draws = True
                    keys.add(han_forms.font_key(span.get('font', '')) or 'an unnamed font')
    return draws, keys


def han_forms_report(doc, mapping_lang, output_lang, reference_dir=None):
    """(lines, status, findings) for gate 20: every face that drew CJK glyphs on
    a page is judged by han_forms.judge_program against the Noto reference of
    the language's convention and the other's, at the face's weight.

    Only the faces that drew CJK glyphs on a page are judged, matched by name
    to the page's spans: a face that is embedded but drew none — the source
    document's face strip_text leaves in the resources, a heading face that
    drew only Latin — neither fails the page nor attests it. A drawing face
    that carries no probes, or that is not an embedded program, is reported
    as cannot attest for that page even when another drawing face passes.
    Each face is judged once (per xref) and reported once with its pages.

    mapping_lang is the mapping's lang and is authoritative: a non-CJK value
    means the page's CJK is not the target's script (a leak; those gates own
    it) and nothing is printed. output_lang is the delivered /Lang, used only
    when the mapping gives none; a non-CJK /Lang on a page that draws CJK text
    is REVIEW, not silence — it may be the original's, never updated. A
    mapping lang that is not a language tag at all (a display name) is REVIEW.

    status is FAIL if any drawing face draws the other convention, else REVIEW
    if anything could not be judged, else PASS; None when no page draws CJK
    glyphs or the mapping names a non-CJK language.
    """
    drawing_by_page = {page.number: _cjk_drawing_fonts(page) for page in doc}
    cjk_pages = [page for page in doc if drawing_by_page[page.number][0]]
    if not cjk_pages:
        return [], None, []
    all_pages = [p.number + 1 for p in cjk_pages]

    def pages_of(nums):
        return 'page ' + ', '.join(str(n) for n in sorted(set(nums)))

    mapping_lang = (mapping_lang or '').strip()
    output_lang = (output_lang or '').strip()
    lang = mapping_lang or output_lang
    convention = han_forms.convention_for_lang(lang)
    if not convention and mapping_lang:
        if han_forms.is_language_tag(mapping_lang):
            return [], None, []      # a real non-CJK target: the page's CJK is a leak, those gates own it
        reason = (f'lang "{mapping_lang}" is not a language tag; pass a BCP 47 tag such as ja or '
                  f'zh-Hans (not a display name) so the gate can name the convention')
        return ([f'REVIEW han-forms: {reason} [{pages_of(all_pages)}]'], 'REVIEW',
                [Finding(p, 'lang', reason) for p in all_pages])
    if not convention and output_lang:
        reason = (f'the output declares /Lang "{output_lang}" while the page draws CJK text; '
                  f'pass --translations with lang to name the convention the page should '
                  f'follow (the /Lang may be the original\'s, never updated)')
        return ([f'REVIEW han-forms: {reason} [{pages_of(all_pages)}]'], 'REVIEW',
                [Finding(p, 'lang', reason) for p in all_pages])
    ok, reason = han_forms.reference_status(convention, reference_dir)
    if not ok:
        name = han_forms.CONVENTIONS[convention][0] if convention else ''
        head = f'REVIEW han-forms{" " + name if name else ""}'
        where = 'lang' if not convention else 'reference'
        return ([f'{head}: {reason} [{pages_of(all_pages)}]'], 'REVIEW',
                [Finding(p, where, reason) for p in all_pages])

    name = han_forms.CONVENTIONS[convention][0]
    fonts = {}       # xref -> (face name, {keys}, program bytes or None)
    judged = {}      # xref -> [HanResult, pages]
    unattested = {}  # page number -> why its CJK text could not be attested
    for page in cjk_pages:
        _, drawing = drawing_by_page[page.number]
        matched = set()        # drawing keys some embedded face accounts for
        without_probes = []    # faces that drew CJK glyphs but carry no probes
        unjudgeable = []       # faces that drew CJK glyphs but have no program
        for entry in page.get_fonts(full=True):
            xref = entry[0]
            if xref not in fonts:
                try:
                    info = doc.extract_font(xref)
                    face = re.sub(r'^[A-Z]{6}\+', '', info[0] or '')
                    program = info[3] or None
                except Exception:
                    face, program = str(entry[3]), None
                keys = {han_forms.font_key(face), han_forms.font_key(str(entry[3]))}
                if program:
                    keys.add(han_forms.font_key(han_forms.program_psname(program)))
                keys.discard('')
                fonts[xref] = (face, keys, program)
            face, keys, program = fonts[xref]
            hit = keys & drawing
            if not hit:
                continue
            matched |= hit
            if not program:
                unjudgeable.append(face)
                continue
            if xref not in judged:
                judged[xref] = [han_forms.judge_program(program, convention, reference_dir,
                                                        face=face), []]
            result, pages = judged[xref]
            if result.status == 'SKIP':
                without_probes.append(face)
                continue
            pages.append(page.number + 1)
        unmatched = drawing - matched if drawing else {'an unnamed font'}
        parts = []
        if without_probes:
            parts.append(f'{", ".join(sorted(set(without_probes)))} drew CJK text but carries '
                         f'none of the probes "{han_forms.HAN_CHARS}" — rebuild that subset with '
                         f'prepare_font.py, which adds them')
        if unjudgeable or unmatched:
            parts.append(f'the CJK text drawn with {", ".join(sorted(set(unjudgeable) | unmatched))} '
                         f'is not an embedded program this gate can judge — check a render with '
                         f'a reader of the language')
        if parts:
            unattested[page.number + 1] = '; '.join(parts)

    lines, statuses, findings = [], [], []
    for xref, (result, pages) in sorted(judged.items()):
        if result.status == 'SKIP' or not pages:
            continue
        line = f'{result.line()} [{pages_of(pages)}]'
        if result.status == 'FAIL':
            line += (' — a reader of the language sees the other region\'s shapes; rebuild '
                     'with a face of the language (references/fonts.md)')
        lines.append(line)
        statuses.append(result.status)
        text = result.line() if result.status in ('PASS', 'FAIL') else result.reason
        findings.extend(Finding(p, result.face, text) for p in sorted(set(pages)))
    by_reason = {}
    for pno, reason in unattested.items():
        by_reason.setdefault(reason, []).append(pno)
    for reason, pages in sorted(by_reason.items()):
        lines.append(f'REVIEW han-forms {name}: cannot attest [{pages_of(pages)}]: {reason}')
        statuses.append('REVIEW')
        findings.extend(Finding(p, convention, f'cannot attest: {reason}') for p in sorted(pages))
    if 'FAIL' in statuses:
        status = 'FAIL'
    elif 'REVIEW' in statuses:
        status = 'REVIEW'
    elif 'PASS' in statuses:
        status = 'PASS'
    else:
        status = None
    return lines, status, findings


def page_unextractable(page):
    """True when the page looks scanned: visible content, no text layer."""
    if page.get_text().strip():
        return False
    if page.get_images():
        return True
    return ink(page) >= VISIBLE_INK


def ink(page):
    px = page.get_pixmap(dpi=72)
    return dark_pixels(px.samples, px.n)


# Boxes are compared to a hundredth of a point: a stage that re-serialises a
# box must not fail it, while any real resize (A4 against Letter is 17 pt)
# is far above this. PyMuPDF reads box values as 32-bit floats (612.01 comes
# back as 612.0100098), so the comparison also allows that representation
# error — two roundings of at most 2**-24 relative each — or a box exactly
# 0.01 pt off would fail.
PAGE_BOX_TOLERANCE = 0.01


def _box_differs(x, y):
    return any(abs(p - q) > PAGE_BOX_TOLERANCE + 2.4e-7 * max(1.0, abs(p), abs(q))
               for p, q in zip(x, y))


def _box(rect):
    return '[' + ', '.join(f'{v:g}' for v in rect) + ']'


def page_parity(original, translation):
    """Every way the translation's pages differ from the original's.

    A translation has exactly the original's pages: the same count, and on
    each page the same media box, crop box and rotation. `original` and
    `translation` are open documents. Findings name the page: an original page
    the translation lacks, a translated page with no original, then a box or
    rotation that changed on a page both have.
    """
    findings = [Finding(i + 1, 'missing', 'no page in the translation')
                for i in range(len(translation), len(original))]
    findings += [Finding(i + 1, 'extra', 'no page in the original')
                 for i in range(len(original), len(translation))]
    for i in range(min(len(original), len(translation))):
        a, b = original[i], translation[i]
        for where, x, y in (('media-box', a.mediabox, b.mediabox),
                            ('crop-box', a.cropbox, b.cropbox)):
            if _box_differs(x, y):
                findings.append(Finding(i + 1, where, f'original {_box(x)}, translation {_box(y)}'))
        if a.rotation != b.rotation:
            findings.append(Finding(i + 1, 'rotation',
                                    f'original {a.rotation}, translation {b.rotation}'))
    return findings


def _execute_verify(orig, trans, fill_text='Test value 123', allow=None, min_ink=0.4,
                    source_regex=None, source_words_from=None, allow_extra_prefix=None,
                    translations=None, segments=None, fail_on_review=False,
                    reference_fonts=None, typography=False, widget_text=None):
    """Run structural gates. Returns (exit_code, gates). Prints as it goes."""
    from .mapping import FORMAT, load_mapping
    from .results import MappingError
    document, typography_check = None, None
    if translations:
        try:
            document = load_mapping(translations, segments)
        except MappingError as exc:
            detail = (exc.refusals.get('typography') or [{}])[0]
            if detail.get('reason') == 'stale-extraction' and detail.get('detail', '').startswith('cannot read extraction:'):
                typography = True
            else:
                raise
    is_typography = document is not None and document.format == FORMAT
    if typography or is_typography:
        from .typography_verify import inspect_output
        typography_check = inspect_output(orig, trans, document if is_typography else None)
    conf = (document.legacy if document is not None and not is_typography else {})
    # A multi-word --allow entry is a phrase: it matches a whole run and
    # nothing else (row 20). Single words behave as before. Until now a
    # phrase was accepted and silently matched nothing.
    given = [a.strip() for a in (allow or []) if a and a.strip()]
    keep = {a for a in given if re.search(r'\s', a)}
    allow = set(a.lower() for a in given if a not in keep)

    o, j = pymupdf.open(orig), pymupdf.open(trans)
    # The compliance notice names the form it translates in the source
    # language; the reader has to find that form. The original's /Title,
    # quoted as one run, is therefore kept rather than counted.
    otitle_keep = ((o.metadata or {}).get('title') or '').strip()
    if otitle_keep:
        keep.add(otitle_keep)
    kept = []
    fail = 0
    gates = []

    def record(name, status, message='', findings=()):
        gates.append(GateResult(name=name, status=status, message=message,
                                findings=tuple(findings)))

    # Gate 4 is keyed to the source document's script, not to Latin.
    o_text = '\n'.join(p.get_text() or '' for p in o)
    j_text = '\n'.join(p.get_text() or '' for p in j)
    src_script = dominant_script(o_text) or 'Latin'
    out_script = target_script(j_text, o_text)
    log.info(f'leak scan: source script {src_script}; output script {out_script or "none"}')
    same_spaceless = src_script in SPACELESS_SCRIPTS and out_script == src_script
    source_words = None
    src_re = None
    if source_words_from:
        source_words = source_words_from_segments(source_words_from, allow, src_script)
        log.info(f'leak scan: {len(source_words)} source words from {source_words_from}')
    elif out_script == src_script and not same_spaceless:
        source_words = source_words_from_text(o_text, allow, src_script)
        log.info(f'leak scan: same script on both sides; using {len(source_words)} '
              f'document words from the original')
    if not source_words:
        source_words = None
        src_re = re.compile(source_regex or r'[A-Za-z]{4,}') if src_script == 'Latin' \
            else (re.compile(source_regex) if source_regex else None)

    onames = {w.field_name: w.field_type_string for p in o for w in p.widgets()}
    jnames = {w.field_name: w.field_type_string for p in j for w in p.widgets()}
    missing = set(onames) - set(jnames)
    mismatch = [k for k in onames if k in jnames and onames[k] != jnames[k]]
    extra = {k for k in set(jnames) - set(onames)
             if not (allow_extra_prefix and k.startswith(allow_extra_prefix))}
    log.info(f'fields: {len(onames)} original / {len(jnames)} translated')
    parity = []
    for label, bad in [('missing', missing), ('type-mismatch', mismatch),
                       ('unexpected-extra', extra)]:
        if bad:
            log.info(f'FAIL field {label}: {sorted(bad)[:10]}')
            fail = 1
            parity.extend(Finding(None, label, name) for name in sorted(bad))
    if not (missing or mismatch or extra):
        log.info('PASS field parity')
        record('field-parity', 'PASS')
    else:
        record('field-parity', 'FAIL', findings=parity)

    # Widget text is translated by rewriting the DISPLAY half of each /Opt
    # entry. The export half is what /V holds and what a viewer submits;
    # a translated export silently breaks the form's data.
    oopt, jopt = choice_exports(orig), choice_exports(trans)
    drifted = sorted(name for name in set(oopt) | set(jopt)
                     if oopt.get(name) != jopt.get(name))
    if drifted:
        log.info(f'FAIL /Opt export values changed ({len(drifted)}):')
        for name in drifted[:10]:
            log.info(f'   {name}: {oopt.get(name)} -> {jopt.get(name)}')
        fail = 1
        record('opt-export-parity', 'FAIL',
               f'{len(drifted)} choice field(s) changed',
               findings=[Finding(None, name, f'{oopt.get(name)} -> {jopt.get(name)}')
                         for name in drifted])
    elif oopt:
        log.info(f'PASS /Opt export parity ({len(oopt)} choice field(s))')
        record('opt-export-parity', 'PASS',
               f'{len(oopt)} choice field(s)')

    # Gate 22: tooltips, dropdown labels and text defaults never reach a
    # content stream, so the leak scan cannot see them; a strip without
    # --widget-text ships them in the source language. REVIEW, not FAIL:
    # a label that is data stays on purpose, and the mapping says which.
    from .strip_text import WidgetTextError
    wt_mapping, wt_source = load_widget_text_mapping(widget_text, translations)
    try:
        checked, left = widget_text_left(orig, trans, wt_mapping)
    except WidgetTextError as exc:
        wt_source = f'the widget-text mapping was not used: {exc}'
        checked, left = widget_text_left(orig, trans, None)
    if left:
        log.info(f'REVIEW widget text left in the source language ({len(left)}):')
        for field, label, text in left[:10]:
            log.info(f'   {field}: {label}: {text}')
        if wt_mapping is None or wt_source.startswith('the widget-text mapping was not used'):
            log.info(f'   ({wt_source}; a deleted key or a target equal to its '
                     f'source marks a string kept on purpose: --widget-text PATH)')
        record('widget-text', 'REVIEW', f'{len(left)} of {checked} string(s); {wt_source}',
               findings=[Finding(None, field, f'{label}: {text}')
                         for field, label, text in left])
    elif checked:
        log.info(f'PASS widget text ({checked} string(s) translated or kept on purpose)')
        record('widget-text', 'PASS', f'{checked} string(s)')

    if onames:
        ttarget = cbtarget = None
        for p in j:
            for w in p.widgets():
                if ttarget is None and w.field_type_string == 'Text':
                    ttarget = w.field_name
                if cbtarget is None and w.field_type_string == 'CheckBox':
                    cbtarget = w.field_name
        for p in j:
            for w in p.widgets():
                if w.field_name == ttarget:
                    w.field_value = fill_text
                    w.update()
                if w.field_name == cbtarget:
                    w.field_value = True
                    w.update()
        fd, fill_path = tempfile.mkstemp(suffix='.pdf')
        os.close(fd)
        try:
            j.save(fill_path)
            t = pymupdf.open(fill_path)
            got_t = (ttarget is None) or any(w.field_value == fill_text for p in t for w in p.widgets()
                        if w.field_name == ttarget)
            got_cb = (cbtarget is None) or any(
                w.field_value for p in t for w in p.widgets()
                if w.field_name == cbtarget)
            t.close()
            ok = got_t and got_cb
            log.info(('PASS' if ok else 'FAIL') + ' fill round-trip')
            fail |= (0 if ok else 1)
            broken = []
            if not got_t:
                broken.append(Finding(None, ttarget, 'text value did not survive save and reopen'))
            if not got_cb:
                broken.append(Finding(None, cbtarget, 'checkbox did not survive save and reopen'))
            record('fill-roundtrip', 'PASS' if ok else 'FAIL', findings=broken)
        finally:
            try:
                os.remove(fill_path)
            except OSError:
                pass
    else:
        log.info('SKIP fill round-trip (no fields)')
        record('fill-roundtrip', 'SKIP', 'no fields')

    # A02: every gate below walks only the pages both files have, so a page
    # the translation lost or gained, or one whose box or rotation changed,
    # was invisible to all of them. One status line; details are indented.
    pages = page_parity(o, j)
    if pages:
        log.info(f'FAIL page parity: {len(o)} original / {len(j)} translated, '
                 f'{len(pages)} difference(s)')
        for f in pages[:10]:
            log.info(f'   page {f.page} {f.where}: {f.text}')
        if len(pages) > 10:
            log.info(f'   … {len(pages) - 10} more not shown')
        fail = 1
        record('page-parity', 'FAIL', f'{len(o)} original / {len(j)} translated',
               findings=pages)
    else:
        log.info(f'PASS page parity: {len(o)} page(s), same boxes and rotation')
        record('page-parity', 'PASS', f'{len(o)} page(s)')

    jc = pymupdf.open(trans)
    ink_status = None
    unextractable = False
    scans = []
    inks = []
    for i in range(min(len(o), len(jc))):
        if page_unextractable(o[i]):
            nimg = len(o[i].get_images())
            page_ink = ink(o[i])
            log.info(f'FAIL page {i+1} no extractable text with visible content '
                  f'(images={nimg}, ink={page_ink}). '
                  f'This looks scanned — scans are out of scope with or without an OCR layer; do not ship.')
            fail = 1
            unextractable = True
            scans.append(Finding(i + 1, 'page', f'images={nimg}, ink={page_ink} px'))
            continue
        do, dj = ink(o[i]), ink(jc[i])
        if do < INK_SKIP:
            log.info(f'SKIP page {i+1} ink ratio (original negligible ink: {do} px)')
            if ink_status is None:
                ink_status = 'SKIP'
            inks.append(Finding(i + 1, 'page', f'negligible ink: {do} px'))
            continue
        ratio = dj / max(do, 1)
        ok = min_ink <= ratio <= 3.0
        log.info(f'{"PASS" if ok else "FAIL"} page {i+1} ink ratio: {ratio:.2f}')
        fail |= (0 if ok else 1)
        if not ok:
            ink_status = 'FAIL'
        elif ink_status != 'FAIL':
            ink_status = 'PASS'
        inks.append(Finding(i + 1, 'page', f'{"PASS" if ok else "FAIL"} ink ratio {ratio:.2f}'))
    if unextractable:
        record('extractable-text', 'FAIL', findings=scans)
    if ink_status:
        record('ink-ratio', ink_status, findings=inks)

    invisible = invisible_text_pages(orig)
    for pno, fraction in invisible:
        log.info(f'FAIL page {pno+1} invisible text layer: stripping the text changes '
              f'{fraction:.1%} of its span area. This looks like an OCR\'d scan; the '
              f'words the reader sees are pixels. Do not ship.')
        fail = 1
    if not invisible:
        log.info('PASS text layer is visible')
        record('visible-text', 'PASS')
    else:
        record('visible-text', 'FAIL', findings=[
            Finding(pno + 1, 'page', f'stripping the text changes {fraction:.1%} of its span area')
            for pno, fraction in invisible])

    # Canonical text layer (audit H4/H5). Authored strings count as
    # deliberate; so does anything already in the original, whose own
    # drift we cannot rewrite.
    #
    # The mapping is read first, before anything touches the output document:
    # a mapping this malformed should fail as a mapping, not as whatever the
    # output's content stream happens to do next.
    authored = o_text
    if document is not None:
        authored += '\n' + '\n'.join(collect_translation_targets(document))

    # Read the translated document's text once. `jc` is not touched between
    # here and `jc.close()`, and get_text() re-derives layout from the content
    # stream on every call, so the gates below share one pass instead of
    # taking three.
    page_gettext = [page.get_text() or '' for page in jc]
    page_actualtext = [extract_actualtext(page) for page in jc]
    page_search = '\n'.join('\n'.join([t] + a)
                            for t, a in zip(page_gettext, page_actualtext))

    drift = drifted_characters(page_search, authored)
    if drift:
        log.info(f'FAIL text layer is not canonical ({len(drift)} character(s) '
              f'nobody authored — ToUnicode drift; the page looks right but '
              f'the words cannot be searched or copied):')
        for ch, n in drift[:10]:
            log.info(f'   U+{ord(ch):04X} {unicodedata.name(ch, "?")} x{n}')
        fail = 1
        record('canonical-text', 'FAIL', f'{len(drift)} character(s)', findings=[
            Finding(None, f'U+{ord(ch):04X}', f'{unicodedata.name(ch, "?")} x{n}')
            for ch, n in drift])
    else:
        log.info('PASS canonical text layer')
        record('canonical-text', 'PASS')

    unshaped, saw_arabic = unshaped_arabic_pages(jc)
    for pno, n in unshaped:
        log.info(f'FAIL page {pno+1} Arabic drawn unshaped: {n} joining letters in isolated '
              f'form and none connected. The run bypassed the Story engine; do not ship.')
        fail = 1
    if unshaped:
        record('arabic-letterforms', 'FAIL', f'{len(unshaped)} page(s)', findings=[
            Finding(pno + 1, 'page', f'{n} joining letters in isolated form, none connected')
            for pno, n in unshaped])
    elif saw_arabic:
        log.info('PASS Arabic letterforms joined')
        record('arabic-letterforms', 'PASS')

    shaping_lines, shaping_status, shaping_findings = conjunct_shaping_report(jc)
    for line in shaping_lines:
        log.info(line)
    if shaping_status == 'FAIL':
        fail = 1
    if shaping_status:
        record('conjunct-shaping', shaping_status, findings=shaping_findings)

    kinsoku_lines, kinsoku_status, kinsoku_findings = kinsoku_report(jc)
    for line in kinsoku_lines:
        log.info(line)
    if kinsoku_status:
        record('kinsoku', kinsoku_status,
               f'{len(kinsoku_findings)} line(s)' if kinsoku_status == 'REVIEW' else '',
               findings=kinsoku_findings)

    mapping_lang = (document.lang or '').strip() if document is not None else ''
    output_lang = declared_language(jc)
    han_lines, han_status, han_findings = han_forms_report(jc, mapping_lang, output_lang,
                                                           reference_fonts)
    for line in han_lines:
        log.info(line)
    if han_status == 'FAIL':
        fail = 1
    if han_status:
        record('han-forms', han_status, findings=han_findings)

    # Two buckets, because "a Latin word survived" and "a sentence went
    # untranslated" are completely different findings and must not score alike.
    #
    #   RUNNING TEXT  - three or more consecutive source-script words. That is
    #                   an untranslated phrase ("please press the Clear This
    #                   Form button"), a real defect. Hard fail.
    #   ISOLATED      - one or two adjacent tokens. Overwhelmingly these are
    #                   things that SHOULD stay verbatim: official document and
    #                   form names (Schedule C, Form W-2, Form 1040), statutes,
    #                   brand and product names, other proper nouns. Reported
    #                   for review, never a failure - translating an agency's
    #                   form name is itself an error, since the reader has to
    #                   locate a document that carries that exact name.
    running, isolated = [], []
    tell_ran = False
    if same_spaceless:
        target_conv = han_forms.convention_for_lang(mapping_lang)
        source_conv = cjk_tell.convention_of(o_text)
        if not mapping_lang:
            why = 'no lang in the mapping'
        elif target_conv not in cjk_tell.REPERTOIRE:
            why = f'lang "{mapping_lang}" names no Han convention'
        elif source_conv is None:
            why = ('the source\'s convention is not clear: no '
                   'single repertoire holds every one of its characters, or several do')
        elif (source_conv, target_conv) not in cjk_tell.STRONG_PAIRS:
            why = (f'the pair {cjk_tell.REPERTOIRE[source_conv][0]} -> '
                   f'{cjk_tell.REPERTOIRE[target_conv][0]} is not separable by repertoire (measured)')
        else:
            why = None
        if why is None:
            tell_lines, tell_status, tell_findings = cjk_tell_report(jc, target_conv, keep, kept, allow,
                                                                     source=source_conv)
            for line in tell_lines:
                log.info(line)
            if tell_status == 'FAIL':
                fail = 1
            record('leak-cjk', tell_status, f'{len(tell_findings)} line(s)' if tell_findings else '',
                   findings=tell_findings)
            log.info(f'SKIP leak scan: source and output share the spaceless {src_script} family; '
                  f'judged by the CJK tell (leak-cjk) — a line of Han both languages share is '
                  f'invisible to the tell; lean on --translations and the visual pass')
            record('leak-scan', 'SKIP', 'judged by leak-cjk')
            tell_ran = True
        else:
            log.info(f'REVIEW leak scan: source and output share the spaceless {src_script} '
                  f'family; the scan cannot tell them apart. Rely on --translations and '
                  f'the visual pass.')
            log.info(f'   (CJK tell not applied: {why})')
            record('leak-scan', 'REVIEW',
                   f'source and output share the spaceless {src_script} family',
                   findings=[Finding(None, 'script', src_script)])
    else:
        for i in range(len(jc)):
            r, iso = scan_leaks(page_gettext[i], allow, source_words=source_words,
                                src_re=src_re, script=src_script,
                                keep=keep, kept=kept)
            running.extend((i + 1, ph) for ph in r)
            isolated.extend((i + 1, w) for w in iso)

    if kept:
        uniq = sorted(set(kept))
        log.info(f'note: {len(kept)} source-language run(s) kept as the original '
              f'/Title or an allowlisted phrase: ' + '; '.join(uniq[:5]))
    if not tell_ran:
        if running:
            log.info(f'FAIL untranslated running text ({len(running)}):')
            for pg, ph in running[:10]:
                log.info(f'   p{pg}: {ph}')
            fail = 1
            record('leak-running', 'FAIL', f'{len(running)}',
                   findings=[Finding(pg, 'run', ph) for pg, ph in running])
        else:
            log.info('PASS no untranslated running text')
            record('leak-running', 'PASS')

        if isolated:
            uniq = sorted({w for _, w in isolated})
            log.info(f'REVIEW isolated source-script tokens ({len(uniq)}) - expected for '
                  f'form names, statutes and proper nouns; confirm each is deliberate:')
            log.info('   ' + ', '.join(uniq[:20]))
            record('leak-isolated', 'REVIEW', f'{len(uniq)}',
                   findings=[Finding(pg, 'token', w) for pg, w in sorted(set(isolated))])
        else:
            log.info('PASS isolated source-script tokens: none')
            record('leak-isolated', 'PASS', 'none')

    if document is not None:
        segfile = document.extraction if is_typography else load_segments_file(translations, segments)
        meta_cores = document_only_cores(segfile)
        hay = page_search
        blanks = empty_translation_targets(conf)
        if blanks:
            log.info(f'FAIL empty translation targets ({len(blanks)}):')
            for t in blanks[:30]:
                log.info(f'   {t.replace(chr(10), " ")[:80]}')
            fail = 1
            record('empty-targets', 'FAIL', f'{len(blanks)}',
                   findings=[Finding(None, 'target', t) for t in blanks])
        else:
            log.info('PASS no empty translation targets')
            record('empty-targets', 'PASS')
        # /Title and outline titles are cores but not page text; the
        # document-metadata gate is what checks those landed.
        targets = collect_translation_targets(document, exclude_cores=meta_cores)
        missing = [] if is_typography else missing_translation_targets(hay, targets)
        if is_typography:
            log.info(f'{typography_check.status} authored occurrence placement: {typography_check.message}')
            record('placement', typography_check.status, typography_check.message, typography_check.findings)
            if typography_check.status == 'FAIL':
                fail = 1
        elif missing:
            log.info(f'FAIL missing translation targets ({len(missing)}):')
            for t in missing[:30]:
                log.info(f'   {t.replace(chr(10), " ")[:80]}')
            fail = 1
            record('placement', 'FAIL', f'{len(missing)}',
                   findings=[Finding(None, 'target', t) for t in missing])
        else:
            log.info('PASS authored translations present')
            record('placement', 'PASS')

        marks = ['\n'.join(a) for a in page_actualtext]
        unmarked = unmarked_shaped_targets(marks, targets)
        if unmarked:
            log.info(f'FAIL shaped-script targets not drawn by the Story engine '
                  f'({len(unmarked)}): no /ActualText span carries them, so '
                  f'they were placed glyph by glyph — joining and conjuncts '
                  f'are broken:')
            for t in unmarked[:20]:
                log.info(f'   {t.replace(chr(10), " ")[:80]}')
            fail = 1
            record('shaped-actualtext', 'FAIL', f'{len(unmarked)}',
                   findings=[Finding(None, 'target', t) for t in unmarked])
        elif any(needs_shaping(t) for t in targets):
            log.info('PASS shaped-script targets carry /ActualText')
            record('shaped-actualtext', 'PASS')

        leftover = leftover_button_captions(
            pushbutton_captions(o),
            pushbutton_captions(jc),
            hay,
            skip=conf.get('skip') or [],
            hidden=hidden_pushbuttons(jc),
        )
        if leftover:
            log.info(f'FAIL untranslated button captions ({len(leftover)}):')
            for name, cap in leftover[:20]:
                log.info(f'   {cap} ({name})')
            fail = 1
            record('button-captions', 'FAIL', f'{len(leftover)}',
                   findings=[Finding(None, name, cap) for name, cap in leftover])
        else:
            log.info('PASS button captions')
            record('button-captions', 'PASS')

        clipped = overflowing_button_captions(jc)
        if clipped:
            log.info(f'FAIL caption wider than widget ({len(clipped)}):')
            for name, cap in clipped[:20]:
                log.info(f'   {cap} ({name})')
            fail = 1
            record('caption-width', 'FAIL', f'{len(clipped)}',
                   findings=[Finding(None, name, cap) for name, cap in clipped])
        else:
            log.info('PASS caption width')
            record('caption-width', 'PASS')

        segs = (segfile.get('segments')
                if isinstance(segfile, dict) else segfile)
        if segs is None:
            log.info('SKIP override marker gate (no segments.json beside the mapping; '
                  'pass --segments)')
            record('override-markers', 'SKIP', 'no segments.json')
        else:
            misses = override_marker_misses(conf, segs)
            if misses:
                log.info(f'FAIL override drops source marker or tail ({len(misses)}):')
                for contains, token in misses[:20]:
                    log.info(f'   "{contains}" is missing "{token}"')
                fail = 1
                record('override-markers', 'FAIL', f'{len(misses)}',
                       findings=[Finding(None, contains, token) for contains, token in misses])
            elif conf.get('overrides'):
                log.info('PASS override parts keep markers and tails')
                record('override-markers', 'PASS')

        meta_misses = document_metadata_misses(o, jc, document if is_typography else conf)
        if meta_misses:
            log.info(f'FAIL document metadata ({len(meta_misses)}):')
            for what, detail in meta_misses[:10]:
                log.info(f'   [{what}] {detail}')
            fail = 1
            record('metadata', 'FAIL', ', '.join(what for what, _ in meta_misses),
                   findings=[Finding(None, what, detail) for what, detail in meta_misses])
        else:
            log.info('PASS document metadata')
            record('metadata', 'PASS')
        if not mapping_lang:
            log.info('REVIEW document metadata: translations.json has no "lang"; '
                  'the output still declares the source language to screen '
                  'readers and hyphenation')
            record('metadata-lang', 'REVIEW', 'translations.json has no "lang"',
                   findings=[Finding(None, 'lang', 'translations.json has no "lang"')])

        report = scale_report_for(trans)
        if report is None:
            log.info(f'SKIP scaled runs: no {SCALE_REPORT} beside the output '
                  f'(a build from before it was written)')
            record('scaled-runs', 'SKIP', f'no {SCALE_REPORT}')
        elif report:
            log.info(f'REVIEW scaled runs ({len(report)}) — ship them or reword '
                  f'them, and name them in the delivery:')
            # Computed once, tolerantly, so a malformed sidecar entry (a
            # non-numeric ratio, a missing/non-integer page) cannot crash
            # either the console line below or the finding it mirrors.
            scaled = []
            for r in report:
                p = r.get('page')
                try:
                    ratio = f'{float(r.get("ratio", 1)):.2f}x'
                except (TypeError, ValueError):
                    ratio = f'{r.get("ratio")}x'
                scaled.append(Finding(p + 1 if isinstance(p, int) else None, ratio,
                                      str(r.get('key') or '')))
            for r, finding in zip(report[:20], scaled[:20]):
                log.info(f'   p{r.get("page")} {finding.where}: '
                      f'{str(r.get("key") or "")[:60]}')
            if len(report) > 20:
                log.info(f'   ... {len(report) - 20} more in {SCALE_REPORT}')
            record('scaled-runs', 'REVIEW', f'{len(report)}', findings=scaled)
        else:
            log.info('PASS scaled runs: none, everything ships at source size')
            record('scaled-runs', 'PASS')

        spans = collect_identifier_spans(o)
        missing_ids = missing_identifier_spans(
            hay, spans, allow_translate=conf.get('allow_translate') or [])
        if missing_ids:
            log.info(f'FAIL missing write/find/say identifiers ({len(missing_ids)}):')
            for t in missing_ids[:30]:
                log.info(f'   {t.replace(chr(10), " ")[:80]}')
            fail = 1
            record('identifiers', 'FAIL', f'{len(missing_ids)}',
                   findings=[Finding(None, 'identifier', t) for t in missing_ids])
        else:
            log.info('PASS write/find/say identifiers')
            record('identifiers', 'PASS')

    if typography_check is not None:
        log.info(f'{typography_check.status} typography: {typography_check.message}')
        record('typography', typography_check.status, typography_check.message, typography_check.findings)
        if typography_check.status == 'FAIL':
            fail = 1
    o.close()
    j.close()
    jc.close()
    rc = 1 if fail else 0
    if fail_on_review and rc == 0:
        reviews = sum(1 for g in gates if g.status == 'REVIEW')
        if reviews:
            log.info(f'FAIL: {reviews} REVIEW line(s) with --fail-on-review')
            rc = 1
    return rc, gates


def run_verify(orig, trans, fill_text='Test value 123', allow=None, min_ink=0.4,
               source_regex=None, source_words_from=None, allow_extra_prefix=None,
               translations=None, segments=None, fail_on_review=False,
               reference_fonts=None, typography=False, widget_text=None):
    """Run structural gates. Returns a VerifyVerdict; does not print or exit.

    Silent because the gates log and no handler is attached, not because
    stdout is redirected. The old `redirect_stdout(io.StringIO())` silenced
    the whole PROCESS for the duration — a service running two jobs lost the
    other one's output entirely, which is C5's defect and is pinned by
    `test_two_threads_running_run_verify_do_not_swallow_each_other`.
    That regression checks stdout isolation, not PyMuPDF thread safety. Use
    separate processes and job directories for concurrent PDF jobs; see
    `references/consumer-guide.md`.
    """
    rc, gates = _execute_verify(
        orig, trans, fill_text=fill_text, allow=allow, min_ink=min_ink,
        source_regex=source_regex, source_words_from=source_words_from,
        allow_extra_prefix=allow_extra_prefix, translations=translations,
        segments=segments, fail_on_review=fail_on_review,
        reference_fonts=reference_fonts, typography=typography,
        widget_text=widget_text)
    return _verdict(rc, gates, orig, trans, fail_on_review)


def _verdict(rc, gates, orig, trans, fail_on_review):
    """The verdict both entry points hand out: paths absolute, whatever the
    caller passed, so a report written from the library reads like one
    written by the CLI."""
    return VerifyVerdict(exit_code=rc, gates=tuple(gates),
                         original=os.path.abspath(orig), output=os.path.abspath(trans),
                         fail_on_review=fail_on_review)


def verify(orig, trans, fill_text='Test value 123', allow=None, min_ink=0.4,
           source_regex=None, source_words_from=None, allow_extra_prefix=None,
           translations=None, segments=None, fail_on_review=False,
           reference_fonts=None, typography=False, widget_text=None):
    """Run structural gates. Returns 0 on pass, 1 on any failure.

    The loud shape, unchanged: same signature, same printed bytes, same return
    code. It is now a thin wrapper that attaches the console handler around the
    silent body — see `run_verify` for the one a service should call.
    """
    with console():
        rc, _ = _execute_verify(
            orig, trans, fill_text=fill_text, allow=allow, min_ink=min_ink,
            source_regex=source_regex, source_words_from=source_words_from,
            allow_extra_prefix=allow_extra_prefix, translations=translations,
            segments=segments, fail_on_review=fail_on_review,
            reference_fonts=reference_fonts, typography=typography,
            widget_text=widget_text)
    return rc


def _remove_stale_report(path):
    """No report from an earlier run may survive at path. Best effort: True when
    nothing is there afterwards.

    pipeline.py rebuild writes to a fixed path, so a reused work dir is the
    normal case; a report that outlives a refused write would tell a consumer
    PASS for a job that failed, about a document it did not ask for. A missing
    report any consumer can detect; a stale one none can.
    """
    if not os.path.exists(path):
        return True
    try:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)   # Windows refuses to unlink read-only
    except OSError:
        pass
    try:
        os.remove(path)
        return True
    except OSError as exc:
        log.info(f'  (could not remove the previous report at {path}: {exc}; '
              f'what is there does not describe this run)')
        return False


def write_report(verdict, path):
    """verdict.to_dict() as JSON at path, written whole or not at all: a temp
    file beside it moved into place. A failure is printed, never raised, and
    removes an earlier report when the directory allows it (best effort: a
    read-only directory keeps the old file, and the console says so)."""
    text = json.dumps(verdict.to_dict(), ensure_ascii=False, indent=1)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix='.verify_report-', suffix='.tmp',
                                   dir=os.path.dirname(os.path.abspath(path)))
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError as exc:
        log.info(f'  (could not write {path}: {exc})')
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        _remove_stale_report(path)


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
    from ._console import missing_option_value
    value_options = ('--allow', '--report', '--fill-text', '--min-ink', '--source-regex',
        '--source-words-from', '--allow-extra-prefix', '--translations', '--segments', '--reference-fonts',
        '--widget-text')
    for token in argv:
        name, separator, _ = token.partition('=')
        if separator and name in value_options:
            log.info(f'usage: {name}=VALUE is not supported; use {name} VALUE')
            return 2
    missing = missing_option_value(argv, value_options)
    if missing:
        log.info(f'usage: {missing} requires a value')
        return 2
    orig, trans = argv[0], argv[1]
    allow = [w for w in (_arg(argv, '--allow', '') or '').split(',') if w]
    t0 = time.perf_counter()
    report = _arg(argv, '--report', None)
    if report:
        _remove_stale_report(report)   # the only report that can exist is this run's
    rc, gates = _execute_verify(
        orig, trans,
        fill_text=_arg(argv, '--fill-text', 'Test value 123'),
        allow=allow,
        min_ink=float(_arg(argv, '--min-ink', '0.4')),
        source_regex=_arg(argv, '--source-regex', None),
        source_words_from=_arg(argv, '--source-words-from', None),
        allow_extra_prefix=_arg(argv, '--allow-extra-prefix', None),
        translations=_arg(argv, '--translations', None),
        segments=_arg(argv, '--segments', None),
        fail_on_review='--fail-on-review' in argv,
        reference_fonts=_arg(argv, '--reference-fonts', None),
        typography='--typography' in argv,
        widget_text=_arg(argv, '--widget-text', None),
    )
    if report:
        write_report(_verdict(rc, gates, orig, trans, '--fail-on-review' in argv), report)
    log.info(f'elapsed {time.perf_counter()-t0:.2f}s')
    return rc


if __name__ == '__main__':
    configure_cli_streams()
    raise SystemExit(main())
