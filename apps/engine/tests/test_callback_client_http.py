"""engine-boundary S3-engine-server: `HttpCallbackTransport` (INTERFACE §5.1, §6.4) driven
through the real `CallbackClient` retry loop, against a stdlib stub that can drop the
connection or answer non-200. Also exercises the transport's `unix:` address form directly,
since `test_server.py`'s own render tests only drive the `http://127.0.0.1:<port>` form."""
from __future__ import annotations

import json
import shutil
import socket
import socketserver
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from pdft_engine_contract.items import TranslationItem

from pdft_engine import callback_client
from pdft_engine.pipeline import CallbackFailure


class _Stub:
    """An HTTP stub: the first `closes` POSTs are dropped mid-connection (no bytes written),
    and afterwards it answers `fixed_status` (200 -> a `translated` echo reply)."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self._closes_remaining = 0
        self._fixed_status = 200
        self._server: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()

    @property
    def address(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def close_next(self, n: int) -> None:
        with self._lock:
            self._closes_remaining = n

    def answer_with_status(self, status: int) -> None:
        self._fixed_status = status

    def start(self) -> _Stub:
        owner = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                with owner._lock:
                    close_now = owner._closes_remaining > 0
                    if close_now:
                        owner._closes_remaining -= 1
                if close_now:
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                req = json.loads(raw)
                with owner._lock:
                    owner.requests.append(req)
                if owner._fixed_status != 200:
                    body = json.dumps({"error": {"code": "unauthorized", "detail": None}}).encode()
                    self.send_response(owner._fixed_status)
                else:
                    results = [{"id": it["id"], "translation": it["text"]} for it in req["items"]]
                    reply = {
                        "render_id": req["render_id"],
                        "seq": req["seq"],
                        "next_seq": req["seq"] + 1,
                        "status": "translated",
                        "cause": None,
                        "results": results,
                    }
                    body = json.dumps(reply).encode()
                    self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


@pytest.fixture
def stub():
    s = _Stub().start()
    yield s
    s.stop()


def _client(address: str, token: str = "f" * 64) -> callback_client.CallbackClient:
    transport = callback_client.HttpCallbackTransport(address, token=token)
    return callback_client.CallbackClient(
        transport, render_id="1" * 32, job_id="2" * 32, token=token
    )


def test_one_dropped_connection_then_an_identical_repost_succeeds(stub) -> None:
    stub.close_next(1)
    client = _client(stub.address)
    reply = client.translate((TranslationItem(id="u1", text="hola «aa»"),), "es")
    assert reply.status == "translated"
    assert reply.results[0].translation == "hola «aa»"
    # exactly one request actually reached the handler (the dropped one was never parsed);
    # it carries seq 0 -- the SAME body the dropped attempt would have sent.
    assert len(stub.requests) == 1
    assert stub.requests[0]["seq"] == 0


def test_two_dropped_connections_raise_callback_unreachable(stub) -> None:
    stub.close_next(2)
    client = _client(stub.address)
    with pytest.raises(CallbackFailure) as excinfo:
        client.translate((TranslationItem(id="u1", text="hola"),), "es")
    assert excinfo.value.terminal_cause == "callback_unreachable"
    assert stub.requests == []


def test_409_raises_callback_refused_with_no_repost(stub) -> None:
    stub.answer_with_status(409)
    client = _client(stub.address)
    with pytest.raises(CallbackFailure) as excinfo:
        client.translate((TranslationItem(id="u1", text="hola"),), "es")
    assert excinfo.value.terminal_cause == "callback_refused"
    assert len(stub.requests) == 1


# --------------------------------------------------------------------------- unix: address form


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix-domain sockets only (Linux/macOS)")
def test_unix_socket_address_form_round_trips() -> None:
    class _UnixThreadingServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    socket_dir = Path(
        tempfile.mkdtemp(dir="/tmp", prefix="pdft-")
    )  # GC-5: macOS's 104-byte UDS path limit
    sock_path = str(socket_dir / "cb.sock")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            req = json.loads(raw)
            results = [{"id": it["id"], "translation": it["text"]} for it in req["items"]]
            reply = {"render_id": req["render_id"], "seq": req["seq"], "next_seq": req["seq"] + 1,
                    "status": "translated", "cause": None, "results": results}
            body = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = _UnixThreadingServer(sock_path, H)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = _client(f"unix:{sock_path}")
        reply = client.translate((TranslationItem(id="u1", text="hola"),), "es")
        assert reply.status == "translated"
        assert reply.results[0].translation == "hola"
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(socket_dir, ignore_errors=True)
