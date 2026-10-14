"""Regressions for the four findings of the independent benchmark review (2026-10-14).

Each test reproduces a defect in the first eight benchmark commits:

1. a failed or refused purchase could still yield result PASS (and unlock Level 3);
2. a settlement whose durability was never observed, or was lost, still passed;
3. cleanup could delete the ledger and wallet volumes while confirmed work was unresolved
   (see benchmark/test_cli.py CleanupQuiescence);
4. a buy accepted by the server whose response was lost left no recoverable order.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from benchmark import analysis
from benchmark.correlation import CorrelationError, CorrelationStore, recover
from benchmark.engine import PortalRefused
from benchmark.test_workload import Harness


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_level(self, level, **faults):
        harness = Harness(level, Path(tempfile.mkdtemp(dir=self.path)), **faults)
        return harness, harness.execute()

    def codes(self, summary):
        return [reason["code"] for reason in summary["stopReasons"]]


class FalsePass(Case):
    """Finding 1: PASS must mean every expected purchase settled, verified and reconciled."""

    def test_refused_demo_start_cannot_pass(self):
        _, summary = self.run_level(1, refuse={"demo-start": 1})
        self.assertNotEqual(summary["result"], "PASS")
        self.assertIn("PORTAL_REFUSED", self.codes(summary))

    def test_refused_buy_cannot_pass(self):
        _, summary = self.run_level(1, refuse={"buy": 1})
        self.assertNotEqual(summary["result"], "PASS")
        self.assertIn("PORTAL_REFUSED", self.codes(summary))

    def test_refused_order_lookup_cannot_pass(self):
        _, summary = self.run_level(1, refuse={"order": 1})
        self.assertNotEqual(summary["result"], "PASS")
        self.assertIn("PORTAL_REFUSED", self.codes(summary))

    def test_refused_confirmation_cannot_pass(self):
        _, summary = self.run_level(1, refuse={"confirm": 1})
        self.assertNotEqual(summary["result"], "PASS")

    def test_missing_final_order_evidence_fails_closed(self):
        _, summary = self.run_level(1, probe_drops_order=True)
        self.assertNotEqual(summary["result"], "PASS")
        self.assertIn("ACCEPTANCE_FAILED", self.codes(summary))
        self.assertTrue(any("final order evidence" in p for p in summary["acceptance"]["problems"]))

    def test_missing_receipt_cannot_pass(self):
        _, summary = self.run_level(1, missing_receipt=True)
        self.assertNotEqual(summary["result"], "PASS")

    def test_level0_without_valid_monitoring_cannot_pass(self):
        _, summary = self.run_level(0, no_samples=True)
        self.assertNotEqual(summary["result"], "PASS")
        _, summary = self.run_level(0, sample_errors=2)
        self.assertNotEqual(summary["result"], "PASS")

    def test_every_pass_satisfies_acceptance(self):
        for level in (0, 1, 2):
            _, summary = self.run_level(level)
            self.assertEqual(summary["result"], "PASS", summary["stopReasons"])
            self.assertEqual(summary["acceptance"], {"passed": True, "problems": [], "durabilityRequired": True})
            self.assertEqual(analysis.acceptance_problems(summary), [])

    def test_acceptance_requires_the_expected_purchase_count(self):
        _, summary = self.run_level(2)
        summary["transactions"] = summary["transactions"][:1]
        self.assertTrue(any("expected 2 purchase" in p for p in analysis.acceptance_problems(summary)))

    def test_any_recorded_problem_fails_acceptance(self):
        _, summary = self.run_level(1)
        summary["transactions"][0]["problems"] = [{"code": "CANCEL_REFUSED", "detail": "x"}]
        self.assertTrue(any("CANCEL_REFUSED" in p for p in analysis.acceptance_problems(summary)))


class Durability(Case):
    """Finding 2: with wait_durability, a settlement that never becomes (or stops being) durable cannot pass."""

    def test_durability_never_observed(self):
        _, summary = self.run_level(1, never_durable=True)
        self.assertNotEqual(summary["result"], "PASS")
        self.assertEqual(summary["stopReason"]["code"], "DURABILITY_NOT_OBSERVED")
        self.assertEqual(summary["transactions"][0]["outcome"], "SETTLED_NOT_DURABLE")

    def test_durability_lost_after_first_observation(self):
        _, summary = self.run_level(1, durability_lost_after_seen=True)
        self.assertNotEqual(summary["result"], "PASS")
        self.assertIn("DURABILITY_LOST", self.codes(summary))

    def test_durable_settlement_passes(self):
        _, summary = self.run_level(1)
        self.assertEqual(summary["result"], "PASS")
        tx = summary["transactions"][0]
        self.assertTrue(tx["durabilityRequired"])
        self.assertTrue(tx["durabilityObserved"])

    def test_no_wait_durability_is_explicit(self):
        _, summary = self.run_level(1, wait_durability=False, never_durable=True)
        self.assertEqual(summary["result"], "PASS", summary["stopReasons"])
        self.assertFalse(summary["transactions"][0]["durabilityRequired"])
        self.assertFalse(summary["acceptance"]["durabilityRequired"])


class LostBuyResponse(Case):
    """Finding 4: an accepted buy whose response is lost is recovered with the same key, never a new one."""

    def test_lost_response_after_server_acceptance(self):
        harness, summary = self.run_level(1, lose_buy_responses=1)
        self.assertEqual(summary["result"], "PASS", summary["stopReasons"])
        self.assertEqual(len(harness.exchange.orders), 1, "no second purchase")
        self.assertEqual(len(set(harness.exchange.buy_keys)), 1, "one key for the logical buy")
        self.assertEqual(summary["transactions"][0]["orderId"], next(iter(harness.exchange.orders)))

    def test_correlation_material_never_reaches_evidence(self):
        harness, summary = self.run_level(1, lose_buy_responses=1)
        key = harness.exchange.buy_keys[0]
        self.assertNotIn(key, json.dumps(summary))
        events = Path(harness.run.events.path).read_text()
        record = json.loads(next(harness.correlation_dir.glob("*.json")).read_text())
        for secret in (record["sessionHash"], record["idempotencyKey"]):
            self.assertNotIn(secret, events)
            self.assertNotIn(secret, json.dumps(summary))
        self.assertNotIn(key, events)

    def test_record_is_written_before_the_buy_and_kept_private(self):
        harness, summary = self.run_level(1)
        (path,) = harness.correlation_dir.glob("*.json")
        record = json.loads(path.read_text())
        self.assertEqual(record["status"], "ACCEPTED")
        self.assertEqual(record["orderId"], summary["transactions"][0]["orderId"])
        self.assertEqual(record["idempotencyKey"], harness.exchange.buy_keys[0])
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(harness.correlation_dir.stat().st_mode & 0o777), "0o700")
        self.assertEqual([h["to"] for h in record["history"]], ["ACCEPTED"])

    def test_repeated_submissions_use_one_identical_key(self):
        harness, _ = self.run_level(2, lose_buy_responses=2)
        by_buyer: dict[str, set] = {}
        for order in harness.exchange.orders.values():
            by_buyer.setdefault(order["buyer"], set()).add(order["key"])
        self.assertEqual(len(harness.exchange.orders), 2)
        self.assertTrue(all(len(keys) == 1 for keys in by_buyer.values()))
        self.assertEqual(len(set(harness.exchange.buy_keys)), 2, "one key per logical buy, never a replacement")

    def test_unresolvable_result_fails_closed_without_another_purchase(self):
        harness, summary = self.run_level(1, buy_transport_down=True, orders_down=True)
        self.assertEqual(summary["result"], "PARTIAL")
        self.assertEqual(summary["stopReason"]["code"], "PURCHASE_AMBIGUOUS")
        self.assertEqual(summary["transactions"][0]["outcome"], "PURCHASE_AMBIGUOUS")
        self.assertEqual(len(harness.exchange.orders), 1)
        self.assertEqual(len(set(harness.exchange.buy_keys)), 1)
        self.assertLessEqual(len(harness.exchange.buy_keys), 3)
        self.assertNotIn("confirm", harness.exchange.calls)
        (record,) = CorrelationStore.all_records(harness.correlation_dir)
        self.assertEqual(record["status"], "AMBIGUOUS")

    def test_lookup_recovers_an_accepted_buy_after_transport_loss(self):
        harness, summary = self.run_level(1, buy_transport_down=True)
        self.assertEqual(summary["transactions"][0]["orderId"], next(iter(harness.exchange.orders)))
        self.assertIn("purchaseRecoveredByLookup", summary["transactions"][0]["timeline"])
        (record,) = CorrelationStore.all_records(harness.correlation_dir)
        self.assertEqual((record["status"], record["orderId"]), ("ACCEPTED", next(iter(harness.exchange.orders))))

    def test_crash_then_reconcile_recovers_by_lookup_only(self):
        harness, summary = self.run_level(1, buy_transport_down=True, orders_down=True)
        # The harness has gone (crash or PARTIAL exit); later, with the portal reachable again:
        harness.exchange.faults.pop("orders_down")
        calls = len(harness.exchange.calls)
        results = recover(harness.correlation_dir, "run-test", harness.exchange, sleep=lambda s: None)
        self.assertEqual([(r["status"], r["orderId"]) for r in results],
                         [("ACCEPTED", next(iter(harness.exchange.orders)))])
        self.assertEqual(harness.exchange.calls[calls:], ["orders"], "recovery never submits a purchase")
        self.assertNotIn("sessionHash", json.dumps(results))
        self.assertNotIn(harness.exchange.buy_keys[0], json.dumps(results))

    def test_crash_before_any_answer_with_no_server_order(self):
        harness, _ = self.run_level(1)
        store = CorrelationStore(harness.correlation_dir, "run-crash")
        store.begin("P1", account="judge-00000000000000000000", session="s" * 64, key="bench-" + "0" * 24,
                    offer_id="o" * 32, instrument="zwa-gold-reference", quantity=1)
        harness.exchange.wallets["judge-00000000000000000000"] = {"session": "s" * 64, "state": "READY"}
        (result,) = recover(harness.correlation_dir, "run-crash", harness.exchange, sleep=lambda s: None)
        self.assertEqual(result["status"], "NOT_ACCEPTED")

    def test_refusal_after_acceptance_keeps_the_order_and_never_confirms(self):
        harness = Harness(1, Path(tempfile.mkdtemp(dir=self.path)), lose_buy_responses=1)
        original = harness.exchange._portal
        seen = []

        def portal(action, actor, role, session, payload):
            if action == "buy":
                seen.append(1)
                if len(seen) == 2:
                    raise PortalRefused("injected refusal of the resend")
            return original(action, actor, role, session, payload)
        harness.exchange._portal = portal
        summary = harness.execute()
        self.assertNotEqual(summary["result"], "PASS")
        self.assertEqual(len(harness.exchange.orders), 1)
        self.assertNotIn("confirm", harness.exchange.calls)
        (record,) = CorrelationStore.all_records(harness.correlation_dir)
        self.assertEqual((record["status"], record["orderId"]), ("ACCEPTED", next(iter(harness.exchange.orders))))
        self.assertEqual(next(iter(harness.exchange.orders.values()))["state"], "CANCELLED")

    def test_records_survive_and_cannot_be_downgraded_or_reassigned(self):
        harness, summary = self.run_level(1)
        store = CorrelationStore(harness.correlation_dir, "run-test")
        with self.assertRaises(CorrelationError):
            store.update("P1", status="AMBIGUOUS")
        with self.assertRaises(CorrelationError):
            store.update("P1", status="ACCEPTED", order_id="f" * 32)
        with self.assertRaises(FileExistsError):
            store.begin("P1", account="a", session="s" * 64, key="k" * 20, offer_id="o", instrument="i", quantity=1)


if __name__ == "__main__":
    unittest.main()
