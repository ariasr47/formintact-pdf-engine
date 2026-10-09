"""Golden-file PNG-diff comparator (SPEC §2 'Golden-file tolerance — pinned, not
shaped'). The three literal values below are the single most load-bearing
constants in the design doc; do not re-derive or 'tune' them independently."""
from pathlib import Path

PIXEL_CHANNEL_DELTA_THRESHOLD = 8  # 0-255 scale; a pixel differs iff ANY channel exceeds this
PIXEL_DIFF_PASS_THRESHOLD_PCT = 0.001  # 0.1% of total page pixels


def pixel_diff_ratio(golden_path: Path, actual_path: Path) -> float | None:
    """Returns the fraction of differing pixels, or None on a dimension mismatch
    (an automatic fail — there is no defined ratio to compute)."""
    import fitz  # deferred: quarantine

    golden = fitz.Pixmap(str(golden_path))
    actual = fitz.Pixmap(str(actual_path))
    if (golden.width, golden.height) != (actual.width, actual.height):
        return None
    total = golden.width * golden.height
    differing = 0
    for y in range(golden.height):
        for x in range(golden.width):
            gp, ap = golden.pixel(x, y), actual.pixel(x, y)
            if any(
                abs(g - a) > PIXEL_CHANNEL_DELTA_THRESHOLD
                for g, a in zip(gp[:3], ap[:3], strict=False)
            ):
                differing += 1
    return differing / total


def passes(golden_path: Path, actual_path: Path) -> bool:
    ratio = pixel_diff_ratio(golden_path, actual_path)
    if ratio is None:
        return False
    return ratio <= PIXEL_DIFF_PASS_THRESHOLD_PCT


from pdft_engine.native_owner import on_native_thread as _on_native_thread  # noqa: E402

pixel_diff_ratio = _on_native_thread(pixel_diff_ratio)
