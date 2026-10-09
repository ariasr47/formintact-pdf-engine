"""Read-only rendered span-size census."""
from pathlib import Path

from tests.measure.partition import extract_spans


def distinct_sizes(pdf: Path, page_no: int) -> set[float]:
    return {round(span.size, 2) for span in extract_spans(pdf, page_no)}
