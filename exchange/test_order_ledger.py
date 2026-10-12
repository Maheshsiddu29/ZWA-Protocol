"""Durable offer allocation, idempotency, and exact quote regressions."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest

from checkout.api import recorded
from exchange.order_ledger import OrderError, OrderLedger


class OfferLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="zwa4e-ledger-", dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.ledger = OrderLedger(Path(self.temp.name) / "exchange.sqlite")
        self.quote, _ = recorded()
        self.offer = self.ledger.create_offer(
            seller_id="seller", instrument_id="gold", asset_base=self.quote["assetId"],
            chain_genesis="a" * 64, scan_height=113, scan_anchor="b" * 64,
            inventory_units=7, capacity=7, idempotency_key="seller-gold")

    def test_offer_allocation_rejects_double_listing_and_idempotency_mismatch(self):
        same = self.ledger.create_offer(
            seller_id="seller", instrument_id="gold", asset_base=self.quote["assetId"],
            chain_genesis="a" * 64, scan_height=113, scan_anchor="b" * 64,
            inventory_units=7, capacity=7, idempotency_key="seller-gold")
        self.assertEqual(same["id"], self.offer["id"])
        with self.assertRaises(OrderError):
            self.ledger.create_offer(
                seller_id="seller", instrument_id="gold", asset_base=self.quote["assetId"],
                chain_genesis="a" * 64, scan_height=113, scan_anchor="b" * 64,
                inventory_units=7, capacity=1, idempotency_key="seller-gold")
        with self.assertRaises(OrderError):
            self.ledger.create_offer(
                seller_id="seller", instrument_id="gold", asset_base=self.quote["assetId"],
                chain_genesis="a" * 64, scan_height=113, scan_anchor="b" * 64,
                inventory_units=7, capacity=1, idempotency_key="another-offer")

    def test_concurrent_requests_cannot_reserve_same_units(self):
        def request(index):
            try:
                return self.ledger.request_purchase(
                    offer_id=self.offer["id"], buyer_id=f"buyer{index}",
                    quantity=5, idempotency_key=f"request{index}",
                    chain_genesis="a" * 64)["id"]
            except OrderError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(request, (1, 2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        with self.ledger._connection() as db:
            reserved = db.execute("SELECT reserved FROM offers WHERE id=?", (self.offer["id"],)).fetchone()[0]
        self.assertEqual(reserved, 5)

    def test_request_quote_and_preapproval_cancel_are_durable(self):
        order = self.ledger.request_purchase(
            offer_id=self.offer["id"], buyer_id="buyer", quantity=7,
            idempotency_key="buy-once", chain_genesis="a" * 64)
        repeated = self.ledger.request_purchase(
            offer_id=self.offer["id"], buyer_id="buyer", quantity=7,
            idempotency_key="buy-once", chain_genesis="a" * 64)
        self.assertEqual(order["id"], repeated["id"])
        with self.assertRaises(OrderError):
            self.ledger.request_purchase(
                offer_id=self.offer["id"], buyer_id="buyer", quantity=1,
                idempotency_key="buy-once", chain_genesis="a" * 64)
        with self.assertRaises(OrderError):
            self.ledger.request_purchase(
                offer_id=self.offer["id"], buyer_id="buyer", quantity=7,
                idempotency_key="buy-once", chain_genesis="c" * 64)
        with self.assertRaises(OrderError):
            self.ledger.attach_quote(order["id"], {**self.quote, "quantity": 6},
                                     observed_height=self.quote["targetHeight"] - 1)
        with self.assertRaises(OrderError):
            self.ledger.attach_quote(order["id"], self.quote,
                                     observed_height=self.quote["targetHeight"])
        ready = self.ledger.attach_quote(order["id"], self.quote,
                                         observed_height=self.quote["targetHeight"] - 1)
        self.assertEqual(ready["state"], "QUOTE_READY")
        self.assertEqual(ready["quote_digest"], self.quote["quoteDigest"])
        self.assertEqual(self.ledger.attach_quote(order["id"], self.quote,
                                                  observed_height=self.quote["targetHeight"] - 1)["id"], order["id"])
        with self.assertRaises(OrderError):
            self.ledger.close_before_approval(order["id"], buyer_id="stranger", state="CANCELLED")
        closed = self.ledger.close_before_approval(order["id"], buyer_id="buyer", state="CANCELLED")
        self.assertEqual(closed["state"], "CANCELLED")
        self.assertEqual(self.ledger.close_before_approval(order["id"], buyer_id="buyer", state="CANCELLED")["state"], "CANCELLED")
        with self.ledger._connection() as db:
            events = db.execute("SELECT sequence,to_state FROM order_events WHERE order_id=? ORDER BY sequence", (order["id"],)).fetchall()
            reserved = db.execute("SELECT reserved FROM offers WHERE id=?", (self.offer["id"],)).fetchone()[0]
        self.assertEqual([(row[0], row[1]) for row in events],
                         [(1, "BUYER_REQUESTED"), (2, "QUOTE_READY"), (3, "CANCELLED")])
        self.assertEqual(reserved, 0)


if __name__ == "__main__":
    unittest.main()
