"""Accepted Japanese inputs share the same native language and role selection."""
from pathlib import Path

import pytest
from pdft_engine_contract import target_language

from pdft_engine import skill_placement
from tests.support.deliver import deliver
from tests.support.translators import PseudoTranslator

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("target", ["Japanese", "ja", "ja-JP", "日本語"])
def test_japanese_aliases_resolve_to_the_same_native_tag_and_role(target) -> None:
    tag = target_language.bcp47(target)
    assert tag == "ja"
    assert skill_placement.resolve_font_roles(tag)["regular"].name == "NotoSansJP-Regular.ttf"


def test_japanese_alias_delivers_through_the_document_native_path(tmp_path) -> None:
    result, outcome = deliver(
        FIXTURES / "benchmark-invoice.pdf", [1], tmp_path / "out",
        translator=PseudoTranslator(), target_lang="ja-JP",
    )
    assert result.is_file()
    assert outcome.status == "done"
    assert outcome.pages and outcome.pages[0].blocks
