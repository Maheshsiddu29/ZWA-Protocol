#!/usr/bin/env python3
"""A TEST-DOUBLE exchange backend for driving the website in a browser. NOT A REAL CHAIN.

    python3 tests/acceptance/web_test_double_exchange.py [--pace 0.4]

Runs, in one process and a disposable /private/tmp/zwa4e-test-* root, the real exchange.portal_server
handler, exchange.portal, the schema-4 ledger, exchange.order_service.OrderService and
exchange.demo_signer.DemoSigner, wired exactly as benchmark/test_integration.py wires them. The pinned
tx-tool, Groth16 verifier, coordinator and Zebra are replaced by the existing test doubles (DemoTools and
a JSON-RPC stub), so every order runs the real state machine and evidence checks but **no transaction,
proof or block is real**. Use it to test the website's flow and failure handling, never as settlement
evidence.

It prints one JSON line with the portal URL, a fresh portal token and a control URL, then serves until
interrupted. Fault injection (loopback only):

    POST {control}/fault {"loseBuyAnswers": 2}   next 2 buys commit, then answer the generic internal error
    POST {control}/fault {"loseConfirmAnswers": 1} next confirmation commits, then answers the generic error
    POST {control}/fault {"portalDown": 3}        next 3 `buy` calls get HTTP 502 (nothing applied); reads work
    POST {control}/fault {"uncertain": true}      the next order's coordinator logs an execution, then stops
    POST {control}/fault {"dealerPause": true}    the dealer pauses after the buyer signs (order fails)
    GET  {control}/state                          ledger orders (id, state) for assertions
"""

from __future__ import annotations

import argparse
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
import threading
import time

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY))

from benchmark.test_integration import Integration  # noqa: E402
from exchange import portal as portal_module, portal_server  # noqa: E402


class Faults:
    def __init__(self):
        self.lock = threading.Lock()
        self.lose_buy_answers = 0
        self.lose_confirm_answers = 0
        self.portal_down = 0
        self.uncertain = False
        self.dealer_pause = False

    def take(self, name: str) -> bool:
        with self.lock:
            value = getattr(self, name)
            if not value:
                return False
            setattr(self, name, value - 1 if type(value) is int and value is not True else False)
            return True


class Backend(Integration):
    def runTest(self):  # noqa: N802 - unittest requires a test method to construct the case
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pace", type=float, default=0.4, help="seconds between exchange loop steps")
    args = parser.parse_args()
    faults = Faults()

    # A committed purchase whose answer is lost: the portal's generic internal error after the commit.
    real_buy = portal_module.Portal.buy

    def buy(self, *a, **kw):
        result = real_buy(self, *a, **kw)
        if faults.take("lose_buy_answers"):
            raise RuntimeError("injected: answer lost after commit")
        return result
    portal_module.Portal.buy = buy
    real_confirm = portal_module.Portal.confirm

    def confirm(self, *a, **kw):
        result = real_confirm(self, *a, **kw)
        if faults.take("lose_confirm_answers"):
            raise RuntimeError("injected: answer lost after commit")
        return result
    portal_module.Portal.confirm = confirm

    real_post = portal_server.Handler.do_POST

    def do_post(handler):
        body = handler.rfile.read(int(handler.headers.get("Content-Length") or 0))
        try:
            action = json.loads(body).get("action")
        except ValueError:
            action = None
        if action == "buy" and faults.take("portal_down"):
            handler._send(502, {"error": "injected outage"})  # never reaches the portal
            return
        handler.rfile = io.BytesIO(body)
        real_post(handler)
    portal_server.Handler.do_POST = do_post

    backend = Backend()
    backend.setUp()
    real_accept = backend.accept_unique

    def accept(order):
        if faults.take("uncertain"):
            # The coordinator logged an execution and then stopped without a session result: the transaction
            # may be in the mempool or lost, so the order is never released automatically.
            backend.accepted.add(order["id"])
            (backend.config.order_dir(order["id"]) / "coordinator.log.raw").write_text(
                f"PHASE35G_EXECUTED txid={secrets.token_hex(32)} height=999 rpc_block_hash=x\n")
            backend.alive = False
            return
        real_accept(order)
    backend.accept_unique = accept

    def pause_dealer(order):
        if faults.take("dealer_pause"):
            backend.ledger.set_setting("dealerPolicy", "PAUSED")
    backend.on_state["BUYER_APPROVED"] = pause_dealer

    # Slow the loop so a browser sees each stage (the real chain takes tens of seconds per purchase).
    real_wait = backend.halt.wait
    backend.halt.wait = lambda _timeout=None: real_wait(args.pace)

    class Control(BaseHTTPRequestHandler):
        def _send(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path != "/state":
                return self._send(404, {})
            with backend.ledger._connection() as db:
                orders = [dict(r) for r in db.execute("SELECT id,buyer_id,state,quantity,idempotency_key FROM orders ORDER BY created_at")]
            self._send(200, {"orders": orders, "loopErrors": backend.loop_errors[-5:],
                             "dealerPolicy": backend.ledger.setting("dealerPolicy")})

        def do_POST(self):  # noqa: N802
            if self.path == "/fault":
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                with faults.lock:
                    faults.lose_buy_answers = int(body.get("loseBuyAnswers", faults.lose_buy_answers))
                    faults.lose_confirm_answers = int(body.get("loseConfirmAnswers", faults.lose_confirm_answers))
                    faults.portal_down = int(body.get("portalDown", faults.portal_down))
                    faults.uncertain = bool(body.get("uncertain", faults.uncertain))
                    faults.dealer_pause = bool(body.get("dealerPause", faults.dealer_pause))
                return self._send(200, {"ok": True})
            if self.path == "/resume-dealer":
                backend.ledger.set_setting("dealerPolicy", "ACTIVE")
                return self._send(200, {"ok": True})
            self._send(404, {})

        def log_message(self, *a):
            pass

    control = ThreadingHTTPServer(("127.0.0.1", 0), Control)
    threading.Thread(target=control.serve_forever, daemon=True).start()
    print(json.dumps({"testDoubles": True, "root": str(backend.root),
                      "portalUrl": f"http://localhost:{backend.portal_http.server_address[1]}",
                      "portalToken": backend.token,
                      "control": f"http://127.0.0.1:{control.server_address[1]}"}), flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        control.shutdown()
        backend.doCleanups()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
