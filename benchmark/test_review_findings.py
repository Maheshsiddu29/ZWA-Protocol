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


if __name__ == "__main__":
    unittest.main()
