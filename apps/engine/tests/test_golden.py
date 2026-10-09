"""Pinned RGB pixel comparator behavior."""
from pathlib import Path

import fitz

from pdft_engine.golden import (
    PIXEL_CHANNEL_DELTA_THRESHOLD,
    PIXEL_DIFF_PASS_THRESHOLD_PCT,
    passes,
    pixel_diff_ratio,
)


def _png(path: Path, width=10, height=10, color=(255, 255, 255)):
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)
    pix.clear_with(color[0])
    pix.save(path)


def test_identical_images_pass_with_zero_diff(tmp_path) -> None:
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    _png(a)
    b.write_bytes(a.read_bytes())
    assert pixel_diff_ratio(a, b) == 0.0
    assert passes(a, b)


def test_channel_delta_threshold_and_fraction_are_literal() -> None:
    assert PIXEL_CHANNEL_DELTA_THRESHOLD == 8
    assert PIXEL_DIFF_PASS_THRESHOLD_PCT == 0.001


def test_dimension_mismatch_is_an_automatic_failure(tmp_path) -> None:
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    _png(a, 10, 10)
    _png(b, 11, 10)
    assert pixel_diff_ratio(a, b) is None
    assert passes(a, b) is False
