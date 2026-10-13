"""Internal HTTP front for the fixed-action portal bridge (container deployment).

    python3 -m exchange.portal_server --root /var/lib/zwa/zwa4e-demo --port 8790

Listens only on the private service network. The website calls
POST /v1/portal with the same fixed JSON shape it would pipe to
`python3 -m exchange.portal`, plus a shared bearer token, so the website
container needs no access to the ledger, order evidence or any wallet home.
GET /healthz reports liveness; GET /readyz checks configuration, ledger and
chain identity. Nothing else is served.
"""

from __future__ import annotations

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sys

from .config import ConfigError, load as load_config
from .order_ledger import OrderError
from .portal import Portal, PortalError, handle

MAX_BODY = 8192


def _token() -> bytes:
    value = os.environ.get("ZWA_PORTAL_TOKEN", "")
    if len(value) < 32:
        raise SystemExit("ZWA_PORTAL_TOKEN must be set to at least 32 characters")
    return value.encode()


class Handler(BaseHTTPRequestHandler):
    token = b""
    server_version = "zwa-portal"
    sys_version = ""

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._send(200, {"status": "ok"})
        elif self.path == "/readyz":
            try:
                portal = Portal(load_config())
                active, height = portal.live()
                self._send(200 if active else 503, {"ready": active, "chainHeight": height,
                                                    "mode": portal.config.approval_mode})
            except Exception:
                self._send(503, {"ready": False})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/portal":
            self._send(404, {"error": "not found"})
            return
        supplied = self.headers.get("Authorization", "").removeprefix("Bearer ").encode()
        if not hmac.compare_digest(supplied, self.token):
            self._send(401, {"ok": False, "error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._send(413, {"ok": False, "error": "request is too large"})
            return
        try:
            result = handle(json.loads(self.rfile.read(length)))
            self._send(200, {"ok": True, "data": result})
        except (PortalError, OrderError, ConfigError) as exc:
            self._send(200, {"ok": False, "error": str(exc)[:200]})
        except Exception:
            self._send(200, {"ok": False, "error": "request could not be completed"})

    def log_message(self, format: str, *args) -> None:
        # Structured access log without bodies, cookies or tokens.
        print(json.dumps({"event": "PORTAL_REQUEST", "path": self.path, "status": args[1] if len(args) > 1 else None}),
              file=sys.stderr, flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    os.umask(0o077)
    os.environ["ZWA_PRIVATE_EXCHANGE_ROOT"] = args.root
    load_config(args.root)  # fail fast on a missing or invalid configuration
    Handler.token = _token()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
