# -*- coding: utf-8 -*-
"""Gate 20, han-forms: the face that drew a CJK page uses the forms its
language expects.

Han unification gives 直 骨 海 one code point each; a Japanese face and a
Simplified Chinese face draw them differently, and a reader sees the wrong
region at once. This skill never selects a face — the caller does — so the
delivered document is judged: every embedded face on a page that draws CJK
text is rendered off its own font program (the way gate 18 re-probes a
conjunct face) and compared pixel for pixel with the Noto reference of the
language's convention and with the other convention's, both instanced at
the embedded face's weight.

Measured (docs/BRIEF-han-forms-gate.md; PyMuPDF 1.28.2, the fetched Noto
Sans JP / SC variable faces): a face against its own reference at equal
weight reads 0.000 on every probe; against the other region 0.17–0.51;
weight alone (400 vs 100) reads 0.51–0.55, as large as region, which is why
references are instanced at the delivered weight. 東 is drawn the same in
both conventions: a face that differs from both references on 東 is not a
Noto face and is REVIEW, never matched to the nearer reference.

Nothing here reads the network or prints. The reference faces are files the
caller provides: tests/fonts/ in a checkout, a directory of its own in a
service (verify --reference-fonts DIR, run_verify(reference_fonts=DIR)).
"""
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import pymupdf

HAN_PROBES = '\u76f4\u9aa8\u6d77'       # 直 骨 海 — drawn differently in JP and SC
HAN_CONTROL = '\u6771'                   # 東 — the same in both: the family check
HAN_CHARS = HAN_PROBES + HAN_CONTROL     # what prepare_font adds to every CJK subset

OWN_MAX = 0.02          # a face equals its own reference (measured 0.000)
OTHER_MIN = 0.10        # and differs from the other region (measured 0.17–0.51)
MIN_PROBES_PRESENT = 2  # fewer of the three probe glyphs present: SKIP, not judged
MIN_SEPARATING = 2      # probes, of the three, that must separate for a verdict

# convention -> (display name, reference file in the reference directory; None
# when no reference has been measured for it)
CONVENTIONS = {
    'JP': ('Japanese', 'NotoSansJP-VF.ttf'),
    'SC': ('Simplified Chinese', 'NotoSansSC-VF.ttf'),
    'TC': ('Traditional Chinese', None),
    'KR': ('Korean', None),
}
OTHER = {'JP': 'SC', 'SC': 'JP'}

# verify.SCRIPT_RANGES['CJK'], repeated here because verify imports this
# module; tests.test_han_forms pins the two against each other.
CJK_RANGES = ((0x3040, 0x30FF), (0x31F0, 0x31FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF),
              (0xF900, 0xFAFF), (0xFF66, 0xFF9F), (0x20000, 0x2FA1F))

RENDER_SIZE, RENDER_ZOOM, RENDER_ORIGIN, RENDER_PAGE = 48, 4, (40, 120), 200


def is_cjk(ch):
    """The test verify.script_of makes for 'CJK': a letter in the Han, kana
    or half-width kana blocks. Punctuation (。、「) is not."""
    if not unicodedata.category(ch).startswith('L'):
        return False
    o = ord(ch)
    return any(a <= o <= b for a, b in CJK_RANGES)


def has_cjk(text):
    return any(is_cjk(ch) for ch in text or '')


def font_key(name):
    """A font name reduced to what survives the spelling differences between a
    PDF's /BaseFont, a FontDescriptor's /FontName and a program's PostScript
    name: subset tag, spaces, hyphens and underscores removed, lower case."""
    name = re.sub(r'^[A-Z]{6}\+', '', str(name or ''))
    return re.sub(r'[\s\-_]', '', name).lower()


def convention_for_lang(lang):
    """'JP', 'SC', 'TC' or 'KR' for a BCP 47 tag (ja, zh-Hans, zh-TW, ko, jpn…);
    None for any other language or no language."""
    tags = [t for t in (lang or '').strip().lower().replace('_', '-').split('-') if t]
    if not tags:
        return None
    if tags[0] in ('ja', 'jpn'):
        return 'JP'
    if tags[0] in ('ko', 'kor'):
        return 'KR'
    if tags[0] in ('zh', 'zho', 'chi'):
        if 'hant' in tags[1:] or any(t in ('tw', 'hk', 'mo') for t in tags[1:]):
            return 'TC'
        return 'SC'
    return None


def is_language_tag(lang):
    """A well-formed BCP 47 tag (ja, zh-Hans, es-MX, jpn; '_' accepted for '-'):
    a 2–3 letter primary subtag and optional subtags of 1–8 letters or digits.
    A display name (Japanese, 日本語) is not one."""
    return bool(re.fullmatch(r'[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*',
                             (lang or '').strip().replace('_', '-')))


def default_reference_dir():
    """tests/fonts/ of a checkout — the fetcher's faces. An installed package
    has no such directory; a consumer passes its own."""
    return Path(__file__).resolve().parents[1] / 'tests' / 'fonts'


def _dir(reference_dir):
    return Path(reference_dir) if reference_dir is not None else default_reference_dir()


def reference_path(convention, reference_dir=None):
    """The convention's variable reference face in the directory, or None."""
    _, filename = CONVENTIONS.get(convention, (None, None))
    if not filename:
        return None
    path = _dir(reference_dir) / filename
    return path if path.is_file() else None


def reference_face(convention, weight, reference_dir=None):
    """Path of the convention's reference instanced at weight, or None when the
    convention has no reference, the file is missing, or the wght axis does
    not reach the weight.

    Instancing takes seconds (JP 4.3 s, SC 7.7 s measured), so the instance is
    cached as <stem>-wght<weight>.ttf beside the source when that directory
    can be written, else under the system temp directory. It is written to a
    temp name and moved into place: a concurrent job never reads a
    half-written file, and two writers produce the same bytes.
    """
    src = reference_path(convention, reference_dir)
    if src is None:
        return None
    weight = int(weight)
    name = f'{src.stem}-wght{weight}.ttf'
    for cache_dir in (src.parent, Path(tempfile.gettempdir()) / 'pdf-translate-han-forms'):
        cached = cache_dir / name
        if cached.is_file():
            return cached
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=name + '.', suffix='.tmp.ttf', dir=str(cache_dir))
        except OSError:
            continue
        os.close(fd)
        try:
            _instance(src, weight, tmp)
            os.replace(tmp, cached)
            return cached
        except OSError:
            _discard(tmp)
            continue
        except Exception:       # not a variable font, or the axis does not reach the weight
            _discard(tmp)
            return None
    return None


def _discard(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _instance(src, weight, out):
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer
    with TTFont(str(src)) as font:
        axes = {a.axisTag: a for a in font['fvar'].axes} if 'fvar' in font else {}
        wght = axes.get('wght')
        if wght is None or not wght.minValue <= weight <= wght.maxValue:
            raise ValueError(f'{src.name} has no wght axis reaching {weight}')
        instancer.instantiateVariableFont(font, {'wght': weight}, inplace=True)
        font.save(out)


def render(font, ch):
    """Grey raster (samples, width, height) of one character drawn with a
    pymupdf.Font at the geometry the table was measured at."""
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=RENDER_PAGE, height=RENDER_PAGE)
        tw = pymupdf.TextWriter(page.rect)
        tw.append(RENDER_ORIGIN, ch, font=font, fontsize=RENDER_SIZE)
        tw.write_text(page)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM),
                              colorspace=pymupdf.csGRAY, alpha=False)
        return bytes(pix.samples), pix.width, pix.height
    finally:
        doc.close()


_INK = bytes(1 if v < 128 else 0 for v in range(256))


def diff_ratio(a, b):
    """Pixels that are ink in one raster and not the other, over the pixels
    that are ink in either: 0.0 for identical rasters, 1.0 for rasters of
    different size. The samples become one bit per pixel (ink or not) packed
    into an int, so the union and the difference are two bit counts."""
    if a[1:] != b[1:]:
        return 1.0
    ma = int.from_bytes(a[0].translate(_INK), 'big')
    mb = int.from_bytes(b[0].translate(_INK), 'big')
    union = (ma | mb).bit_count()
    return (ma ^ mb).bit_count() / union if union else 0.0


def weight_of(program):
    """OS/2 usWeightClass of a font program held in memory, or None when it
    cannot be read (a bare CFF program, a Type 3 face)."""
    try:
        from fontTools.ttLib import TTFont
        with TTFont(BytesIO(program), lazy=True) as font:
            return int(font['OS/2'].usWeightClass)
    except Exception:
        return None


def program_psname(program):
    """PostScript name (name id 6, else the full name 4) of a font program held
    in memory, or '' when it cannot be read."""
    try:
        from fontTools.ttLib import TTFont
        with TTFont(BytesIO(program), lazy=True) as font:
            names = font['name']
            return names.getDebugName(6) or names.getDebugName(4) or ''
    except Exception:
        return ''


@dataclass(frozen=True)
class HanResult:
    """One face judged against one convention. status is PASS, FAIL, REVIEW
    or SKIP; SKIP means the face carries too few of the probes to be judged
    (a Latin face on a CJK page), and the report turns a page with nothing
    but SKIPs into "cannot attest"."""
    status: str
    face: str
    convention: str      # 'JP' / 'SC' / 'TC' / 'KR', or '' when no lang named one
    weight: int | None
    ratios: tuple        # ((char, own, other), …) for the probes judged
    reason: str

    def line(self):
        """One report line in verify's PASS/FAIL/REVIEW/SKIP style."""
        want = CONVENTIONS[self.convention][0] if self.convention in CONVENTIONS else ''
        head = f'{self.status} han-forms{" " + want if want else ""}'
        if self.status in ('PASS', 'FAIL'):
            # PASS/FAIL only reach here for JP/SC: reference_status bounces TC/KR
            # (no reference measured) to REVIEW before a convention is ever judged.
            other = CONVENTIONS[OTHER[self.convention]][0]
            cells = ', '.join(f'{ch} {own:.3f}/{oth:.3f}' for ch, own, oth in self.ratios)
            drawn = want if self.status == 'PASS' else other
            return (f'{head}: {self.face} draws {drawn} forms '
                    f'({cells} vs {want}/{other} at wght {self.weight})')
        return f'{head}: {self.reason}'


def reference_status(convention, reference_dir=None):
    """(True, '') when the convention can be judged with the faces in the
    directory; else (False, reason) — the document-level REVIEW reasons."""
    if convention not in CONVENTIONS:
        return False, ('no "lang" names the convention; pass lang in translations.json '
                       '(ja, zh-Hans, …) so the delivered faces can be judged')
    name, filename = CONVENTIONS[convention]
    if filename is None:
        return False, (f'no reference face measured for {name}; the gate judges Japanese '
                       f'and Simplified Chinese')
    ref_dir = _dir(reference_dir)
    missing = [CONVENTIONS[c][1] for c in (convention, OTHER[convention])
               if not (ref_dir / CONVENTIONS[c][1]).is_file()]
    if missing:
        return False, (f'reference faces not found in {ref_dir} ({", ".join(missing)}); '
                       f'fetch them (tools/fetch_test_fonts.py) or pass --reference-fonts DIR')
    return True, ''


def judge_program(program, convention, reference_dir=None, face=''):
    """Judge a font program (bytes) against the convention's reference and the
    other convention's, both instanced at the program's own weight."""
    ref_dir = _dir(reference_dir)
    face = face or ''
    ok, reason = reference_status(convention, ref_dir)
    if not ok:
        return HanResult('REVIEW', face, convention if convention in CONVENTIONS else '',
                         None, (), reason)
    try:
        font = pymupdf.Font(fontbuffer=program)
    except Exception as exc:
        return HanResult('REVIEW', face, convention, None, (),
                         f'could not load the embedded font {face}: {exc}')
    face = face or font.name
    present = [ch for ch in HAN_PROBES if font.has_glyph(ord(ch))]
    if len(present) < MIN_PROBES_PRESENT or not font.has_glyph(ord(HAN_CONTROL)):
        return HanResult('SKIP', face, convention, None, (),
                         f'{face} does not carry the probes "{HAN_CHARS}"')
    weight = weight_of(program)
    if weight is None:
        return HanResult('REVIEW', face, convention, None, (),
                         f'cannot read the weight (OS/2) of {face}; cannot read OS/2 (a bare '
                         f'CFF or Type 3 program)')
    own_ref = reference_face(convention, weight, ref_dir)
    other_ref = reference_face(OTHER[convention], weight, ref_dir)
    if own_ref is None or other_ref is None:
        return HanResult('REVIEW', face, convention, weight, (),
                         f'the reference faces cannot be instanced at wght {weight} for {face}')
    own_font = pymupdf.Font(fontfile=str(own_ref))
    other_font = pymupdf.Font(fontfile=str(other_ref))

    def pair(ch):
        mine = render(font, ch)
        return diff_ratio(mine, render(own_font, ch)), diff_ratio(mine, render(other_font, ch))

    c_own, c_other = pair(HAN_CONTROL)
    if c_own > OWN_MAX or c_other > OWN_MAX:
        return HanResult('REVIEW', face, convention, weight, ((HAN_CONTROL, c_own, c_other),),
                         f'no reference of this family: {face} differs from both Noto references '
                         f'on {HAN_CONTROL} ({c_own:.3f}/{c_other:.3f}), which is drawn the same '
                         f'in both conventions — not a Noto face, or not the same build or weight '
                         f'as the references (references/fonts.md); check a render with a reader '
                         f'of the language')
    ratios = tuple((ch, *pair(ch)) for ch in present)
    own_wins = sum(own <= OWN_MAX and other >= OTHER_MIN for _, own, other in ratios)
    other_wins = sum(other <= OWN_MAX and own >= OTHER_MIN for _, own, other in ratios)
    if own_wins >= MIN_SEPARATING and not other_wins:
        return HanResult('PASS', face, convention, weight, ratios, '')
    if other_wins >= MIN_SEPARATING and not own_wins:
        return HanResult('FAIL', face, convention, weight, ratios, '')
    cells = ', '.join(f'{ch} {own:.3f}/{other:.3f}' for ch, own, other in ratios)
    return HanResult('REVIEW', face, convention, weight, ratios,
                     f'inconclusive for {face} ({cells} vs own/other at wght {weight}); '
                     f'check a render with a reader of the language')


def judge_file(fontfile, convention, reference_dir=None):
    """judge_program for a font file (prepare_font's subset)."""
    from .shaping_probe import font_psname
    with open(fontfile, 'rb') as f:
        program = f.read()
    return judge_program(program, convention, reference_dir,
                         face=font_psname(fontfile) or os.path.basename(str(fontfile)))
