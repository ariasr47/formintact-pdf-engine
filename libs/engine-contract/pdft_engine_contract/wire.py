"""Engine API + translate-callback wire models (engine-boundary INTERFACE §2-§8). AUTHORITATIVE:
both processes import these classes. Request models forbid unknown fields (§2)."""
from __future__ import annotations

import json
from typing import Annotated, Literal, Union

from pydantic import (AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter,
                      model_serializer, model_validator)
from pydantic.json_schema import models_json_schema

from pdft_engine_contract.coverage import PageCoverage
from pdft_engine_contract.events import BlockProgressEvent, StageEvent, WarningEvent
from pdft_engine_contract.naming import artifact_matches

JobId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
Hex64 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
PreparedHandle = Annotated[str, StringConstraints(pattern=r"^prep-[0-9a-f]{32}$")]
PageNumber = Annotated[int, Field(ge=1)]


def _ascending(pages: list[int]) -> list[int]:
    if not pages or any(b <= a for a, b in zip(pages, pages[1:])):
        raise ValueError("pages must be non-empty, strictly ascending and unique")
    return pages


Pages = Annotated[list[PageNumber], Field(min_length=1), AfterValidator(_ascending)]

Route = Literal["old-engine", "japanese-library"]
EngineErrorCode = Literal["invalid_request", "job_not_found", "input_not_found", "page_out_of_range",
                          "render_in_progress", "prepared_state_mismatch", "internal_error"]
ProbeOutcome = Literal["accepted", "rejected"]
ProbeRejectionCause = Literal["not_a_pdf", "encrypted", "zero_pages", "too_many_pages"]
ThumbnailKind = Literal["source", "translated_preview"]
TerminalStatus = Literal["done", "partial", "failed"]
TerminalCause = Literal["provider_auth", "attempt_refused", "callback_refused", "callback_unreachable"]
CallbackReplyStatus = Literal["translated", "skipped", "fatal", "refused", "cancelled"]
CallbackReplyCause = Literal["provider_rate_limit", "provider_error", "translation_protocol", "engine_error",
                             "provider_auth", "attempt_refused"]
CallbackErrorCode = Literal["unauthorized", "invalid_request", "callback_out_of_order",
                            "callback_replay_mismatch", "callback_limit", "callback_oversize", "internal_error"]
PROVIDER_COPY_REASONS = frozenset({"provider_rate_limit", "provider_error", "translation_protocol",
                                   "masking_incomplete"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(_Strict):
    status: Literal["ok"]
    engine_version: str
    pymupdf_version: str
    pdf_translate_version: str
    python_version: str


class ProbeRequest(_Strict):
    job_id: JobId
    max_pages: int = Field(ge=1)


class ProbePage(_Strict):
    number: PageNumber
    has_text_layer: bool


class ProbeRejection(_Strict):
    cause: ProbeRejectionCause
    value: int | None
    limit: int | None


class ProbeResponse(_Strict):
    outcome: ProbeOutcome
    pages: list[ProbePage] | None
    rejection: ProbeRejection | None

    @model_validator(mode="after")
    def _one_branch(self):
        if (self.outcome == "accepted") != (self.pages is not None and self.rejection is None):
            raise ValueError("accepted carries pages and no rejection; rejected the reverse")
        return self


class ThumbnailRequest(_Strict):
    job_id: JobId
    kind: ThumbnailKind
    page: PageNumber


class ArtifactResponse(_Strict):
    artifact: str


class CountRequest(_Strict):
    job_id: JobId
    pages: Pages


class CountPage(_Strict):
    page: PageNumber
    layout_units: int = Field(ge=0)
    extract_failed: bool
    # S4-count-wire (GATE Z ruling, amendment 2026-09-25): the number of non-empty text
    # blocks on this page, by the exact rule `count_billable_units` uses (`text.strip()`).
    # Lets the app rebuild `BillableUnits.per_page_block_counts` from the wire alone.
    text_block_count: int = Field(ge=0)


class CountResponse(_Strict):
    source_chars: int = Field(ge=0)
    text_block_count: int = Field(ge=0)
    kept_page_count: int = Field(ge=0)
    pages: list[CountPage]


class WireItem(_Strict):
    id: str
    text: str
    # prompt-fit-and-bold: the most characters the translation should hold. Optional,
    # absent == null, and never emitted as null, so a budget-free item keeps today's bytes.
    budget: int | None = Field(default=None, ge=1, le=10000, strict=True)

    @model_serializer(mode="wrap")
    def _omit_null_budget(self, handler):
        data = handler(self)
        if data.get("budget") is None:
            data.pop("budget", None)
        return data


class PrepareRequest(_Strict):
    job_id: JobId
    pages: Pages
    target_lang: str = Field(max_length=200)
    dnt_terms: list[str]
    route: Route


class PreparedRequestWire(_Strict):
    request_index: int = Field(ge=0)
    items: list[WireItem]


class PrepareResponse(_Strict):
    prepared: PreparedHandle
    digest: Hex64
    route: Route
    target_lang: str
    pages: list[PageNumber]
    requests: list[PreparedRequestWire]

    @model_validator(mode="after")
    def _indexed(self):
        if [r.request_index for r in self.requests] != list(range(len(self.requests))):
            raise ValueError("request_index must equal the request's position")
        return self


class PreparedRef(_Strict):
    handle: PreparedHandle
    digest: Hex64


class CallbackRef(_Strict):
    token: Hex64


class RenderRequest(_Strict):
    job_id: JobId
    render_id: JobId
    pages: Pages
    target_lang: str = Field(max_length=200)
    dnt_terms: list[str]
    route: Route
    prepared: PreparedRef | None
    callback: CallbackRef

    @model_validator(mode="after")
    def _hosted_has_no_terms(self):
        if self.prepared is not None and self.dnt_terms:
            raise ValueError("a prepared render carries dnt_terms == []")
        return self


class ProgressLine(_Strict):
    type: Literal["progress"]
    event: Annotated[Union[StageEvent, BlockProgressEvent, WarningEvent], Field(discriminator="type")]

    @model_validator(mode="after")
    def _engine_progress_only(self):
        if self.event.seq != 0:
            raise ValueError("engine progress carries seq 0; the app's store stamps seq")
        if isinstance(self.event, BlockProgressEvent) and self.event.page_finished:
            raise ValueError("page_finished is the app's to emit")
        return self


class PageArtifactLine(_Strict):
    type: Literal["page_artifact"]
    page: PageNumber
    artifact: str

    @model_validator(mode="after")
    def _name(self):
        if not artifact_matches("page", self.artifact, self.page):
            raise ValueError("artifact name outside the closed page pattern")
        return self


class DocumentArtifactLine(_Strict):
    type: Literal["document_artifact"]
    artifact: str

    @model_validator(mode="after")
    def _name(self):
        if not artifact_matches("document", self.artifact):
            raise ValueError("artifact name outside the closed document pattern")
        return self


class TelemetryPage(_Strict):
    page: PageNumber
    engine_ms: int = Field(ge=0)


class Telemetry(_Strict):
    route: Route
    engine_version: str
    pages: list[TelemetryPage]


class TerminalLine(_Strict):
    type: Literal["terminal"]
    status: TerminalStatus
    cause: TerminalCause | None
    pages: list[PageCoverage]
    telemetry: Telemetry

    @model_validator(mode="after")
    def _checks(self):
        if [t.page for t in self.telemetry.pages] != [p.page_number for p in self.pages]:
            raise ValueError("telemetry pages must equal pages[].page_number")
        for page in self.pages:
            for entry in page.blocks:
                for rec in (entry, *entry.units):
                    if rec.reason in PROVIDER_COPY_REASONS and (rec.reason_lead or rec.reason_detail):
                        raise ValueError("provider copy is rendered by the app, never the engine")
        return self


StreamLine = Annotated[Union[ProgressLine, PageArtifactLine, DocumentArtifactLine, TerminalLine],
                       Field(discriminator="type")]
STREAM_LINE = TypeAdapter(StreamLine)


class AssembleRequest(_Strict):
    job_id: JobId
    pages: Pages


class CancelResponse(_Strict):
    job_id: JobId
    cancelled_renders: int = Field(ge=0)


class EngineError(_Strict):
    code: EngineErrorCode
    detail: str | None


class EngineErrorEnvelope(_Strict):
    error: EngineError


class CallbackRequest(_Strict):
    render_id: JobId
    job_id: JobId
    seq: int = Field(ge=0)
    target_lang: str = Field(max_length=200)
    items: list[WireItem]


class WireResult(_Strict):
    id: str
    translation: str


_REPLY_TABLE = {
    "translated": ({None}, True),
    "skipped": ({"provider_rate_limit", "provider_error", "translation_protocol", "engine_error"}, False),
    "fatal": ({"provider_auth"}, False),
    "refused": ({"attempt_refused"}, False),
    "cancelled": ({None}, False),
}


class CallbackReply(_Strict):
    render_id: JobId
    seq: int = Field(ge=0)
    next_seq: int = Field(ge=1)
    status: CallbackReplyStatus
    cause: CallbackReplyCause | None
    results: list[WireResult] | None

    @model_validator(mode="after")
    def _table(self):
        causes, has_results = _REPLY_TABLE[self.status]
        if self.next_seq != self.seq + 1 or self.cause not in causes or (self.results is not None) != has_results:
            raise ValueError("reply violates INTERFACE §5.3's table or next_seq = seq + 1")
        return self


class CallbackError(_Strict):
    code: CallbackErrorCode
    detail: str | None


class CallbackErrorEnvelope(_Strict):
    error: CallbackError


WIRE_MODELS = (HealthResponse, ProbeRequest, ProbeResponse, ThumbnailRequest, ArtifactResponse, CountRequest,
               CountResponse, PrepareRequest, PrepareResponse, RenderRequest, ProgressLine, PageArtifactLine,
               DocumentArtifactLine, TerminalLine, AssembleRequest, EngineErrorEnvelope, CallbackRequest,
               CallbackReply, CallbackErrorEnvelope, CancelResponse)


def export_text(models=WIRE_MODELS) -> str:
    _, schema = models_json_schema([(m, "validation") for m in models], title="engine-boundary wire")
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def is_fresh(snapshot_text: str) -> bool:
    return snapshot_text == export_text()
