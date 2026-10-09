"""A document commit is atomic and precedes disposable preview publication."""
from pathlib import Path

from pdft_engine import pdfops
from tests.support import direct_sink
from tests.support.translators import PseudoTranslator

FIXTURES = Path(__file__).parent / "fixtures"


def test_document_commits_before_preview_failure(tmp_path, monkeypatch) -> None:
    calls = []
    committed = tmp_path / "document.pdf"

    def finalize_document(produced):
        calls.append("document")
        produced.replace(committed)
        return True

    def broken_preview(*_args, **_kwargs):
        calls.append("preview")
        raise OSError("preview unavailable")

    monkeypatch.setattr(pdfops, "write_single_page_pdf", broken_preview)
    outcome = direct_sink.run_pipeline_compat(
        pdf_path=FIXTURES / "benchmark-invoice.pdf", kept_pages=[1],
        target_lang="Spanish", dnt_terms=[], translator=PseudoTranslator(),
        provider="anthropic", finalize_page=lambda *_args: False,
        finalize_document=finalize_document, is_cancelled=lambda: False,
    )
    assert outcome.status == "done"
    assert calls[0] == "document"
    assert committed.is_file() and pdfops.page_count(committed) == 1


def test_cancellation_before_extract_publishes_nothing() -> None:
    published = []
    outcome = direct_sink.run_pipeline_compat(
        pdf_path=FIXTURES / "benchmark-invoice.pdf", kept_pages=[1],
        target_lang="Spanish", dnt_terms=[], translator=PseudoTranslator(),
        provider="anthropic", finalize_page=lambda *_args: published.append("page") or True,
        finalize_document=lambda *_args: published.append("document") or True,
        is_cancelled=lambda: True,
    )
    assert outcome.status == "failed"
    assert published == []


def test_preview_slice_contains_exactly_one_selected_page(tmp_path) -> None:
    out = tmp_path / "preview.pdf"
    pdfops.write_single_page_pdf(FIXTURES / "benchmark-invoice.pdf", 2, out)
    assert pdfops.page_count(out) == 1
