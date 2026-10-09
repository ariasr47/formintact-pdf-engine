#!/usr/bin/env python3
"""Engine-only health check for publish/engine.Dockerfile. Run inside the container:
    docker exec <name> python3 /app/publish/engine_health_smoke.py
GET /healthz over the engine's Unix socket must answer 200 with status "ok" within 60 s. stdlib only."""
import http.client
import json
import socket
import sys
import time

SOCKET = "/tmp/pdft-sock/engine.sock"
DEADLINE_S = 60.0


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout):
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._path)
        self.sock = sock


def main() -> int:
    deadline, last = time.monotonic() + DEADLINE_S, "no answer yet"
    while time.monotonic() < deadline:
        try:
            conn = _UnixHTTPConnection(SOCKET, timeout=2.0)
            conn.request("GET", "/healthz")
            response = conn.getresponse()
            body = response.read()
            conn.close()
            if response.status == 200 and json.loads(body).get("status") == "ok":
                print("engine healthy: GET /healthz 200, status ok")
                return 0
            last = f"HTTP {response.status}"
        except (OSError, ValueError) as exc:
            last = type(exc).__name__
        time.sleep(0.5)
    print(f"engine not healthy within 60 s ({last})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
