# -*- coding: utf-8 -*-
"""Conjunct-shaping probe: does this face, in this renderer, form conjuncts?

Arabic has a glyph-form tell (isolated presentation forms mean the run was
drawn letter by letter; verify gate 12). Devanagari and the other
conjunct-forming scripts have none in the text layer: the shaper writes
glyph ids, so a broken conjunct and a correct one read the same. What does
change is the number of glyphs drawn. Render a probe string that contains
a merging cluster twice off the SAME face — once glyph by glyph
(``insert_text``, no shaping) and once through the Story engine
(``insert_htmlbox``, HarfBuzz, the path retypeset uses for every shaped
run) — and count glyphs with ``page.get_texttrace()``. Shaping merges the
cluster, so the shaped count is lower.

Measured (PyMuPDF 1.28.0 and 1.28.2, the Noto faces ``tools/fetch_test_fonts.py``
fetches), code points -> naive glyphs / shaped glyphs:

    Devanagari  क्षत्रिय   8 -> 8 / 4     ksha and tra conjuncts
    Devanagari  हिन्दी     6 -> 6 / 5     half-form nda
    Devanagari  कमल        3 -> 3 / 3     no conjunct: NOT a probe, 1.00 either way
    Bengali     ক্ষ        3 -> 3 / 1     ksha ligature
    Bengali     বাংলা      5 -> 5 / 5     no conjunct: NOT a probe
    Tamil       க்ஷ        3 -> 3 / 1     ksha ligature
    Khmer       ខ្មែរ      5 -> 5 / 4     coeng ma stacks below kha
    Myanmar     သင်္ဘော    7 -> 7 / 5     kinzi and stacked bha
    Myanmar     မြန်မာ     6 -> 6 / 6     medial ra, asat: no stack, NOT a probe
    Arabic      مكتبة      5 -> 5 / 8     shaping ADDS glyphs: the tell runs backwards
    Thai        ป้า        3 -> 3 / 3     mark stacking never changes the count
    Thai        กำไร       4 -> 4 / 5     sara am decomposes: backwards
    Hebrew      שָׁלוֹם    7 -> 7 / 7     niqqud stacks: blind

Three consequences the code enforces:

1. Only probe strings known to contain a merging cluster are judged
   (``PROBES``). A blanket "shaped < naive" rule on arbitrary text produces
   false failures (कमल, বাংলা, မြန်မာ shape correctly at 1.00).
2. Arabic is not judged here; gate 12 reads its letterforms.
3. Thai, Lao and Hebrew-with-niqqud (``BLIND_SCRIPTS``) are reported as
   REVIEW, never PASS: they need a reference raster or a reader. Conjunct
   scripts nobody has measured a face for (``UNMEASURED_CONJUNCT_SCRIPTS``)
   are REVIEW too — an attestation needs an expected count.

Method warning (AGENTS.md): ``insert_text(fontfile=...)`` without
``fontname=`` silently falls back to Helvetica and draws one middle dot per
code point, which looks exactly like an unshaped run. Both arms therefore
assert which font drew the run, read from the texttrace span, and a
mismatch is a FAIL regardless of the counts.
"""
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import PurePath

import pymupdf


@dataclass(frozen=True)
class Probe:
    script: str
    text: str
    expected_shaped: int    # glyphs the Noto reference face draws through the Story engine
    note: str


PROBES = (
    Probe('Devanagari', 'क्षत्रिय', 4, 'ksha and tra conjuncts; Noto Sans Devanagari 8 -> 4'),
    Probe('Bengali', 'ক্ষ', 1, 'ksha ligature; Noto Sans Bengali 3 -> 1'),
    Probe('Tamil', 'க்ஷ', 1, 'ksha ligature; Noto Sans Tamil 3 -> 1'),
    Probe('Khmer', 'ខ្មែរ', 4, 'coeng ma below kha; Noto Sans Khmer 5 -> 4'),
    Probe('Myanmar', 'သင်္ဘော', 5, 'kinzi and stacked bha; Noto Sans Myanmar 7 -> 5'),
)
PROBE_FOR = {p.script: p for p in PROBES}

# Scripts where correct shaping never changes the glyph count, so this
# tell cannot see a broken run. Reported as REVIEW, never PASS.
BLIND_SCRIPTS = {
    'Thai': 'mark stacking never changes the count (ป้า 3 -> 3) and sara am adds one (กำไร 4 -> 5)',
    'Lao': 'mark stacking, the same structure as Thai',
    'Hebrew niqqud': 'the marks stack on the base letter (שָׁלוֹם 7 -> 7)',
}

# Conjunct-forming scripts retypeset routes through the Story engine for
# which no face has been measured here, so there is no expected count to
# attest against. Reported as REVIEW until a probe row is measured.
UNMEASURED_CONJUNCT_SCRIPTS = frozenset((
    'Gurmukhi', 'Gujarati', 'Oriya', 'Telugu', 'Kannada', 'Malayalam',
    'Sinhala', 'Tibetan',
))

# Letter ranges for the scripts this module talks about. Hebrew appears
# only as its niqqud marks: bare Hebrew needs direction, not shaping.
SCRIPT_RANGES = {
    'Devanagari': ((0x0900, 0x097F), (0xA8E0, 0xA8FF)),
    'Bengali': ((0x0980, 0x09FF),),
    'Gurmukhi': ((0x0A00, 0x0A7F),),
    'Gujarati': ((0x0A80, 0x0AFF),),
    'Oriya': ((0x0B00, 0x0B7F),),
    'Tamil': ((0x0B80, 0x0BFF),),
    'Telugu': ((0x0C00, 0x0C7F),),
    'Kannada': ((0x0C80, 0x0CFF),),
    'Malayalam': ((0x0D00, 0x0D7F),),
    'Sinhala': ((0x0D80, 0x0DFF),),
    'Thai': ((0x0E00, 0x0E7F),),
    'Lao': ((0x0E80, 0x0EFF),),
    'Tibetan': ((0x0F00, 0x0FFF),),
    'Myanmar': ((0x1000, 0x109F), (0xA9E0, 0xA9FF), (0xAA60, 0xAA7F)),
    'Khmer': ((0x1780, 0x17FF),),
    'Hebrew niqqud': ((0x05B0, 0x05C7),),
}

PROBE_FONTSIZE = 24


@dataclass(frozen=True)
class ProbeResult:
    script: str
    probe: str
    codepoints: int
    naive_glyphs: int
    shaped_glyphs: int
    expected_shaped: int
    naive_font: str
    shaped_font: str
    expected_font: str
    status: str         # PASS | FAIL | SKIP | REVIEW
    reason: str

    def line(self):
        """One report line in verify's PASS/FAIL/REVIEW/SKIP style."""
        if self.status in ('PASS', 'FAIL'):
            return (f'{self.status} conjunct shaping {self.script}: "{self.probe}" '
                    f'{self.naive_glyphs} -> {self.shaped_glyphs} glyphs '
                    f'({self.expected_font}); {self.reason}')
        return f'{self.status} conjunct shaping {self.script}: {self.reason}'


# Every character any SCRIPT_RANGES entry covers, as one class: scripts_in
# looks only at the characters it matches (R-48).
_ANY_SCRIPT = re.compile('[' + ''.join(f'{re.escape(chr(a))}-{re.escape(chr(b))}'
                                       for ranges in SCRIPT_RANGES.values()
                                       for a, b in ranges) + ']')


def scripts_in(text):
    """Names from SCRIPT_RANGES whose characters appear in text."""
    found = set()
    for ch in set(_ANY_SCRIPT.findall(text or '')):
        o = ord(ch)
        for name, ranges in SCRIPT_RANGES.items():
            if any(a <= o <= b for a, b in ranges):
                found.add(name)
                break
    return found


def _normalize_font_name(name):
    # MuPDF may report an embedded face with its subset tag (ABCDEF+Name).
    name = re.sub(r'^[A-Z]{6}\+', '', str(name or ''))
    return re.sub(r'[\s\-_]', '', name).lower()


def font_psname(fontfile):
    """PostScript name (name id 6), falling back to the full name (4)."""
    try:
        from fontTools import ttLib
        with ttLib.TTFont(fontfile, lazy=True) as font:
            names = font['name']
            return (names.getDebugName(6) or names.getDebugName(4) or '')
    except Exception:
        return ''


def font_covers(fontfile, text):
    """True if the face's cmap maps every non-space character of text."""
    try:
        from fontTools import ttLib
        with ttLib.TTFont(fontfile, lazy=True) as font:
            cmap = font.getBestCmap() or {}
    except Exception:
        return False
    return all(ord(ch) in cmap for ch in text if not ch.isspace())


def _css_url(path):
    escaped = re.sub(r'[\x00-\x1f\x7f"\\]',
                     lambda m: f'\\{ord(m[0]):x} ', PurePath(path).as_posix())
    return f'"{escaped}"'


def _texttrace_glyphs(page):
    """(glyphs drawn, {font names}) from the page's text trace."""
    glyphs, fonts = 0, set()
    for span in page.get_texttrace():
        chars = span.get('chars') or []
        if not chars:
            continue
        glyphs += len(chars)
        fonts.add(str(span.get('font') or ''))
    return glyphs, fonts


def _one_font(fonts):
    return sorted(fonts)[0] if len(fonts) == 1 else ' + '.join(sorted(fonts)) or '(none)'


def naive_glyphs(fontfile, text, fontsize=PROBE_FONTSIZE):
    """Glyphs drawn when the text is placed glyph by glyph, no shaping.

    fontname= is passed on purpose: without it MuPDF falls back to
    Helvetica and the result is meaningless (AGENTS.md method warning).
    """
    doc = pymupdf.open()
    try:
        page = doc.new_page()
        page.insert_text((40, 80), text, fontname='probe', fontfile=str(fontfile),
                         fontsize=fontsize)
        count, fonts = _texttrace_glyphs(page)
    finally:
        doc.close()
    return count, _one_font(fonts)


def shaped_glyphs(fontfile, text, fontsize=PROBE_FONTSIZE):
    """Glyphs drawn when the same face places the text through the Story
    engine (HarfBuzz), the path retypeset uses for every shaped run."""
    doc = pymupdf.open()
    try:
        page = doc.new_page()
        css = f'@font-face {{font-family: probe; src: url({_css_url(fontfile)});}}'
        body = (f'<p style="font-family: probe; font-size: {fontsize}pt; '
                f'margin: 0; padding: 0">{text}</p>')
        page.insert_htmlbox(pymupdf.Rect(40, 40, 560, 140), body, css=css,
                            archive=pymupdf.Archive(
                                os.path.dirname(os.path.abspath(fontfile))
                                or '.'), scale_low=0)
        count, fonts = _texttrace_glyphs(page)
    finally:
        doc.close()
    return count, _one_font(fonts)


def judge(probe, naive_glyphs, shaped_glyphs, naive_font, shaped_font, expected_font):
    """Turn the two measurements into a verdict. Pure; no rendering."""
    cps = len(probe.text)

    def result(status, reason):
        return ProbeResult(script=probe.script, probe=probe.text, codepoints=cps,
                           naive_glyphs=naive_glyphs, shaped_glyphs=shaped_glyphs,
                           expected_shaped=probe.expected_shaped,
                           naive_font=naive_font, shaped_font=shaped_font,
                           expected_font=expected_font, status=status, reason=reason)

    if not expected_font:
        return result('FAIL', 'cannot identify the face (no usable name table), so '
                              'nothing proves which font drew the probe')
    want = _normalize_font_name(expected_font)
    for arm, drawn in (('naive', naive_font), ('shaped', shaped_font)):
        if _normalize_font_name(drawn) != want:
            return result('FAIL', f'{arm} arm was drawn by {drawn or "(none)"}, not '
                                  f'{expected_font}: font fallback, the counts mean nothing')
    if naive_glyphs != cps:
        return result('FAIL', f'naive arm drew {naive_glyphs} glyphs for {cps} code points; '
                              f'the unshaped arm must be one glyph per code point')
    if shaped_glyphs >= naive_glyphs:
        return result('FAIL', f'no conjunct formed: the Story engine drew {shaped_glyphs} '
                              f'glyphs, glyph-by-glyph drew {naive_glyphs}; the face has no '
                              f'usable GSUB for {probe.script}')
    reason = f'{probe.note.split(";")[0]}'
    if shaped_glyphs != probe.expected_shaped:
        reason += (f' (this face: {shaped_glyphs} glyphs; the Noto reference face: '
                   f'{probe.expected_shaped})')
    return result('PASS', reason)


def review_reason(script):
    """Why a script gets REVIEW instead of a verdict; None if it is judged."""
    if script in BLIND_SCRIPTS:
        return (f'no glyph-count tell: {BLIND_SCRIPTS[script]}; needs a reference '
                f'raster or a reader of the script')
    if script in UNMEASURED_CONJUNCT_SCRIPTS:
        return ('no probe measured for this script; add a row to '
                'shaping_probe.PROBES with a measured face first')
    return None


def review_result(script, reason):
    return ProbeResult(script=script, probe='', codepoints=0, naive_glyphs=0,
                       shaped_glyphs=0, expected_shaped=0, naive_font='',
                       shaped_font='', expected_font='', status='REVIEW', reason=reason)


def probe_font(fontfile, scripts=None):
    """Probe one face. Returns a ProbeResult per requested script.

    scripts=None probes every row in PROBES. A requested script that is
    blind or unmeasured comes back as REVIEW; one the face does not cover
    as SKIP; an unknown script yields no row.
    """
    fontfile = str(fontfile)
    wanted = list(scripts) if scripts is not None else [p.script for p in PROBES]
    expected_font = font_psname(fontfile)
    out = []
    for script in wanted:
        why = review_reason(script)
        if why:
            out.append(review_result(script, why))
            continue
        probe = PROBE_FOR.get(script)
        if probe is None:
            continue
        if not font_covers(fontfile, probe.text):
            out.append(ProbeResult(script=script, probe=probe.text, codepoints=len(probe.text),
                                   naive_glyphs=0, shaped_glyphs=0,
                                   expected_shaped=probe.expected_shaped, naive_font='',
                                   shaped_font='', expected_font=expected_font,
                                   status='SKIP', reason=f'{expected_font or fontfile} does '
                                   f'not cover the probe "{probe.text}"'))
            continue
        n_count, n_font = naive_glyphs(fontfile, probe.text)
        s_count, s_font = shaped_glyphs(fontfile, probe.text)
        out.append(judge(probe, n_count, s_count, n_font, s_font, expected_font))
    return out


def probe_font_bytes(buffer, ext='ttf', scripts=None):
    """Probe a font program held in memory (an embedded font extracted
    from a PDF). Writes it to a temporary file for the renderer."""
    fd, path = tempfile.mkstemp(suffix=f'.{ext or "ttf"}', prefix='probe-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(buffer)
        return probe_font(path, scripts)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
