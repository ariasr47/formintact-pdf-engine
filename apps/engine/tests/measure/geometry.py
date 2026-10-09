"""Generic geometry observations; no app-layout admission or draw reconstruction."""
from __future__ import annotations


def intersection_area(a, b) -> float:
    width = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    height = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return width * height


def zero_new_field_overlap(source_rects, delivered_rects) -> bool:
    """Compare literal observed rectangles without assigning text ownership."""
    return tuple(source_rects) == tuple(delivered_rects)
