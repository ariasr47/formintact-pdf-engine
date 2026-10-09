"""Fixture ground truth: names, page counts, and encryption flag. The text-layer
classification pins live in test_pdfops.py (they need the heuristic under test)."""
from pathlib import Path

import fitz
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("name", "pages"),
    [
        ("benchmark-invoice.pdf", 8),
        ("partly-scanned.pdf", 8),
        ("all-scanned.pdf", 4),
        ("zero-page.pdf", 0),
        ("over-200-pages.pdf", 201),
        ("exactly-200-pages.pdf", 200),
    ],
)
def test_fixture_page_counts(name: str, pages: int) -> None:
    with fitz.open(FIXTURES / name) as doc:
        assert doc.page_count == pages


def test_encrypted_fixture_needs_password() -> None:
    with fitz.open(FIXTURES / "encrypted.pdf") as doc:
        assert doc.needs_pass


def test_not_a_pdf_fixture_has_no_pdf_header() -> None:
    assert not (FIXTURES / "not-a-pdf.bin").read_bytes().startswith(b"%PDF")


def test_v4_state_leak_and_space_run_fixtures_reproduce_byte_for_byte() -> None:
    """PLAN V4: both committed files reproduce byte-for-byte from the generator."""
    from tests.fixtures.generate_fixtures import (
        make_graphics_state_leak,
        make_internal_space_run,
    )

    names = ("graphics_state_leak.pdf", "internal_space_run.pdf")
    before = {n: (FIXTURES / n).read_bytes() for n in names}
    make_graphics_state_leak()
    make_internal_space_run()
    for n, data in before.items():
        assert (FIXTURES / n).read_bytes() == data, f"{n} is not deterministic"


def test_invoice_d1_style_axes_present() -> None:
    """D1 (SPEC §2): each of the 8 invoice pages carries 4 lines — uniform
    bold-serif header, plain control, and two genuinely mixed-style blocks.
    Raw fitz assertions only (no product extraction code exists yet)."""
    import fitz  # test-only import

    with fitz.open(FIXTURES / "benchmark-invoice.pdf") as doc:
        for page in doc:
            raw = page.get_text("dict")
            text_blocks = [b for b in raw["blocks"] if b.get("type") == 0]
            assert len(text_blocks) == 4
            header_spans = text_blocks[0]["lines"][0]["spans"]
            assert len(header_spans) == 1  # uniform: one span, one style
            assert "Times-Bold" in header_spans[0]["font"]
            lineitem_spans = [sp for ln in text_blocks[2]["lines"] for sp in ln["spans"]]
            fonts = {sp["font"] for sp in lineitem_spans}
            assert len(fonts) == 2  # genuinely two-span, two-family mixed block
            amount_spans = [sp for ln in text_blocks[3]["lines"] for sp in ln["spans"]]
            amount_fonts = {sp["font"] for sp in amount_spans}
            assert len(amount_fonts) == 2
