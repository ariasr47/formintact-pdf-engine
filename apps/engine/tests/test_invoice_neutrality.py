"""Native invoice delivery preserves document structure and contains capture output."""
from pathlib import Path

from pdft_engine import pdfops
from tests.support.deliver import deliver
from tests.support.translators import PseudoTranslator

FIXTURES = Path(__file__).parent / "fixtures"


def test_invoice_native_delivery_preserves_selected_page_count(tmp_path) -> None:
    result, outcome = deliver(
        FIXTURES / "benchmark-invoice.pdf", [1, 3], tmp_path / "out",
        translator=PseudoTranslator(), target_lang="Spanish",
    )
    assert outcome.status == "done"
    assert pdfops.page_count(result) == 2
    assert [page.page_number for page in outcome.pages] == [1, 3]


def test_hostile_capture_environment_produces_no_capture_bundle(tmp_path, monkeypatch) -> None:
    capture = tmp_path / "capture"
    monkeypatch.setenv("PDF_TRANSLATE_CAPTURE_DIR", str(capture))
    monkeypatch.setenv("PDF_TRANSLATE_CAPTURE_BELOW", "1")
    deliver(
        FIXTURES / "benchmark-invoice.pdf", [1], tmp_path / "out",
        translator=PseudoTranslator(), target_lang="Spanish",
    )
    assert not capture.exists()
