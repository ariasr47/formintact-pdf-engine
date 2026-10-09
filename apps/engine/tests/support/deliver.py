"""engine-boundary S3-reproof-outputs: the engine-environment counterpart of
`apps/api/tests/support/acceptance_run.deliver`. Same call shape (`deliver(pdf, pages,
out_dir, *, translator=None, target_lang="ja", widget_observer=None,
respect_ambient_engine_flag=False, dnt_terms=())`), but drives a REAL engine render end
to end through `pdft_engine.service`'s public facade (`check_render` + `run_render`),
never through `api`'s in-process compat plumbing -- so a delivered-artifact test proved
here is proved against the actual engine-environment code path (AC 8's "also passes in
the engine environment", AC 9, AC 10), not a stand-in.

The callback is answered by an in-process `_LocalResponder` standing in for the app's
real callback listener: for each `CallbackRequest` it calls
`translator.translate_batch(items, target_lang=...)` once, exactly as the app's
`TranslateHandler` would; a raised `ProviderError` maps onto a `skipped` or `fatal`
reply per INTERFACE §5.3's table, and every other outcome is `translated`. For the
deterministic translators this suite uses, that equals the app path (S1-exit's own
parity proof covers the *app* side of that equivalence; this file only needs the
*engine* side to answer the same way a real callback would)."""
from __future__ import annotations

import json
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pdft_engine_contract import naming
from pdft_engine_contract.items import TranslationItem
from pdft_engine_contract.wire import (
    CallbackRef,
    CallbackReply,
    CallbackRequest,
    RenderRequest,
    TerminalLine,
    WireResult,
)

from pdft_engine import pdfops
from pdft_engine import service as engine_service
from pdft_engine import settings as engine_settings
from tests.support.translators import SpacelessCJKTranslator


class ProviderError(Exception):
    """Test-only counterpart of `api.jobs.translator.ProviderError` (never imported
    here -- `apps/engine/tests` cannot see `api`, GC-10). None of this suite's
    translators raise it today (they are all deterministic pure functions), but the
    local responder below honours it exactly as the real callback listener honours a
    real `ProviderError`, so a future translator that DOES raise it gets the same
    skipped/fatal mapping (INTERFACE §5.3's table) without reaching across the lane
    boundary."""

    def __init__(self, cause: str) -> None:
        super().__init__(cause)
        self.cause = cause


def _route_for(target_lang: str) -> str:
    """The compatibility token for the sole native route.

    Unsupported target input is refused by the engine before work; it is never
    converted into the retired token by test support.
    """
    del target_lang
    return "japanese-library"


@dataclass
class _LocalResponder:
    """A `CallbackTransport` (`pdft_engine.callback_client.CallbackTransport`'s
    Protocol: `.post(body: bytes) -> tuple[int, bytes]`) that answers in-process,
    never over a socket."""

    translator: object

    def post(self, body: bytes) -> tuple[int, bytes]:
        req = CallbackRequest.model_validate_json(body)
        items = [TranslationItem(id=it.id, text=it.text) for it in req.items]
        try:
            results = self.translator.translate_batch(items, target_lang=req.target_lang)
        except ProviderError as exc:
            status = "fatal" if exc.cause == "provider_auth" else "skipped"
            reply = CallbackReply(render_id=req.render_id, seq=req.seq, next_seq=req.seq + 1,
                                  status=status, cause=exc.cause, results=None)
        except Exception:
            # Mirrors the app's one native callback strategy:
            # an unclassified translator exception is never a hard render failure, it is one
            # skipped batch with the closed cause `engine_error` (INTERFACE §5.3's table).
            reply = CallbackReply(render_id=req.render_id, seq=req.seq, next_seq=req.seq + 1,
                                  status="skipped", cause="engine_error", results=None)
        else:
            reply = CallbackReply(
                render_id=req.render_id, seq=req.seq, next_seq=req.seq + 1,
                status="translated", cause=None,
                results=[WireResult(id=r.id, translation=r.translation) for r in results],
            )
        return 200, reply.model_dump_json().encode("utf-8")


def deliver(
    pdf: Path, pages: list[int], out_dir: Path, *, translator=None, target_lang: str = "ja",
    widget_observer=None, respect_ambient_engine_flag: bool = False,
    dnt_terms: list[str] = (),
) -> tuple[Path, TerminalLine]:
    """Stages `JOB_TMP_BASE/job-<id>/original.pdf`, resolves the route exactly as the
    app would, then drives `pdft_engine.service.check_render` + `run_render` with a
    `_LocalResponder`. Every `page_artifact`/`document_artifact` line is finalized by
    renaming the engine's own artifact out of the job dir into `out_dir`, mirroring
    `acceptance_run.deliver`'s `finalize`/`finalize_document`. Returns
    `(result_path, terminal_line)`; `terminal_line.pages` is a `list[PageCoverage]`,
    the same contract type `tests.measure.report_from_outcome` already reads off
    `api.jobs.outcome.PipelineOutcome.pages`, so it is a drop-in `outcome` there too.

    `dnt_terms` is not a parameter of `acceptance_run.deliver` (which always passes
    `[]`): added here because `test_golden.py`'s invoice delivery needs a DNT term and
    previously bypassed `deliver()` entirely to get one. Every existing call shape
    (positional pdf/pages/out_dir, keyword-only translator/target_lang) is unaffected.
    """
    if widget_observer is not None:
        raise NotImplementedError(
            "widget_observer has no hook over the wire (INTERFACE §4): "
            "pdft_engine.service.run_render never forwards one to pipeline.run_pipeline"
        )
    out_dir.mkdir(parents=True, exist_ok=True)

    del respect_ambient_engine_flag
    route = _route_for(target_lang)
    try:
        job_id = uuid4().hex
        render_id = uuid4().hex
        job_dir = engine_settings.job_dir(job_id)
        job_dir.mkdir(parents=True)
        shutil.copyfile(pdf, job_dir / naming.ORIGINAL)

        req = RenderRequest(
            job_id=job_id, render_id=render_id, pages=list(pages), target_lang=target_lang,
            dnt_terms=list(dnt_terms), route=route, prepared=None,
            callback=CallbackRef(token=secrets.token_hex(32)),
        )
        ctx = engine_service.check_render(req)

        lines: list[str] = []

        def emit(line: str) -> None:
            lines.append(line)
            payload = json.loads(line)
            if payload["type"] == "page_artifact":
                (job_dir / payload["artifact"]).rename(out_dir / f"page-{payload['page']}.pdf")
            elif payload["type"] == "document_artifact":
                (job_dir / payload["artifact"]).rename(out_dir / "document.pdf")

        transport = _LocalResponder(translator or SpacelessCJKTranslator())
        engine_service.run_render(ctx, emit=emit, transport=transport)

        assert lines, "run_render emitted no lines -- no terminal line to read the outcome from"
        terminal_payload = json.loads(lines[-1])
        assert terminal_payload["type"] == "terminal", (
            f"expected the terminal line last, got {terminal_payload.get('type')!r}"
        )
        outcome = TerminalLine.model_validate(terminal_payload)
    finally:
        pass

    # S2 (DESIGN-2026-09-17 whole-document-delivery §3): the library route finalizes a
    # whole document, never per-page pages -- prefer `document.pdf` by presence, the
    # same rule `acceptance_run.deliver` and the store both use.
    document = out_dir / "document.pdf"
    if document.is_file():
        result = document
    else:
        result = out_dir / "result.pdf"
        result.write_bytes(pdfops.assemble_result_pdf(pdf, out_dir, pages))
    return result, outcome


def render_delivered(result_pdf: Path, pages: list[int], out_dir: Path) -> dict[int, Path]:
    """Verbatim copy of `acceptance_run.render_delivered` -- `pdfops.render_for_golden_diff`
    is engine-native, so this needed no rewrite beyond the import path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rendered = {}
    for n in pages:
        png = out_dir / f"delivered-{n}.png"
        pdfops.render_for_golden_diff(result_pdf, n, png)
        rendered[n] = png
    return rendered
