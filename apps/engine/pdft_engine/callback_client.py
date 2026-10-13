"""engine-boundary INTERFACE §6.4: the engine's own callback client. Posts one translate
request per call, at most one re-post on a transport failure, sequential per render.
Engine-side only -- never imported by app modules (S1-client-cut, PLAN.md)."""
from __future__ import annotations

import http.client
import socket
from typing import Protocol

from pdft_engine_contract.items import TranslationItem, TranslationResult
from pdft_engine_contract.wire import CallbackReply, CallbackRequest, WireItem
from pydantic import ValidationError

from pdft_engine import settings as engine_settings
from pdft_engine.pipeline import CallbackFailure, TranslateReply


class CallbackTransportError(Exception):
    """Connection refused, a reset, or end of stream before a complete response (INTERFACE §6.4)."""


class CallbackTransport(Protocol):
    def post(self, body: bytes) -> tuple[int, bytes]: ...


class CallbackClient:
    def __init__(
        self, transport: CallbackTransport, *, render_id: str, job_id: str, token: str
    ) -> None:
        self._transport, self._render_id, self._job_id = transport, render_id, job_id
        self._next_seq = 0

    def translate(self, items: tuple[TranslationItem, ...], target_lang: str) -> TranslateReply:
        seq = self._next_seq
        body = (
            CallbackRequest(
                render_id=self._render_id,
                job_id=self._job_id,
                seq=seq,
                target_lang=target_lang,
                items=[WireItem(id=i.id, text=i.text, budget=i.budget) for i in items],
            )
            .model_dump_json()
            .encode()
        )
        for attempt in (0, 1):  # re-post the IDENTICAL body exactly once, transport failure only
            try:
                status, raw = self._transport.post(body)
                break
            except CallbackTransportError as exc:
                if attempt == 1:
                    raise CallbackFailure("callback_unreachable") from exc
        if status != 200:
            raise CallbackFailure("callback_refused")
        try:
            reply = CallbackReply.model_validate_json(raw)
        except ValidationError as exc:
            raise CallbackFailure("callback_refused") from exc
        if reply.render_id != self._render_id or reply.seq != seq:
            raise CallbackFailure("callback_refused")
        self._next_seq = reply.next_seq
        return TranslateReply(
            status=reply.status,
            cause=reply.cause,
            results=None
            if reply.results is None
            else tuple(
                TranslationResult(id=r.id, translation=r.translation) for r in reply.results
            ),
        )


# ---------------------------------------------------------------------------
# S3-engine-server (INTERFACE §1, §5.1, §6.4): the real transport. Added at the
# end of the file so `CallbackClient` above (already relied on in-process at S1)
# keeps its lines.
# ---------------------------------------------------------------------------


class _AddressedHTTPConnection(http.client.HTTPConnection):
    """Connects over `unix:<path>` or loopback TCP, per `settings.ParsedAddress`. `connect()`
    applies the 5 s CONNECT timeout only; the socket's timeout is cleared right after, so no
    read on this connection ever times out (INTERFACE §1: "a callback's reply: no read
    timeout")."""

    def __init__(self, addr: engine_settings.ParsedAddress, connect_timeout_s: float = 5.0) -> None:
        super().__init__("localhost")           # host is used only for the Host header
        self._addr = addr
        self._connect_timeout_s = connect_timeout_s

    def connect(self) -> None:
        if self._addr.kind == "unix":
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(self._connect_timeout_s)
            sock.connect(self._addr.path)
        else:
            sock = socket.create_connection(
                (self._addr.host, self._addr.port), timeout=self._connect_timeout_s
            )
        sock.settimeout(None)
        self.sock = sock


_TRANSPORT_ERRORS = (
    ConnectionError,
    http.client.RemoteDisconnected,
    http.client.IncompleteRead,
    OSError,
)


class HttpCallbackTransport:
    """`CallbackTransport` over the wire (INTERFACE §5.1, §6.4). Opens a NEW connection per
    `post()` -- no pooling, no reuse across calls -- and sends `POST /internal/engine/translate`
    with `Authorization: Bearer <token>`."""

    def __init__(self, address: str, *, token: str) -> None:
        self._addr = engine_settings.parse_address(address)
        self._token = token

    def post(self, body: bytes) -> tuple[int, bytes]:
        conn = _AddressedHTTPConnection(self._addr)
        try:
            conn.request("POST", "/internal/engine/translate", body=body, headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
            })
            resp = conn.getresponse()
            raw = resp.read()
            return resp.status, raw
        except _TRANSPORT_ERRORS as exc:
            raise CallbackTransportError(str(exc)) from exc
        finally:
            conn.close()
