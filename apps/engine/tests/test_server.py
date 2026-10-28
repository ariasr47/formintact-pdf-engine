"""engine-boundary S3-engine-server: the FastAPI server over a Unix socket (INTERFACE §1-§4).
Talks to the app through a stdlib `AF_UNIX` HTTP client (`tests/support/server_process.py`),
never httpx (SPEC §7: "no httpx" in the engine environment)."""
from __future__ import annotations

import json
import logging
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from pdft_engine_contract.naming import ENGINE_SPOOL
from pdft_engine_contract.wire import STREAM_LINE

from pdft_engine import server as engine_server
from pdft_engine import service as engine_service
from pdft_engine import settings as engine_settings
from tests.support.server_process import ServerThread, start_server
from tests.support.stub_callback import EchoCallbackStub

FIXTURES = Path(__file__).parent / "fixtures"


def _stage(job_tmp_base: Path, job_id: str, fixture: str) -> Path:
    d = job_tmp_base / f"job-{job_id}"
    d.mkdir(parents=True)
    shutil.copyfile(FIXTURES / fixture, d / "original.pdf")
    return d


def _socket_dir() -> Path:
    return Path(tempfile.mkdtemp(dir="/tmp", prefix="pdft-"))


@pytest.fixture
def callback_stub():
    stub = EchoCallbackStub().start()
    yield stub
    stub.stop()


@pytest.fixture
def engine(isolated_job_env: Path, callback_stub, monkeypatch):
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("the engine server speaks HTTP over a Unix-domain socket (runs on Linux/macOS)")
    socket_dir = _socket_dir()
    sock_path = str(socket_dir / "e.sock")
    monkeypatch.setenv("ENGINE_ADDRESS", f"unix:{sock_path}")
    monkeypatch.setenv("APP_CALLBACK_ADDRESS", callback_stub.address)
    handle = start_server(engine_server.app, sock_path)
    yield handle, isolated_job_env
    handle.stop()
    shutil.rmtree(socket_dir, ignore_errors=True)


def _render_body(job_id: str, render_id: str, token: str, *, pages=(1,),
                 target_lang="Spanish", route="japanese-library") -> dict:
    return {
        "job_id": job_id, "render_id": render_id, "pages": list(pages), "target_lang": target_lang,
        "dnt_terms": [], "route": route, "prepared": None, "callback": {"token": token},
    }


# --------------------------------------------------------------------------- /healthz


def test_healthz_shape_and_versions(engine) -> None:
    handle, _base = engine
    status, raw = handle.request("GET", "/healthz")
    assert status == 200
    body = json.loads(raw)
    assert body["status"] == "ok"
    assert body["pymupdf_version"] == "1.28.0"
    assert body["pdf_translate_version"] == "89.0.13"
    assert set(body) == {
        "status",
        "engine_version",
        "pymupdf_version",
        "pdf_translate_version",
        "python_version",
    }


# --------------------------------------------------------------------------- 200/404/422 envelopes


def test_probe_200_then_404_then_422(engine) -> None:
    handle, base = engine
    jid = "11" * 16
    _stage(base, jid, "simple_ruled.pdf")

    status, raw = handle.request("POST", "/v1/probe", {"job_id": jid, "max_pages": 200})
    assert status == 200
    assert json.loads(raw)["outcome"] == "accepted"

    status, raw = handle.request("POST", "/v1/probe", {"job_id": "22" * 16, "max_pages": 200})
    assert status == 404
    assert json.loads(raw) == {"error": {"code": "job_not_found", "detail": None}}

    status, raw = handle.request("POST", "/v1/probe", {"job_id": "not-a-job-id", "max_pages": 200})
    assert status == 422
    assert json.loads(raw) == {"error": {"code": "invalid_request", "detail": None}}


def test_422_invalid_request_never_echoes_a_planted_dnt_term(engine) -> None:
    handle, _base = engine
    planted = "PLANTED-DO-NOT-TRANSLATE-TERM"
    status, raw = handle.request("POST", "/v1/prepare", {
        "job_id": "33" * 16, "pages": [], "target_lang": "Spanish",  # empty pages -> 422
        "dnt_terms": [planted], "route": "japanese-library",
    })
    assert status == 422
    assert json.loads(raw) == {"error": {"code": "invalid_request", "detail": None}}
    assert planted.encode() not in raw


def test_service_level_422_never_echoes_a_planted_callback_token(engine) -> None:
    handle, base = engine
    jid = "44" * 16
    _stage(base, jid, "simple_ruled.pdf")
    planted_token = "a" * 64
    status, raw = handle.request("POST", "/v1/render",
                                 _render_body(jid, "40" * 16, planted_token, pages=(999,)))
    assert status == 422
    body = json.loads(raw)
    assert body["error"]["code"] == "page_out_of_range"
    assert planted_token.encode() not in raw


# ------------------------------------------------------------ /v1/render pre-stream checks


def test_render_checks_run_in_interface_order(engine) -> None:
    """INTERFACE §3.6: page-out-of-range (check 4) is answered before render_in_progress
    (check 5), even when a render is already streaming for the same job."""
    handle, base = engine
    jid = "55" * 16
    _stage(base, jid, "simple_ruled.pdf")

    first = handle.stream("POST", "/v1/render", _render_body(jid, "50" * 16, "b" * 64))
    assert first.status == 200
    try:
        status, raw = handle.request("POST", "/v1/render",
                                     _render_body(jid, "51" * 16, "c" * 64, pages=(999,)))
        assert status == 422
        assert json.loads(raw)["error"]["code"] == "page_out_of_range"
    finally:
        while first.readline():
            pass
        first.close()


def test_render_in_progress_for_a_second_render_of_the_same_job(engine) -> None:
    handle, base = engine
    jid = "66" * 16
    _stage(base, jid, "simple_ruled.pdf")

    first = handle.stream("POST", "/v1/render", _render_body(jid, "60" * 16, "d" * 64))
    assert first.status == 200
    try:
        status, raw = handle.request("POST", "/v1/render", _render_body(jid, "61" * 16, "e" * 64))
        assert status == 409
        assert json.loads(raw) == {"error": {"code": "render_in_progress", "detail": None}}
    finally:
        while first.readline():
            pass
        first.close()


# ------------------------------------------------------------ render stream + terminal


def test_render_streams_ndjson_ending_in_one_valid_terminal_line(engine) -> None:
    handle, base = engine
    jid = "77" * 16
    _stage(base, jid, "simple_ruled.pdf")

    stream = handle.stream("POST", "/v1/render", _render_body(jid, "70" * 16, "f" * 64))
    assert stream.status == 200
    lines = []
    while True:
        raw_line = stream.readline()
        if not raw_line:
            break
        lines.append(raw_line.rstrip(b"\n"))
    stream.close()

    assert lines
    parsed = [STREAM_LINE.validate_json(line) for line in lines]
    terminals = [p for p in parsed if p.type == "terminal"]
    assert len(terminals) == 1
    assert parsed[-1].type == "terminal"  # exactly one terminal event, and it is the last line
    assert terminals[0].status == "done"
    assert terminals[0].telemetry.route == "japanese-library"


# ------------------------------------------------------------ client disconnect (named proof)


def test_client_disconnect_ends_the_render_within_5s(engine) -> None:
    handle, base = engine
    jid = "88" * 16
    _stage(base, jid, "simple_ruled.pdf")
    render_id = "80" * 16

    stream = handle.stream("POST", "/v1/render", _render_body(jid, render_id, "1" * 64))
    assert stream.status == 200
    first = stream.readline()
    assert first
    stream.close()                                     # abrupt disconnect mid-stream

    def render_gone() -> bool:
        registry_empty = render_id not in engine_service._RENDERS
        thread_gone = not any(t.name == f"render-{render_id[:8]}" for t in threading.enumerate())
        return registry_empty and thread_gone

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if render_gone():
            return
        time.sleep(0.05)
    pytest.fail("render registry entry / render thread outlived the client disconnect by 5s")


# --------------------------------------------------------------------------- DELETE /v1/jobs/{id}


def test_delete_job_cancels_in_flight_render_then_zero_for_unknown_job(engine) -> None:
    handle, base = engine
    jid = "99" * 16
    _stage(base, jid, "simple_ruled.pdf")

    stream = handle.stream("POST", "/v1/render", _render_body(jid, "90" * 16, "2" * 64))
    assert stream.status == 200
    try:
        status, raw = handle.request("DELETE", f"/v1/jobs/{jid}")
        assert status == 200
        assert json.loads(raw) == {"job_id": jid, "cancelled_renders": 1}

        unknown = "0" * 32
        status, raw = handle.request("DELETE", f"/v1/jobs/{unknown}")
        assert status == 200
        assert json.loads(raw) == {"job_id": unknown, "cancelled_renders": 0}
    finally:
        while stream.readline():
            pass
        stream.close()


def test_delete_job_malformed_id_is_422(engine) -> None:
    handle, _base = engine
    status, raw = handle.request("DELETE", "/v1/jobs/not-a-valid-id")
    assert status == 422
    assert json.loads(raw) == {"error": {"code": "invalid_request", "detail": None}}


# --------------------------------------------------------------------------- spool


def test_spool_cleared_at_startup_a_planted_file_survives_a_non_healthz_call(engine) -> None:
    """U3 (GATE Z, 2026-09-25): the spool is cleared at start-up ONLY, never per call -- a
    per-call clear would race a concurrent render's self-cleaning library-route temp dir
    (council/decisions.md; INTERFACE §2 amended note). A file planted after start-up must
    survive both an exempt /healthz call AND an ordinary call."""
    handle, base = engine
    spool = base / ENGINE_SPOOL
    assert spool.is_dir()
    assert list(spool.iterdir()) == []                      # cleared at start-up

    (spool / "leftover.txt").write_text("x")
    handle.request("GET", "/healthz")
    assert (spool / "leftover.txt").exists()

    handle.request("POST", "/v1/probe", {"job_id": "0" * 32, "max_pages": 1})
    assert (spool / "leftover.txt").exists()                # no per-call clear


# --------------------------------------------------------------------------- access log


def test_access_log_lines_are_method_path_status_only(engine, caplog) -> None:
    handle, base = engine
    jid = "aa" * 16
    _stage(base, jid, "simple_ruled.pdf")
    caplog.set_level(logging.INFO, logger="pdft_engine.access")

    status, _raw = handle.request("POST", "/v1/count", {"job_id": jid, "pages": [1]})
    assert status == 200

    access_records = [r for r in caplog.records if r.name == "pdft_engine.access"]
    assert access_records
    assert access_records[-1].getMessage() == "POST /v1/count 200"


# ------------------------------------------------------------ addressing (startup refusal)


def test_parse_address_rejects_a_non_loopback_http_host() -> None:
    with pytest.raises(ValueError):
        engine_settings.parse_address("http://10.0.0.1:9")


def test_app_refuses_to_start_with_an_invalid_engine_address(
    isolated_job_env, callback_stub, monkeypatch
) -> None:
    monkeypatch.setenv("ENGINE_ADDRESS", "http://10.0.0.1:9")
    monkeypatch.setenv("APP_CALLBACK_ADDRESS", callback_stub.address)
    socket_dir = _socket_dir()
    sock_path = str(socket_dir / "e.sock")
    try:
        thread = ServerThread(uvicorn.Config(engine_server.app, uds=sock_path, log_level="warning"))
        thread.start()
        thread.join(timeout=10.0)
        assert not thread.is_alive()
        assert thread.server.started is False
        # uvicorn turns a lifespan startup exception into `SystemExit` (its own failure-to-boot
        # signal) rather than re-raising it; the raw `ValueError` is asserted at the settings
        # layer by `test_parse_address_rejects_a_non_loopback_http_host` above, and confirmed
        # here in stderr's own traceback (captured by pytest, not re-asserted structurally).
        assert isinstance(thread.error, SystemExit)
        assert thread.error.code != 0
    finally:
        shutil.rmtree(socket_dir, ignore_errors=True)
