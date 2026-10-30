"""Three things that read as source text and are not, or are not one line.

Each cost a whole refund in the Spanish corpus of 2026-10-26, and none raises anything
on its own: a tagged image's description failed the strip check (NASA), an invisible
line-end glyph kept 14 lines in English (JPL), and blanks drawn with their label put
the translation on a form field (B101).
"""

from __future__ import annotations

import json
from pathlib import Path

import pikepdf
import pymupdf
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables.DefaultTable import DefaultTable

from pdf_translate import run_extract, run_retypeset, run_strip
from pdf_translate.strip_text import (
    leftover_page_text,
    page_rawdict_without_annots,
    page_textdict_without_annots,
)
from pdf_translate.typography import _program_observation

FONTS = Path(__file__).resolve().parents[3] / 'apps' / 'engine' / 'pdft_engine' / 'assets' / 'fonts'
FULL = {role: FONTS / f'FormIntactSans-{name}.ttf' for role, name in (
    ('regular', 'Regular'), ('bold', 'Bold'), ('italic', 'Italic'), ('bold_italic', 'BoldItalic'))}


def _segments(tmp_path, source):
    run_extract(str(source), outdir=str(tmp_path))
    return json.loads((tmp_path / 'segments.json').read_text(encoding='utf-8'))['segments']


def _image_with_description(path: Path) -> None:
    pdf = pikepdf.new()
    image = pdf.make_stream(b'\xff\x00\x00', Type=pikepdf.Name.XObject,
                            Subtype=pikepdf.Name.Image, Width=1, Height=1, BitsPerComponent=8,
                            ColorSpace=pikepdf.Name.DeviceRGB)
    content = (b'/Span << /ActualText (Student Worksheet) >> BDC '
               b'q 200 0 0 50 50 700 cm /Im0 Do Q EMC')
    pdf.pages.append(pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name.Page, MediaBox=[0, 0, 612, 792],
        Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=image)),
        Contents=pdf.make_stream(content))))
    pdf.save(path)


def test_an_image_description_is_not_text_left_on_the_page(tmp_path):
    path = tmp_path / 'tagged.pdf'
    _image_with_description(path)
    with pymupdf.open(path) as document:
        assert 'Student Worksheet' in document[0].get_text()
    assert leftover_page_text(path) == []


def _font(path: Path) -> Path:
    """Glyph 1 draws nothing and glyph 2 draws a box; nothing names either."""
    order = ['.notdef', 'blank', 'box']
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(order)
    pen = TTGlyphPen(None)
    pen.moveTo((100, 0))
    pen.lineTo((100, 700))
    pen.lineTo((500, 700))
    pen.closePath()
    ink = pen.glyph()
    builder.setupGlyf({'.notdef': ink, 'blank': TTGlyphPen(None).glyph(), 'box': ink})
    builder.setupHorizontalMetrics({name: (600, 0) for name in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupCharacterMap({})
    builder.setupOS2()
    builder.setupPost(formatType=3.0)
    builder.setupMaxp()
    builder.setupNameTable({'familyName': 'Test', 'styleName': 'Regular'})
    del builder.font['cmap']
    builder.save(path)
    return path


def test_a_damaged_font_table_is_unresolved_and_never_a_crash(tmp_path):
    """HUD-90105-A embeds a font with an empty name table: the whole order failed."""
    path = _font(tmp_path / 'test.ttf')
    with TTFont(path) as font:
        font['name'] = DefaultTable('name')
        font['name'].data = b''
        font.save(path)
    observed = _program_observation(path.read_bytes())
    assert observed['unresolved'] == ['unreadable-font-program']


def _unmapped_source(path: Path, font: Path) -> None:
    pdf = pikepdf.new()
    name = pikepdf.Name('/ABCDEF+Test')
    descendant = pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.CIDFontType2, BaseFont=name,
        CIDSystemInfo=pikepdf.Dictionary(Registry=pikepdf.String('Adobe'),
                                         Ordering=pikepdf.String('Identity'), Supplement=0),
        FontDescriptor=pikepdf.Dictionary(
            Type=pikepdf.Name.FontDescriptor, FontName=name, Flags=4,
            FontBBox=[0, -200, 1000, 800], ItalicAngle=0, Ascent=800, Descent=-200,
            CapHeight=700, StemV=80, FontFile2=pdf.make_stream(font.read_bytes())),
        CIDToGIDMap=pikepdf.Name.Identity)
    type0 = pdf.make_indirect(pikepdf.Dictionary(
        Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type0, BaseFont=name,
        Encoding=pikepdf.Name('/Identity-H'), DescendantFonts=[pdf.make_indirect(descendant)]))
    pdf.pages.append(pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name.Page, MediaBox=[0, 0, 612, 792],
        Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=type0)),
        Contents=pdf.make_stream(b'BT /F1 12 Tf 72 720 Td <0001> Tj 72 0 Td <0002> Tj ET'))))
    pdf.save(path)


def _characters(data) -> list[str]:
    return [char['c'] for block in data['blocks'] for line in block.get('lines', ())
            for span in line['spans'] for char in span['chars']]


def _text(data) -> str:
    return ''.join(span['text'] for block in data['blocks'] for line in block.get('lines', ())
                   for span in line['spans'])


def test_only_a_glyph_with_no_outline_reads_as_a_space(tmp_path):
    path = tmp_path / 'unmapped.pdf'
    _unmapped_source(path, _font(tmp_path / 'test.ttf'))
    with pymupdf.open(path) as document:
        assert _characters(page_rawdict_without_annots(document[0])) == [' ', '�']
        assert _text(page_textdict_without_annots(document[0])).count('�') == 1


def _column_source(path: Path) -> None:
    """Two rows of a form table, and a phrase with its blanks in it."""
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=300)
        fields = []
        row = '_' * 20 + ' When '
        page.insert_text((40, 60), row, fontname='helv', fontsize=8)
        fields.append((40, 60, '_' * 20))
        row = '_' * 15 + '   Case number   ' + '_' * 20
        page.insert_text((40, 120), row, fontname='helv', fontsize=8)
        fields.append((40, 120, '_' * 15))
        fields.append((40 + pymupdf.get_text_length('_' * 15 + '   Case number   ', fontname='helv',
                                                    fontsize=8), 120, '_' * 20))
        row = '_' * 15 + ' District of ' + '_' * 20
        page.insert_text((40, 200), row, fontname='helv', fontsize=8)
        fields.append((40, 200, '_' * 15))
        for index, (x, y, blank) in enumerate(fields):
            field = pymupdf.Widget()
            field.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
            field.field_name = f'f{index}'
            width = pymupdf.get_text_length(blank, fontname='helv', fontsize=8)
            field.rect = pymupdf.Rect(x, y - 9, x + width, y + 2)
            page.add_widget(field)
        document.save(path)


def test_a_label_between_fields_is_its_own_line_but_a_phrase_is_not(tmp_path):
    original = tmp_path / 'original.pdf'
    _column_source(original)
    cores = [s['core'] for s in _segments(tmp_path, original)]
    assert 'When' in cores and 'Case number' in cores
    assert any('District of' in core and '_' in core for core in cores)


def test_a_field_set_slightly_into_its_label_still_stops_the_label(tmp_path):
    """B101's header field lands up to 1.2 pt inside the label on some pages."""
    original = tmp_path / 'original.pdf'
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=200)
        page.insert_text((40, 120), 'Name: ' + '_' * 30, fontname='helv', fontsize=11)
        start = 40 + pymupdf.get_text_length('Name:', fontname='helv', fontsize=11)
        field = pymupdf.Widget()
        field.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        field.field_name = 'name'
        field.rect = pymupdf.Rect(start - 1.5, 108, 300, 122)
        page.add_widget(field)
        document.save(original)
    _segments(tmp_path, original)
    stripped, output = tmp_path / 'stripped.pdf', tmp_path / 'output.pdf'
    run_strip(str(original), str(stripped))
    mapping = tmp_path / 'mapping.json'
    mapping.write_text(json.dumps({
        'lang': 'es', 'fonts': {role: str(path) for role, path in FULL.items()},
        'translations': {'Name:': 'Nombre completo:'}, 'allow_scale': ['Name:'],
    }), encoding='utf-8')
    run_retypeset(str(stripped), str(tmp_path / 'segments.json'), str(mapping), str(output),
                  original=str(original))
    with pymupdf.open(output) as document:
        label = document[0].search_for('completo:')
    assert label and label[0].x1 <= start
