"""Wire model behavioural tests (engine-boundary S1-wire-models PLAN "Tests first"). Each test
constructs a valid control first, then the one invalid shape the bullet names, so a validator that
accidentally rejects everything cannot pass these silently."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from pdft_engine_contract.coverage import CoverageEntry, PageCoverage, SourceRect, UnitCoverage
from pdft_engine_contract.events import BlockProgressEvent, StageEvent
from pdft_engine_contract.wire import (
    CallbackReply,
    CallbackRef,
    CountPreflight,
    CountRequest,
    CountResponse,
    PrepareResponse,
    PreparedRef,
    ProbeRequest,
    ProgressLine,
    RenderRequest,
    Telemetry,
    TelemetryPage,
    TerminalLine,
    WireItem,
    WireResult,
)

JOB_ID = "0" * 32
RENDER_ID = "1" * 32
PREP_HANDLE = "prep-" + "0" * 32
DIGEST = "0" * 64
TOKEN = "0" * 64


def _rect() -> SourceRect:
    return SourceRect(x0=0.0, y0=0.0, x1=1.0, y1=1.0)


def _unit(reason=None, reason_lead=None, reason_detail=None) -> UnitCoverage:
    return UnitCoverage(
        unit_id="u1", attempt=0, outcome="skipped" if reason else "fitted", reason=reason,
        reason_message=None, reason_lead=reason_lead, reason_detail=reason_detail,
        style_key="s1", source_size=10.0, origin_x=0.0, origin_y=0.0,
        source_rects=[_rect()], warnings=[],
    )


def _entry(reason=None, reason_lead=None, reason_detail=None, with_unit=False) -> CoverageEntry:
    units = [_unit(reason, reason_lead, reason_detail)] if with_unit else []
    return CoverageEntry(
        block_id="b1", outcome="skipped" if reason else "fitted", reason=reason,
        reason_message=None, reason_lead=reason_lead, reason_detail=reason_detail,
        warnings=[], masked_terms=[], units=units,
    )


def _page(entry: CoverageEntry, page_number: int = 1) -> PageCoverage:
    return PageCoverage(page_number=page_number, page_flag=None, page_flag_message=None, blocks=[entry])


def _terminal(pages, telemetry_pages) -> TerminalLine:
    return TerminalLine(
        type="terminal", status="done", cause=None, pages=pages,
        telemetry=Telemetry(route="old-engine", engine_version="dev", pages=telemetry_pages),
    )


@pytest.mark.parametrize(
    "pages", [[], [2, 1], [1, 1], [0, 1]],
    ids=["empty", "not_ascending", "duplicated", "contains_zero"],
)
def test_pages_list_rejects_empty_unordered_duplicated_or_zero(pages):
    CountRequest(job_id=JOB_ID, pages=[1, 2, 3])  # valid control
    with pytest.raises(ValidationError):
        CountRequest(job_id=JOB_ID, pages=pages)


def test_unknown_field_is_rejected():
    ProbeRequest(job_id=JOB_ID, max_pages=5)  # valid control
    with pytest.raises(ValidationError):
        ProbeRequest(job_id=JOB_ID, max_pages=5, extra_field=1)


def test_render_request_rejects_prepared_with_dnt_terms():
    RenderRequest(
        job_id=JOB_ID, render_id=RENDER_ID, pages=[1], target_lang="es", dnt_terms=[],
        route="old-engine", prepared=PreparedRef(handle=PREP_HANDLE, digest=DIGEST),
        callback=CallbackRef(token=TOKEN),
    )  # valid control
    with pytest.raises(ValidationError):
        RenderRequest(
            job_id=JOB_ID, render_id=RENDER_ID, pages=[1], target_lang="es", dnt_terms=["ACME"],
            route="old-engine", prepared=PreparedRef(handle=PREP_HANDLE, digest=DIGEST),
            callback=CallbackRef(token=TOKEN),
        )


def test_callback_reply_rejects_next_seq_not_seq_plus_one():
    CallbackReply(render_id=RENDER_ID, seq=0, next_seq=1, status="cancelled", cause=None, results=None)  # valid
    with pytest.raises(ValidationError):
        CallbackReply(render_id=RENDER_ID, seq=0, next_seq=2, status="cancelled", cause=None, results=None)


_VALID_REPLY_ROWS = [
    ("translated", None, [WireResult(id="a", translation="b")]),
    ("skipped", "provider_rate_limit", None),
    ("fatal", "provider_auth", None),
    ("refused", "attempt_refused", None),
    ("cancelled", None, None),
]
_INVALID_REPLY_CAUSES = {
    "translated": "provider_auth",
    "skipped": "provider_auth",
    "fatal": "attempt_refused",
    "refused": "provider_auth",
    "cancelled": "provider_auth",
}


@pytest.mark.parametrize("status,cause,results", _VALID_REPLY_ROWS, ids=[r[0] for r in _VALID_REPLY_ROWS])
def test_callback_reply_table_accepts_its_valid_shape(status, cause, results):
    CallbackReply(render_id=RENDER_ID, seq=0, next_seq=1, status=status, cause=cause, results=results)


@pytest.mark.parametrize("status,cause,results", _VALID_REPLY_ROWS, ids=[r[0] for r in _VALID_REPLY_ROWS])
def test_callback_reply_table_rejects_one_invalid_shape(status, cause, results):
    with pytest.raises(ValidationError):
        CallbackReply(render_id=RENDER_ID, seq=0, next_seq=1, status=status,
                       cause=_INVALID_REPLY_CAUSES[status], results=results)


def test_progress_line_rejects_nonzero_seq():
    ProgressLine(type="progress", event=StageEvent(type="stage", seq=0, stage="translate", page=None))  # valid
    with pytest.raises(ValidationError):
        ProgressLine(type="progress", event=StageEvent(type="stage", seq=1, stage="translate", page=None))


def test_progress_line_rejects_block_progress_with_page_finished():
    ProgressLine(
        type="progress",
        event=BlockProgressEvent(type="block_progress", seq=0, page=1, page_finished=False),
    )  # valid
    with pytest.raises(ValidationError):
        ProgressLine(
            type="progress",
            event=BlockProgressEvent(type="block_progress", seq=0, page=1, page_finished=True),
        )


def test_terminal_line_rejects_telemetry_pages_mismatch():
    pages = [_page(_entry())]
    _terminal(pages, [TelemetryPage(page=1, engine_ms=0)])  # valid control
    with pytest.raises(ValidationError):
        _terminal(pages, [TelemetryPage(page=2, engine_ms=0)])


@pytest.mark.parametrize(
    "reason", ["provider_rate_limit", "provider_error", "translation_protocol", "masking_incomplete"],
)
def test_terminal_line_rejects_provider_copy_on_the_four_reasons(reason):
    clean_pages = [_page(_entry(reason=reason, with_unit=True))]
    _terminal(clean_pages, [TelemetryPage(page=1, engine_ms=0)])  # valid: no copy filled by the engine
    dirty_pages = [_page(_entry(reason=reason, reason_lead="filled by the app", with_unit=True))]
    with pytest.raises(ValidationError):
        _terminal(dirty_pages, [TelemetryPage(page=1, engine_ms=0)])


# prompt-fit-and-bold AC 7: the optional per-item budget on the engine wire.
def test_wire_item_budget_accepts_its_range_and_rejects_everything_else():
    for budget in (1, 10000):
        assert WireItem(id="0", text="x", budget=budget).budget == budget  # valid controls
    for budget in (0, 10001, "5", True, 5.0):
        with pytest.raises(ValidationError):
            WireItem.model_validate({"id": "0", "text": "x", "budget": budget})
    with pytest.raises(ValidationError):
        WireItem.model_validate({"id": "0", "text": "x", "extra": 1})


def test_wire_item_without_a_budget_keeps_todays_bytes():
    assert WireItem(id="0", text="x").model_dump_json() == '{"id":"0","text":"x"}'
    assert WireItem.model_validate({"id": "0", "text": "x", "budget": None}).model_dump_json() == (
        '{"id":"0","text":"x"}')
    assert WireItem(id="0", text="x", budget=12).model_dump_json() == '{"id":"0","text":"x","budget":12}'


def test_prepare_response_round_trips_an_item_with_a_budget():
    body = {"prepared": PREP_HANDLE, "digest": DIGEST, "route": "japanese-library",
            "target_lang": "Spanish", "pages": [1],
            "requests": [{"request_index": 0, "items": [{"id": "0", "text": "a", "budget": 40},
                                                        {"id": "1", "text": "b"}]}]}
    parsed = PrepareResponse.model_validate(body)
    assert [i.budget for i in parsed.requests[0].items] == [40, None]
    assert PrepareResponse.model_validate_json(parsed.model_dump_json()) == parsed


COUNT = {"source_chars": 10, "text_block_count": 2, "kept_page_count": 1,
         "pages": [{"page": 1, "layout_units": 2, "extract_failed": False, "text_block_count": 2}]}
FOUND = {"signed": True, "certified": False, "dynamic_xfa": False, "letter_count": 8,
         "rtl_letter_count": 2}


def test_count_response_carries_an_optional_preflight():
    assert CountResponse.model_validate(COUNT).preflight is None  # old engine: missing
    parsed = CountResponse.model_validate({**COUNT, "preflight": FOUND})
    assert parsed.preflight == CountPreflight(**FOUND)
    assert CountResponse.model_validate_json(parsed.model_dump_json()) == parsed
    assert CountResponse.model_validate({**COUNT, "preflight": None}).preflight is None


def test_count_response_rejects_unknown_keys_and_rtl_above_letters():
    CountResponse.model_validate({**COUNT, "preflight": FOUND})  # control
    with pytest.raises(ValidationError):
        CountResponse.model_validate({**COUNT, "future": 1})
    with pytest.raises(ValidationError):
        CountResponse.model_validate({**COUNT, "preflight": {**FOUND, "future": 1}})
    with pytest.raises(ValidationError):
        CountResponse.model_validate(
            {**COUNT, "preflight": {**FOUND, "letter_count": 8, "rtl_letter_count": 9}})


def test_snapshot_lists_count_preflight_as_optional():
    snapshot = Path(__file__).resolve().parents[1] / "schema" / "engine-wire.schema.json"
    defs = json.loads(snapshot.read_text(encoding="utf-8"))["$defs"]
    assert "CountPreflight" in defs
    assert "preflight" not in defs["CountResponse"]["required"]
