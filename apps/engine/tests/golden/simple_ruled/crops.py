"""Crop rectangles for simple_ruled.pdf, in PDF POINTS, derived ONCE by a human
(from the fixture's own generator literals in generate_fixtures.py -- this file is
ours to regenerate, so a reader can see exactly what the crops are drawn around)
and committed as literals (SPEC §3). The reference is always the ORIGINAL page
render, never a re-cut baseline.

Same shape as tests/golden/fl150/crops.py (AC 18), page always 1. No
GREY_PATCH_CROPS assertion here -- this fixture's blocks are narrow enough that
the pre-fix engine's opaque fill lands mostly on already-white paper, so a grey
patch is not this fixture's distinguishing red state; the vertical table divider
(RULE_CROPS) and the Signature overflow (FILLIN_CROPS) are."""

Crop = tuple[int, float, float, float, float]  # page, x0, y0, x1, y1

# AC 1 shape -- exactly 6, all on page 1. The 10x10pt boxes generate_fixtures.py
# draws at (72, 100 + i*30) for i in 0..5.
CHECKBOX_CROPS: list[Crop] = [
    (1, 72.0, 100.0, 82.0, 110.0),
    (1, 72.0, 130.0, 82.0, 140.0),
    (1, 72.0, 160.0, 82.0, 170.0),
    (1, 72.0, 190.0, 82.0, 200.0),
    (1, 72.0, 220.0, 82.0, 230.0),
    (1, 72.0, 250.0, 82.0, 260.0),
]
assert len(CHECKBOX_CROPS) == 6, "AC 18 pins SIX checkbox crops on page 1 (simple_ruled)"

# AC 2 -- rules and cell borders. The vertical divider at x=300 inside each of the
# 3 data rows is destroyed by the pre-fix engine's per-block opaque fill (measured
# red, ratio 0.25, against known-bad) because today's fill spans the WHOLE row
# block bbox (Label N + Value N collapsed into one block) -- exactly the
# column-collapse defect SPEC §2.2 names. The 4 horizontal rules sit outside any
# block bbox and are unaffected either way; committed anyway as the AC 2 positive
# check.
RULE_CROPS: list[Crop] = [
    (1, 298.0, 306.0, 302.0, 318.0),   # vertical divider, row 1 -- RED vs known-bad
    (1, 298.0, 330.0, 302.0, 342.0),   # vertical divider, row 2 -- RED vs known-bad
    (1, 298.0, 354.0, 302.0, 366.0),   # vertical divider, row 3 -- RED vs known-bad
    (1, 72.0, 299.0, 523.0, 301.0),    # top horizontal rule
    (1, 72.0, 371.0, 523.0, 373.0),    # bottom horizontal rule
]

# AC 5 -- the a-h lettered-item label starts, one crop per item. A narrow strip
# immediately left of and above where each item's first glyph begins -- blank in
# the original (SPEC's "the strip ... is blank in the original" rule). Rows from
# extract_blocks(): 1-9..1-16, y0 = 409.25 + i*16.
LABEL_START_CROPS: list[Crop] = [
    (1, 64.0, 407.0, 72.0, 413.0),
    (1, 64.0, 423.0, 72.0, 429.0),
    (1, 64.0, 439.0, 72.0, 445.0),
    (1, 64.0, 455.0, 72.0, 461.0),
    (1, 64.0, 471.0, 72.0, 477.0),
    (1, 64.0, 487.0, 72.0, 493.0),
    (1, 64.0, 503.0, 72.0, 509.0),
    (1, 64.0, 519.0, 72.0, 525.0),
]
assert len(LABEL_START_CROPS) == 8, "AC 5 pins one crop per lettered item a-h"

# AC 6 -- fill-in areas: regions EMPTY in the original. The blank strip between
# "Signature:" (block bbox ends x=117.58) and the underline drawn at (140,602).
# Measured RED against known-bad (ratio ~0.028): the pre-fix engine's translated
# "Signature:" run overflows past the original block's own x1 into this blank
# strip -- the "delivered page is a different colour where the original carried no
# text" defect AC 4/AC 6 exist to close.
FILLIN_CROPS: list[Crop] = [
    (1, 140.0, 588.0, 400.0, 601.0),
]

# Not part of this fixture's committed set (SPEC's simple_ruled shape omits
# RIGHT_MARGIN_CROPS / TEXT_REGION_CROPS -- those are FL-150-specific: the CJK
# overshoot band and the "page was rebuilt" proof).
RIGHT_MARGIN_CROPS: list[Crop] = []
TEXT_REGION_CROPS: list[Crop] = []

# AC 4's set: every committed crop in which the original render contains no
# glyphs (a rule/checkbox is vector ink, not a text glyph, and belongs here).
NO_GLYPH_CROPS: list[Crop] = (
    CHECKBOX_CROPS + RULE_CROPS + LABEL_START_CROPS + FILLIN_CROPS
)
