"""The engine lists a core as serif only when every span of it is set in a serif face.

Lost, nothing fails: a serif title simply comes back sans (homepage invoice,
2026-10-26). MuPDF's own serif flag also marks NotoSans, so the class must come
from the font program or a standard face's name.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

from pdft_engine.skill_placement import _FONTS_DIR, _serif_cores


def test_serif_spans_make_a_serif_core_and_sans_or_mixed_do_not(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=200)
        page.insert_font(fontname="nsans", fontfile=str(_FONTS_DIR / "NotoSans-Regular.ttf"))
        page.insert_text((40, 40), "Invoice", fontname="tiro", fontsize=20)
        page.insert_text((40, 80), "Bill to", fontname="nsans", fontsize=12)
        page.insert_text((40, 120), "Mixed", fontname="tiro", fontsize=12)
        page.insert_text((80, 120), "line", fontname="helv", fontsize=12)
        document.save(source)
    rows = [
        {"core": "Invoice", "page": 0, "bbox": [38, 20, 140, 46]},
        {"core": "Bill to", "page": 0, "bbox": [38, 68, 120, 84]},
        {"core": "Mixed line", "page": 0, "bbox": [38, 108, 120, 124]},
    ]
    assert _serif_cores(source, rows) == {"Invoice"}


def test_black_spans_make_a_black_core_and_bold_does_not(tmp_path: Path) -> None:
    """DS-64's Arial Black headings came back in Bold, visibly lighter (2026-10-29)."""
    from pdft_engine.skill_placement import _black_cores

    source = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=400, height=200)
        page.insert_font(fontname="black", fontfile=str(_FONTS_DIR / "FormIntactSans-Black.ttf"))
        page.insert_font(fontname="bold", fontfile=str(_FONTS_DIR / "FormIntactSans-Bold.ttf"))
        page.insert_text((40, 40), "WARNING", fontname="black", fontsize=12)
        page.insert_text((40, 80), "Bill to", fontname="bold", fontsize=12)
        document.save(source)
    rows = [
        {"core": "WARNING", "page": 0, "bbox": [38, 26, 140, 44]},
        {"core": "Bill to", "page": 0, "bbox": [38, 66, 120, 84]},
    ]
    assert _black_cores(source, rows) == {"WARNING"}
