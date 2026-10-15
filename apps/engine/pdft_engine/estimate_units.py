"""Read-only source counting for quote preflight; never authors native requests."""
from __future__ import annotations

from pathlib import Path

from pdft_engine_contract.billing import BillableUnits

from pdft_engine.preflight import letter_counts

__all__ = ["BillableUnits", "count_billable_units", "count_pages"]


def _page_stats(pdf_path: Path, page_number: int) -> tuple[int, int, int, int]:
    import fitz

    with fitz.open(pdf_path) as document:
        blocks = document[page_number - 1].get_text("blocks")
    texts = [
        str(block[4])
        for block in blocks
        if len(block) > 4 and str(block[4]).strip()
    ]
    letters, rtl = letter_counts(texts)
    return sum(len(text) for text in texts), len(texts), letters, rtl


def count_billable_units(pdf_path: Path, kept_pages: list[int]) -> BillableUnits:
    per_page = [_page_stats(pdf_path, page)[:2] for page in kept_pages]
    return BillableUnits(
        source_chars=sum(chars for chars, _blocks in per_page),
        text_block_count=sum(blocks for _chars, blocks in per_page),
        kept_page_count=len(kept_pages),
        per_page_block_counts=tuple(blocks for _chars, blocks in per_page),
    )


def count_pages(
    pdf_path: Path,
    pages: list[int],
) -> tuple[tuple[int, int], list[tuple[int, int, bool, int]], tuple[int, int]]:
    source_chars = 0
    text_blocks = 0
    letters = rtl = 0
    observations: list[tuple[int, int, bool, int]] = []
    for page in pages:
        try:
            chars, blocks, page_letters, page_rtl = _page_stats(pdf_path, page)
        except Exception:
            observations.append((page, 0, True, 0))
            continue
        source_chars += chars
        text_blocks += blocks
        letters += page_letters
        rtl += page_rtl
        observations.append((page, blocks, False, blocks))
    return (source_chars, text_blocks), observations, (letters, rtl)


from pdft_engine.native_owner import on_native_thread as _on_native_thread  # noqa: E402

_page_stats = _on_native_thread(_page_stats)
