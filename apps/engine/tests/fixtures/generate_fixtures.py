"""Regenerates every committed test fixture. Deterministic; run from apps/engine:
    uv run python tests/fixtures/generate_fixtures.py
Fixtures are committed binaries; this script is the record of how they were made.
engine-boundary S2-move put the fixture DATA at apps/engine/tests/fixtures (git mv);
S4-test-split moved this generator script (a Python support module, not data) to sit
next to that data -- `apps/api/tests` may no longer name PyMuPDF as a direct import.
A relative parents[] computation, not an import of tests.support.paths, so
`python tests/fixtures/generate_fixtures.py` keeps working when run directly (its
own directory on sys.path, not the apps/engine package root)."""
from pathlib import Path

import fitz

OUT = Path(__file__).resolve().parents[4] / "apps" / "engine" / "tests" / "fixtures"

# A zero-page PDF cannot be SAVED by MuPDF, so it is handwritten: a syntactically
# minimal PDF whose page tree has /Count 0. fitz opens it (repairing the xref) and
# reports page_count == 0 — exactly the shape the zero_pages validation must catch.
ZERO_PAGE_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
    b"trailer<</Root 1 0 R/Size 3>>\n"
    b"%%EOF\n"
)


def _grey_png() -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 80))
    pix.clear_with(200)
    return pix.tobytes("png")


def _text_page(doc: fitz.Document, lines: list[str]) -> None:
    page = doc.new_page(width=595, height=842)  # A4 portrait
    for i, line in enumerate(lines):
        page.insert_text(fitz.Point(72, 100 + 18 * i), line, fontsize=11)


def _image_page(doc: fitz.Document, png: bytes) -> None:
    page = doc.new_page(width=595, height=842)
    page.insert_image(fitz.Rect(72, 72, 400, 500), stream=png)


def make_invoice() -> None:
    with fitz.open() as doc:
        for n in range(1, 9):
            page = doc.new_page(width=595, height=842)  # A4 portrait
            # Line 1: uniform-style block (header) — whole line bold+serif (tibo).
            page.insert_text(
                fitz.Point(72, 100), f"INVOICE 2026-{n:04d} — Benchmark Fixture Page {n} of 8",
                fontsize=13, fontname="tibo",
            )
            # Line 2: plain control block ("Bill to") — unchanged, helv regular.
            page.insert_text(
                fitz.Point(72, 130), "Bill to: Redline Verlag GmbH, Musterstrasse 12, 10115 Berlin",
                fontsize=11, fontname="helv",
            )
            # Line 3: mixed-style block — label (helv) + longer italic run (tiit),
            # same baseline. Dominant share ~0.74-0.76 — below the 0.85 threshold.
            label3 = f"Line item {n}: "
            page.insert_text(fitz.Point(72, 160), label3, fontsize=11, fontname="helv")
            w3 = fitz.Font("helv").text_length(label3, fontsize=11)
            page.insert_text(
                fitz.Point(72 + w3, 160), "professional document translation services",
                fontsize=11, fontname="tiit",
            )
            # Line 4: mixed-style block — label (helv) + bold amount (hebo), same
            # baseline. Measured dominant share ~0.745, same band as block 3.
            label4 = "Amount due: "
            page.insert_text(fitz.Point(72, 190), label4, fontsize=11, fontname="helv")
            w4 = fitz.Font("helv").text_length(label4, fontsize=11)
            page.insert_text(
                fitz.Point(72 + w4, 190), f"EUR {n * 125}.00 — payable within 30 days",
                fontsize=11, fontname="hebo",
            )
        doc.save(OUT / "benchmark-invoice.pdf")


def make_partly_scanned() -> None:
    png = _grey_png()
    with fitz.open() as doc:
        for n in range(1, 9):
            if n in (3, 4):  # the §4 copy example: "Pages 3–4", "6 of 8"
                _image_page(doc, png)
            else:
                _text_page(
                    doc, [f"Readable text content on page {n} — more than twenty characters."]
                )
        doc.save(OUT / "partly-scanned.pdf")


def make_all_scanned() -> None:
    png = _grey_png()
    with fitz.open() as doc:
        for _ in range(4):
            _image_page(doc, png)
        doc.save(OUT / "all-scanned.pdf")


def make_encrypted() -> None:
    with fitz.open() as doc:
        for n in (1, 2):
            _text_page(doc, [f"Secret page {n} — this fixture is AES-256 encrypted."])
        doc.save(
            OUT / "encrypted.pdf",
            encryption=fitz.PDF_ENCRYPT_AES_256,
            user_pw="secret",
            owner_pw="secret",
        )


def make_page_count(name: str, pages: int) -> None:
    with fitz.open() as doc:
        for n in range(1, pages + 1):
            _text_page(doc, [f"Page {n} of {pages} — boundary fixture text content line."])
        doc.save(OUT / name)


def make_simple_ruled() -> None:
    """The in-repo adversarial fixture (AC 18): checkboxes, a ruled table, a
    label/value two-column row set, lettered items a-h, and a fill-in area with an
    underline. Every criterion of ACs 1-6 must also hold here, and unlike FL-150
    this file is ours to regenerate, so a reader can see exactly what the crops
    are drawn around."""
    with fitz.open() as doc:
        page = doc.new_page(width=595, height=842)
        # 6 checkboxes, 10x10 pt, on a 30 pt pitch, with their labels to the right.
        for i in range(6):
            y = 100 + i * 30
            page.draw_rect(fitz.Rect(72, y, 82, y + 10), color=(0, 0, 0), width=0.75)
            page.insert_text(fitz.Point(92, y + 9), f"Option {i + 1}", fontsize=10, fontname="helv")
        # A ruled 3-row table: horizontal rules + one vertical divider.
        for i in range(4):
            page.draw_line(fitz.Point(72, 300 + i * 24), fitz.Point(523, 300 + i * 24), width=0.75)
        page.draw_line(fitz.Point(300, 300), fitz.Point(300, 372), width=0.75)
        for i in range(3):
            y = 300 + i * 24
            page.insert_text(fitz.Point(78, y + 16), f"Label {i + 1}", fontsize=10, fontname="helv")
            page.insert_text(
                fitz.Point(306, y + 16), f"Value {i + 1}", fontsize=10, fontname="helv"
            )
        # Lettered items a-h, each on its own line, its own y.
        for i, letter in enumerate("abcdefgh"):
            page.insert_text(
                fitz.Point(72, 420 + i * 16), f"{letter}. Item {letter} of the lettered list",
                fontsize=10, fontname="helv",
            )
        # A fill-in area: an underline with nothing above it.
        page.insert_text(fitz.Point(72, 600), "Signature:", fontsize=10, fontname="helv")
        page.draw_line(fitz.Point(140, 602), fitz.Point(400, 602), width=0.75)
        doc.save(OUT / "simple_ruled.pdf")


def make_stub_floor() -> None:
    """RETIRED as a red-capable fixture by layout-fit-fidelity, kept committed and
    unregenerated as a historical record (its own generation code is left
    untouched below). FINDING, recorded rather than silently dropped
    (evidence/CANARY.md, test_terminal_rung_floor.py): `obstacles.py`'s
    `available_width` is MECHANICALLY never narrower than a unit's own source
    bbox ("cannot make anything worse" clamp, SPEC §2.2), so this fixture's
    narrow-TEXTBOX-column design -- built to trap the OLD bbox.width-bound
    engine -- no longer constrains width at all: every one of its units now
    scores `page_translated_share == 1.0` under `SpacelessCJKTranslator`. AC 24b
    (truncated-unit floor) and AC 30 Case B (below-floor terminal state) both now
    point at an already-reached REAL page instead: `fl150_print.pdf` page 2
    (T0=51, T1=3, share=0.255). Regenerating this fixture as a genuine width trap
    turns out to be near-impossible by construction (the same clamp that closes
    the width-truncation defect this feature exists to fix also closes off
    "short cell, long translation" as a synthetic trap, absent a real same-row
    obstacle AND an expanding translator neither of which SpacelessCJKTranslator
    is). `main()` below no longer calls this function; the file stays committed,
    unused by any test, as the historical record SPEC §9's documentation
    discipline asks for.

    AC 24b's / AC 30 Case B's ORIGINAL synthetic red state: a page whose every unit sits in
    a bbox that cannot hold more than the truncation marker. The pre-fix engine never
    truncates on FL-150, so this defect class has no known-bad render -- this fixture
    IS the named synthetic red state (SPEC AC 24b, AC 19).

    REDESIGNED by the verification lane (Task 16, rendered-not-asserted): a single
    `insert_text` call per row (the original design) makes the block's OWN bbox
    width exactly match that row's rendered text -- self-defeating, since the
    original text always "fits" the box it was measured from, whatever its length,
    and the sibling row 30pt below leaves enough vertical room for the CJK-
    substituted text to wrap and grow into without ever truncating (verified: it
    doesn't). `insert_textbox` instead pre-wraps each row's text into a genuinely
    NARROW column (40pt wide), so the row's bbox is bounded by the COLUMN, not by
    the content -- the CJK-substituted text (full-width glyphs, ~2x narrower
    columns) needs roughly twice as many lines as the column already uses, which
    the tight row pitch cannot accommodate. Verified truncated on all 6 rows.

    RETUNED by the verification lane (Task 17, AC 30 Case B): the first pitch
    (85pt, a 5pt gap) truncated all 6 rows but only down to ~53% of each unit's
    translated characters surviving on average -- AC 24b's per-UNIT floor ("keeps
    more than the marker") was satisfied, but the PAGE-level
    `page_translated_share` (0.529) stayed just above `PAGE_TRANSLATED_MIN_SHARE`
    (0.5), so this fixture could not serve as Case B's red state until tightened.
    84pt (a 4pt gap) truncates every row further (share ~0.456) while every unit
    still keeps at least 1 drawn character (AC 24b's floor still holds) and the 6
    rows still extract as 6 SEPARATE blocks -- verified empirically: a 2pt gap
    (82pt pitch) merges all 6 rows into ONE PyMuPDF block instead, which changes
    the growth-ceiling geometry entirely and is NOT what this fixture is for."""
    with fitz.open() as doc:
        page = doc.new_page(width=200, height=520)
        label = "Label {i} with a very great deal of descriptive text about this field"
        for i in range(6):
            y0 = 10 + i * 84
            rect = fitz.Rect(20, y0, 60, y0 + 80)  # a narrow 40pt column, 80pt tall
            leftover = page.insert_textbox(rect, label.format(i=i), fontsize=8, fontname="helv")
            assert leftover >= 0, f"row {i} overflowed the ORIGINAL column: {leftover}"
        doc.save(OUT / "stub_floor.pdf")


def make_vector_obstacles() -> None:
    """SPEC §2.2a's committed fixture (R4 pass 7, fifth geometry added at pass 8).
    One page, FIVE geometries, chosen so that EVERY admission literal and the page
    clamp each has a red proof (`tests/test_vector_obstacles.py` reads this file
    directly against `pdfops.vector_rects`):

    1. a bordered cell whose own text sits inside it -- the CONTAINING edge bounds
       instead of unfitting (the trap a solid-body reading would fall into).
    2. an 18 x 9pt drawn checkbox right of a short label -- the BLOCKING edge
       stops the wrap at its own near edge.
    3. a 2x2pt speck and a 0.4x0.4pt tick -- both fail `VECTOR_MIN_EXTENT_PT`
       (4.0pt on the long axis) and must be admitted by NEITHER test.
    4. a full-page 5% grey wash, drawn FIRST (bottom of z-order) so it does not
       obscure the rest of the page's content -- fails `VECTOR_MAX_AREA_RATIO`
       (0.5) at 100% page coverage.
    5. a 0.5 x 120pt bleed rule in the TOP band whose x1 runs 12pt past the
       page's right edge (612 + 12 = 624), plus a long label in a LOWER band with
       no right obstacle of its own -- the rule clears both admission literals,
       so the UNCLAMPED union would hand `content_box.x1` an off-page value and
       license the label to draw past the sheet; the page clamp (§2.2a,
       `content_box = union(...) INTERSECTED WITH the page rectangle`) is what
       keeps it on the sheet.
    """
    with fitz.open() as doc:
        page = doc.new_page(width=612, height=792)  # US Letter

        # Geometry 4 FIRST -- a full-page 5% grey wash (fill (0.95, 0.95, 0.95),
        # 100% page area >= VECTOR_MAX_AREA_RATIO). Drawn before everything else
        # so it never occludes the rest of the fixture's content.
        page.draw_rect(
            fitz.Rect(0, 0, 612, 792), color=None, fill=(0.95, 0.95, 0.95), fill_opacity=1.0,
        )

        # Geometry 1 -- a bordered cell CONTAINING its own text.
        page.draw_rect(fitz.Rect(60, 90, 300, 112), color=(0, 0, 0), width=0.75)
        page.insert_text(fitz.Point(70, 107), "Cell label text", fontsize=10, fontname="helv")

        # Geometry 2 -- an 18x9pt checkbox BLOCKING a short label's wrap.
        page.insert_text(fitz.Point(60, 148), "Option", fontsize=10, fontname="helv")
        page.draw_rect(fitz.Rect(150, 140, 168, 149), color=(0, 0, 0), width=0.75)

        # Geometry 3 -- a 2x2pt speck and a 0.4x0.4pt tick, both below
        # VECTOR_MIN_EXTENT_PT on their LONG axis.
        page.draw_rect(fitz.Rect(400, 178, 402, 180), color=(0, 0, 0), fill=(0, 0, 0))
        page.draw_rect(fitz.Rect(450, 178.0, 450.4, 178.4), color=(0, 0, 0), fill=(0, 0, 0))

        # Geometry 5 -- a bleed rule 12pt past the page's right edge (top band),
        # and a long label in a lower band with no obstacle of its own to its
        # right (the label the page clamp must keep on the sheet).
        page.draw_rect(fitz.Rect(504, 20, 624, 20.5), color=(0, 0, 0), fill=(0, 0, 0))
        page.insert_text(
            fitz.Point(60, 760),
            "A long unbounded label with no printed cell or checkbox to its right",
            fontsize=10, fontname="helv",
        )
        doc.save(OUT / "vector_obstacles.pdf")


def make_page_box_anomaly() -> None:
    """SPEC §2.2a's page-box-guard red proof (R4 pass 9, §3 AC 38). Two pages, no
    LLM and no spend, built via `Document.xref_set_key` because PyMuPDF's own
    `set_cropbox`/`set_mediabox` API REFUSES a CropBox outside its MediaBox --
    exactly the anomaly page 2 needs to prove.

    Page 1 -- ATTEMPTS G1's falsifier (a negative CropBox x0, MediaBox shifted to
    match so containment still holds and G2 does not also fire). **Recorded
    finding, not a silent gap**: PyMuPDF/MuPDF's `page.rect` (`fz_bound_page`) is
    ALWAYS reported at a normalized (0, 0) origin -- confirmed three ways during
    this build (the API route above, a hand-written minimal PDF opened cold, and
    this xref-mutated page all report `page.rect.x0 == page.rect.y0 == 0.0`
    regardless of the underlying MediaBox/CropBox origin; extracted word
    coordinates are RE-BASED to the same normalized frame). **So G1, as specified
    against `page.rect`, cannot be violated through this pinned toolchain** --
    this is exactly the escape hatch SPEC §2.2a/§3 AC 38 names: "G1 stays a
    runtime guard" (harmless, correct, and never observed firing on a real file
    this library can open). Page 1 is kept in the fixture as the recorded attempt,
    not deleted, per the same discipline `stub_floor.pdf` uses.

    Page 2 -- G2's falsifier: a CropBox genuinely NOT contained in its MediaBox.
    `page.mediabox.contains(page.cropbox)` is False for this page, confirmed
    empirically (unlike G1, PyMuPDF's `.mediabox`/`.cropbox` PROPERTIES do carry
    the raw, non-normalized box values through -- this is the "one-print" fact
    §2.2a's build obligation asks for)."""
    with fitz.open() as doc:
        page1 = doc.new_page(width=612, height=792)
        page1.insert_text(
            fitz.Point(72, 100), "Anomaly page 1 -- G1's attempted falsifier.", fontsize=11,
        )
        xref1 = page1.xref
        # MediaBox and CropBox shifted TOGETHER to a negative origin -- containment
        # holds (G2 does not also fire), isolating G1's falsifier alone.
        doc.xref_set_key(xref1, "MediaBox", "[-36 0 576 792]")
        doc.xref_set_key(xref1, "CropBox", "[-36 0 576 792]")

        page2 = doc.new_page(width=612, height=792)
        page2.insert_text(fitz.Point(72, 100), "Anomaly page 2 -- G2's falsifier.", fontsize=11)
        xref2 = page2.xref
        # MediaBox stays the ordinary (0, 0, 612, 792); CropBox is set OUTSIDE it
        # on both edges -- genuinely not contained, PyMuPDF's own validated
        # set_cropbox() API refuses this, hence the direct xref write.
        doc.xref_set_key(xref2, "CropBox", "[-50 -50 700 850]")

        doc.save(OUT / "page_box_anomaly.pdf")


def make_graphics_state_leak() -> None:
    """AC 36's committed state-leaking control.

    One page whose content stream ENDS with a non-default fill colour
    (`0.55 0.55 0.55 rg`) and a non-default ExtGState alpha (`/GS0 gs` with
    `/ca 0.35`) still active, plus one ordinary black sentence to translate.
    The leak is a property of the INPUT document, not of any build of ours,
    which is why AC 36's red proof cannot be the pre-feature build.

    Geometry: US Letter, one Helvetica 11 pt sentence at (72, 120). The
    inserted text is itself wrapped in q…Q by MuPDF so it stays black; the
    leak is appended AFTER that restore so subsequently appended content
    inherits grey fill + 0.35 alpha unless the rebuild issues its own wrap.
    """
    with fitz.open() as doc:
        page = doc.new_page(width=612, height=792)
        page.insert_text(
            fitz.Point(72, 120),
            "Please translate this ordinary black sentence.",
            fontsize=11, fontname="helv",
        )
        gs_xref = doc.get_new_xref()
        doc.update_object(gs_xref, "<< /Type /ExtGState /ca 0.35 >>")
        es_xref = doc.get_new_xref()
        doc.update_object(es_xref, f"<< /GS0 {gs_xref} 0 R >>")
        res_kind, res_val = doc.xref_get_key(page.xref, "Resources")
        assert res_kind == "xref", res_kind
        res_xref = int(res_val.split()[0])
        doc.xref_set_key(res_xref, "ExtGState", f"{es_xref} 0 R")
        cxref = page.get_contents()[0]
        doc.update_stream(cxref, doc.xref_stream(cxref) + b"\n0.55 0.55 0.55 rg\n/GS0 gs\n")
        doc.set_metadata({})
        doc.save(OUT / "graphics_state_leak.pdf", garbage=0, deflate=False, no_new_id=True)


def make_internal_space_run() -> None:
    """AC 37's committed positive-control fixture.

    One page, two Helvetica 10 pt spans:
      * (72, 120) 'over' + 8 spaces + 'here' — internal run advance
        8 × ~2.78 pt ≈ 22.2 pt, which exceeds 1.5 × 10 = 15 pt.
      * (72, 160) 'under' + 4 spaces + 'here' — 4 × ~2.78 ≈ 11.1 pt,
        deliberately just under the threshold.
    The census must report exactly one locus, the over-threshold span.
    """
    with fitz.open() as doc:
        page = doc.new_page(width=612, height=792)
        page.insert_text(
            fitz.Point(72, 120), "over" + (" " * 8) + "here",
            fontsize=10, fontname="helv",
        )
        page.insert_text(
            fitz.Point(72, 160), "under" + (" " * 4) + "here",
            fontsize=10, fontname="helv",
        )
        doc.set_metadata({})
        doc.save(OUT / "internal_space_run.pdf", garbage=0, deflate=False, no_new_id=True)


def main() -> None:
    make_invoice()
    make_simple_ruled()
    make_partly_scanned()
    make_all_scanned()
    make_encrypted()
    (OUT / "zero-page.pdf").write_bytes(ZERO_PAGE_PDF)
    make_page_count("over-200-pages.pdf", 201)
    make_page_count("exactly-200-pages.pdf", 200)
    (OUT / "not-a-pdf.bin").write_bytes(
        b"This is definitely not a PDF file - just plain bytes for the not_a_pdf path.\n" * 4
    )
    # make_stub_floor() -- RETIRED, see its own docstring. stub_floor.pdf stays
    # committed (no test reads it any more) rather than deleted, as the record.
    make_vector_obstacles()
    make_page_box_anomaly()
    make_graphics_state_leak()
    make_internal_space_run()
    print(f"fixtures written to {OUT}")


if __name__ == "__main__":
    main()
