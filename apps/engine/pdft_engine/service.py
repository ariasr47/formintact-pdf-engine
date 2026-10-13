"""The one engine façade both transports call (SPEC §2). Contract errors raise EngineRequestError;
anything else is the transport's 500 internal_error."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import json
import platform
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from pdft_engine_contract import naming, target_language
from pdft_engine_contract.wire import (
    ArtifactResponse,
    AssembleRequest,
    CountPage,
    CountRequest,
    CountResponse,
    DocumentArtifactLine,
    HealthResponse,
    PageArtifactLine,
    PreparedRequestWire,
    PrepareRequest,
    PrepareResponse,
    ProbePage,
    ProbeRejection,
    ProbeRequest,
    ProbeResponse,
    ProgressLine,
    RenderRequest,
    Telemetry,
    TelemetryPage,
    TerminalLine,
    ThumbnailRequest,
    WireItem,
)

from pdft_engine import callback_client, estimate_units, pdfops, pipeline, prepared_state
from pdft_engine import settings as engine_settings


class EngineRequestError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status, self.code = status, code


class StreamClosed(Exception):
    """Raised by `emit` once the app has closed the render stream (a write failed).
    `_StreamSink` swallows it during the run -- marking the render cancelled so the
    engine's own cancellation checkpoints wind the run down normally (INTERFACE §4:
    "a stream write fails" is one of the three engine-side cancellation triggers) --
    and `run_render` lets it end its own final terminal-line write silently: the app
    already knows the stream ended without one (its own consumption loop, §4 step 5)."""


def _job_dir(job_id: str) -> Path:
    path = engine_settings.job_dir(job_id)
    if not path.is_dir():
        raise EngineRequestError(404, "job_not_found")
    return path


def _input(path: Path) -> Path:
    if not path.is_file():
        raise EngineRequestError(404, "input_not_found")
    return path


def _pages_in_range(pdf: Path, pages) -> None:
    if max(pages) > pdfops.page_count(pdf):
        raise EngineRequestError(422, "page_out_of_range")


def health() -> HealthResponse:
    return HealthResponse(status="ok", engine_version=engine_settings.engine_version(),
                          pymupdf_version=importlib.metadata.version("pymupdf"),
                          pdf_translate_version=importlib.metadata.version("pdf-translate"),
                          python_version=platform.python_version())


def probe(req: ProbeRequest) -> ProbeResponse:
    original = _input(_job_dir(req.job_id) / naming.ORIGINAL)
    try:
        layers = pdfops.probe(original, req.max_pages)
    except pdfops.PdfValidationError as exc:
        return ProbeResponse(
            outcome="rejected",
            pages=None,
            rejection=ProbeRejection(cause=exc.cause, value=exc.value, limit=exc.limit),
        )
    return ProbeResponse(
        outcome="accepted",
        rejection=None,
        pages=[ProbePage(number=i + 1, has_text_layer=t) for i, t in enumerate(layers)],
    )


def thumbnail(req: ThumbnailRequest) -> ArtifactResponse:
    job_dir = _job_dir(req.job_id)
    original = _input(job_dir / naming.ORIGINAL)
    _pages_in_range(original, [req.page])
    if req.kind == "source":
        source, raster_page, name = original, req.page, naming.new_artifact_name("thumb", req.page)
    else:
        source = _input(job_dir / naming.page_name(req.page))
        raster_page, name = 1, naming.new_artifact_name("translated_preview", req.page)
    out = job_dir / name
    try:
        pdfops.render_thumbnail(source, raster_page, out)
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    return ArtifactResponse(artifact=name)


def count(req: CountRequest) -> CountResponse:
    original = _input(_job_dir(req.job_id) / naming.ORIGINAL)
    _pages_in_range(original, req.pages)
    totals, pages = estimate_units.count_pages(original, list(req.pages))
    return CountResponse(
        source_chars=totals[0],
        text_block_count=totals[1],
        kept_page_count=len(req.pages),
        pages=[
            CountPage(page=n, layout_units=u, extract_failed=f, text_block_count=t)
            for n, u, f, t in pages
        ],
    )


def prepare(req: PrepareRequest) -> PrepareResponse:
    """INTERFACE §3.5. Stages a fresh prep-<32 hex> handle under the job dir, prepares
    with zero provider/model I/O, and writes the on-disk codec. Any exception (bad
    input, extraction failure, a callee that raises) removes the half-built handle
    before propagating -- a caller never sees a partial `prep-*` directory."""
    if req.route != pipeline.NATIVE_ROUTE:
        raise EngineRequestError(422, "invalid_request")
    try:
        target_language.bcp47(req.target_lang)
    except target_language.UnrecognisedTargetLanguage:
        raise EngineRequestError(422, "invalid_request") from None
    job_dir = _job_dir(req.job_id)
    original = _input(job_dir / naming.ORIGINAL)
    root = job_dir / naming.new_prepared_handle()
    root.mkdir()
    try:
        state = pipeline.prepare_document_state(
            pdf_path=original, selected_pages=tuple(req.pages), target_language=req.target_lang,
            dnt_terms=list(req.dnt_terms), route=req.route, root=root,
        )
        digest = prepared_state.write(root, state, engine_version=engine_settings.engine_version())
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise
    return PrepareResponse(
        prepared=root.name, digest=digest, route=req.route, target_lang=req.target_lang,
        pages=list(req.pages),
        requests=[
            PreparedRequestWire(
                request_index=r.request_index,
                items=[WireItem(id=i.id, text=i.text, budget=i.budget) for i in r.items],
            )
            for r in state.requests
        ],
    )


def assemble(req: AssembleRequest) -> bytes:
    job_dir = _job_dir(req.job_id)
    if not any((job_dir / naming.page_name(n)).is_file() for n in req.pages):
        raise EngineRequestError(404, "input_not_found")
    return pdfops.assemble_result_pdf(job_dir / naming.ORIGINAL, job_dir, list(req.pages))


# ---------------------------------------------------------------------------
# S1-client-cut (INTERFACE §3.6, §4, §6.4): the in-flight render registry and the
# render itself. Added at the end of the file so no web-cited line above moves (GC-3).
# ---------------------------------------------------------------------------


@dataclass
class _RenderEntry:
    job_id: str
    cancel: threading.Event = field(default_factory=threading.Event)


_RENDERS: dict[str, _RenderEntry] = {}
_RENDERS_LOCK = threading.Lock()


@dataclass(frozen=True)
class RenderContext:
    """Everything `run_render` needs, resolved once by `check_render` (INTERFACE §3.6
    checks 2-6; check 1 is the caller's own model validation)."""

    job_id: str
    render_id: str
    job_dir: Path
    pages: list[int]
    target_lang: str
    dnt_terms: list[str]
    route: str
    token: str
    prepared: pipeline.PreparedDocumentState | None
    entry: _RenderEntry


def check_render(req: RenderRequest) -> RenderContext:
    """INTERFACE §3.6 checks 2-6, in order. A refusal after registration (check 6)
    deregisters before raising, so a rejected render never blocks a retry."""
    if req.route != pipeline.NATIVE_ROUTE:
        raise EngineRequestError(422, "invalid_request")
    try:
        target_language.bcp47(req.target_lang)
    except target_language.UnrecognisedTargetLanguage:
        raise EngineRequestError(422, "invalid_request") from None
    job_dir = _job_dir(req.job_id)                                       # check 2
    original = _input(job_dir / naming.ORIGINAL)                          # check 3
    _pages_in_range(original, req.pages)                                  # check 4
    entry = _RenderEntry(job_id=req.job_id)
    with _RENDERS_LOCK:
        if any(e.job_id == req.job_id for e in _RENDERS.values()):        # check 5
            raise EngineRequestError(409, "render_in_progress")
        _RENDERS[req.render_id] = entry
    try:
        prepared = None
        if req.prepared is not None:                                     # check 6
            try:
                prepared = prepared_state.verify_and_load(
                    job_dir, req.prepared.handle, req.prepared.digest,
                    route=req.route, target_lang=req.target_lang, pages=req.pages,
                )
            except prepared_state.PreparedStateMismatch:
                raise EngineRequestError(409, "prepared_state_mismatch") from None
    except BaseException:
        with _RENDERS_LOCK:
            _RENDERS.pop(req.render_id, None)
        raise
    return RenderContext(
        job_id=req.job_id, render_id=req.render_id, job_dir=job_dir, pages=list(req.pages),
        target_lang=req.target_lang, dnt_terms=list(req.dnt_terms), route=req.route,
        token=req.callback.token, prepared=prepared, entry=entry,
    )


class RenderHooks(Protocol):
    """Reserved for cross-process observability (S3+). No hook fires before then."""


@dataclass
class _StreamSink:
    """`pipeline.RenderSink` over the wire (INTERFACE §4, §6.4). `emit` writes one
    NDJSON line (a JSON string) and raises `StreamClosed` once the app has closed the
    stream; every per-event write swallows that and marks the render cancelled instead
    of raising into `pipeline.run_pipeline` (whose own blanket exception handler would
    otherwise absorb it as an unclassified failure)."""

    ctx: RenderContext
    emit: Callable[[str], None]
    client: callback_client.CallbackClient
    callback_seconds: float = 0.0
    _scratch: Path | None = field(default=None, repr=False)
    _document_artifact: str | None = field(default=None, repr=False)
    _document_sha256: str | None = field(default=None, repr=False)

    def _write(self, line) -> None:
        try:
            self.emit(line.model_dump_json())
        except StreamClosed:
            self.ctx.entry.cancel.set()

    def progress(self, event) -> None:
        self._write(ProgressLine(type="progress", event=event))

    def page_artifact(self, page_number: int, produced: Path) -> None:
        name = naming.new_artifact_name("page", page_number)
        produced.rename(self.ctx.job_dir / name)
        self._write(PageArtifactLine(type="page_artifact", page=page_number, artifact=name))

    def document_artifact(self, produced: Path) -> None:
        name = naming.new_artifact_name("document")
        committed = self.ctx.job_dir / name
        produced.rename(committed)
        self._document_artifact = name
        self._document_sha256 = hashlib.sha256(committed.read_bytes()).hexdigest()
        self._write(DocumentArtifactLine(type="document_artifact", artifact=name))

    def translate(self, items, target_lang: str):
        started = time.perf_counter()
        try:
            reply = self.client.translate(items, target_lang)
        finally:
            self.callback_seconds += time.perf_counter() - started
        if reply.status == "cancelled":
            self.ctx.entry.cancel.set()
        return reply

    def is_cancelled(self) -> bool:
        return self.ctx.entry.cancel.is_set()

    def scratch(self) -> Path:
        if self._scratch is None:
            self._scratch = engine_settings.make_scratch(self.ctx.job_dir, self.ctx.render_id)
        return self._scratch


def _canonical_json(value) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _native_proof_path(ctx: RenderContext) -> Path:
    return ctx.job_dir / f"native-proof-{ctx.render_id}"


@dataclass(frozen=True)
class _NativeProofInputs:
    segments: bytes
    selected_pages: bytes
    preparation: bytes
    source_sha256: str
    prepared_digest: str


def _capture_native_proof_inputs(
    ctx: RenderContext,
    state: pipeline.PreparedDocumentState,
    *,
    persist: bool,
) -> _NativeProofInputs:
    """Freeze exactly the native preparation that this render will consume."""
    if (
        state.route != ctx.route
        or state.target_language != ctx.target_lang
        or state.selected_pages != tuple(ctx.pages)
        or state.library.selected_original_pages != tuple(ctx.pages)
    ):
        raise ValueError("native proof preparation binding mismatch")

    prepared = state.library.prepared_document
    root = prepared.workdir.parent
    preparation = root / prepared_state.STATE_FILE
    if persist:
        if preparation.exists():
            raise FileExistsError(preparation)
        written_digest = prepared_state.write(
            root,
            state,
            engine_version=engine_settings.engine_version(),
        )
    else:
        written_digest = None
    preparation_bytes = preparation.read_bytes()
    prepared_digest = hashlib.sha256(preparation_bytes).hexdigest()
    if written_digest is not None and written_digest != prepared_digest:
        raise ValueError("prepared native state digest mismatch")

    if prepared.segments_path is None:
        raise ValueError("prepared native segment path is absent")
    segments = prepared.segments_path.read_bytes()
    if hashlib.sha256(segments).hexdigest() != prepared.segments_sha256:
        raise ValueError("prepared native segment hash mismatch")
    source_sha256 = hashlib.sha256(
        (ctx.job_dir / naming.ORIGINAL).read_bytes()
    ).hexdigest()
    if source_sha256 != state.source_sha256:
        raise ValueError("prepared native source hash mismatch")
    return _NativeProofInputs(
        segments=segments,
        selected_pages=_canonical_json(list(state.library.selected_original_pages)),
        preparation=preparation_bytes,
        source_sha256=source_sha256,
        prepared_digest=prepared_digest,
    )


def _publish_native_proof(
    ctx: RenderContext,
    outcome: pipeline.EngineOutcome,
    document_artifact: str | None,
    document_sha256: str | None,
    inputs: _NativeProofInputs,
) -> None:
    """Atomically retain the immutable native inputs and terminal core accounting."""
    proof = _native_proof_path(ctx)
    if proof.exists():
        raise FileExistsError(proof)

    if document_artifact is None:
        raise ValueError("committed native document artifact is absent")
    if (
        not isinstance(document_sha256, str)
        or len(document_sha256) != 64
        or any(character not in "0123456789abcdef" for character in document_sha256)
    ):
        raise ValueError("committed native document hash is absent")
    payloads = {
        "segments.json": inputs.segments,
        "core-outcomes.json": _canonical_json(
            [
                {
                    "core": core,
                    "native_kind": native_kind,
                    "outcome": terminal,
                    "ratio": ratio,
                }
                for core, terminal, ratio, native_kind in outcome.core_outcomes
            ]
        ),
        "selected-pages.json": inputs.selected_pages,
        "preparation.json": inputs.preparation,
    }
    manifest = _canonical_json(
        {
            "schema": 1,
            "job_id": ctx.job_id,
            "render_id": ctx.render_id,
            "source_sha256": inputs.source_sha256,
            "prepared_digest": inputs.prepared_digest,
            "output": {
                "artifact": document_artifact,
                "sha256": document_sha256,
            },
            "files": {
                name: hashlib.sha256(content).hexdigest()
                for name, content in sorted(payloads.items())
            }
        }
    )
    staging = ctx.job_dir / f"{proof.name}.tmp-{uuid4().hex}"
    try:
        staging.mkdir()
        for name, content in payloads.items():
            (staging / name).write_bytes(content)
        (staging / "manifest.json").write_bytes(manifest)
        staging.rename(proof)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def run_render(
    ctx: RenderContext,
    *,
    emit: Callable[[str], None],
    transport: callback_client.CallbackTransport,
    hooks: RenderHooks | None = None,
) -> None:
    """Drives `pipeline.run_pipeline` through a `_StreamSink` wired to `transport`
    (INTERFACE §4, §6.4), then emits the `TerminalLine` with telemetry. `finally` drops
    the sink's scratch and deregisters the render -- whatever the outcome."""
    client = callback_client.CallbackClient(
        transport, render_id=ctx.render_id, job_id=ctx.job_id, token=ctx.token,
    )
    sink = _StreamSink(ctx=ctx, emit=emit, client=client)
    proof = _native_proof_path(ctx)
    proof_inputs: _NativeProofInputs | None = None

    def observe_preparation(state: pipeline.PreparedDocumentState) -> None:
        nonlocal proof_inputs
        if proof_inputs is not None:
            raise ValueError("multiple native preparations observed")
        proof_inputs = _capture_native_proof_inputs(ctx, state, persist=True)

    try:
        if proof.exists():
            raise FileExistsError(proof)
        if ctx.prepared is not None:
            proof_inputs = _capture_native_proof_inputs(
                ctx,
                ctx.prepared,
                persist=False,
            )
        outcome = pipeline.run_pipeline(
            pdf_path=ctx.job_dir / naming.ORIGINAL,
            kept_pages=ctx.pages,
            target_lang=ctx.target_lang,
            dnt_terms=ctx.dnt_terms,
            route=ctx.route,
            sink=sink,
            prepared=ctx.prepared,
            preparation_observer=observe_preparation,
        )
    finally:
        if sink._scratch is not None:
            engine_settings.drop_scratch(sink._scratch)
        with _RENDERS_LOCK:
            _RENDERS.pop(ctx.render_id, None)
    if (
        outcome.status == "done"
        and not ctx.entry.cancel.is_set()
    ):
        if proof_inputs is None:
            raise ValueError("completed native render has no preparation proof")
        _publish_native_proof(
            ctx,
            outcome,
            sink._document_artifact,
            sink._document_sha256,
            proof_inputs,
        )
    telemetry = Telemetry(
        route=ctx.route, engine_version=engine_settings.engine_version(),
        pages=[TelemetryPage(page=n, engine_ms=ms) for n, ms in sorted(outcome.page_ms.items())],
    )
    terminal = TerminalLine(type="terminal", status=outcome.status, cause=outcome.cause,
                            pages=outcome.pages, telemetry=telemetry)
    # StreamClosed: the app already closed the stream (INTERFACE §4 step 5 is its own to take)
    with contextlib.suppress(StreamClosed):
        emit(terminal.model_dump_json())


def cancel(job_id: str) -> int:
    with _RENDERS_LOCK:
        matching = [entry for entry in _RENDERS.values() if entry.job_id == job_id]
    for entry in matching:
        entry.cancel.set()
    return len(matching)


def cancel_all() -> None:
    """Fence render callbacks before draining native work on engine shutdown."""
    with _RENDERS_LOCK:
        for entry in _RENDERS.values():
            entry.cancel.set()
