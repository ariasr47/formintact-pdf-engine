"""The native engine emits typed progress without credentials or payload logs."""
from pathlib import Path

from pdft_engine_contract.events import StageEvent

from tests.support import direct_sink
from tests.support.translators import PseudoTranslator

FIXTURES = Path(__file__).parent / "fixtures"


def _run(recorded, tmp_path):
    document = tmp_path / "document.pdf"

    def commit(produced):
        produced.replace(document)
        return True

    return direct_sink.run_pipeline_compat(
        pdf_path=FIXTURES / "benchmark-invoice.pdf", kept_pages=[1],
        target_lang="Spanish", dnt_terms=[], translator=PseudoTranslator(),
        provider="anthropic", finalize_page=lambda *_args: True,
        finalize_document=commit,
        is_cancelled=lambda: False, emit=recorded.append,
    )


def test_native_document_emits_extract_then_translate_stage(tmp_path) -> None:
    recorded = []
    outcome = _run(recorded, tmp_path)
    stages = [event.stage for event in recorded if isinstance(event, StageEvent)]
    assert outcome.status == "done"
    assert stages[:2] == ["extract", "translate"]


def test_progress_never_contains_key_or_source_payload_fields(tmp_path) -> None:
    recorded = []
    _run(recorded, tmp_path)
    for event in recorded:
        dumped = event.model_dump()
        assert "api_key" not in dumped
        assert "authorization" not in dumped
        assert "source_text" not in dumped
