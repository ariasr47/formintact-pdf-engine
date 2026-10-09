"""Billable-unit counter (estimate-consent B3). The exclusion-keyed cache half of
this file's original tests stays app-side (JobStore's cache/generation bookkeeping,
not engine output) -- see apps/api/tests/test_estimate_units.py, S4-test-split."""
from pathlib import Path

from pdft_engine.estimate_units import count_billable_units

FIXTURES = Path(__file__).parent / "fixtures"


def test_invoice_has_positive_chars_and_blocks() -> None:
    u = count_billable_units(
        FIXTURES / "benchmark-invoice.pdf", kept_pages=[1, 2, 3, 4, 5, 6, 7, 8]
    )
    assert u.source_chars > 0
    assert u.text_block_count > 0
    assert u.kept_page_count == 8


def test_per_page_block_counts_one_entry_per_kept_page_summing_to_total() -> None:
    """The read-only quote census retains one measured block count per kept page."""
    u = count_billable_units(
        FIXTURES / "benchmark-invoice.pdf", kept_pages=[1, 2, 3, 4, 5, 6, 7, 8]
    )
    assert len(u.per_page_block_counts) == 8
    assert sum(u.per_page_block_counts) == u.text_block_count
    assert all(c >= 0 for c in u.per_page_block_counts)


def test_scanned_only_is_zero() -> None:
    u = count_billable_units(FIXTURES / "all-scanned.pdf", kept_pages=[1])
    assert u.source_chars == 0
    assert u.text_block_count == 0
