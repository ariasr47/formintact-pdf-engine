"""S3-engine-server test support: runs `pdft_engine.server.app` over a Unix socket in a
background thread, and a stdlib client that talks to it over `AF_UNIX` (SPEC §7: engine tests
never use httpx). Test-only -- never imported by production code."""
from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from dataclasses import dataclass

import uvicorn


class UnixHTTPConnection(http.client.HTTPConnection):
    """`http.client.HTTPConnection` over `AF_UNIX` instead of TCP. `host` is used only to
    build the `Host` header; the real destination is `socket_path`."""

    def __init__(self, socket_path: str, timeout: float = 60.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


@dataclass
class StreamedResponse:
    """A render's NDJSON response, read one line at a time (chunked transfer-encoding is
    decoded transparently by `http.client.HTTPResponse`, including on `.readline()`)."""

    _conn: UnixHTTPConnection
    _resp: http.client.HTTPResponse

    @property
    def status(self) -> int:
        return self._resp.status

    def readline(self) -> bytes:
        return self._resp.readline()

    def close(self) -> None:
        self._conn.close()


class ServerThread(threading.Thread):
    """Runs a `uvicorn.Server` for the app's whole lifetime (INTERFACE §1 dev/test form).
    Started off the main thread, so uvicorn installs no signal handlers (its own guard)."""

    def __init__(self, config: uvicorn.Config) -> None:
        super().__init__(daemon=True)
        self.server = uvicorn.Server(config)
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.server.run()
        except BaseException as exc:                       # noqa: BLE001 -- surfaced to the test
            self.error = exc

    def stop(self, timeout: float = 10.0) -> None:
        self.server.should_exit = True
        self.join(timeout=timeout)


@dataclass
class ServerHandle:
    thread: ServerThread
    socket_path: str

    def request(self, method: str, path: str, json_body: dict | None = None,
               timeout: float = 30.0) -> tuple[int, bytes]:
        conn = UnixHTTPConnection(self.socket_path, timeout=timeout)
        body = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        headers = {"Content-Type": "application/json"} if body is not None else {}
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        return resp.status, raw

    def stream(
        self, method: str, path: str, json_body: dict, timeout: float = 30.0
    ) -> StreamedResponse:
        conn = UnixHTTPConnection(self.socket_path, timeout=timeout)
        body = json.dumps(json_body).encode("utf-8")
        conn.request(method, path, body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        return StreamedResponse(conn, resp)

    def stop(self) -> None:
        self.thread.stop()


def start_server(app, socket_path: str, *, expect_healthy: bool = True,
                 deadline_s: float = 15.0) -> ServerHandle:
    config = uvicorn.Config(app, uds=socket_path, log_level="warning")
    thread = ServerThread(config)
    thread.start()
    if not expect_healthy:
        return ServerHandle(thread=thread, socket_path=socket_path)
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if thread.server.started:
            return ServerHandle(thread=thread, socket_path=socket_path)
        if not thread.is_alive():
            raise RuntimeError(f"engine server thread exited before startup: {thread.error!r}")
        time.sleep(0.02)
    raise TimeoutError("engine server never became healthy")
