"""Read-only form observations over source and delivered PDFs."""
from pathlib import Path

import fitz

from tests.measure.partition import MeasureResult


def field_set(pdf: Path) -> set[tuple[str, str]]:
    with fitz.open(pdf) as document:
        return {
            (widget.field_name, str(widget.field_type))
            for page in document
            for widget in page.widgets()
        }


def has_acroform(pdf: Path) -> bool:
    with fitz.open(pdf) as document:
        return bool(document.pdf_catalog()) and bool(field_set(pdf))


def ac35_field_parity(source_pdf: Path, delivered_pdf: Path, _report=None) -> MeasureResult:
    source = field_set(source_pdf)
    delivered = field_set(delivered_pdf)
    numbers = {
        "source_n": len(source),
        "delivered_n": len(delivered),
        "symmetric_difference": len(source ^ delivered),
        "delivered_has_acroform": int(has_acroform(delivered_pdf)),
    }
    return MeasureResult(
        source == delivered and bool(source) and bool(numbers["delivered_has_acroform"]),
        numbers,
    )
