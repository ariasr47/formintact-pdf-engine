"""Real native Japanese rendering and disclosed regular-only role behavior."""
from pathlib import Path

import fitz

from pdft_engine import skill_placement
from tests.support.deliver import deliver
from tests.support.translators import CompactCJKTranslator

FIXTURES = Path(__file__).parent / "fixtures"


def test_japanese_native_document_contains_target_script(tmp_path) -> None:
    result, outcome = deliver(
        FIXTURES / "benchmark-invoice.pdf", [1], tmp_path / "out",
        translator=CompactCJKTranslator(), target_lang="Japanese",
    )
    with fitz.open(result) as document:
        text = "".join(page.get_text() for page in document)
    assert outcome.status == "done"
    assert any(
        unit.outcome == "fitted"
        for page in outcome.pages
        for block in page.blocks
        for unit in block.units
    )
    assert any("\u4e00" <= char <= "\u9fff" for char in text)


def test_cjk_roles_are_honest_regular_only_aliases() -> None:
    japanese = skill_placement.resolve_font_roles("ja")
    simplified = skill_placement.resolve_font_roles("zh-Hans")
    assert set(japanese) == set(simplified) == {"regular"}
    assert japanese["regular"].name == "NotoSansJP-Regular.ttf"
    assert simplified["regular"].name == "NotoSansSC-Regular.ttf"
