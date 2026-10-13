"""Falsifying source-state cases for retained PDF operator replay."""
import json
from pathlib import Path

import pikepdf
import pymupdf
import pytest

from pdf_translate import run_extract, run_retypeset, run_strip
from .test_source_retention import _target_font, _spans


def _job(work, prefix=b'', *, body=None, font_alias=None, page_size=(400, 240)):
    template = work / 'template.pdf'
    original = work / 'original.pdf'
    with pymupdf.open() as document:
        page = document.new_page(width=page_size[0], height=page_size[1])
        page.insert_text((40, 60), 'KEEP', fontname='helv', fontsize=14)
        document.save(template)
    with pikepdf.open(template) as document:
        page = document.pages[0]
        if font_alias is not None:
            page.Resources.Font[font_alias] = page.Resources.Font['/helv']
            del page.Resources.Font['/helv']
        stream = (body if body is not None else
                  b'BT /helv 14 Tf 1 0 0 1 40 180 Tm (KEEP) Tj ET\n')
        page.Contents = document.make_stream(prefix + stream)
        document.save(original)
    stripped = work / 'stripped.pdf'
    run_strip(str(original), str(stripped))
    extracted = run_extract(str(original), str(work))
    font = _target_font(work / 'target.ttf')
    mapping = work / 'mapping.json'
    mapping.write_text(json.dumps({'lang': 'es', 'fonts': {'regular': str(font)},
                                  'translations': {'KEEP': 'KEEP', 'X': 'Changed'}}))
    return dict(original=original, stripped=stripped, segments=Path(extracted.segments_path),
                mapping=mapping, output=work / 'out.pdf')


def _render(job):
    return run_retypeset(str(job['stripped']), str(job['segments']), str(job['mapping']),
                        str(job['output']), original=str(job['original']))


def _native_text(path, text):
    """Observe source spans and painted glyphs independently through MuPDF."""
    spans, glyphs = [], []
    with pymupdf.open(path) as document:
        for page_number, page in enumerate(document):
            for block in page.get_text('rawdict')['blocks']:
                for line in block.get('lines', []):
                    for span in line['spans']:
                        if ''.join(char['c'] for char in span['chars']) == text:
                            spans.append({
                                'page': page_number, 'font': span['font'],
                                'size': span['size'], 'color': span['color'],
                                'origin': tuple(span['origin']), 'bbox': tuple(span['bbox']),
                                'direction': tuple(line['dir']),
                                'chars': tuple((char['c'], tuple(char['origin']),
                                                tuple(char['bbox'])) for char in span['chars']),
                            })
            for trace in page.get_texttrace():
                painted = ''.join(chr(char[0]) for char in trace['chars'])
                start = painted.find(text)
                while start >= 0:
                    glyphs.append({
                        'page': page_number, 'font': trace['font'], 'size': trace['size'],
                        'color': trace['color'], 'opacity': trace['opacity'],
                        'type': trace['type'], 'wmode': trace['wmode'],
                        'direction': tuple(trace['dir']),
                        'chars': tuple(trace['chars'][start:start + len(text)]),
                    })
                    start = painted.find(text, start + len(text))
    return spans, glyphs


def _assert_redrawn(job, reason=None, core='KEEP'):
    """An unvouched identity run is redrawn and reported, never replayed.

    The document still delivers (a-job-always-delivers): retention leaves the
    run out instead of refusing the job, and the source operator is not copied.
    """
    source_fonts = {span['font'] for span in _native_text(job['original'], core)[0]}
    assert source_fonts
    result = _render(job)
    assert job['output'].exists()
    rows = [row for row in result.warnings
            if row['kind'] == 'source_not_retained' and row['core'] == core]
    assert rows, result.warnings
    if reason is not None:
        assert rows[0]['reason'] == reason
    drawn = _native_text(job['output'], core)[0]
    assert drawn and not any(span['font'] in source_fonts for span in drawn)
    return result


def _rgb_pixels(path, *, clip=None):
    with pymupdf.open(path) as document:
        pixels = document[0].get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=clip,
                                        colorspace=pymupdf.csRGB, alpha=False)
        return pixels.width, pixels.height, pixels.n, pixels.samples


def test_retained_source_accepts_zero_operand_line_advance_after_sole_show(tmp_path):
    job = _job(tmp_path, body=(
        b'BT /helv 14 Tf 1 0 0 1 40 180 Tm (KEEP) Tj T* ET\n'))
    source_spans = _spans(job['original'], 'KEEP')
    source_native = _native_text(job['original'], 'KEEP')
    source_pixels = _rgb_pixels(job['original'])
    assert len(source_spans) == len(source_native[0]) == len(source_native[1]) == 1
    assert len(source_native[1][0]['chars']) == 4
    assert source_native[1][0]['type'] == 0 and source_native[1][0]['opacity'] == 1
    assert min(source_pixels[3]) < 255

    _render(job)

    assert _spans(job['output'], 'KEEP') == source_spans
    assert _native_text(job['output'], 'KEEP') == source_native
    assert _rgb_pixels(job['output']) == source_pixels
    from pdf_translate import source_retention
    with pikepdf.open(job['original']) as original, pikepdf.open(job['output']) as output:
        assert source_retention._resource_signature(original.pages[0].Resources.Font['/helv']) == (
            source_retention._resource_signature(output.pages[0].Resources.Font['/helv']))


def test_retained_tj_slice_keeps_glyphs_and_pixels_with_trailing_line_advance(tmp_path):
    job = _job(tmp_path, page_size=(800, 240), body=(
        b'BT /helv 14 Tf 1 0 0 1 40 180 Tm [(KEEP) -15000 (X)] TJ T* ET\n'))
    source_native = _native_text(job['original'], 'KEEP')
    crop = pymupdf.Rect(35, 40, 110, 80)
    source_pixels = _rgb_pixels(job['original'], clip=crop)
    segments = json.loads(job['segments'].read_text())['segments']
    assert {segment['core'] for segment in segments} >= {'KEEP', 'X'}
    assert len(source_native[0]) == len(source_native[1]) == 1
    assert len(source_native[1][0]['chars']) == 4
    assert min(source_pixels[3]) < 255

    _render(job)

    assert _native_text(job['output'], 'KEEP') == source_native
    assert _spans(job['output'], 'KEEP') == _spans(job['original'], 'KEEP')
    assert _rgb_pixels(job['output'], clip=crop) == source_pixels
    assert len(_spans(job['output'], 'Changed')) == 1
    assert not _spans(job['output'], 'X')


def test_retained_trailing_line_advance_keeps_following_bt_state_and_geometry(tmp_path):
    job = _job(tmp_path, body=(
        b'BT /helv 14 Tf 24 TL 1 0 0 1 40 180 Tm (KEEP) Tj T* ET\n'
        b'BT 210 110 Td (KEEP) Tj ET\n'))
    source_native = _native_text(job['original'], 'KEEP')
    source_pixels = _rgb_pixels(job['original'])
    assert len(source_native[0]) == len(source_native[1]) == 2
    assert len({span['origin'] for span in source_native[0]}) == 2

    _render(job)

    assert _native_text(job['output'], 'KEEP') == source_native
    assert _spans(job['output'], 'KEEP') == _spans(job['original'], 'KEEP')
    assert _rgb_pixels(job['output']) == source_pixels
    from pdf_translate import source_retention
    with pikepdf.open(job['output']) as output:
        painted = [group for group in source_retention._groups(output.pages[0]) if group['shows']]
    assert len(painted) == 2
    assert all(group['show_state']['TL'] == 24 and group['show_state']['font_size'] == 14
               for group in painted)


@pytest.mark.parametrize('program', [
    b'T* (KEEP) Tj',
    b'(KEEP) Tj 1 T*',
    b'.3 .4 .5 rg (KEEP) Tj T*',
], ids=['before-show', 'malformed', 'earlier-device-color'])
def test_retained_line_advance_refuses_unproved_objects(tmp_path, program):
    job = _job(tmp_path, body=(
        b'BT /helv 14 Tf 1 0 0 1 40 180 Tm ' + program + b' ET\n'))
    assert len(_spans(job['original'], 'KEEP')) == 1
    _assert_redrawn(job, 'unsupported-text-object')


def test_retained_line_advance_refuses_a_later_second_show(tmp_path):
    # Leading separates the two painted shows so occlusion cannot mask the
    # existing multiple-show refusal. It is interpreted only by the native PDF reader.
    job = _job(tmp_path, body=(
        b'BT /helv 14 Tf 40 TL 1 0 0 1 40 180 Tm (KEEP) Tj T* (X) Tj ET\n'))
    assert len(_spans(job['original'], 'KEEP')) == len(_spans(job['original'], 'X')) == 1
    _assert_redrawn(job, 'multiple-show')


def test_retained_text_refuses_clip_from_prior_unretained_text_show(tmp_path):
    # Resetting Tr does not undo the clipping path accumulated at ET.
    job = _job(tmp_path, b'BT /helv 140 Tf 1 0 0 1 20 170 Tm 7 Tr (X) Tj 0 Tr ET\n')
    assert len(_spans(job['original'], 'KEEP')) == 1
    mapping = json.loads(job['mapping'].read_text())
    mapping['skip'] = ['X']  # The 140 pt clipping helper is not part of this case.
    job['mapping'].write_text(json.dumps(mapping))
    _assert_redrawn(job, 'unsupported-text-object')


def test_one_unvouched_identity_run_does_not_cost_the_page_its_vouched_runs(tmp_path):
    # Regression (dogfood F1, 2026-10-09): one unchanged run the reader could
    # not vouch refused the whole document after payment. It is now redrawn
    # and reported; the vouched run on the same page is still replayed exactly.
    job = _job(tmp_path, body=(
        b'BT /helv 14 Tf 1 0 0 1 40 180 Tm (KEEP) Tj ET\n'
        b'BT /helv 14 Tf 40 TL 1 0 0 1 40 120 Tm (ABC) Tj T* (DEF) Tj ET\n'))
    mapping = json.loads(job['mapping'].read_text())
    mapping['translations'].update({'ABC': 'ABC', 'DEF': 'DEF'})
    job['mapping'].write_text(json.dumps(mapping))
    source_keep = _native_text(job['original'], 'KEEP')
    result = _assert_redrawn(job, 'multiple-show', core='ABC')
    assert {row['core'] for row in result.warnings if row['kind'] == 'source_not_retained'} == {
        'ABC', 'DEF'}
    assert _native_text(job['output'], 'KEEP') == source_keep


def test_text_clip_restored_by_q_does_not_reject_later_visible_identity(tmp_path):
    job = _job(tmp_path, b'q BT /helv 14 Tf 1 0 0 1 20 200 Tm 7 Tr (X) Tj 0 Tr ET Q\n')
    _render(job)
    assert _spans(job['output'], 'KEEP') == _spans(job['original'], 'KEEP')


def test_retained_show_requires_its_positioning_before_show(tmp_path):
    # The last matrix cancels a shear only AFTER the glyphs were painted.
    # Counts of one Tf and one Tm cannot justify using that matrix for ink.
    job = _job(tmp_path, body=(
        b'1 .2 0 1 40 180 cm BT /helv 14 Tf (KEEP) Tj '
        b'1 -.2 0 1 0 0 Tm ET\n'))
    assert len(_spans(job['original'], 'KEEP')) == 1
    _assert_redrawn(job, 'unsupported-text-object')


def test_retained_plan_refuses_two_claims_for_the_same_source_glyphs(tmp_path):
    job = _job(tmp_path)
    extracted = json.loads(job['segments'].read_text())
    keep = next(segment for segment in extracted['segments'] if segment['core'] == 'KEEP')
    extracted['segments'].append(dict(keep, id=9999))
    job['segments'].write_text(json.dumps(extracted))
    _assert_redrawn(job, 'overlapping-source-claims')


def test_retained_source_refuses_later_white_text_occlusion(tmp_path):
    job = _job(tmp_path, body=(
        b'BT /helv 14 Tf 1 0 0 1 40 180 Tm (KEEP) Tj ET\n'
        b'1 g BT /helv 36 Tf 1 0 0 1 40 180 Tm (MMM) Tj ET\n'))
    assert len(_spans(job['original'], 'KEEP')) == 1
    mapping = json.loads(job['mapping'].read_text())
    mapping['skip'] = ['MMM']  # The helper's white paint must not be resurrected.
    job['mapping'].write_text(json.dumps(mapping))
    _assert_redrawn(job, 'covered-source')


@pytest.mark.parametrize('placement, rule, axis', [
    (b'/helv 14 Tf 1 0 0 1 40 180 Tm', b'40 177 m 140 177 l S', 3),
    (b'/helv 1 Tf 14 0 0 14 40 180 Tm', b'40 177 m 140 177 l S', 3),
    (b'/helv 1 Tf 0 14 -14 0 80 180 Tm', b'83 180 m 83 230 l S', 2),
])
def test_retained_unembedded_font_uses_actual_ink_for_adjacent_rule(tmp_path, placement, rule, axis):
    # Helvetica's font box extends below its baseline into the rule. Its
    # actual KEEP glyphs do not: the rule is three points below the baseline.
    job = _job(tmp_path, body=(
        b'BT ' + placement + b' (KEEP) Tj ET\n1 w ' + rule + b'\n'))
    with pymupdf.open(job['original']) as document:
        assert not document.extract_font(document[0].get_fonts()[0][0])[3]
        assert _spans(job['original'], 'KEEP')[0]['bbox'][axis] > (63 if axis == 3 else 83)
    _render(job)
    assert _spans(job['output'], 'KEEP') == _spans(job['original'], 'KEEP')


@pytest.mark.parametrize('position', [b'0 -40 Td', b'1 0 0 1 40 140 Tm'])
@pytest.mark.parametrize('tag', [b'Content', b'Caption', b'Span'])
def test_retained_source_from_positioned_multi_show_group(tmp_path, position, tag):
    job = _job(tmp_path, body=(
        b'BT /' + tag + b' << /MCID 2 >> BDC 1 i /helv 14 Tf '
        b'1 0 0 1 40 180 Tm (KEEP) Tj '
        + position + b' (X) Tj EMC ET\n'))
    _render(job)
    assert _spans(job['output'], 'KEEP') == _spans(job['original'], 'KEEP')
    assert len(_spans(job['output'], 'Changed')) == 1


def test_retained_source_from_single_show_mcid_scope(tmp_path):
    job = _job(tmp_path, body=(
        b'BT /Caption << /MCID 2 >> BDC 1 i /helv 14 Tf '
        b'1 0 0 1 40 180 Tm (KEEP) Tj EMC ET\n'))
    _render(job)
    assert _spans(job['output'], 'KEEP') == _spans(job['original'], 'KEEP')


@pytest.mark.parametrize('scope', [b'/Span << /MCID 2 /ActualText (KEEP) >>',
                                  b'/OC << /MCID 2 >>'])
def test_retained_source_refuses_non_mcid_marked_semantics(tmp_path, scope):
    job = _job(tmp_path, body=(
        b'BT ' + scope + b' BDC /helv 14 Tf '
        b'1 0 0 1 40 180 Tm (KEEP) Tj '
        b'0 -40 Td (X) Tj EMC ET\n'))
    _assert_redrawn(job)


@pytest.mark.parametrize('scope', [b'/OC << /MCID 3 >>',
                                  b'/Span << /ActualText (KEEP) >>',
                                  b'/Span /UnknownProperties'])
def test_retained_source_refuses_unsafe_enclosing_marked_scope(tmp_path, scope):
    job = _job(tmp_path, body=(
        scope + b' BDC BT /Caption << /MCID 2 >> BDC /helv 14 Tf '
        b'1 0 0 1 40 180 Tm (KEEP) Tj '
        b'0 -40 Td (X) Tj EMC ET EMC\n'))
    _assert_redrawn(job)


@pytest.mark.parametrize('number', [b'2.0', b'true', b'-1'])
def test_retained_source_refuses_invalid_mcid_types(tmp_path, number):
    job = _job(tmp_path, body=(
        b'BT /Caption << /MCID ' + number + b' >> BDC /helv 14 Tf '
        b'1 0 0 1 40 180 Tm (KEEP) Tj '
        b'0 -40 Td (X) Tj EMC ET\n'))
    _assert_redrawn(job)


def test_translated_form_object_does_not_require_optional_source_ink(tmp_path):
    job = _job(tmp_path)
    with pikepdf.open(job['original']) as document:
        page = document.pages[0]
        form = document.make_stream(page.Contents.read_bytes())
        form.Type, form.Subtype = pikepdf.Name.XObject, pikepdf.Name.Form
        form.BBox, form.Resources = page.MediaBox, page.Resources
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Form=form))
        page.Contents = document.make_stream(b'q /Form Do Q\n')
        document.save(tmp_path / 'form.pdf')
    job['original'] = tmp_path / 'form.pdf'
    run_strip(str(job['original']), str(job['stripped']))
    extracted = run_extract(str(job['original']), str(tmp_path / 'form-extraction'))
    job['segments'] = Path(extracted.segments_path)
    mapping = json.loads(job['mapping'].read_text())
    mapping['translations']['KEEP'] = 'Changed'
    job['mapping'].write_text(json.dumps(mapping))
    _render(job)
    assert len(_spans(job['output'], 'Changed')) == 1
    assert not _spans(job['output'], 'KEEP')


@pytest.mark.parametrize('second', [b'', b'0 -3 Td (X) Tj'])
def test_retained_source_uses_vouched_font_inherited_from_prior_bt(tmp_path, second):
    # Tf is text state, which persists across BT/ET. The PDF's actual font
    # size is one, with its scale supplied by the text matrix.
    job = _job(tmp_path, body=(
        b'BT /helv 1 Tf ET\n'
        b'BT /Caption << /MCID 2 >> BDC '
        b'14 0 0 14 40 180 Tm (KEEP) Tj ' + second + b' EMC ET\n'))
    _render(job)
    assert _spans(job['output'], 'KEEP') == _spans(job['original'], 'KEEP')
    if second:
        assert len(_spans(job['output'], 'Changed')) == 1


@pytest.mark.parametrize('intent', [b'RelativeColorimetric', b'AbsoluteColorimetric',
                                   b'Perceptual', b'Saturation'])
def test_retained_source_preserves_explicit_rendering_intent(tmp_path, intent):
    job = _job(tmp_path, body=(
        b'BT /Content << /MCID 2 >> BDC .3 .4 .5 rg /' + intent + b' ri '
        b'/helv 14 Tf 1 0 0 1 40 180 Tm (KEEP) Tj EMC ET\n'))
    _render(job)
    assert _spans(job['output'], 'KEEP') == _spans(job['original'], 'KEEP')


@pytest.mark.parametrize('show', [b'(X) Tj', b'[(X)] TJ', b'[-100 (X)] TJ'])
def test_retained_source_from_vouched_multi_show_implicit_advances(tmp_path, show):
    job = _job(tmp_path, body=(
        b'BT /helv 14 Tf 1 0 0 1 40 180 Tm (KEEP) Tj ' + show + b' ET\n'))
    mapping = json.loads(job['mapping'].read_text())
    mapping['translations'] = {'KEEPX': 'KEEPX'}
    job['mapping'].write_text(json.dumps(mapping))
    _render(job)
    assert _spans(job['output'], 'KEEPX') == _spans(job['original'], 'KEEPX')


@pytest.mark.parametrize('alias', ['/F A', '/FéA'])
def test_retained_inherited_font_preserves_escaped_resource_name(tmp_path, alias):
    encoded = pikepdf.unparse_content_stream([pikepdf.ContentStreamInstruction(
        [pikepdf.Name(alias), 14], pikepdf.Operator('Tf'))])
    job = _job(tmp_path, font_alias=alias, body=(
        b'BT ' + encoded + b' ET\nBT 1 0 0 1 40 180 Tm (KEEP) Tj ET\n'))
    from pdf_translate import source_retention
    with pikepdf.open(job['original']) as document:
        group = source_retention._groups(document.pages[0])[1]
        document.pages[0].Contents = document.make_stream(source_retention._payload(group))
        fonts = [item.operands for item in pikepdf.parse_content_stream(document.pages[0])
                 if str(item.operator) == 'Tf']
        assert fonts and all(len(values) == 2 and values[0] == pikepdf.Name(alias)
                             for values in fonts)
    _render(job)
    assert _spans(job['output'], 'KEEP') == _spans(job['original'], 'KEEP')


def test_retained_explicit_td_chain_uses_actual_native_glyph_positions(tmp_path):
    # A form's small relative text moves accumulate in native single precision.
    # Composing the same decimals in Python double precision changes the last
    # baseline, even though every relative position was explicitly supplied.
    moves = (b'34.372 -0.25 Td (X) Tj ', b'-34.372 -1.928 Td (X) Tj ',
             b'0 -1.929 Td (X) Tj ', b'0 -1.928 Td (X) Tj ',
             b'0 -1.928 Td (X) Tj ', b'34.113 0 Td (X) Tj ',
             b'9.034 0 Td (X) Tj ', b'-43.146 -1.928 Td (X) Tj ',
             b'33.579 0 Td (X) Tj ', b'-33.579 -1.929 Td (X) Tj ')
    job = _job(tmp_path, page_size=(612, 792), body=(
        b'BT /helv 1 Tf 6 0 0 6 37.587 738.987 Tm (X) Tj ' + b''.join(moves) +
        b'0 -1.929 Td (KEEP) Tj ET\n'))
    _render(job)
    assert _spans(job['output'], 'KEEP') == _spans(job['original'], 'KEEP')


def test_retained_adjacent_shows_vouch_ink_despite_font_box_overlap(tmp_path):
    # The one-unit TJ move makes the character boxes overlap slightly, while
    # the printed P and X remain separated. Appending independently vouched
    # shows must not turn that pre-existing metric overlap into occlusion.
    job = _job(tmp_path, body=(
        b'BT /helv 14 Tf 1 0 0 1 40 180 Tm (KEEP) Tj [1 (X)] TJ ET\n'))
    mapping = json.loads(job['mapping'].read_text())
    mapping['translations'] = {'KEEPX': 'KEEPX'}
    job['mapping'].write_text(json.dumps(mapping))
    _render(job)
    assert _spans(job['output'], 'KEEPX') == _spans(job['original'], 'KEEPX')


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_retained_implicit_shows_use_unrotated_asymmetric_crop_frame(tmp_path, rotation):
    job = _job(tmp_path, body=(
        b'BT /helv 14 Tf 1 0 0 1 40 180 Tm (KEEP) Tj (X) Tj ET\n'))
    cropped = tmp_path / 'cropped.pdf'
    with pikepdf.open(job['original']) as document:
        document.pages[0].CropBox = pikepdf.Array([25, 35, 375, 225])
        document.pages[0].Rotate = rotation
        document.save(cropped)
    job['original'] = cropped
    run_strip(str(cropped), str(job['stripped']))
    extracted = run_extract(str(cropped), str(tmp_path / 'cropped-extraction'))
    job['segments'] = Path(extracted.segments_path)
    mapping = json.loads(job['mapping'].read_text())
    mapping['translations'] = {'KEEPX': 'KEEPX'}
    job['mapping'].write_text(json.dumps(mapping))
    _render(job)
    assert _spans(job['output'], 'KEEPX') == _spans(job['original'], 'KEEPX')


def test_retained_implicit_show_refuses_native_noninvertible_ctm(tmp_path):
    # The text is visible because Tm compensates the very small CTM. MuPDF's
    # inversion nevertheless refuses that CTM; a zero/unchanged inverse cannot
    # be used as evidence for either original baseline.
    job = _job(tmp_path, body=(
        b'.000000001 0 0 .000000001 0 0 cm BT /helv 1 Tf '
        b'14000000000 0 0 14000000000 40000000000 180000000000 Tm (KEEP) Tj (X) Tj ET\n'))
    assert len(_spans(job['original'], 'KEEPX')) == 1
    mapping = json.loads(job['mapping'].read_text())
    mapping['translations'] = {'KEEPX': 'KEEPX'}
    job['mapping'].write_text(json.dumps(mapping))
    mapping['fonts']['regular'] = str(_target_font(
        tmp_path / 'target-x.ttf', chars=' 0123456789ABCDEFGHKMPX'))
    job['mapping'].write_text(json.dumps(mapping))
    assert pymupdf.Matrix().invert(pymupdf.Matrix(1e-9, 0, 0, 1e-9, 0, 0)) == 1
    _assert_redrawn(job, core='KEEPX')


def test_retained_sheared_source_cannot_resurrect_covered_glyph_ink(tmp_path):
    job = _job(tmp_path, body=(
        b'BT /helv 14 Tf 1 0 2 1 40 180 Tm (KEEP) Tj ET\n'
        b'1 g 86 185 14 6 re f\n'))
    assert len(_spans(job['original'], 'KEEP')) == 1
    crop = pymupdf.Rect(86, 49, 100, 55)
    with pymupdf.open(job['original']) as document:
        before = document[0].get_pixmap(clip=crop, alpha=False, colorspace=pymupdf.csRGB)
        assert min(before.samples) == 255
    # Retention must not replay the covered source ink; the run is redrawn.
    _assert_redrawn(job)


def test_retained_text_refuses_later_sheared_white_glyph_occlusion(tmp_path):
    job = _job(tmp_path, body=(
        b'BT /helv 14 Tf 1 0 0 1 40 180 Tm (KEEP) Tj ET\n'
        b'1 g BT /helv 36 Tf 1 0 2 1 5 180 Tm (X) Tj ET\n'))
    mapping = json.loads(job['mapping'].read_text())
    mapping['skip'] = ['X']
    job['mapping'].write_text(json.dumps(mapping))
    # Retention must not replay the occluded source ink; the run is redrawn.
    _assert_redrawn(job)


def test_retained_orthogonal_nonuniform_source_uses_both_actual_scales(tmp_path):
    job = _job(tmp_path, body=(
        b'BT /helv 1 Tf 8.64 0 0 9 40 180 Tm (KEEP) Tj ET\n'))
    _render(job)
    assert _spans(job['output'], 'KEEP') == _spans(job['original'], 'KEEP')


@pytest.mark.parametrize('relative_y, covered', [(-40, False), (-20, True)])
def test_retained_label_vouches_multiline_leader_with_synthetic_space(tmp_path, relative_y, covered):
    # Extraction adds a synthetic space for the TJ gap. It is not a painted
    # glyph. The later text operation spans two rows, with KEEP between them,
    # but neither row paints over KEEP.
    job = _job(tmp_path, body=(
        b'BT /helv 14 Tf 1 0 0 1 40 180 Tm (KEEP) Tj ET\n'
        b'1 g BT /helv 14 Tf 1 0 0 1 200 200 Tm [(....) -250 ($)] TJ '
        + f'-160 {relative_y} Td (X) Tj ET\n'.encode('ascii')))
    from pdf_translate import source_retention
    with pymupdf.open(job['original']) as document:
        assert any(char.get('synthetic') and char['c'] == ' '
                   for span in source_retention._spans(document[0]) for char in span['chars'])
    mapping = json.loads(job['mapping'].read_text())
    mapping['skip'] = ['X', '.... $']
    job['mapping'].write_text(json.dumps(mapping))
    if covered:
        _assert_redrawn(job)
    else:
        _render(job)
        assert _spans(job['output'], 'KEEP') == _spans(job['original'], 'KEEP')


def test_retained_text_refuses_painted_space_from_source_tounicode(tmp_path):
    job = _job(tmp_path, body=(
        b'BT /helv 14 Tf 1 0 0 1 40 180 Tm (KEEP) Tj ET\n'
        b'1 g BT /helv 36 Tf 1 0 2 1 5 180 Tm (X) Tj ET\n'))
    changed = tmp_path / 'space-code.pdf'
    with pikepdf.open(job['original']) as document:
        document.pages[0].Resources.Font['/helv'].ToUnicode = document.make_stream(
            b'/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n'
            b'/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n'
            b'/CMapName /PaintedSpace def /CMapType 2 def\n'
            b'1 begincodespacerange <00> <FF> endcodespacerange\n'
            b'1 beginbfchar <58> <0020> endbfchar\n'
            b'endcmap CMapName currentdict /CMap defineresource pop end end\n')
        document.save(changed)
    job['original'] = changed
    run_strip(str(changed), str(job['stripped']))
    extracted = run_extract(str(changed), str(tmp_path / 'space-extraction'))
    job['segments'] = Path(extracted.segments_path)
    with pymupdf.open(changed) as document:
        assert any(t['chars'] and all(c[0] == 32 and c[1] > 0 for c in t['chars'])
                   for t in document[0].get_texttrace())
    # Retention must not replay ink mapped to a space; the run is redrawn.
    _assert_redrawn(job)


def test_unchanged_dotted_line_preserves_original_space_metrics(tmp_path):
    text = 'HEADER          SUFFIX.....'
    job = _job(tmp_path, body=(
        b'BT /helv 14 Tf 1 0 0 1 40 180 Tm (KEEP) Tj ET\n'
        b'BT /helv 14 Tf 1 0 0 1 40 130 Tm (' + text.encode('ascii') + b') Tj ET\n'))
    segments = json.loads(job['segments'].read_text())['segments']
    dotted = [segment for segment in segments if segment.get('dots')]
    assert len(dotted) == 1 and dotted[0]['core'] == 'HEADER          SUFFIX'
    mapping = json.loads(job['mapping'].read_text())
    complete_face = _target_font(tmp_path / 'dotted-target.ttf', chars=' .HEADER SUFFIXKP')
    mapping['fonts']['regular'] = str(complete_face)
    mapping['translations'][dotted[0]['core']] = dotted[0]['core']
    job['mapping'].write_text(json.dumps(mapping))
    before = _spans(job['original'], text)
    assert len(before) == 1
    _render(job)
    assert _spans(job['output'], text) == before


def test_unchanged_dotted_tj_line_preserves_synthetic_gap_and_glyphs(tmp_path):
    job = _job(tmp_path, body=(
        b'BT /helv 14 Tf 1 0 0 1 40 180 Tm (KEEP) Tj ET\n'
        b'BT /helv 14 Tf 1 0 0 1 40 130 Tm [(HEADER) -250 (SUFFIX.....)] TJ ET\n'))
    from pdf_translate import source_retention
    with pymupdf.open(job['original']) as document:
        spans = [span for span in source_retention._spans(document[0]) if 'HEADER' in span['text']]
    assert len(spans) == 1
    assert any(char.get('synthetic') and char['c'] == ' ' for char in spans[0]['chars'])
    text = spans[0]['text']
    dotted = [segment for segment in json.loads(job['segments'].read_text())['segments']
              if segment.get('dots')]
    assert len(dotted) == 1
    mapping = json.loads(job['mapping'].read_text())
    face = _target_font(tmp_path / 'synthetic-dotted-target.ttf', chars=' .HEADER SUFFIXKP')
    mapping['fonts']['regular'] = str(face)
    mapping['translations'][dotted[0]['core']] = dotted[0]['core']
    job['mapping'].write_text(json.dumps(mapping))
    before = _spans(job['original'], text)
    assert len(before) == 1
    _render(job)
    assert _spans(job['output'], text) == before


def test_context_superscript_flag_does_not_change_retained_glyph_program(tmp_path):
    # Reorder two disjoint, complete output text programs. MuPDF changes its
    # contextual superscript classification even though every glyph keeps its
    # original font, position and bounds. Verify the actual reordered file.
    job = _job(tmp_path, body=(
        b'BT /helv 8 Tf 1 0 0 1 90 180 Tm (KEEP) Tj ET\n'
        b'BT /helv 9 Tf 1 0 0 1 40 178 Tm (CHANGE) Tj ET\n'))
    from pdf_translate import source_retention
    with pymupdf.open(job['original']) as document:
        before_raw = next(span for span in source_retention._spans(document[0])
                          if span['text'] == 'KEEP')
    assert not before_raw['flags'] & pymupdf.TEXT_FONT_SUPERSCRIPT
    mapping = json.loads(job['mapping'].read_text())
    mapping['translations']['CHANGE'] = 'Changed'
    job['mapping'].write_text(json.dumps(mapping))
    _render(job)
    with pymupdf.open(job['stripped']) as stripped:
        retained = source_retention.prepare(str(job['original']), stripped,
            json.loads(job['segments'].read_text())['segments'], mapping['translations'])
    reordered = tmp_path / 'reordered.pdf'
    with pikepdf.open(job['output']) as document:
        groups = [group for group in source_retention._groups(document.pages[0]) if group['shows']]
        assert len(groups) == 2
        document.pages[0].Contents = document.make_stream(b'\n'.join(
            source_retention._payload(group) for group in reversed(groups)))
        document.save(reordered)
    with pymupdf.open(reordered) as document:
        after_spans = source_retention._spans(document[0])
        after_raw = next(span for span in after_spans if span['text'] == 'KEEP')
    assert after_raw['flags'] & pymupdf.TEXT_FONT_SUPERSCRIPT, [
        {key:span[key] for key in ('text', 'origin', 'font', 'size', 'flags')}
        for span in after_spans]
    assert _spans(reordered, 'KEEP') == _spans(job['original'], 'KEEP')
    source_retention.verify_final(reordered, retained)


@pytest.mark.parametrize('overhang', [False, True])
def test_inline_translation_respects_retained_suffix_inside_dotted_row(tmp_path, overhang):
    job = _job(tmp_path, body=(
        b'BT /helv 9 Tf 1 0 0 1 40 180 Tm (HEADER' + b' '*49 +
        b'SUFFIX.....) Tj ET\n'
        b'BT /helv 9 Tf 1 0 0 1 160 180 Tm (CHANGE) Tj ET\n'))
    from pdf_translate import source_retention
    with pymupdf.open(job['original']) as document:
        source = [span for span in source_retention._spans(document[0])
                  if 'SUFFIX' in span['text']]
    assert len(source) == 1
    before = _spans(job['original'], source[0]['text'])
    assert len(before) == 1
    mapping = json.loads(job['mapping'].read_text())
    mapping['translations'] = {segment['core']: segment['core']
        for segment in json.loads(job['segments'].read_text())['segments']}
    mapping['translations']['CHANGE'] = 'Changed'
    if overhang:
        from fontTools.ttLib import TTFont
        from fontTools.pens.ttGlyphPen import TTGlyphPen
        face = tmp_path / 'overhanging-target.ttf'
        with TTFont(mapping['fonts']['regular']) as font:
            glyph = font.getBestCmap()[ord('d')]
            pen = TTGlyphPen(None)
            pen.moveTo((80, 0))
            pen.lineTo((80, 700))
            pen.lineTo((900, 700))
            pen.lineTo((900, 0))
            pen.closePath()
            font['glyf'][glyph] = pen.glyph()
            assert font['hmtx'][glyph][0] == 760
            font.save(face)
        mapping['fonts']['regular'] = str(face)
    job['mapping'].write_text(json.dumps(mapping))
    _render(job)
    assert _spans(job['output'], source[0]['text']) == before
    assert len(_spans(job['output'], 'Changed')) == 1
