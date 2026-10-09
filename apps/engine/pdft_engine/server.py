"""engine-boundary INTERFACE §1-§4: uvicorn + FastAPI over service.py. Sync endpoints; the render
body is an ASYNC generator, so client disconnect closes it deterministically (it does not rely on
Starlette closing a sync generator; the named proof is test_server's disconnect case)."""
import logging
import queue
import shutil
import tempfile
import threading
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pdft_engine_contract import naming
from pdft_engine_contract.wire import (
    AssembleRequest,
    CountRequest,
    EngineError,
    EngineErrorEnvelope,
    PrepareRequest,
    ProbeRequest,
    RenderRequest,
    ThumbnailRequest,
)

from pdft_engine import callback_client, native_owner, service, settings

access = logging.getLogger("pdft_engine.access")
_END = object()


def _error(status: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=EngineErrorEnvelope(error=EngineError(code=code, detail=None)).model_dump(),
    )


def clear_spool() -> None:
    spool = settings.job_tmp_base() / naming.ENGINE_SPOOL
    if spool.is_dir():
        for entry in spool.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)


@asynccontextmanager
async def lifespan(app):
    native_owner.start()
    settings.parse_address(settings.engine_address())          # refuses non-127.0.0.1 http hosts
    spool = settings.job_tmp_base() / naming.ENGINE_SPOOL
    spool.mkdir(parents=True, exist_ok=True)
    clear_spool()
    tempfile.tempdir = str(spool)                              # INTERFACE §2 backstop
    try:
        yield
    finally:
        service.cancel_all()
        await anyio.to_thread.run_sync(native_owner.close)
        tempfile.tempdir = None


app = FastAPI(lifespan=lifespan, openapi_url=None, docs_url=None, redoc_url=None)


@app.middleware("http")
async def access_log(request: Request, call_next):
    # U3 (GATE Z, 2026-09-25, council/decisions.md; INTERFACE §2 amended note): the spool is
    # cleared at start-up ONLY, never per call. pdf-translate v54's library route leaves a
    # self-cleaning TemporaryDirectory under the spool while a render is in flight
    # (strip_text.invisible_text_pages); a per-call clear here would race and delete it out
    # from under a concurrent render. This matches the app's own start-up-only spool sweep
    # (apps/api/api/jobs/tempdirs.py:100, from store.py:737).
    response = await call_next(request)
    access.info("%s %s %s", request.method, request.url.path, response.status_code)
    return response


@app.exception_handler(RequestValidationError)
async def _invalid(request, exc):  # never echo request values (INTERFACE §2)
    return _error(422, "invalid_request")


@app.exception_handler(service.EngineRequestError)
async def _refused(request, exc):
    return _error(exc.status, exc.code)


@app.exception_handler(Exception)
async def _internal(request, exc):
    return _error(500, "internal_error")


@app.get("/healthz")
def healthz():
    return service.health()


@app.post("/v1/probe")
def probe(req: ProbeRequest):
    return service.probe(req)


@app.post("/v1/thumbnail")
def thumbnail(req: ThumbnailRequest):
    return service.thumbnail(req)


@app.post("/v1/count")
def count(req: CountRequest):
    return service.count(req)


@app.post("/v1/prepare")
def prepare(req: PrepareRequest):
    return service.prepare(req)


@app.post("/v1/assemble")
def assemble(req: AssembleRequest):
    return Response(content=service.assemble(req), media_type="application/pdf")


@app.delete("/v1/jobs/{job_id}")
def cancel(job_id: str):
    if not naming.JOB_ID_RE.fullmatch(job_id):
        return _error(422, "invalid_request")
    return {"job_id": job_id, "cancelled_renders": service.cancel(job_id)}


@app.post("/v1/render")
def render(req: RenderRequest):
    ctx = service.check_render(req)  # raises EngineRequestError before any byte
    lines: queue.Queue = queue.Queue(maxsize=256)
    closed = threading.Event()
    transport = callback_client.HttpCallbackTransport(
        settings.app_callback_address(), token=req.callback.token
    )

    def emit(line: str) -> None:
        while True:
            if closed.is_set():
                raise service.StreamClosed()
            try:
                lines.put(line, timeout=0.25)
                return
            except queue.Full:
                continue

    def work() -> None:
        try:
            service.run_render(ctx, emit=emit, transport=transport)
        finally:
            while not closed.is_set():
                try:
                    lines.put(_END, timeout=0.25)
                    break
                except queue.Full:
                    continue

    threading.Thread(target=work, name=f"render-{req.render_id[:8]}", daemon=True).start()

    def take():
        try:
            return lines.get(timeout=0.5)
        except queue.Empty:
            return None

    async def body():
        try:
            while True:
                item = await anyio.to_thread.run_sync(take)
                if item is None:
                    continue
                if item is _END:
                    return
                yield (item + "\n").encode("utf-8")
        finally:
            closed.set()
            ctx.entry.cancel.set()

    return StreamingResponse(body(), media_type="application/x-ndjson")
