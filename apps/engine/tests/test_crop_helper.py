"""The instrument's own instrument. A crop comparator that silently compared the
wrong pixels would make every crop criterion in this feature vacuously green."""
from pathlib import Path

import fitz

from tests.support.crops import crop_png, crop_ratio


def _page_png(path: Path, mark: bool) -> None:
    """A 100x100 pt page, white, with an optional 10x10 pt black square at (20,20)."""
    with fitz.open() as doc:
        page = doc.new_page(width=100, height=100)
        if mark:
            page.draw_rect(fitz.Rect(20, 20, 30, 30), color=None, fill=(0, 0, 0))
        pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
        pix.save(path, output="png")


def test_crop_is_scaled_by_the_pinned_zoom(tmp_path: Path) -> None:
    src = tmp_path / "a.png"
    _page_png(src, mark=False)
    out = tmp_path / "crop.png"
    crop_png(src, (20.0, 20.0, 30.0, 30.0), out)
    pix = fitz.Pixmap(str(out))
    assert (pix.width, pix.height) == (20, 20)  # 10 pt * zoom 2.0


def test_a_crop_over_the_difference_reports_non_zero(tmp_path: Path) -> None:
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    _page_png(a, mark=False)
    _page_png(b, mark=True)
    assert crop_ratio(a, b, (20.0, 20.0, 30.0, 30.0), tmp_path / "s") == 1.0


def test_a_crop_away_from_the_difference_reports_exactly_zero(tmp_path: Path) -> None:
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    _page_png(a, mark=False)
    _page_png(b, mark=True)
    assert crop_ratio(a, b, (60.0, 60.0, 80.0, 80.0), tmp_path / "s") == 0.0
