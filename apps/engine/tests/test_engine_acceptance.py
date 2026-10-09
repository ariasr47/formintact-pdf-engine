"""Small end-to-end native acceptance checks with deterministic providers."""
from pathlib import Path

import fitz

from pdft_engine import pdfops
from tests.support.deliver import deliver
from tests.support.translators import PseudoTranslator

FIXTURES = Path(__file__).parent / "fixtures"


def test_selected_page_native_output_excludes_unselected_source_text(tmp_path) -> None:
    source = tmp_path / "selected.pdf"
    with fitz.open() as document:
        for text in ("SELECTED UNIQUE ALPHA", "EXCLUDED UNIQUE OMEGA"):
            page = document.new_page(width=300, height=200)
            page.insert_text((30, 80), text, fontsize=12)
        document.save(source)

    result, outcome = deliver(
        source, [1], tmp_path / "out", translator=PseudoTranslator(), target_lang="Spanish",
    )
    with fitz.open(result) as document:
        text = "".join(page.get_text() for page in document)
        assert document.page_count == 1
    assert "EXCLUDED UNIQUE OMEGA" not in text
    assert [page.page_number for page in outcome.pages] == [1]


def test_native_delivery_is_offline_and_preserves_requested_page_count(
    tmp_path,
    monkeypatch,
) -> None:
    import socket

    monkeypatch.setattr(socket, "create_connection", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("network used")
    ))
    result, outcome = deliver(
        FIXTURES / "benchmark-invoice.pdf", [1, 2], tmp_path / "out",
        translator=PseudoTranslator(), target_lang="Spanish",
    )
    assert outcome.status == "done"
    assert pdfops.page_count(result) == 2
