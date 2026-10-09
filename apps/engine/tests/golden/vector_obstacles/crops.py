"""Crop rectangles for vector_obstacles.pdf, in PDF POINTS, derived ONCE
from the committed generator literals in generate_fixtures.make_vector_obstacles
and committed as literals. First capture of a new fixture, not a re-cut
(SPEC §2.2a). The reference is always the ORIGINAL page render.

Each geometry has a crop that reddens when its literal is removed (AC 11):
the checkbox member reddens (ratio 0.3071) when §2.2a's vector obstacles
are disabled under the first-capture lengthening translator; speck/tick
stay admitted-out by VECTOR_MIN_EXTENT_PT and the wash by
VECTOR_MAX_AREA_RATIO (they are not obstacles either way once disabled,
so those crops stay 0.0 against known-bad and prove the exclusion
literals independently via test_vector_obstacles.py).

known-bad/page-1.png is the delivered render with
`pdfops._vector_obstacles_enabled = False` through
`_LengtheningTranslator` (see test_vector_obstacle_crops.py). First
capture of a new fixture, not a re-cut of any shipped golden.
"""

Crop = tuple[int, float, float, float, float]  # page, x0, y0, x1, y1

# Geometry 2 -- the 18x9pt blocking checkbox at (150, 140, 168, 149).
CHECKBOX_CROPS: list[Crop] = [
    (1, 150.0, 140.0, 168.0, 149.0),
]
assert len(CHECKBOX_CROPS) == 1

# Geometry 1 -- 2pt strip on the containing cell's right edge (x=300).
CELL_BORDER_CROPS: list[Crop] = [
    (1, 298.0, 90.0, 302.0, 112.0),
]

# Geometry 3 -- speck (2x2) and tick (0.4x0.4), both below VECTOR_MIN_EXTENT_PT.
SPECK_CROPS: list[Crop] = [
    (1, 399.0, 177.0, 403.0, 181.0),
    (1, 449.5, 177.5, 451.0, 179.0),
]

# Geometry 5 -- 2pt right-edge crop in the unbounded-label band (SPEC: the
# unclamped run reddens this crop; with the page clamp it stays 0.0).
RIGHT_EDGE_CROPS: list[Crop] = [
    (1, 610.0, 750.0, 612.0, 772.0),
]

# Geometry 4 -- a wash-only patch far from every text unit. The wash is
# excluded by VECTOR_MAX_AREA_RATIO; the crop stays the page colour.
WASH_CROPS: list[Crop] = [
    (1, 10.0, 400.0, 40.0, 430.0),
]

# AC 4's set: originally-blank ink (vector, not a text glyph).
NO_GLYPH_CROPS: list[Crop] = (
    CHECKBOX_CROPS + CELL_BORDER_CROPS + SPECK_CROPS + RIGHT_EDGE_CROPS + WASH_CROPS
)
