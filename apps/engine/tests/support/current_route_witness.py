"""engine-boundary S4-test-split: the engine-environment counterpart of
`apps/api/tests/support/current_route_witness.py` -- pure-PDF-generation half only
(`witness_pdf_bytes`/`write_witness_pdf`/the `WITNESS_*` constants, all `fitz`-only,
zero `api` dependency). The app-side copy's `materialize_witness(client, stub)` is
App-only (drives a real `TestClient` + `QaStubProvider` HTTP round trip) and stays
there for `test_conformance_materialization.py`; duplicated rather than imported
across the lane boundary for the same reason `support/translators.py` is.

Small real-PDF witness for the shipped non-Japanese obstacle-fit route.

The fixture is generated from ordinary WinAnsi Helvetica text so its extracted
geometry is deterministic and easy to inspect.  Six units share one structured
block; the caller may lengthen one unit through a scriptable translator to produce
a mixed fitted/terminal block while the other units remain ordinary fits."""
from __future__ import annotations

from pathlib import Path

import fitz

WITNESS_DNT_TERM = "PETITIONER"
WITNESS_LENGTHENED_UNIT = "1-0#1"
WITNESS_WARNING_UNIT = "1-0#2"
WITNESS_MULTI_ORIGIN_UNIT = "1-1#0"


def witness_pdf_bytes() -> bytes:
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    rows = (
        (100, "Current position", WITNESS_DNT_TERM),
        (115, "Employment history", "Senior analyst"),
        (130, "Employment history", "Worked in operations and compliance"),
    )
    for baseline, label, value in rows:
        row_font = "times-bold" if baseline == 115 else "helv"
        page.insert_text((72, baseline), label, fontsize=10, fontname=row_font)
        value_x = 500 if baseline == 100 else 300
        page.insert_text(
            (value_x, baseline), value, fontsize=10,
            fontname=row_font,
        )
    page.insert_text(
        (72, 220), "Retained source line one with ordinary text.", fontsize=10, fontname="helv"
    )
    page.insert_text(
        (72, 235), "Retained source line two with ordinary text.", fontsize=10, fontname="helv"
    )
    second_page = document.new_page(width=595, height=842)
    second_page.insert_text(
        (72, 100), "Independent extraction fault sentinel", fontsize=10, fontname="helv"
    )
    payload = document.tobytes()
    document.close()
    return payload


def write_witness_pdf(directory: Path) -> Path:
    path = directory / "current-route-witness.pdf"
    path.write_bytes(witness_pdf_bytes())
    return path
