"""Read-only PDF text, page, field, and bounded-matching observations."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import fitz


@dataclass(frozen=True)
class MeasureResult:
    passed: bool
    numbers: dict


@dataclass(frozen=True)
class ExtractedSpan:
    text: str
    bbox: tuple[float, float, float, float]
    origin: tuple[float, float]
    size: float


@dataclass(frozen=True)
class ExtractedLine:
    bbox: tuple[float, float, float, float]
    spans: tuple[ExtractedSpan, ...]


@dataclass(frozen=True)
class ExtractedWord:
    text: str
    bbox: tuple[float, float, float, float]


def canonical_preservation_value(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("nonfinite geometry")
    rounded = round(float(value), 4)
    return 0.0 if rounded == 0.0 else rounded


def canonical_bbox(bbox) -> tuple[float, float, float, float]:
    if len(bbox) != 4:
        raise ValueError("bbox")
    return tuple(canonical_preservation_value(value) for value in bbox)


def canonical_origin(origin) -> tuple[float, float]:
    if len(origin) != 2:
        raise ValueError("origin")
    return tuple(canonical_preservation_value(value) for value in origin)


def unique_bounded_match(left, right, *, tolerance: float = 0.5) -> bool:
    """Require a one-to-one rectangle match within a literal edge tolerance."""
    unmatched = list(right)
    for source in left:
        candidates = [
            index
            for index, candidate in enumerate(unmatched)
            if len(source) == len(candidate)
            and all(
                abs(float(a) - float(b)) <= tolerance
                for a, b in zip(source, candidate, strict=True)
            )
        ]
        if len(candidates) != 1:
            return False
        unmatched.pop(candidates[0])
    return not unmatched


def extract_lines(pdf: Path, page_no: int) -> list[ExtractedLine]:
    with fitz.open(pdf) as document:
        raw = document[page_no - 1].get_text("dict")
    lines = []
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            spans = tuple(
                ExtractedSpan(
                    text=span.get("text", ""),
                    bbox=tuple(span["bbox"]),
                    origin=tuple(span["origin"]),
                    size=float(span["size"]),
                )
                for span in line.get("spans", [])
            )
            if spans:
                lines.append(ExtractedLine(bbox=tuple(line["bbox"]), spans=spans))
    return lines


def extract_spans(pdf: Path, page_no: int) -> list[ExtractedSpan]:
    return [span for line in extract_lines(pdf, page_no) for span in line.spans]


def extract_words(pdf: Path, page_no: int) -> list[ExtractedWord]:
    with fitz.open(pdf) as document:
        rows = document[page_no - 1].get_text("words")
    return [
        ExtractedWord(text=row[4], bbox=tuple(row[:4]))
        for row in rows
    ]


def page_count(pdf: Path) -> int:
    with fitz.open(pdf) as document:
        return document.page_count


def page_size(pdf: Path, page_no: int) -> tuple[float, float]:
    with fitz.open(pdf) as document:
        rect = document[page_no - 1].rect
        return rect.width, rect.height


def widget_field_rects(pdf: Path, page_no: int) -> list[tuple[float, float, float, float]]:
    with fitz.open(pdf) as document:
        return [tuple(widget.rect) for widget in document[page_no - 1].widgets()]
