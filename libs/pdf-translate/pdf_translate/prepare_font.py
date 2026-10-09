#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stage 3 of pdf-translate: produce render-safe, small, embeddable fonts.

Subsets a font to the exact characters your translations use, and — the part
that saves you from a silent disaster — ASSERTS the result actually
rasterizes in MuPDF by rendering a sample and counting dark pixels.
`Font.has_glyph()` passing proves nothing: MuPDF silently draws NOTHING for
CJK glyphs from subsetted CFF/OTF fonts. Only TrueType-flavored (glyf) fonts
are safe. For CJK, start from a variable TTF (e.g. Google Fonts Noto Sans JP)
and pass --instance to extract a weight. See references/fonts.md for
per-script font sources. A Japanese or Chinese subset also carries 直 骨 海
東, which verify's han-forms gate renders off the embedded program; with
`lang` and the reference faces present (`--reference-fonts DIR`, default
`tests/fonts/`), the subset is judged here first and a face of the wrong
convention is refused.

It also reads the source font's OS/2.fsType and REFUSES a font whose vendor
forbids embedding or subsetting: that licence problem would otherwise end up
inside a file somebody else redistributes. --allow-restricted downgrades the
refusal to a warning if you hold a licence that permits embedding — but a
restricted-licence face is one FreeType itself declines to load, so the
rasterization assert usually refuses it anyway. Pick an OFL font (the Noto
family always is).

Usage:
  python3 prepare_font.py FONT.ttf translations.json OUT.ttf \
      [--instance wght=700] [--sample "text in target script"]
      [--allow-restricted]

Skip subsetting entirely (use the font as-is) when the font is already small,
or when it will also serve form-field input (users type characters outside
your translations — see field_fonts.py).
"""
import logging
import os
import shutil
import string
import subprocess
import sys
import tempfile
from pathlib import Path

import pymupdf

from . import han_forms, shaping_probe
from ._console import console, _arg
from ._pixels import dark_pixels
from .results import FontError, FontResult, PdfTranslateError
from .mapping import CLASSES, FORMAT, ROLES, charset_for, load_mapping
from .typography import validate_font

log = logging.getLogger(__name__)


def find_pyftsubset():
    """Locate pyftsubset: PATH, then next to the interpreter, then fontTools."""
    found = shutil.which('pyftsubset')
    if found:
        return [found]
    exe_dir = Path(sys.executable).parent
    for name in ('pyftsubset.exe', 'pyftsubset'):
        for candidate in (exe_dir / name, exe_dir / 'Scripts' / name):
            if candidate.is_file():
                return [str(candidate)]
    return [sys.executable, '-m', 'fontTools.subset']


# OS/2.fsType, the font's own embedding permission. Bit 1 set (and bits 2/3
# clear) means "restricted licence": the vendor forbids embedding at all.
# Bit 9 is "no subsetting" and bit 8 is "bitmap embedding only" — neither is
# a font this pipeline can use, since it embeds a subset outline font.
FSTYPE_RESTRICTED = 0x0002
FSTYPE_NO_SUBSET = 0x0100
FSTYPE_BITMAP_ONLY = 0x0200


def embedding_permission(path):
    """(fsType, reason) — reason is None when embedding a subset is allowed.

    A font is a licensed asset. Embedding one whose vendor forbids it puts
    the licence problem inside a file somebody else will redistribute, and
    nothing downstream can see it: the PDF renders perfectly.
    """
    try:
        from fontTools import ttLib
        with ttLib.TTFont(path, lazy=True) as font:
            os2 = font.get('OS/2')
            fs = int(getattr(os2, 'fsType', 0) or 0) if os2 is not None else 0
    except Exception as exc:
        return None, f'could not read OS/2.fsType ({exc})'
    if fs & FSTYPE_RESTRICTED and not (fs & 0x000C):
        return fs, ('the font\'s OS/2.fsType says restricted licence: the '
                    'vendor forbids embedding it in a document')
    if fs & FSTYPE_NO_SUBSET:
        return fs, ('the font\'s OS/2.fsType forbids subsetting, and this '
                    'pipeline embeds a subset')
    if fs & FSTYPE_BITMAP_ONLY:
        return fs, ('the font\'s OS/2.fsType allows bitmap embedding only; '
                    'this pipeline embeds outlines')
    return fs, None


def job_charset(conf):
    """Every character this mapping will draw, as a set.

    ONE place: when translations.json grows a block that retypeset draws,
    add it here too or the subset will not cover it. Two blocks were
    missed exactly that way and canary run 3 found both (row 30) — a
    `null` core, which is legal wherever an override covers every
    occurrence, raised on `chars.update(None)`; and `notices`, whose text
    is the one string in the file that is not the translation of an
    existing span.
    """
    chars = set(string.printable)
    for v in (conf.get('translations') or {}).values():
        # None is "covered by an override, never drawn as a plain value"
        # (row 29), not a string to iterate.
        if v:
            chars.update(str(v))
    for m in conf.get('merges') or []:
        chars.update(m.get('html') or '')
    for o in conf.get('overrides') or []:
        for part in o.get('parts') or []:
            chars.update(part.get('text') or '')
    for n in conf.get('notices') or []:
        chars.update(str(n.get('text') or ''))
    # A weight split, not a glyph.
    chars.discard('‖')
    chars.update('.…·—–「」（）『』、。・　％＄€£¥')
    return chars


def _variable_subset(font_in, chars):
    """font_in cut down to chars, still variable, as an in-memory TTFont.

    Instancing reads every glyph's variations, so instancing a 20,000-glyph
    CJK face costs 5 to 13 s where instancing its subset costs a fraction of
    a second (R-50). This subset keeps everything the instancer and the final
    subset could use: every layout feature, name record and table, glyph
    names and the .notdef outline. The final subset then prunes it exactly as
    it pruned the full instance.
    """
    from fontTools import subset
    options = subset.Options()
    options.layout_features = ['*']
    options.name_IDs = ['*']
    options.name_languages = ['*']
    options.name_legacy = True
    options.glyph_names = True
    options.notdef_outline = True
    options.legacy_kern = True
    options.symbol_cmap = True
    options.legacy_cmap = True
    options.drop_tables = []
    options.passthrough_tables = True
    font = subset.load_font(font_in, options, lazy=False)
    subsetter = subset.Subsetter(options)
    subsetter.populate(text=''.join(sorted(chars)))
    subsetter.subset(font)
    return font


def name_static_instance(font, wght):
    """Rename a face instanced at weight `wght` after that weight.

    A variable font's default instance may be a different weight than the
    one asked for (Noto Sans JP defaults to Thin), and instancing does not
    rewrite the name table. /BaseFont would then advertise "Thin" for
    Regular outlines. Cosmetic, but it misleads anyone inspecting the PDF.
    """
    try:
        fam = (font['name'].getDebugName(16)
               or font['name'].getDebugName(1) or 'Subset')
        style = {100: 'Thin', 200: 'ExtraLight', 300: 'Light',
                 400: 'Regular', 500: 'Medium', 600: 'SemiBold',
                 700: 'Bold', 800: 'ExtraBold',
                 900: 'Black'}.get(int(wght), str(int(wght)))
        fam = fam.split(' Thin')[0].strip()
        for rec in font['name'].names:
            if rec.nameID == 1:
                rec.string = fam
            elif rec.nameID == 2:
                rec.string = style
            elif rec.nameID in (4, 6):
                full = f'{fam} {style}'
                rec.string = (full if rec.nameID == 4
                              else full.replace(' ', ''))
    except Exception as e:
        log.info(f'  (name-table touch-up skipped: {e})')


def prepare_font(font_in, trf, font_out, instance=None, sample=None,
                 allow_restricted=False, reference_fonts=None, *,
                 font_class=None, font_role=None):
    """Subset (and optionally instance) a font; rasterization-assert the result.

    Returns 0 on success, 1 if the font does not rasterize the sample.

    Legacy positional arguments, printed bytes and return codes stay unchanged.
    `run_prepare_font` is the one a service calls — it returns a
    `FontResult` and raises `FontError` instead of printing and returning 1.
    """
    with console():
        try:
            run_prepare_font(font_in, trf, font_out, instance=instance,
                             sample=sample,
                             allow_restricted=allow_restricted,
                             reference_fonts=reference_fonts,
                             font_class=font_class, font_role=font_role)
        except PdfTranslateError as exc:
            log.info(exc.console_line)
            return exc.exit_code
    return 0


def run_prepare_font(font_in, trf, font_out, instance=None, sample=None,
                     allow_restricted=False, reference_fonts=None, *,
                     font_class=None, font_role=None):
    """Subset (and optionally instance) a font; rasterization-assert the result.

    Silent. Returns a FontResult. Raises FontError on every refusal, each one
    carrying the exact console line the loud wrapper prints — which is what
    keeps `prepare_font.py`'s console byte-identical while the refusals become
    data a consumer can branch on.
    """
    document = load_mapping(trf)
    typography = document.format == FORMAT
    if typography:
        if font_class not in CLASSES or font_role not in ROLES:
            raise FontError('typography preparation requires font_class and font_role',
                            face=str(font_in), reason='missing-font-role')
        selected = Path(font_in)
        font_in = str(selected if selected.is_absolute() else Path(trf).resolve().parent / selected)
        font_out = str(font_out)
        chars = charset_for(document, font_class, font_role)
        if not chars:
            raise FontError(f'no targets require {font_class}/{font_role}',
                            face=font_in, reason='missing-font-role')
        conf = {'lang': document.lang}
    else:
        if font_class is not None or font_role is not None:
            raise FontError('class/role selectors require a typography mapping',
                            face=str(font_in), reason='missing-font-role')
        conf = document.legacy
        chars = job_charset(conf)

    fs, reason = embedding_permission(font_in)
    if reason and fs is not None:
        if allow_restricted:
            log.info(f'WARNING: {reason} (fsType {fs}). Continuing because '
                  f'--allow-restricted was passed; the licence is yours to '
                  f'clear. The renderer may still refuse the font: FreeType '
                  f'declines to load a restricted-licence face at all, so '
                  f'the rasterization assert below can fail anyway.')
        else:
            raise FontError(
                'the face is not licensed for embedding',
                console_line=(
                    f'FAIL: {reason} (OS/2.fsType {fs}). Pick a font licensed '
                    f'for embedding — the Noto family is OFL and always is '
                    f'(references/fonts.md). --allow-restricted overrides this '
                    f'if you hold a licence that permits it.'),
                exit_code=1, face=font_in, reason='fstype')
    elif reason:
        log.info(f'NOTE: {reason}; embedding permission not checked.')

    cjk_job = han_forms.has_cjk(''.join(chars))
    # A conjunct-forming script gets its probe cluster added to the subset
    # (a dozen glyphs at most), so the face this job embeds can be attested
    # here, and again by verify from the font program inside the output.
    job_scripts = sorted(shaping_probe.scripts_in(''.join(chars)))
    for script in job_scripts:
        probe = shaping_probe.PROBE_FOR.get(script)
        if probe:
            chars.update(probe.text)
    if cjk_job:
        # Gate 20 renders these four off the embedded program; a Japanese or
        # Chinese subset without them is REVIEW "cannot attest" (measured: a
        # subset built from a target with no probe character lacks 直).
        chars.update(han_forms.HAN_CHARS)

    # The instanced source and the charset are working files: nothing is left
    # beside the output. The instance is of the job's subset, so it could not
    # serve as a field face anyway; field_fonts takes the variable face
    # (references/fonts.md).
    with tempfile.TemporaryDirectory(prefix='prepare_font-') as work:
        src = font_in
        if instance and typography:
            from fontTools.varLib.instancer import instantiateVariableFont
            try:
                tag, value = instance.split('=')
                with _variable_subset(font_in, chars) as font:
                    # STAT-driven names/style bits accompany real variation outlines.
                    # This must not become a metadata-only emphasis substitution.
                    instantiateVariableFont(font, {tag: float(value)}, inplace=True,
                                            updateFontNames=True)
                    src = os.path.join(work, 'instanced.ttf')
                    font.save(src)
            except (OSError, ValueError, KeyError) as exc:
                raise FontError(f'cannot instantiate target font: {exc}', face=font_in,
                                reason='uninstantiated-font') from exc
        elif instance:
            tag, val = instance.split('=')
            from fontTools.varLib.instancer import instantiateVariableFont
            src = os.path.join(work, 'instanced.ttf')
            with _variable_subset(font_in, chars) as font:
                instantiateVariableFont(font, {tag: float(val)}, inplace=True)
                name_static_instance(font, float(val))
                font.save(src)

        if typography:
            validate_font(src, font_class, font_role, chars)

        charfile = os.path.join(work, 'chars.txt')
        with open(charfile, 'w', encoding='utf-8') as f:
            f.write(''.join(sorted(chars)))
        cmd = find_pyftsubset() + [src, f'--text-file={charfile}',
                                   f'--output-file={font_out}']
        try:
            subprocess.run(cmd, check=True)
        except FileNotFoundError:
            raise FontError(
                'pyftsubset not found',
                console_line=('FAIL: pyftsubset not found. Install fonttools '
                              '(used as: python -m fontTools.subset) and retry.'),
                exit_code=1, face=font_in, reason='no-pyftsubset')

    if typography:
        validate_font(font_out, font_class, font_role, chars)
        if not sample:
            sample = ''.join(sorted(ch for ch in chars if not ch.isspace())[:12])
        validate_font(font_out, font_class, font_role, set(sample))

    if not sample:
        non_ascii = [c for c in chars if ord(c) > 0x2000]
        sample = ''.join(sorted(non_ascii)[:12]) or 'Sample text 123'
    font = pymupdf.Font(fontfile=font_out)
    doc = pymupdf.open()
    p = doc.new_page()
    tw = pymupdf.TextWriter(p.rect)
    try:
        tw.append((72, 100), sample, font=font, fontsize=14)
        tw.write_text(p)
        pix = p.get_pixmap(dpi=100, clip=pymupdf.Rect(60, 80, 560, 120))
        dark = dark_pixels(pix.samples, pix.n, below=128)
    except Exception as exc:
        # MuPDF raises rather than returning when it cannot even build a
        # face for the sample. That is the same answer as "draws nothing":
        # report it, do not traceback.
        doc.close()
        raise FontError(
            'the subset cannot draw the sample',
            console_line=(f'FAIL: the subset font cannot draw the sample '
                          f'"{sample[:30]}" ({exc}). Use a glyf-flavored TTF '
                          f'that covers the target script, or pass --sample '
                          f'text the font actually has.'),
            exit_code=1, face=font_out, reason='no-raster')
    per_char = dark / max(len(sample.strip()), 1)
    doc.close()
    if per_char < 15:
        raise FontError(
            'the face does not rasterize the sample',
            console_line=(f'FAIL: font does not rasterize sample '
                          f'"{sample[:30]}" ({dark} dark px). Use a '
                          f'glyf-flavored TTF source.'),
            exit_code=1, face=font_out, reason='no-raster')
    if job_scripts:
        # Rasterizing proves the glyphs exist; it does not prove they join.
        # Ask the subset directly (gate 18): the probe cluster must lose
        # glyphs through the Story engine, or every conjunct is broken.
        probes = shaping_probe.probe_font(font_out, scripts=job_scripts)
        for r in probes:
            log.info(r.line())
        if any(r.status == 'FAIL' for r in probes):
            raise FontError(
                'the face does not shape this job\'s conjuncts',
                console_line=(
                    f'FAIL: {font_out} does not shape the conjuncts this job '
                    f'draws; a page built with it shows consonant+halant '
                    f'where a conjunct belongs. Use a glyf TTF that carries '
                    f'the script\'s GSUB tables (references/fonts.md).'),
                exit_code=1, face=font_out, reason='no-conjuncts')
    if cjk_job:
        # Ask the subset now what verify will ask the embedded program later
        # (gate 20), so a job set with the wrong region's face stops here.
        convention = han_forms.convention_for_lang(conf.get('lang'))
        if convention and han_forms.reference_status(convention, reference_fonts)[0]:
            result = han_forms.judge_file(font_out, convention, reference_fonts)
            log.info(result.line())
            if result.status == 'FAIL':
                want = han_forms.CONVENTIONS[convention][0]
                raise FontError(
                    'the face draws the other region\'s Han forms',
                    console_line=(
                        f'FAIL: {font_out} draws the other region\'s Han '
                        f'forms; a {want} reader sees the wrong shapes for '
                        f'{han_forms.HAN_PROBES}. Use a {want} face '
                        f'(references/fonts.md).'),
                    exit_code=1, face=font_out, reason='wrong-han-convention')
    subset_bytes = os.path.getsize(font_out)
    log.info(f'OK: {font_out} ({subset_bytes//1024} KB, '
             f'{len(chars)} chars, render check {dark} px)')
    return FontResult(output=font_out, face=font_in,
                      roles=(font_role,) if typography else tuple(sorted((conf.get('fonts') or {}).keys())),
                      glyphs_added=len(chars), subset_bytes=subset_bytes,
                      instance=instance or '')


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    from ._console import missing_option_value
    missing = missing_option_value(argv, ('--instance', '--sample', '--reference-fonts', '--font-class', '--font-role'))
    if missing:
        with console():
            log.info(f'usage: {missing} requires a value')
        return 2
    font_in, trf, font_out = argv[0], argv[1], argv[2]
    instance = _arg(argv, '--instance')
    sample = _arg(argv, '--sample')
    reference_fonts = _arg(argv, '--reference-fonts')
    font_class = _arg(argv, '--font-class')
    font_role = _arg(argv, '--font-role')
    return prepare_font(font_in, trf, font_out, instance=instance,
                        sample=sample,
                        allow_restricted='--allow-restricted' in argv,
                        reference_fonts=reference_fonts,
                        font_class=font_class, font_role=font_role)


if __name__ == '__main__':
    raise SystemExit(main())
