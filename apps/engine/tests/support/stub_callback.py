"""S3-engine-server test support: a stdlib translate-callback stub (INTERFACE §5). Answers
every `/internal/engine/translate` POST with a `translated` echo reply -- the fixed shape
`test_server.py` needs to drive a render to completion without any provider. Test-only."""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class EchoCallbackStub:
    requests: list[dict] = field(default_factory=list)
    _server: ThreadingHTTPServer | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def address(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> EchoCallbackStub:
        owner = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):    # never log bodies or noise the test run
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                req = json.loads(raw)
                with owner._lock:
                    owner.requests.append(req)
                results = [{"id": it["id"], "translation": it["text"]} for it in req["items"]]
                reply = {
                    "render_id": req["render_id"],
                    "seq": req["seq"],
                    "next_seq": req["seq"] + 1,
                    "status": "translated",
                    "cause": None,
                    "results": results,
                }
                body = json.dumps(reply).encode("utf-8")
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
