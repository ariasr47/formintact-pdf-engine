"""The retired app transformation modules stay absent."""
from pathlib import Path

PACKAGE = Path(__file__).parents[1] / "pdft_engine"


def test_retired_transform_modules_do_not_exist() -> None:
    assert [name for name in ("fit.py", "layout.py", "obstacles.py", "type0.py")
            if (PACKAGE / name).exists()] == []


def test_retained_pdf_utility_has_no_retired_import_or_insertion_algorithm() -> None:
    text = (PACKAGE / "pdfops.py").read_text(encoding="utf-8")
    for token in (
        "pdft_engine.fit", "pdft_engine.layout", "pdft_engine.obstacles",
        "prepare_unit_draws", "rebuild_page_to_file", "apply_strip_plan",
    ):
        assert token not in text
