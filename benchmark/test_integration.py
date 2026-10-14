"""Controlled, isolated integration: the harness against the real exchange code, without cryptography.

The harness's own relay and probe run as separate processes, exactly the
programs it sends into the portal container, against:

* the real exchange.portal_server HTTP handler and exchange.portal.handle,
* the real ledger (schema 4), exchange.order_service.OrderService and
  exchange.demo_signer.DemoSigner, writing real order evidence files,

in a disposable /private/tmp/zwa4e-test-* root. The pinned tx-tool, the
Groth16 node verifier and Zebra are replaced by the existing exchange test
doubles (exchange/test_demo.py DemoTools) and a local JSON-RPC stub. So these
tests prove the harness's control flow, safety gates and evidence against
the real application state machine; they prove nothing about cryptographic
cost, and none of their timings is a measurement.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from benchmark import analysis, engine, evidence as ev, safety
from benchmark.correlation import CorrelationStore
from benchmark.engine import EngineError, PortalRelay
from benchmark.test_workload import FakeMonitor
from benchmark.watchdog import StopController, Thresholds
from benchmark.workload import LevelPlan, LevelRun, Timing
from checkout.quote import from_terms
from exchange import portal_server
from exchange.config import DEMO_LIMITS
from exchange.demo_ledger import DemoLedger
from exchange.test_demo import ASSETS, DemoBase, block_hash

REPOSITORY = Path(__file__).resolve().parents[1]
# test_demo's sign_round patches subprocess.run module-wide while the exchange loop signs; the harness binds
# subprocess.run when Docker is constructed, and the probe helper here binds it at import in the same way.
RUN = subprocess.run
PRICES = {"zwa-gold-reference": 1000, "zwa-treasury-style": 1250, "zwa-credit-participation": 850}


class ChainStub:
    """JSON-RPC answers derived from DemoTools' fake chain, with the real regtest genesis."""

    def __init__(self, tools):
        self.tools = tools

    def answer(self, method, params):
        tools = self.tools
        if method == "getblockcount":
            return tools.height
        if method == "getblockhash":
            return safety.REGTEST_GENESIS if params[0] == 0 else block_hash(params[0])
        if method == "getblock":
            height = int(params[0])
            block = tools.blocks.get(height, {"tx": [], "hash": block_hash(height)})
            return {"hash": block["hash"], "tx": list(block["tx"]), "height": height}
        if method == "getrawtransaction":
            for height, block in tools.blocks.items():
                if params[0] in block["tx"]:
                    return {"height": height, "hex": "00" * 3000}
            raise KeyError("no such transaction")
        raise KeyError(method)

    def serve(self) -> ThreadingHTTPServer:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                try:
                    reply = {"result": stub.answer(body["method"], body.get("params") or []), "error": None}
                except Exception as exc:  # noqa: BLE001
                    reply = {"result": None, "error": {"message": str(exc)}}
                data = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server


class Integration(DemoBase):
    def setUp(self):
        super().setUp()
        self.signed_roles = []
        self.terminated = None
        self.service._terminate = lambda pid: setattr(self, "terminated", pid)
        # The real regtest genesis, so the harness's chain checks pass for the right reason.
        (self.root / "chain-identity.json").write_text(json.dumps({
            "version": 1, "genesisHash": safety.REGTEST_GENESIS, "anchorHeight": 100, "anchorHash": block_hash(100)}))
        with self.ledger._transaction() as db:
            db.execute("UPDATE offers SET state='CLOSED' WHERE id=?", (self.offer["id"],))
        for instrument, asset in ASSETS.items():
            offer = self.ledger.create_offer(
                seller_id="dealer", instrument_id=instrument, asset_base=asset, chain_genesis=safety.REGTEST_GENESIS,
                scan_height=100, scan_anchor=block_hash(100), inventory_units=1000, capacity=900,
                idempotency_key="bench-" + instrument)
            self.ledger.set_price(seller_id="dealer", offer_id=offer["id"], price_zat_per_unit=PRICES[instrument])
        # DemoTools tracks only gold units for demo wallets; keep per-asset holdings so non-gold purchases
        # reconcile (otherwise the real order service rightly holds them UNCERTAIN).
        self.holdings: dict[tuple[str, str], int] = {}
        plain_signer = self.tools.signer

        def signer(role, *args, wallet=None, timeout=0):
            if args and args[0] == "balances" and wallet:
                native = self.tools.wallets[wallet][0]
                return [f"PHASE35F_BALANCES role={role.title()} scanned_height={self.tools.height} "
                        f"native_zat={native} asset={args[2]} asset_units={self.holdings.get((wallet, args[2]), 0)}"]
            return plain_signer(role, *args, wallet=wallet, timeout=timeout)
        self.tools.signer = signer
        self.rpc = ChainStub(self.tools).serve()
        self.addCleanup(self.rpc.shutdown)
        port = self.rpc.server_address[1]
        self.token = secrets.token_hex(32)
        config = {"version": 2, "root": str(self.root), "container": "zwa-test", "rpcPort": port,
                  "approvalMode": "CUSTODIAL_DEMO_POLICY", "txToolDir": "/nonexistent",
                  "paramsDataHome": "/nonexistent", "nodeBin": "/usr/bin/false", "quoteValiditySeconds": 90,
                  "approvalWaitSeconds": 60, "companionStaleSeconds": 15, "stabilizationBlocks": 110,
                  "rpcHost": "127.0.0.1", "chainManagement": "external", "isolation": "linux-landlock",
                  "demo": {"dealerAccountId": "dealer", **{k: d for k, (_, _, d) in DEMO_LIMITS.items()}}}
        path = self.root / "exchange" / "service-config.json"
        ev.write_new(path, json.dumps(config))
        environ = patch.dict(os.environ, {"ZWA_PRIVATE_EXCHANGE_ROOT": str(self.root)})
        environ.start()
        self.addCleanup(environ.stop)
        portal_server.Handler.token = self.token.encode()
        self.portal_http = ThreadingHTTPServer(("127.0.0.1", 0), portal_server.Handler)
        threading.Thread(target=self.portal_http.serve_forever, daemon=True).start()
        self.addCleanup(self.portal_http.shutdown)
        self.halt = threading.Event()
        self.loop_errors: list[str] = []
        self.accepted: set[str] = set()
        self.on_state = {}  # state -> callback(order) for fault injection
        self.loop = threading.Thread(target=self.exchange_loop, daemon=True)
        self.loop.start()
        self.addCleanup(self.stop_loop)

    def stop_loop(self):
        self.halt.set()
        self.loop.join(timeout=10)

    # ------------------------------------------------------------ the exchange's own loop

    def exchange_loop(self):
        """The order service and demo signer loops, with the coordinator's effects simulated as test_demo does."""
        while not self.halt.is_set():
            try:
                self.signer.heartbeat()
                self.service.tick()
                for order in self.ledger.orders_in(("TRADE_AUTHORIZED",)):
                    if self.ledger.execution(order["id"]) and not self.config.socket_path(order["id"]).exists():
                        self.alive = True
                        self.open_socket(order["id"])
                for state, callback in list(self.on_state.items()):
                    for order in self.ledger.orders_in((state,)):
                        callback(order)
                self.sign_round()
                if self.terminated:
                    self.alive, self.terminated = False, None
                for order in self.ledger.orders_in(("SELLER_APPROVED",)):
                    if order["id"] not in self.accepted:
                        self.accept_unique(order)
            except Exception as exc:  # noqa: BLE001 - surfaced by the test
                self.loop_errors.append(f"{type(exc).__name__}: {exc}")
            self.halt.wait(0.02)

    def accept_unique(self, order):
        """test_demo.accept with a fresh txid per order (settlement txids are unique in the ledger)."""
        self.accepted.add(order["id"])
        work = self.config.order_dir(order["id"])
        quote = from_terms(json.loads((work / "quote/terms.json").read_text()))
        txid = secrets.token_hex(32)
        height = quote["targetHeight"]
        receipt = {"terms_hash": quote["quoteDigest"], "txid": txid, "height": height,
                   "block_hash": block_hash(height), "tx_bytes": 3000}
        (work / "receipt.json").write_text(json.dumps(receipt))
        (work / "coordinator.log.raw").write_text(
            f"PHASE35G_EXECUTED txid={txid} height={height} rpc_block_hash={receipt['block_hash']} "
            "signed_groups_persisted=false\n")
        self.tools.block = {"txid": txid, "hash": receipt["block_hash"], "height": height, "bytes": 3000}
        self.tools.blocks[height] = {"tx": [txid], "hash": receipt["block_hash"]}
        self.tools.height = height
        buyer = self.ledger.wallet_for_pairing(self.ledger.order_quote(order["id"])["buyer_pairing_id"])["id"]
        native, units = self.tools.wallets[buyer]
        self.tools.wallets[buyer] = [native - quote["paymentAmount"], units]
        key = (buyer, quote["assetId"])
        self.holdings[key] = self.holdings.get(key, 0) + quote["quantity"]
        dealer_native, dealer_units = self.tools.wallets["seller"]
        self.tools.wallets["seller"] = [dealer_native + quote["paymentAmount"], dealer_units - quote["quantity"]]
        (work / "quote/session-result.json").write_text(json.dumps({
            "version": 1, "sessionId": quote["quoteDigest"], "source": "LIVE_LOCAL_CLI",
            "outcome": "ACCEPTED", "quote": quote, "receipt": receipt}))
        self.alive = False

    # ------------------------------------------------------------ harness pieces

    def relay(self) -> PortalRelay:
        def spawn():
            return subprocess.Popen([sys.executable, "-c", engine.RELAY, engine.RELAY_MARKER,
                                     str(self.portal_http.server_address[1])],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env={"PATH": "/usr/bin:/bin", "ZWA_PORTAL_TOKEN": self.token}, bufsize=0)
        relay = PortalRelay(None, timeout=30, spawn=spawn)
        self.addCleanup(relay.close)
        return relay

    def probe(self, order_ids=()):
        done = RUN([sys.executable, "-c", engine.PROBE, engine.PROBE_MARKER, *order_ids],
                   capture_output=True, text=True, timeout=60, cwd="/",
                   env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPOSITORY),
                        "ZWA_PRIVATE_EXCHANGE_ROOT": str(self.root)})
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def level(self, level: int, controller: StopController | None = None,
              wrap=None) -> tuple[dict, ev.Redactor, Path]:
        controller = controller or StopController()
        self.correlation_dir = self.root.parent / f"{self.root.name}-correlations"
        self.addCleanup(shutil.rmtree, self.correlation_dir, True)
        relay = self.relay()
        redactor = ev.Redactor()
        events_path = self.root.parent / f"{self.root.name}-events.jsonl"
        self.addCleanup(lambda: events_path.unlink(missing_ok=True))
        events = ev.JsonLines(events_path, redactor)
        run = LevelRun(LevelPlan(level=level, idle_before_s=0, idle_after_s=0, poll_s=0.05),
                       thresholds=Thresholds(), relay=wrap(relay) if wrap else relay, probe=self.probe,
                       monitor=FakeMonitor(Timing()), controller=controller, events=events, redactor=redactor,
                       timing=Timing(), correlations=CorrelationStore(self.correlation_dir, "run-int"))
        try:
            summary = run.execute()
        finally:
            events.close()
        return summary, redactor, events_path

    def assertNoLoopErrors(self):
        self.assertEqual(self.loop_errors, [])

    # ------------------------------------------------------------ tests

    def test_level1_end_to_end_through_the_real_portal_and_ledger(self):
        summary, redactor, events = self.level(1)
        self.assertEqual(summary["result"], "PASS", summary["stopReasons"])
        (tx,) = summary["transactions"]
        self.assertEqual(tx["outcome"], "SETTLED_VERIFIED")
        self.assertEqual(tx["reconciliation"]["class"], "SETTLED")
        self.assertTrue(tx["verifyTx"]["included"])
        self.assertEqual(tx["durability"], "RESTART_DURABLE_UNDER_PINNED_ZEBRA_POLICY_NOT_FINALITY")
        self.assertEqual(summary["consistencyProblems"], [])
        # The real ledger: one order despite the retried duplicate buy; the unit is sold, not reserved.
        with self.ledger._connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM orders").fetchone()[0], 1)
            gold = db.execute("SELECT reserved,sold FROM offers WHERE instrument_id='zwa-gold-reference' "
                              "AND state='OFFER_CREATED'").fetchone()
        self.assertEqual(tuple(gold), (0, 1))
        stages = tx["stages"]
        for name in ("walletSetup", "quoteReadyIncludingQueue", "groth16Verification", "receiptGeneration",
                     "endToEnd", "durability"):
            self.assertIsNotNone(stages[name], name)
            self.assertIsNotNone(stages[name]["seconds"], name)
        # Secrets the harness generated never reach evidence or the summary.
        text = events.read_text()
        self.assertTrue(redactor.values)
        self.assertEqual([v for v in redactor.values if v in text or v in json.dumps(summary)], [])
        self.assertNoLoopErrors()

    def test_level2_concurrent_purchases_queue_on_the_dealer_and_stay_consistent(self):
        summary, _, _ = self.level(2)
        self.assertEqual(summary["result"], "PASS", summary["stopReasons"])
        self.assertEqual([t["outcome"] for t in summary["transactions"]], ["SETTLED_VERIFIED"] * 2)
        txids = {t["txid"] for t in summary["transactions"]}
        self.assertEqual(len(txids), 2)
        snapshot = self.probe()
        self.assertEqual(analysis.inventory_problems(snapshot), [])
        self.assertEqual(sum(o["sold"] for o in snapshot["offers"]), 2)
        self.assertEqual(sum(o["reserved"] for o in snapshot["offers"]), 0)
        self.assertEqual(len({w["account_id"] for w in snapshot["wallets"]}), 2, "separate buyers")
        self.assertNoLoopErrors()

    def test_stop_after_first_confirmation_cancels_the_queued_purchase_through_the_portal(self):
        controller = StopController()
        real = DemoLedger.confirm_quote

        def confirm_then_stop(ledger, *args, **kwargs):
            result = real(ledger, *args, **kwargs)
            controller.trigger("MEM_AVAILABLE_LOW", "injected after the first confirmation")
            return result
        # The portal handler opens a new DemoLedger per request; patch the class it uses.
        with patch.object(DemoLedger, "confirm_quote", confirm_then_stop):
            summary, _, _ = self.level(2, controller)
        self.assertEqual(summary["stopReason"]["code"], "MEM_AVAILABLE_LOW")
        by_outcome = {t["outcome"]: t for t in summary["transactions"]}
        self.assertIn("SETTLED_VERIFIED", by_outcome, "the confirmed purchase drained to settlement")
        # The other buyer either had not ordered yet (its wallet was still being funded) or its order was
        # cancelled through the portal before signing; either way nothing more was confirmed.
        other = (set(by_outcome) - {"SETTLED_VERIFIED"}).pop()
        self.assertIn(other, ("CANCELLED_BEFORE_SIGNING", "ABANDONED_BEFORE_PURCHASE"))
        snapshot = self.probe()
        states = sorted(o["state"] for o in snapshot["orderIndex"])
        self.assertEqual(states, ["CANCELLED", "RECEIPT_AVAILABLE"] if other == "CANCELLED_BEFORE_SIGNING"
                         else ["RECEIPT_AVAILABLE"])
        self.assertEqual(analysis.inventory_problems(snapshot), [])
        self.assertEqual(sum(o["reserved"] for o in snapshot["offers"]), 0, "no reservation is left behind")
        self.assertEqual(summary["result"], "STOPPED")
        self.assertNoLoopErrors()

    def test_dealer_refusal_is_a_failed_order_that_never_executed(self):
        def pause(order):
            self.ledger.set_setting("dealerPolicy", "PAUSED")
        self.on_state["BUYER_APPROVED"] = pause
        summary, _, _ = self.level(1)
        self.assertEqual(summary["stopReason"]["code"], "ORDER_FAILED")
        (tx,) = summary["transactions"]
        self.assertEqual(tx["outcome"], "FAILED_AFTER_CONFIRM")
        self.assertEqual(tx["finalState"], "EXECUTION_FAILED")
        self.assertEqual(tx["reconciliation"]["class"], "NOT_EXECUTED")
        self.assertEqual(summary["result"], "STOPPED")
        self.assertEqual(summary["consistencyProblems"], [])
        self.assertNoLoopErrors()

    def test_lost_buy_response_is_recovered_against_the_real_portal_and_ledger(self):
        keys: list[str] = []

        class LosesFirstBuyAnswer:
            """The real portal accepts the first buy; its answer is lost on the way back."""

            def __init__(self, relay):
                self.relay, self.lost = relay, False

            def portal(self, action, **kwargs):
                result = self.relay.portal(action, **kwargs)
                if action == "buy":
                    keys.append(kwargs["payload"]["idempotencyKey"])
                    if not self.lost:
                        self.lost = True
                        raise EngineError("portal relay did not answer in time")
                return result

            def __getattr__(self, name):
                return getattr(self.relay, name)
        summary, _, _ = self.level(1, wrap=LosesFirstBuyAnswer)
        self.assertEqual(summary["result"], "PASS", summary["stopReasons"])
        self.assertEqual(len(set(keys)), 1, "every submission reused the one key")
        self.assertGreaterEqual(len(keys), 3, "lost answer, same-key resend, duplicate check")
        with self.ledger._connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM orders").fetchone()[0], 1)
        (record,) = CorrelationStore.all_records(self.correlation_dir)
        self.assertEqual((record["status"], record["orderId"]), ("ACCEPTED", summary["transactions"][0]["orderId"]))
        self.assertNoLoopErrors()

    def test_real_orders_lookup_resolves_a_crashed_submission(self):
        from benchmark.correlation import recover
        summary, _, _ = self.level(1)
        order_id = summary["transactions"][0]["orderId"]
        path = next(self.correlation_dir.glob("*.json"))
        record = json.loads(path.read_text())
        record.update(status="SUBMITTING", orderId=None)  # as if the harness died before recording the answer
        path.write_text(json.dumps(record))
        (result,) = recover(self.correlation_dir, "run-int", self.relay(), sleep=lambda s: None)
        self.assertEqual((result["status"], result["orderId"]), ("ACCEPTED", order_id))
        self.assertNoLoopErrors()

    def test_probe_reads_the_real_schema_without_writing_or_leaking(self):
        summary, _, _ = self.level(1)
        self.assertEqual(summary["result"], "PASS", summary["stopReasons"])
        order_id = summary["transactions"][0]["orderId"]
        ledger = self.config.ledger_path
        self.halt.set()
        self.loop.join(timeout=10)
        before = (hashlib.sha256(ledger.read_bytes()).hexdigest(), ledger.stat().st_mtime_ns)
        snapshot = self.probe([order_id])
        self.assertEqual((hashlib.sha256(ledger.read_bytes()).hexdigest(), ledger.stat().st_mtime_ns), before)
        detail = snapshot["orders"][order_id]
        self.assertEqual(detail["state"], "RECEIPT_AVAILABLE")
        self.assertEqual([e["to_state"] for e in detail["events"]][-1], "RECEIPT_AVAILABLE")
        self.assertEqual(detail["evidence"]["INCLUDED"]["txid"], summary["transactions"][0]["txid"])
        self.assertTrue(detail["onChain"]["txInBlock"] and detail["onChain"]["blockHashMatches"])
        for name in ("quote/terms.json", "proof-generation.log", "preflight-authorization.json", "receipt.json",
                     "order-receipt.json", "stabilization.json"):
            self.assertIn(name, detail["artifactMtimeNs"])
        self.assertEqual(analysis.chain_problems(snapshot), [])
        text = json.dumps(snapshot)
        with self.ledger._connection() as db:
            private = [v for row in db.execute("SELECT session_hash,client_hash,wallet_address,id FROM demo_wallets")
                       for v in row] + [r[0] for r in db.execute("SELECT session_hash FROM wallet_pairings")]
        self.assertTrue(private)
        self.assertEqual([v for v in private if v in text], [])
        self.assertNotIn("demo-wallets", text)
        self.assertNoLoopErrors()


if __name__ == "__main__":
    unittest.main()
