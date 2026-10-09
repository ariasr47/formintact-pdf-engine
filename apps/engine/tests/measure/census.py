"""AC 37 — intra-span whitespace-gap census.

A span counts when it contains an INTERNAL run of consecutive spaces whose
advance width reaches SEGMENT_GAP_FONT_SIZE_MULTIPLE (1.5) × font size — the
same threshold layout._internal_gaps applies between spans and structurally
cannot apply inside one.
"""

from __future__ import annotations

from pathlib import Path

import fitz

from .partition import MeasureResult

SEGMENT_GAP_FONT_SIZE_MULTIPLE = 1.5


def _space_runs_in_span(span: dict) -> list[tuple[float, float, float]]:
    """Return (x0, x1, width) for each INTERNAL consecutive-space run."""
    chars = span.get("chars") or []
    if not chars:
        return []
    runs: list[tuple[float, float, float]] = []
    i = 0
    n = len(chars)
    while i < n:
        if chars[i].get("c") != " ":
            i += 1
            continue
        start = i
        while i < n and chars[i].get("c") == " ":
            i += 1
        # Internal: not leading, not trailing.
        if start == 0 or i == n:
            continue
        x0 = float(chars[start]["bbox"][0])
        x1 = float(chars[i - 1]["bbox"][2])
        runs.append((x0, x1, x1 - x0))
    return runs


def census_page(pdf: Path, page_no: int) -> list[dict]:
    loci: list[dict] = []
    with fitz.open(pdf) as doc:
        page = doc[page_no - 1]
        data = page.get_text("rawdict")
    for block in data.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                size = float(span.get("size", 0.0))
                threshold = SEGMENT_GAP_FONT_SIZE_MULTIPLE * size
                text = "".join(ch.get("c", "") for ch in span.get("chars") or [])
                for x0, x1, width in _space_runs_in_span(span):
                    if width + 1e-9 >= threshold:
                        loci.append({
                            "page": page_no,
                            "text": text,
                            "x0": x0,
                            "x1": x1,
                            "width": width,
                            "threshold": threshold,
                            "size": size,
                        })
    return loci


def census_pdf(pdf: Path) -> list[dict]:
    with fitz.open(pdf) as doc:
        n_pages = doc.page_count
    loci: list[dict] = []
    for n in range(1, n_pages + 1):
        loci.extend(census_page(pdf, n))
    return loci


def ac37_internal_space_census(
    source_pdf: Path, delivered_pdf: Path, report: dict,
) -> MeasureResult:
    """Publish every over-threshold internal space run on the source PDF.

    The delivered argument is accepted so the function matches the library
    signature; the census is a property of extracted source spans (AC 37).
    """
    del delivered_pdf, report
    loci = census_pdf(source_pdf)
    return MeasureResult(
        True,
        {"count": len(loci), "loci": loci},
    )
