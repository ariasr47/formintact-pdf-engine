"""Page notices describe typed native retention without inventing a cause."""
import json
from pathlib import Path
from types import SimpleNamespace

from pdf_translate import ExtractResult

from pdft_engine import pdfops, pipeline, skill_placement
from tests.support.deliver import deliver

FIXTURES = Path(__file__).parent / "fixtures"


class _ZeroCallTranslator:
    def __init__(self) -> None:
        self.calls = 0

    def translate_batch(self, _items, *, target_lang):
        self.calls += 1
        raise AssertionError(f"empty native partitions called provider for {target_lang}")


def _page(provider_cause=None, outcome="left_in_original"):
    occurrence = skill_placement.NativeOccurrence(
        subset_page=0, native_id="n", duplicate_ordinal=0, original_page=1,
        page_segment_ordinal=0, bbox=(1.0, 2.0, 4.0, 6.0), origin=(1.0, 5.0),
        source_size=10.0, style_key="native-sha256:" + "a" * 64, core="core",
        source_text="source", source_sha256="b" * 64,
    )
    prepared = SimpleNamespace(
        selected_original_pages=(1,), native_occurrences=(occurrence,), masked=(),
    )
    result = SimpleNamespace(
        outcomes=[skill_placement.CoreOutcome(
            core="core", translation="source", outcome=outcome, ratio=None,
        )],
        reverted=frozenset({"core"}) if provider_cause is None else frozenset(),
        retention_kinds=(
            {"core": "overflow_below_floor"} if provider_cause is None else {}
        ),
    )
    return pipeline._occurrence_coverage(prepared, result, provider_cause)[0]


def test_native_floor_retention_uses_the_source_text_advisory() -> None:
    page = _page()
    assert page.page_flag is None
    assert page.page_source_text_flag == "page_text_left_in_source_language"
    assert "original language" in page.page_source_text_flag_message


def test_provider_failure_uses_one_loud_page_flag_only() -> None:
    page = _page("provider_error")
    assert page.page_flag == "page_returned_untranslated"
    assert page.page_flag_severity == "needs_attention"
    assert page.page_source_text_flag is None


def test_page_notice_copy_pluralizes_without_claiming_a_mechanism() -> None:
    assert pipeline.page_flag_message(1).startswith("1 page")
    assert pipeline.page_flag_message(2).startswith("2 page")


def test_all_scanned_delivery_preserves_copied_unchanged_advisory(tmp_path) -> None:
    source = FIXTURES / "all-scanned.pdf"
    pages = list(range(1, pdfops.page_count(source) + 1))
    translator = _ZeroCallTranslator()

    result, outcome = deliver(
        source,
        pages,
        tmp_path / "all-scanned-delivery",
        translator=translator,
        target_lang="Spanish",
    )

    assert outcome.status == "done"
    assert translator.calls == 0
    assert pdfops.page_count(result) == len(pages)
    assert [page.page_number for page in outcome.pages] == pages
    assert all(page.page_flag == "page_copied_unchanged" for page in outcome.pages)
    assert all(page.page_flag_severity == "advisory" for page in outcome.pages)
    assert all(page.blocks == [] for page in outcome.pages)
    assert all(page.page_source_text_flag is None for page in outcome.pages)


def test_text_bearing_identifier_passthrough_only_page_is_not_marked_scanned(
    tmp_path,
    monkeypatch,
) -> None:
    source = FIXTURES / "simple_ruled.pdf"
    assert pdfops.probe(source, max_pages=1) == [True]
    rows = (
        {
            "id": 1,
            "page": 0,
            "bbox": [20.0, 30.0, 160.0, 45.0],
            "origin": [20.0, 42.0],
            "size": 12.0,
            "core": "FORM-1234",
            "text": "FORM-1234",
            "bold": False,
            "italic": False,
            "color": 0,
            "dir": [1.0, 0.0],
            "passthrough": False,
        },
        {
            "id": 2,
            "page": 0,
            "text": "intentional passthrough",
            "passthrough": True,
        },
    )

    def extract(_source_pdf, workdir):
        segments = Path(workdir) / "segments.json"
        segments.parent.mkdir(parents=True, exist_ok=True)
        segments.write_text(json.dumps(rows), encoding="utf-8")
        return ExtractResult(
            cores=({"text": "FORM-1234"},),
            segments=rows,
            warnings=(),
            segments_path=str(segments),
        )

    def place(*, prepared, **_kwargs):
        return skill_placement.PlacementResult(
            pdf=prepared.source_pdf,
            outcomes=[
                skill_placement.CoreOutcome(
                    core="FORM-1234",
                    translation="FORM-1234",
                    outcome="left_in_original",
                    ratio=None,
                )
            ],
            reverted=frozenset(),
            retention_kinds={},
        )

    monkeypatch.setattr(skill_placement, "run_extract", extract)
    monkeypatch.setattr(skill_placement, "widget_text_scaffold", lambda _source: {})
    monkeypatch.setattr(
        skill_placement.skill_identifiers,
        "identifier_set",
        lambda _warnings, **_kwargs: {"FORM-1234"},
    )
    monkeypatch.setattr(skill_placement, "place_prepared_document", place)
    translator = _ZeroCallTranslator()

    result, outcome = deliver(
        source,
        [1],
        tmp_path / "partition-only-delivery",
        translator=translator,
        target_lang="Spanish",
    )

    assert outcome.status == "done"
    assert translator.calls == 0
    assert pdfops.page_count(result) == 1
    assert [page.page_number for page in outcome.pages] == [1]
    assert outcome.pages[0].page_flag is None
    assert outcome.pages[0].page_flag_severity is None
    assert outcome.pages[0].blocks == []
    assert outcome.pages[0].page_source_text_flag is None
