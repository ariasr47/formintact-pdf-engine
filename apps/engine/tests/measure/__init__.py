"""Small current-route observation helpers used by engine tests."""
from tests.measure.artifact import field_set, has_acroform
from tests.measure.partition import (
    MeasureResult,
    canonical_bbox,
    canonical_origin,
    canonical_preservation_value,
    extract_lines,
    extract_spans,
    extract_words,
    page_count,
    page_size,
    unique_bounded_match,
    widget_field_rects,
)
from tests.measure.sizes import distinct_sizes


def report_from_outcome(outcome) -> dict:
    """Serialize only the existing coverage records; add no inferred draw facts."""
    return {
        "pages": [
            page.model_dump() if hasattr(page, "model_dump") else page
            for page in outcome.pages
        ]
    }


__all__ = [
    "MeasureResult",
    "canonical_bbox",
    "canonical_origin",
    "canonical_preservation_value",
    "distinct_sizes",
    "extract_lines",
    "extract_spans",
    "extract_words",
    "field_set",
    "has_acroform",
    "page_count",
    "page_size",
    "report_from_outcome",
    "unique_bounded_match",
    "widget_field_rects",
]
