"""Cancellation during the one document translation prevents publication."""
from pathlib import Path

from pdft_engine_contract.items import TranslationResult

from tests.support import direct_sink

FIXTURES = Path(__file__).parent / "fixtures"


def test_cancellation_after_provider_reply_abandons_the_whole_document() -> None:
    cancelled = {"value": False}
    published = []

    class CancellingTranslator:
        def translate_batch(self, items, *, target_lang):
            cancelled["value"] = True
            return [TranslationResult(id=item.id, translation=item.text) for item in items]

    outcome = direct_sink.run_pipeline_compat(
        pdf_path=FIXTURES / "benchmark-invoice.pdf", kept_pages=[1, 2],
        target_lang="Spanish", dnt_terms=[], translator=CancellingTranslator(),
        provider="anthropic", finalize_page=lambda *_args: published.append("page") or True,
        finalize_document=lambda *_args: published.append("document") or True,
        is_cancelled=lambda: cancelled["value"],
    )
    assert outcome.status == "failed"
    assert published == []
