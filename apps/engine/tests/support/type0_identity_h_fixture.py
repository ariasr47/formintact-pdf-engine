"""Deterministic Type0 /Identity-H fixture and independent reference builder.

Regeneration is deliberate: run this module with an explicit output directory,
review the rendered source/reference pair, then commit every file together with
the updated provenance.  The reference is manually typeset from the desired
Spanish strings; it never consumes engine output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0
ZOOM = 2.0
DELTA_THRESHOLD = 8
WHOLE_PAGE_RATIO_MAX = 0.001
PROTECTED_CROP_RATIO = 0.0
SOURCE_WORDS = ("Alpha", "Beta", "Gamma", "Delta")
EXPECTED_WORDS = ("Árbol", "Casa", "Gasto", "Fecha")
BASELINES = ((72.0, 112.0), (72.0, 172.0), (72.0, 232.0), (72.0, 268.0))
SIZES = (18.0, 16.0, 17.0, 17.0)
WIDGET_CROP = (350.0, 68.0, 570.0, 148.0)
GRAPHIC_CROP = (340.0, 620.0, 560.0, 742.0)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _font_paths() -> tuple[Path, Path]:
    fonts = Path(__file__).parents[2] / "pdft_engine" / "assets" / "fonts"
    return fonts / "NotoSans-Regular.ttf", fonts / "NotoSerif-Regular.ttf"


def _pdf_name(value: str):
    import pikepdf

    return pikepdf.Name(value if value.startswith("/") else f"/{value}")


def _font_metrics(path: Path) -> dict:
    from fontTools.ttLib import TTFont

    font = TTFont(path, lazy=False)
    try:
        units = font["head"].unitsPerEm
        scale = 1000.0 / units
        glyph_order = font.getGlyphOrder()
        glyph_ids = {name: index for index, name in enumerate(glyph_order)}
        cmap = font.getBestCmap()
        hmtx = font["hmtx"].metrics
        os2 = font["OS/2"]
        return {
            "units": units,
            "scale": scale,
            "bbox": tuple(round(value * scale) for value in (
                font["head"].xMin,
                font["head"].yMin,
                font["head"].xMax,
                font["head"].yMax,
            )),
            "ascent": round(font["hhea"].ascent * scale),
            "descent": round(font["hhea"].descent * scale),
            "cap_height": round(
                getattr(os2, "sCapHeight", font["hhea"].ascent) * scale
            ),
            "glyph_id": {char: glyph_ids[cmap[ord(char)]] for char in set("".join(SOURCE_WORDS))},
            "width": {
                glyph_ids[cmap[ord(char)]]: round(hmtx[cmap[ord(char)]][0] * scale)
                for char in set("".join(SOURCE_WORDS))
            },
        }
    finally:
        font.close()


def _to_unicode(chars: set[str], glyph_id: dict[str, int]) -> bytes:
    rows = []
    for char in sorted(chars, key=lambda item: glyph_id[item]):
        source = glyph_id[char].to_bytes(2, "big").hex().upper()
        target = char.encode("utf-16-be").hex().upper()
        rows.append(f"<{source}> <{target}>")
    body = "\n".join(rows)
    return (
        "/CIDInit /ProcSet findresource begin\n"
        "12 dict begin\n"
        "begincmap\n"
        "/CIDSystemInfo << /Registry (Fixture) /Ordering (Identity) /Supplement 0 >> def\n"
        "/CMapName /Fixture-Identity-H def\n"
        "/CMapType 2 def\n"
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        f"{len(rows)} beginbfchar\n{body}\nendbfchar\n"
        "endcmap\n"
        "CMapName currentdict /CMap defineresource pop\n"
        "end end\n"
    ).encode("ascii")


def _embed_type0(pdf, path: Path, base_name: str):
    import pikepdf

    metrics = _font_metrics(path)
    font_file = pdf.make_stream(path.read_bytes())
    font_file["/Length1"] = len(path.read_bytes())
    descriptor = pdf.make_indirect(pikepdf.Dictionary({
        "/Type": _pdf_name("/FontDescriptor"),
        "/FontName": _pdf_name(base_name),
        "/Flags": 32,
        "/FontBBox": pikepdf.Array(metrics["bbox"]),
        "/ItalicAngle": 0,
        "/Ascent": metrics["ascent"],
        "/Descent": metrics["descent"],
        "/CapHeight": metrics["cap_height"],
        "/StemV": 80,
        "/FontFile2": font_file,
    }))
    widths = pikepdf.Array()
    for glyph, width in sorted(metrics["width"].items()):
        widths.append(glyph)
        widths.append(pikepdf.Array([width]))
    cid_font = pdf.make_indirect(pikepdf.Dictionary({
        "/Type": _pdf_name("/Font"),
        "/Subtype": _pdf_name("/CIDFontType2"),
        "/BaseFont": _pdf_name(base_name),
        "/CIDSystemInfo": pikepdf.Dictionary({
            "/Registry": pikepdf.String("Fixture"),
            "/Ordering": pikepdf.String("Identity"),
            "/Supplement": 0,
        }),
        "/FontDescriptor": descriptor,
        "/CIDToGIDMap": _pdf_name("/Identity"),
        "/DW": 1000,
        "/W": widths,
    }))
    cmap = pdf.make_stream(_to_unicode(set("".join(SOURCE_WORDS)), metrics["glyph_id"]))
    font = pdf.make_indirect(pikepdf.Dictionary({
        "/Type": _pdf_name("/Font"),
        "/Subtype": _pdf_name("/Type0"),
        "/BaseFont": _pdf_name(base_name),
        "/Encoding": _pdf_name("/Identity-H"),
        "/DescendantFonts": pikepdf.Array([cid_font]),
        "/ToUnicode": cmap,
    }))
    return font, metrics["glyph_id"]


def _encoded(text: str, glyph_id: dict[str, int]) -> str:
    return "".join(f"{glyph_id[char]:04X}" for char in text)


def _protected_stream() -> bytes:
    return (
        b"q 0.85 0.15 0.15 RG 3 w 350 60 200 100 re S "
        b"0.15 0.35 0.85 rg 390 92 120 36 re f Q\n"
    )


def _vector_widget_value(value: str) -> bytes:
    glyphs = {
        "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
        "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
        "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
        "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
        "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
        "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
        "N": ("10001", "11001", "10101", "10101", "10011", "10001", "10001"),
        "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    }
    scale = 2
    commands = ["0 g"]
    for index, char in enumerate(value):
        left = 8 + index * 12
        for row, bits in enumerate(glyphs[char]):
            for column, bit in enumerate(bits):
                if bit == "1":
                    commands.append(
                        f"{left + column * scale} {34 - row * scale} {scale} {scale} re f"
                    )
    return (" ".join(commands) + " ").encode("ascii")


def _add_widget(pdf, page) -> None:
    import pikepdf

    helvetica = pdf.make_indirect(pikepdf.Dictionary({
        "/Type": _pdf_name("/Font"),
        "/Subtype": _pdf_name("/Type1"),
        "/BaseFont": _pdf_name("/Helvetica"),
    }))
    appearance = pdf.make_stream(
        b"q 1 1 1 rg 0 0 200 60 re f 0 0 0 RG 1 w 0 0 200 60 re S "
        + _vector_widget_value("UNCHANGED")
        + b"Q"
    )
    appearance["/Type"] = _pdf_name("/XObject")
    appearance["/Subtype"] = _pdf_name("/Form")
    appearance["/FormType"] = 1
    appearance["/BBox"] = pikepdf.Array([0, 0, 200, 60])
    appearance["/Resources"] = pikepdf.Dictionary({
        "/Font": pikepdf.Dictionary({"/Helv": helvetica}),
    })
    widget = pdf.make_indirect(pikepdf.Dictionary({
        "/Type": _pdf_name("/Annot"),
        "/Subtype": _pdf_name("/Widget"),
        "/FT": _pdf_name("/Tx"),
        "/T": pikepdf.String("protected-field"),
        "/V": pikepdf.String("UNCHANGED"),
        "/Rect": pikepdf.Array([360, 654, 560, 714]),
        "/F": 4,
        "/DA": pikepdf.String("/Helv 12 Tf 0 g"),
        "/AP": pikepdf.Dictionary({"/N": appearance}),
    }))
    page["/Annots"] = pikepdf.Array([widget])
    pdf.Root["/AcroForm"] = pikepdf.Dictionary({
        "/Fields": pikepdf.Array([widget]),
        "/DA": pikepdf.String("/Helv 12 Tf 0 g"),
        "/DR": pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/Helv": helvetica})}),
        "/NeedAppearances": False,
    })


def build_source(path: Path) -> None:
    import pikepdf

    sans_path, serif_path = _font_paths()
    with pikepdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(PAGE_WIDTH, PAGE_HEIGHT)).obj
        sans, sans_ids = _embed_type0(pdf, sans_path, "FixtureNotoSans")
        serif, serif_ids = _embed_type0(pdf, serif_path, "FixtureNotoSerif")
        page["/Resources"] = pikepdf.Dictionary({
            "/Font": pikepdf.Dictionary({"/F1": sans, "/F2": serif}),
        })
        first = _protected_stream() + (
            "BT /F1 18 Tf ET\n"
            "q BT /F2 24 Tf ET Q\n"
            f"BT 1 0 0 1 72 680 Tm <{_encoded('Alpha', sans_ids)}> Tj ET\n"
            "BT /F2 16 Tf ET\n"
        ).encode("ascii")
        beta = _encoded("Beta", serif_ids)
        second = (
            f"BT 1 0 0 1 72 620 Tm [<{beta[:4]}> -20 <{beta[4:]}>] TJ ET\n"
            f"BT /F1 17 Tf 24 TL 1 0 0 1 72 584 Tm <{_encoded('Gamma', sans_ids)}> ' ET\n"
            f"BT /F2 17 Tf 24 TL 1 0 0 1 72 548 Tm 0 0 <{_encoded('Delta', serif_ids)}> \" ET\n"
        ).encode("ascii")
        page["/Contents"] = pikepdf.Array([pdf.make_stream(first), pdf.make_stream(second)])
        _add_widget(pdf, page)
        pdf.docinfo["/Creator"] = "pdf-translator deterministic Type0 fixture"
        pdf.docinfo["/CreationDate"] = "D:20260930000000Z"
        path.parent.mkdir(parents=True, exist_ok=True)
        pdf.save(path, deterministic_id=True, compress_streams=True)


def build_expected(source: Path, path: Path) -> None:
    import fitz
    import pikepdf

    copied = path.with_suffix(".copied.pdf")
    stripped = path.with_suffix(".base.pdf")
    with fitz.open(source) as original:
        normalized = fitz.open()
        normalized.insert_pdf(original)
        normalized.save(copied)
        normalized.close()
    with pikepdf.open(copied) as pdf:
        page = pdf.pages[0]
        page["/Contents"] = pdf.make_stream(_protected_stream())
        pdf.save(stripped, deterministic_id=True, compress_streams=True)
    _, serif_path = _font_paths()
    with fitz.open(stripped) as document:
        page = document[0]
        for index, (text, origin, size) in enumerate(
            zip(EXPECTED_WORDS, BASELINES, SIZES, strict=True)
        ):
            page.insert_text(
                fitz.Point(*origin),
                text,
                fontsize=size,
                fontname=f"FixtureExpected{index}",
                fontfile=str(serif_path),
                color=(0, 0, 0),
            )
        document.subset_fonts()
        document.save(path)
    copied.unlink(missing_ok=True)
    stripped.unlink(missing_ok=True)


def render_references(pdf_path: Path, output_dir: Path) -> None:
    import fitz

    with fitz.open(pdf_path) as document:
        page = document[0]
        matrix = fitz.Matrix(ZOOM, ZOOM)
        page.get_pixmap(matrix=matrix, colorspace=fitz.csRGB, alpha=False).save(
            output_dir / "expected-full.png"
        )
        protected = (
            ("protected-widget.png", WIDGET_CROP),
            ("protected-graphic.png", GRAPHIC_CROP),
        )
        for name, crop in protected:
            page.get_pixmap(
                matrix=matrix,
                colorspace=fitz.csRGB,
                alpha=False,
                clip=fitz.Rect(*crop),
            ).save(output_dir / name)


def generate(output_dir: Path) -> None:
    import fitz
    import pikepdf

    output_dir.mkdir(parents=True, exist_ok=True)
    source = output_dir / "source-multi-font-identity-h.pdf"
    expected = output_dir / "expected-spanish.pdf"
    build_source(source)
    build_expected(source, expected)
    render_references(expected, output_dir)
    sans_path, serif_path = _font_paths()
    artifacts = {
        path.name: _sha256(path)
        for path in sorted(output_dir.iterdir())
        if path.is_file() and path.name != "provenance.json"
    }
    provenance = {
        "regeneration_intent": (
            "manual review required; never regenerate as a dependency side effect"
        ),
        "producer": "tests/support/type0_identity_h_fixture.py",
        "produced_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "pdf_metadata_date": "D:20260930000000Z",
        "pymupdf": fitz.VersionBind,
        "pikepdf": pikepdf.__version__,
        "zoom": ZOOM,
        "colorspace": "RGB",
        "alpha": False,
        "delta_threshold": DELTA_THRESHOLD,
        "whole_page_ratio_max": WHOLE_PAGE_RATIO_MAX,
        "protected_crop_ratio": PROTECTED_CROP_RATIO,
        "source_words": list(SOURCE_WORDS),
        "expected_words": list(EXPECTED_WORDS),
        "font_assets": {
            str(path.relative_to(Path(__file__).parents[2])).replace("\\", "/"): _sha256(path)
            for path in (sans_path, serif_path)
        },
        "artifacts": artifacts,
    }
    (output_dir / "provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    generate(args.output_dir.resolve())
