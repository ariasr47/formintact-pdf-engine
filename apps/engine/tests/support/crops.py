"""Crop-scoped pixel diff. SPEC §3's instrument, once, for every rendered
criterion: rasterize at GOLDEN_DIFF_ZOOM = 2.0, compare with the pinned
`api.jobs.golden.pixel_diff_ratio` at PIXEL_CHANNEL_DELTA_THRESHOLD = 8, assert
`== 0.0`.

PIXEL_DIFF_PASS_THRESHOLD_PCT (0.1 %) is deliberately NOT used here and this
module never imports it: 0.1 % of a 20 pt crop at zoom 2 is ~1.6 pixels and would
pass a destroyed checkbox."""
import math
from pathlib import Path

from pdft_engine.golden import pixel_diff_ratio
from pdft_engine.pdfops import GOLDEN_DIFF_ZOOM

Rect = tuple[float, float, float, float]  # x0, y0, x1, y1 in PDF POINTS


def crop_png(src: Path, rect_pt: Rect, out: Path) -> None:
    """Crops a page render to `rect_pt`, converting points to pixels at the one
    pinned zoom. floor on the low edge / ceil on the high edge so a crop never
    silently excludes a boundary pixel of the feature it was drawn around."""
    import fitz

    z = GOLDEN_DIFF_ZOOM
    x0, y0, x1, y1 = rect_pt
    clip = fitz.IRect(
        math.floor(x0 * z), math.floor(y0 * z), math.ceil(x1 * z), math.ceil(y1 * z)
    )
    full = fitz.Pixmap(str(src))
    # `fitz.Pixmap(src, clip)` is documented but this PyMuPDF build's overload
    # resolver (args_match) throws on the two-arg form; `(src, width, height, clip)`
    # -- the "scaled copy" overload with width/height held equal to the source's own
    # -- resolves correctly and produces the same crop.
    fitz.Pixmap(full, full.width, full.height, clip).save(out, output="png")


def crop_ratio(reference_png: Path, actual_png: Path, rect_pt: Rect, scratch: Path) -> float | None:
    """The number every crop criterion asserts on. None means a dimension
    mismatch, which is an automatic fail (same rule as the shipped comparator)."""
    scratch.mkdir(parents=True, exist_ok=True)
    tag = "-".join(f"{v:.2f}" for v in rect_pt)
    ref_crop = scratch / f"ref-{tag}.png"
    act_crop = scratch / f"act-{tag}.png"
    crop_png(reference_png, rect_pt, ref_crop)
    crop_png(actual_png, rect_pt, act_crop)
    return pixel_diff_ratio(ref_crop, act_crop)
