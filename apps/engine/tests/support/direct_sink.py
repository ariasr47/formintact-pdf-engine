"""Provider-free sink for tests that drive the sole native document pipeline.

**Engine-safe subset ONLY** -- see `evidence/S4-test-split-boundary-findings.md`'s
"practical per-test rule". `pdft_engine/pipeline.py` calls `sink.translate(...)`
**exactly once per batch** (pipeline.py:860); the retry-once-per-batch loop and the
batch-scoped cause classification a *real* delivery goes through live in the APP
(`api/jobs/translate_handler.py`'s `TranslateHandler.attempts`), never in the engine.
So this sink calls `translator.translate_batch(...)` exactly once -- no
`TranslateHandler`, no `BYOKAttemptController`, no `api` import at all (GC-10). A test
whose translator raises `ProviderError`/refuses, or that asserts `.call_count` /
retry counts / a provider-caused `reason`/`cause`/`reason_lead`/`failure_lead`, is
App-bucket and must NOT use this helper -- it is really testing `TranslateHandler`.

`ProviderError`/its skipped-vs-fatal mapping is the SAME class `tests.support.deliver`
already defines for the wire/callback layer (`_LocalResponder`); reused here rather
than duplicated, since both are the same test-only stand-in for
`api.jobs.translator.ProviderError` (INTERFACE §5.3's status table)."""
from __future__ import annotations

import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from pdft_engine import settings as engine_settings
from pdft_engine.pipeline import EngineOutcome, TranslateReply, run_pipeline
from tests.support.deliver import ProviderError, _route_for


@dataclass
class CompatSink:
    """Engine-side sink preserving the public test-helper calling convention.
    `scratch()` returns `None` -- `tempfile`'s own system-default location, exactly
    what these tests relied on before this task. `provider_display` is accepted for
    call-shape compatibility but unused (no app-side copy/routing reads it here).
    `emit_fn`/`finalize_document_fn` are optional (keyword-only, default `None`) --
    `run_pipeline_compat` below wires them through for a full-document delivery;
    The helper needs no app callback controller or provider credentials."""

    translator: object
    provider_display: str
    finalize_page_fn: Callable[[int, Path], bool]
    is_cancelled_fn: Callable[[], bool]
    route: str = "japanese-library"
    emit_fn: Callable | None = None
    finalize_document_fn: Callable[[Path], bool] | None = None
    callback_seconds: float = 0.0
    _next_seq: int = field(default=0, repr=False)

    def progress(self, event) -> None:
        if self.emit_fn is not None:
            self.emit_fn(event)

    def page_artifact(self, page_number: int, produced: Path) -> None:
        ok = self.finalize_page_fn(page_number, produced)
        if not ok:
            produced.unlink(missing_ok=True)

    def document_artifact(self, produced: Path) -> None:
        if self.finalize_document_fn is None:
            return
        ok = self.finalize_document_fn(produced)
        if not ok:
            produced.unlink(missing_ok=True)

    def translate(self, items, target_lang: str) -> TranslateReply:
        started = time.perf_counter()
        try:
            results = self.translator.translate_batch(items, target_lang=target_lang)
        except ProviderError as exc:
            self.callback_seconds += time.perf_counter() - started
            status = "fatal" if exc.cause == "provider_auth" else "skipped"
            return TranslateReply(status=status, cause=exc.cause, results=None)
        except Exception:
            # Mirrors the app's one native callback strategy: an unclassified
            # translator exception is one skipped batch, closed cause `engine_error`
            # (INTERFACE §5.3's table) -- never a hard render failure.
            self.callback_seconds += time.perf_counter() - started
            return TranslateReply(status="skipped", cause="engine_error", results=None)
        self.callback_seconds += time.perf_counter() - started
        return TranslateReply(status="translated", cause=None, results=results)

    def is_cancelled(self) -> bool:
        return self.is_cancelled_fn()

    def scratch(self) -> Path | None:
        return None


def _compat_job_dir() -> Path:
    """Scratch goes under `tmp` in `JOB_TMP_BASE`, mirroring `seam_run._compat_job_dir`:
    never `pdf_path.parent`, which is often a checked-in fixtures directory."""
    base = engine_settings.job_tmp_base() / "tmp"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=base, prefix="seam-"))


def run_pipeline_compat(
    *, pdf_path: Path, kept_pages: list[int], target_lang: str, dnt_terms: list[str],
    translator, provider: str,
    finalize_page: Callable[[int, Path], bool], is_cancelled: Callable[[], bool],
    t1_observer: Callable | None = None, widget_observer: Callable | None = None,
    emit: Callable | None = None, finalize_document: Callable[[Path], bool] | None = None,
    attempt_controller=None, prepared_hosted=None,
    job_id: str | None = None, model: str | None = None, api_key: str | None = None,
    route: str | None = None,
) -> EngineOutcome:
    """Drop-in replacement for `seam_run.run_pipeline_compat`'s ENGINE-SAFE call
    shape. `attempt_controller`/`job_id`/`model`/`api_key` are accepted for call-shape
    compatibility but MUST be `None` here (`AssertionError` otherwise) -- they only
    matter to `TranslateHandler`'s retry/cause machinery, which is APP-owned and this
    helper deliberately never builds (see module docstring); a caller that needs them
    is testing App behaviour and must use `fake_engine.py`/a real endpoint instead.
    `prepared_hosted` IS engine-safe (it is just `pipeline.PreparedDocumentState`
    wrapped -- see `test_prepared_state.py`'s own `_wrap`, which documents "only
    `.state` is read off this"): only `.state` is read, then forwarded as
    `run_pipeline`'s own `prepared=` kwarg.

    Builds a `CompatSink` (single translate call, no retry) and calls
    `pdft_engine.pipeline.run_pipeline(...)` directly, returning the RAW
    `EngineOutcome` unwrapped -- no `outcome_from_terminal` needed, `EngineOutcome`
    already carries every field an engine-safe test reads (`.status`, `.pages`,
    `.cause`, `.page_shares`, `.page_ms`). Route resolution reuses
    `tests.support.deliver._route_for` -- the same "app would resolve it this way"
    logic `deliver()` already relies on -- rather than a second copy."""
    assert attempt_controller is None and job_id is None and model is None and api_key is None, (
        "run_pipeline_compat (engine-safe) never builds a TranslateHandler/"
        "BYOKAttemptController -- a caller passing any of these is App-bucket, not "
        "Engine (see tests/support/direct_sink.py's module docstring)"
    )
    resolved_route = route if route is not None else _route_for(target_lang)
    prepared = prepared_hosted.state if prepared_hosted is not None else None
    job_dir = _compat_job_dir()
    try:
        sink = CompatSink(
            translator, provider, finalize_page, is_cancelled,
            emit_fn=emit, finalize_document_fn=finalize_document,
        )
        return run_pipeline(
            pdf_path=pdf_path, kept_pages=list(kept_pages), target_lang=target_lang,
            dnt_terms=dnt_terms, route=resolved_route, sink=sink, prepared=prepared,
            t1_observer=t1_observer, widget_observer=widget_observer,
        )
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
