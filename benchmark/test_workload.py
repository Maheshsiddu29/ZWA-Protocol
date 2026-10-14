"""Benchmark levels against a simulated exchange with injected faults.

FakeExchange follows the real exchange's rules that matter here (one live
quote at a time on the single dealer wallet, idempotent `buy`, reservations
released by CANCELLED/EXPIRED/REJECTED/EXECUTION_FAILED and kept by UNCERTAIN,
receipts only after RECONCILED). It is a test double for the harness's control
flow only: no number it produces is a measurement.
"""

from __future__ import annotations

import json
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest

from benchmark import analysis, evidence as ev, safety
from benchmark.engine import EngineError, PortalRefused
from benchmark.watchdog import GIB, StopController, Thresholds
from benchmark.workload import INSTRUMENTS, LevelPlan, LevelRun, Timing

STEPS = ["BUYER_REQUESTED", "QUOTE_READY", "BUYER_CONFIRMED", "PROOF_VERIFIED", "TRADE_AUTHORIZED",
         "BUYER_APPROVED", "SELLER_APPROVED", "SUBMITTED", "INCLUDED", "RECONCILED", "RECEIPT_AVAILABLE"]
COMMITTED = set(STEPS[1:10]) | {"UNCERTAIN"}
RELEASED = {"CANCELLED", "EXPIRED", "REJECTED", "EXECUTION_FAILED"}
ARTIFACT_AT = {"QUOTE_READY": ["buyer-before.log", "seller-before.log", "quote/terms.json"],
               "PROOF_VERIFIED": ["proof-generation.log", "preflight-authorization.json"],
               "TRADE_AUTHORIZED": ["buyer-review.log", "seller-review.log", "coordinator.log.raw"],
               "BUYER_APPROVED": ["buyer-signer.log", "buyer-approval.json"],
               "SELLER_APPROVED": ["seller-signer.log", "seller-approval.json"],
               "SUBMITTED": ["receipt.json"], "RECONCILED": ["buyer-after.log", "seller-after.log"],
               "RECEIPT_AVAILABLE": ["order-receipt.json"]}
PRICES = {"zwa-gold-reference": 1000, "zwa-treasury-style": 1250, "zwa-credit-participation": 850}


class Clock:
    def __init__(self):
        self.t = 1_760_000_000.0
        self.lock = threading.Lock()

    def now(self):
        with self.lock:
            return self.t

    def sleep(self, seconds):
        with self.lock:
            self.t += seconds
        time.sleep(0.0005)  # let the other purchase threads run


class FakeExchange:
    def __init__(self, clock: Clock, **faults):
        self.clock = clock
        self.faults = faults
        self.lock = threading.Lock()
        self.offers = {secrets.token_hex(16): {"instrument": i, "asset": secrets.token_hex(32), "capacity": 900,
                                                "reserved": 0, "sold": 0} for i in INSTRUMENTS}
        self.wallets: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        self.settlements: dict[str, str] = {}
        self.height = 400
        self.sessions_seen: set[str] = set()
        self.client_hashes: set[str] = set()
        self.calls: list[str] = []
        self.stop_after_calls = faults.get("stop_after_calls")
        self.controller: StopController | None = None

    # ------------------------------------------------------------ portal

    def portal(self, action, *, actor="anonymous", role="anonymous", session, payload=None):
        with self.lock:
            self.calls.append(action)
            if self.controller and self.stop_after_calls and len(self.calls) == self.stop_after_calls:
                self.controller.trigger("MEM_AVAILABLE_LOW", "injected memory pressure")
            if self.faults.get("relay_down") and action == "order" and len(self.calls) % 2:
                raise EngineError("relay hiccup")
            return self._portal(action, actor, role, session, payload or {})

    def _wallet_for(self, actor, session):
        wallet = self.wallets.get(actor)
        if not wallet or wallet["session"] != session:
            raise PortalRefused("demo session expired")
        return wallet

    def _portal(self, action, actor, role, session, payload):
        if action == "demo-start":
            assert actor == "anonymous" and set(payload) == {"clientHash"}
            self.sessions_seen.add(session)
            self.client_hashes.add(payload["clientHash"])
            account = "judge-" + secrets.token_hex(10)
            self.wallets[account] = {"session": session, "state": "REQUESTED"}
            return {"accountId": account, "expiresAt": 0}
        if action == "verify-tx":
            order = next((o for o in self.orders.values() if o.get("txid") == payload["txid"]), None)
            if not order:
                return {"chainActive": True, "found": False}
            return {"chainActive": True, "found": True, "included": not self.faults.get("tx_not_in_block"),
                    "height": order["height"], "blockHash": order["blockHash"], "genesisHash": safety.REGTEST_GENESIS}
        wallet = self._wallet_for(actor, session)
        if action == "wallet":
            if self.faults.get("wallet_fails"):
                wallet["state"] = "FAILED"
            elif not self.faults.get("wallet_stuck"):
                nxt = {"REQUESTED": "CREATED", "CREATED": "FUNDING", "FUNDING": "READY"}
                wallet["state"] = nxt.get(wallet["state"], wallet["state"])
            return {"state": wallet["state"], "nativeZat": 625_000_000 if wallet["state"] == "READY" else None}
        if action == "market":
            return {"chainActive": True, "instruments": [
                {"offerId": oid, "instrumentId": o["instrument"], "priceZatPerUnit": PRICES[o["instrument"]],
                 "availableUnits": o["capacity"] - o["reserved"] - o["sold"], "inventoryVerifiedAtTip": True}
                for oid, o in self.offers.items()]}
        if action == "buy":
            prior = next((o for o in self.orders.values() if o["buyer"] == actor and o["key"] == payload["idempotencyKey"]),
                         None)
            if prior and not self.faults.get("duplicate_bug"):
                return self.view(prior)
            offer = self.offers[payload["offerId"]]
            offer["reserved"] += payload["quantity"]
            oid = secrets.token_hex(16)
            self.orders[oid] = {"id": oid, "offer": payload["offerId"], "buyer": actor, "key": payload["idempotencyKey"],
                                "quantity": payload["quantity"], "state": "BUYER_REQUESTED",
                                "payment": payload["quantity"] * PRICES[offer["instrument"]], "artifacts": {},
                                "digest": secrets.token_hex(32), "polls": 0, "durable": False}
            return self.view(self.orders[oid])
        if action == "order":
            order = self.orders[payload["orderId"]]
            assert order["buyer"] == actor
            self.advance(order)
            return self.view(order)
        if action == "confirm":
            order = self.orders[payload["orderId"]]
            if order["state"] != "QUOTE_READY" or payload["quoteDigest"] != order["digest"]:
                raise PortalRefused("quote changed")
            order["state"] = "BUYER_CONFIRMED"
            return self.view(order)
        if action == "cancel":
            order = self.orders[payload["orderId"]]
            if order["state"] not in STEPS[:5]:
                raise PortalRefused("reservation cannot be released after approval starts")
            self.release(order, "CANCELLED")
            return self.view(order)
        raise AssertionError(action)

    def release(self, order, state):
        order["state"] = state
        if state in RELEASED:
            self.offers[order["offer"]]["reserved"] -= order["quantity"]

    def advance(self, order):
        state = order["state"]
        if state == "RECEIPT_AVAILABLE":
            order["durable"] = True
            return
        if state in RELEASED or state in ("UNCERTAIN", "QUOTE_READY") or state == self.faults.get("stall_at"):
            return
        if state == "BUYER_REQUESTED" and any(o["state"] in COMMITTED for o in self.orders.values() if o is not order):
            return  # queued on the single dealer wallet
        nxt = STEPS[STEPS.index(state) + 1]
        if nxt == self.faults.get("fail_at"):
            self.release(order, "EXECUTION_FAILED")
            order["failure"] = {"reason": "WALLET_APPROVAL_TIMEOUT_OR_COORDINATOR_FAILURE", "transactionSubmitted": False}
            return
        if nxt == self.faults.get("uncertain_at"):
            order["state"] = "UNCERTAIN"
            order["failure"] = {"reason": "COORDINATOR_STOPPED_AFTER_BOTH_APPROVALS"}
            return
        order["state"] = nxt
        for name in ARTIFACT_AT.get(nxt, []):
            order["artifacts"][name] = int(self.clock.now() * 1e9)
        if nxt == "SUBMITTED":
            self.height += 1
            order.update(txid=secrets.token_hex(32), height=self.height, blockHash=secrets.token_hex(32))
            self.settlements[order["id"]] = order["txid"]
        if nxt == "RECEIPT_AVAILABLE":
            offer = self.offers[order["offer"]]
            offer["reserved"] -= order["quantity"]
            offer["sold"] += order["quantity"]

    def view(self, order):
        state = order["state"]
        receipt = None
        if state == "RECEIPT_AVAILABLE" and not self.faults.get("missing_receipt"):
            receipt = {"txid": order["txid"], "height": order["height"], "blockHash": order["blockHash"],
                       "quantity": order["quantity"], "paymentZat": order["payment"], "confirmations": 1,
                       "durability": "RESTART_DURABLE" if order["durable"] else None}
        reconciled = STEPS.index(state) >= STEPS.index("RECONCILED") if state in STEPS else False
        verdict = "REJECTED" if self.faults.get("proof_not_verified") else "VERIFIED"
        return {"id": order["id"], "state": state, "quote": {"digest": order["digest"], "quantity": order["quantity"],
                                                              "paymentZat": order["payment"], "network": "regtest"}
                if state != "BUYER_REQUESTED" else None,
                "proofVerified": True, "proof": {k: verdict for k in analysis.VERDICT_KEYS},
                "buyerApprovalMode": "CUSTODIAL_DEMO_POLICY", "sellerApprovalMode": "CUSTODIAL_DEMO_POLICY",
                "effects": {"assetUnits": order["quantity"], "nativeZat": -order["payment"]} if reconciled else None,
                "receipt": receipt, "failure": order.get("failure"), "settlementDropped": False}

    # ------------------------------------------------------------ probe

    def probe(self, order_ids=()):
        with self.lock:
            offers = [{"id": oid, "instrument_id": o["instrument"], "asset_base": o["asset"], "capacity": o["capacity"],
                       "reserved": o["reserved"] + (1 if self.faults.get("over_reserve") or (
                           self.faults.get("over_reserve_after_orders") and order_ids) else 0), "sold": o["sold"],
                       "state": "OFFER_CREATED"} for oid, o in self.offers.items()]
            orders = {}
            for oid in order_ids:
                order = self.orders[oid]
                evidence = {}
                if order.get("txid") and order["state"] in ("INCLUDED", "RECONCILED", "RECEIPT_AVAILABLE", "UNCERTAIN"):
                    evidence["INCLUDED"] = {"txid": order["txid"], "height": order["height"], "blockHash": order["blockHash"]}
                if order.get("failure"):
                    evidence[order["state"]] = order["failure"]
                orders[oid] = {"id": oid, "state": order["state"], "evidence": evidence,
                               "artifactMtimeNs": dict(order["artifacts"]),
                               "onChain": {"txInBlock": not self.faults.get("tx_not_in_block"),
                                           "blockHashMatches": True, "confirmations": 1} if order.get("txid") else None}
            return {"probe": 1, "approvalMode": "CUSTODIAL_DEMO_POLICY", "integrity": "ok",
                    "identity": {"genesisHash": self.faults.get("genesis", safety.REGTEST_GENESIS)},
                    "chain": {"live": True, "tip": self.height,
                              "genesisHash": self.faults.get("genesis", safety.REGTEST_GENESIS)},
                    "offers": offers, "dealerPolicy": "ACTIVE",
                    "dealerScans": [{"asset_base": o["asset"], "asset_units": 1000 - o["sold"], "scan_height": self.height}
                                    for o in self.offers.values()],
                    "settlements": [{"order_id": k, "txid": v} for k, v in self.settlements.items()],
                    "orderIndex": [{"id": o["id"], "offer_id": o["offer"], "quantity": o["quantity"], "state": o["state"]}
                                   for o in self.orders.values()],
                    "orders": orders}


class FakeMonitor:
    def __init__(self, clock):
        self.clock = clock
        self.samples = []
        self.started = self.stopped = False

    def start(self):
        self.started = True
        t = self.clock.now()
        self.samples = [{"t": t + i, "index": i, "host": {"memTotal": 8 * GIB, "memAvailable": 5 * GIB, "swapUsed": 0,
                                                          "cpuPercent": 20.0, "loadAvg": [0.5, 0.4, 0.3],
                                                          "oomKillCount": 0, "disk": {}},
                         "containers": []} for i in range(3)]

    def stop(self):
        self.stopped = True


class Harness:
    def __init__(self, level: int, tmp: Path, **faults):
        self.clock = Clock()
        self.exchange = FakeExchange(self.clock, **faults)
        self.controller = StopController(clock=self.clock.now)
        self.exchange.controller = self.controller
        self.redactor = ev.Redactor()
        self.events = ev.JsonLines(tmp / "events.jsonl", self.redactor)
        self.monitor = FakeMonitor(self.clock)
        plan = LevelPlan(level=level, idle_before_s=1, idle_after_s=1, baseline_s=5, poll_s=1.0,
                         wait_durability=faults.get("wait_durability", True))
        self.run = LevelRun(plan, thresholds=Thresholds(), relay=self.exchange, probe=self.exchange.probe,
                            monitor=self.monitor, controller=self.controller, events=self.events,
                            redactor=self.redactor, timing=Timing(self.clock.now, self.clock.sleep, interruptible=False))

    def execute(self) -> dict:
        try:
            return self.run.execute()
        finally:
            self.events.close()


class Levels(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def harness(self, level, **faults):
        return Harness(level, self.path, **faults)

    def test_level0_submits_nothing(self):
        h = self.harness(0)
        summary = h.execute()
        self.assertEqual(summary["result"], "PASS")
        self.assertEqual(summary["transactions"], [])
        self.assertNotIn("demo-start", h.exchange.calls)
        self.assertTrue(h.monitor.started and h.monitor.stopped)

    def test_level1_single_settlement_with_receipt_and_verification(self):
        h = self.harness(1)
        summary = h.execute()
        self.assertEqual(summary["result"], "PASS", summary["stopReasons"])
        (tx,) = summary["transactions"]
        self.assertEqual(tx["outcome"], "SETTLED_VERIFIED")
        self.assertEqual(tx["reconciliation"]["class"], "SETTLED")
        self.assertTrue(tx["verifyTx"]["included"])
        self.assertIsNotNone(tx["durability"])
        self.assertEqual(h.exchange.calls.count("demo-start"), 1)
        self.assertEqual(h.exchange.calls.count("confirm"), 1)
        stages = tx["stages"]
        for name in ("walletSetup", "groth16Verification", "buyerActionGroupProveAndSign", "endToEnd"):
            self.assertIsNotNone(stages[name], name)
        self.assertEqual(summary["consistencyProblems"], [])

    def test_level2_two_concurrent_purchases_serialize_safely(self):
        h = self.harness(2)
        summary = h.execute()
        self.assertEqual(summary["result"], "PASS", summary["stopReasons"])
        self.assertEqual([t["outcome"] for t in summary["transactions"]], ["SETTLED_VERIFIED"] * 2)
        self.assertEqual(len(h.exchange.sessions_seen), 2, "separate buyers")
        self.assertEqual(len({t["account"] for t in summary["transactions"]}), 2)
        self.assertEqual(len({t["offerId"] for t in summary["transactions"]}), 2)
        self.assertEqual(h.exchange.calls.count("confirm"), 2)
        self.assertEqual(sum(o["reserved"] for o in h.exchange.offers.values()), 0)
        self.assertEqual(sum(o["sold"] for o in h.exchange.offers.values()), 2)

    def test_level3_concurrency_is_three_and_never_more(self):
        summary = self.harness(3).execute()
        self.assertEqual(summary["concurrency"], 3)
        self.assertEqual(len(summary["transactions"]), 3)
        with self.assertRaises(safety.SafetyError):
            LevelPlan(level=4).purchases()

    def test_duplicate_submission_must_return_the_same_order(self):
        h = self.harness(1, duplicate_bug=True)
        summary = h.execute()
        self.assertEqual(summary["stopReason"]["code"], "DUPLICATE_SUBMISSION_MISMATCH")
        self.assertEqual(summary["transactions"][0]["outcome"], "DUPLICATE_MISMATCH")
        self.assertNotIn("confirm", h.exchange.calls)
        self.assertEqual(h.exchange.calls.count("cancel"), 2, "both the order and its duplicate are cancelled")
        self.assertEqual(sum(o["reserved"] for o in h.exchange.offers.values()), 0)
        self.assertEqual(summary["consistencyProblems"], [])

    def test_existing_inventory_inconsistency_refuses_the_level(self):
        h = self.harness(1, over_reserve=True)
        with self.assertRaisesRegex(EngineError, "reserved 1 but live orders hold 0"):
            h.execute()
        self.assertEqual(h.exchange.calls, [], "nothing is submitted on an inconsistent ledger")

    def test_inventory_over_reservation_during_the_run_is_detected(self):
        summary = self.harness(1, over_reserve_after_orders=True).execute()
        self.assertEqual(summary["result"], "STOPPED")
        self.assertIn("INVENTORY_INCONSISTENT", [r["code"] for r in summary["stopReasons"]])
        self.assertTrue(any("reserved" in p for p in summary["consistencyProblems"]))

    def test_transaction_failure_stops_the_level(self):
        h = self.harness(2, fail_at="BUYER_APPROVED")
        summary = h.execute()
        self.assertEqual(summary["stopReason"]["code"], "ORDER_FAILED")
        # Either buyer may reach the dealer queue first; the other is still unconfirmed when the gate closes.
        by_outcome = {t["outcome"]: t for t in summary["transactions"]}
        self.assertEqual(set(by_outcome), {"FAILED_AFTER_CONFIRM", "CANCELLED_BEFORE_SIGNING"})
        self.assertEqual(by_outcome["FAILED_AFTER_CONFIRM"]["reconciliation"]["class"], "NOT_EXECUTED")
        self.assertEqual(h.exchange.calls.count("confirm"), 1)
        self.assertEqual(sum(o["reserved"] for o in h.exchange.offers.values()), 0, "reservations released")
        self.assertEqual(summary["consistencyProblems"], [])

    def test_missing_receipt(self):
        summary = self.harness(1, missing_receipt=True).execute()
        self.assertEqual(summary["stopReason"]["code"], "RECEIPT_MISSING")
        self.assertEqual(summary["transactions"][0]["outcome"], "SETTLED_WITH_PROBLEMS")

    def test_transaction_not_in_block(self):
        summary = self.harness(1, tx_not_in_block=True).execute()
        self.assertEqual(summary["stopReason"]["code"], "TX_VERIFICATION_FAILED")

    def test_unverified_proof_is_inconsistent(self):
        summary = self.harness(1, proof_not_verified=True).execute()
        self.assertEqual(summary["stopReason"]["code"], "RECEIPT_INCONSISTENT")

    def test_settlement_timeout_drains_without_cancelling_then_is_partial(self):
        h = self.harness(1, stall_at="SELLER_APPROVED")
        summary = h.execute()
        self.assertEqual(summary["stopReason"]["code"], "SETTLEMENT_TIMEOUT")
        self.assertEqual(summary["result"], "PARTIAL")
        tx = summary["transactions"][0]
        self.assertEqual(tx["outcome"], "IN_PROGRESS_AT_DRAIN_END")
        self.assertEqual(tx["reconciliation"]["class"], "IN_PROGRESS")
        self.assertNotIn("cancel", h.exchange.calls, "a confirmed order is never cancelled")
        self.assertIn("drainStarted", tx["timeline"])

    def test_uncertain_order_is_left_to_the_operator(self):
        h = self.harness(1, uncertain_at="SUBMITTED")
        summary = h.execute()
        self.assertEqual(summary["stopReason"]["code"], "ORDER_FAILED")
        self.assertEqual(summary["result"], "PARTIAL")
        self.assertEqual(summary["transactions"][0]["reconciliation"]["class"], "UNCERTAIN")
        self.assertEqual(sum(o["reserved"] for o in h.exchange.offers.values()), 1, "UNCERTAIN keeps the reservation")
        self.assertEqual(summary["consistencyProblems"], [])

    def test_resource_stop_mid_run_cancels_unconfirmed_and_drains_confirmed(self):
        # Stop right after the first purchase's confirmation is sent; the second is still queued.
        h = self.harness(2)
        original = h.exchange._portal

        def portal(action, actor, role, session, payload):
            result = original(action, actor, role, session, payload)
            if action == "confirm" and not h.controller.stopped:
                h.controller.trigger("MEM_AVAILABLE_LOW", "injected after first confirmation")
            return result
        h.exchange._portal = portal
        summary = h.execute()
        self.assertEqual(summary["stopReason"]["code"], "MEM_AVAILABLE_LOW")
        outcomes = sorted(t["outcome"] for t in summary["transactions"])
        self.assertEqual(outcomes, ["CANCELLED_BEFORE_SIGNING", "SETTLED_VERIFIED"])
        self.assertEqual(h.exchange.calls.count("confirm"), 1)
        self.assertEqual(summary["result"], "STOPPED")
        self.assertEqual(summary["consistencyProblems"], [])

    def test_stop_arriving_with_the_quote_is_caught_by_the_confirmation_gate(self):
        h = self.harness(1)
        original = h.exchange._portal

        def portal(action, actor, role, session, payload):
            view = original(action, actor, role, session, payload)
            if action == "order" and view.get("state") == "QUOTE_READY" and not h.controller.stopped:
                h.controller.trigger("SWAP_GROWTH", "injected as the quote arrives")
            return view
        h.exchange._portal = portal
        summary = h.execute()
        self.assertNotIn("confirm", h.exchange.calls)
        self.assertEqual(summary["transactions"][0]["outcome"], "CANCELLED_BEFORE_SIGNING")
        self.assertEqual(sum(o["reserved"] for o in h.exchange.offers.values()), 0)

    def test_a_confirmed_order_is_never_cancelled(self):
        from benchmark.workload import Purchase, Worker
        h = self.harness(1)
        worker = Worker(Purchase("P1", INSTRUMENTS[0], 1), relay=h.exchange, controller=h.controller,
                        thresholds=Thresholds(), events=h.events, redactor=h.redactor, queue_position=0,
                        wait_durability=False, timing=Timing(h.clock.now, h.clock.sleep, interruptible=False))
        worker.p.order_id = "a" * 32
        worker.p.timeline["confirmed"] = 1.0
        worker.cancel_unconfirmed("operator stop")
        self.assertEqual(h.exchange.calls, [])
        h.events.close()

    def test_stop_before_any_wallet_submits_nothing(self):
        h = self.harness(2)
        h.controller.trigger("DISK_FREE_LOW", "already low")
        summary = h.execute()
        self.assertNotIn("demo-start", h.exchange.calls)
        self.assertEqual(summary["transactions"], [])
        self.assertEqual(summary["result"], "STOPPED")

    def test_wallet_timeout_and_failure(self):
        summary = self.harness(1, wallet_stuck=True).execute()
        self.assertEqual(summary["stopReason"]["code"], "WALLET_READY_TIMEOUT")
        self.assertEqual(summary["transactions"][0]["reconciliation"]["class"], "NOT_CREATED")
        summary = Harness(1, Path(tempfile.mkdtemp(dir=self.path)), wallet_fails=True).execute()
        self.assertEqual(summary["stopReason"]["code"], "ORDER_FAILED")

    def test_transient_relay_errors_are_retried(self):
        summary = self.harness(1, relay_down=True).execute()
        self.assertEqual(summary["result"], "PASS", summary["stopReasons"])

    def test_mainnet_chain_is_refused_before_any_submission(self):
        h = self.harness(1, genesis="00040fe8ec8471911baa1db1266ea15dd06b4a8a5c453883c000b031973dce08")
        with self.assertRaisesRegex(safety.SafetyError, "mainnet"):
            h.execute()
        self.assertEqual(h.exchange.calls, [])
        self.assertFalse(h.monitor.started)

    def test_existing_active_or_uncertain_orders_block_a_new_level(self):
        h = self.harness(1)
        h.exchange.orders["x" * 32] = {"id": "x" * 32, "offer": next(iter(h.exchange.offers)), "quantity": 1,
                                       "state": "UNCERTAIN", "buyer": "judge-0", "key": "k", "artifacts": {}}
        with self.assertRaisesRegex(EngineError, "reconcile"):
            h.execute()
        self.assertEqual(h.exchange.calls, [])

    def test_budget_is_reserved_after_preconditions_and_before_any_submission(self):
        calls_at_reservation = []
        h = self.harness(1)
        h.run.before_submit = lambda: calls_at_reservation.append(list(h.exchange.calls))
        h.execute()
        self.assertEqual(calls_at_reservation, [[]], "reserved once, before the first portal call")
        refused = Harness(1, Path(tempfile.mkdtemp(dir=self.path)), over_reserve=True)
        reserved = []
        refused.run.before_submit = lambda: reserved.append(True)
        with self.assertRaises(EngineError):
            refused.execute()
        self.assertEqual(reserved, [], "a level refused by its preconditions reserves nothing")

    def test_secrets_never_reach_events(self):
        h = self.harness(2)
        summary = h.execute()
        text = (self.path / "events.jsonl").read_text()
        for secret in h.exchange.sessions_seen | h.exchange.client_hashes:
            self.assertNotIn(secret, text)
            self.assertNotIn(secret, json.dumps(summary))
        self.assertEqual(ev.scan(self.path, h.redactor.values), [])


class Reconciliation(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(analysis.reconcile(None, None)["class"], "NOT_CREATED")
        self.assertEqual(analysis.reconcile({"evidence": {}}, "QUOTE_READY")["class"], "UNCONFIRMED")
        self.assertEqual(analysis.reconcile({"evidence": {}}, "SELLER_APPROVED")["class"], "IN_PROGRESS")
        self.assertEqual(analysis.reconcile({"evidence": {"UNCERTAIN": {"reason": "x"}}}, "UNCERTAIN")["class"],
                         "UNCERTAIN")
        self.assertEqual(analysis.reconcile({"evidence": {"EXPIRED": {"reason": "q"}}}, "EXPIRED")["class"],
                         "NOT_EXECUTED")
        self.assertEqual(analysis.reconcile({"evidence": {"REJECTED": {"transactionSubmitted": True}}},
                                            "REJECTED")["class"], "UNEXPECTED")
        self.assertEqual(analysis.reconcile({"onChain": {"txInBlock": True, "blockHashMatches": True}},
                                            "RECEIPT_AVAILABLE")["class"], "SETTLED")
        self.assertEqual(analysis.reconcile({"onChain": {"txInBlock": False}}, "RECEIPT_AVAILABLE")["class"],
                         "SETTLED_UNVERIFIED")


class Latencies(unittest.TestCase):
    def test_exact_process_start_is_preferred_and_methods_are_recorded(self):
        ns = lambda t: int(t * 1e9)  # noqa: E731
        detail = {"artifactMtimeNs": {"quote/terms.json": ns(110), "buyer-before.log": ns(104),
                                      "proof-generation.log": ns(130), "preflight-authorization.json": ns(132),
                                      "seller-review.log": ns(140), "buyer-signer.log": ns(160),
                                      "seller-signer.log": ns(180), "receipt.json": ns(185),
                                      "seller-after.log": ns(195), "order-receipt.json": ns(196)}}
        timeline = {"purchaseSubmitted": 100.0, "confirmed": 112.0, "receiptSeen": 197.0}
        procs = [{"label": "groth16:generate", "startedAt": 113.5, "peakRssBytes": 900, "cpuSeconds": 15.0, "samples": 16},
                 {"label": "tx-tool:signer:authorize-live:buyer", "startedAt": 141.0, "peakRssBytes": 5, "cpuSeconds": 1,
                  "samples": 19},
                 {"label": "tx-tool:signer:authorize-live:seller", "startedAt": 161.0, "peakRssBytes": 5,
                  "cpuSeconds": 1, "samples": 19}]
        result = analysis.stage_latencies(detail, timeline, procs)
        stages = result["stages"]
        self.assertEqual(stages["groth16ProofGeneration"]["seconds"], 16.5)
        self.assertIn("process start", stages["groth16ProofGeneration"]["method"])
        self.assertEqual(stages["groth16Verification"]["seconds"], 2.0)
        self.assertEqual(stages["buyerActionGroupProveAndSign"]["seconds"], 19.0)
        self.assertEqual(stages["sellerActionGroupProveAndSign"]["seconds"], 19.0)
        self.assertEqual(stages["assembleBroadcastInclude"]["seconds"], 5.0)
        self.assertEqual(stages["endToEnd"]["seconds"], 96.0)
        self.assertEqual(result["operations"]["groth16Generate"]["peakRssBytes"], 900)
        # Without the process, the bound is labelled as an upper bound including the service tick.
        fallback = analysis.stage_latencies(detail, timeline, [])["stages"]["groth16ProofGeneration"]
        self.assertEqual(fallback["seconds"], 18.0)
        self.assertIn("upper bound", fallback["method"])
        self.assertIsNone(analysis.stage_latencies({}, {}, [])["stages"]["endToEnd"])

    def test_overlapping_boundaries_are_not_reported(self):
        detail = {"artifactMtimeNs": {"seller-signer.log": int(200e9), "receipt.json": int(199e9)}}
        stage = analysis.stage_latencies(detail, {}, [])["stages"]["assembleBroadcastInclude"]
        self.assertIsNone(stage["seconds"])


if __name__ == "__main__":
    unittest.main()
