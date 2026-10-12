"""Durable offer allocation, idempotency, and exact quote regressions."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import tempfile
import time
import unittest

from checkout.api import recorded
from checkout.quote import digest as quote_digest
from exchange.order_ledger import OrderError, OrderLedger


def priced_quote(base: dict, price: int) -> dict:
    """The recorded quote re-priced at an exact per-unit seller price."""
    quote = {k: v for k, v in base.items() if k != "quoteDigest"}
    quote["paymentAmount"] = quote["maximumTotalDebit"] = base["quantity"] * price
    return {**quote, "quoteDigest": quote_digest(quote)}


def pair(ledger: OrderLedger, role: str, account: str, address: str, code: str,
         session: str = "5" * 64) -> dict:
    request = ledger.create_pairing_request(role=role, wallet_address=address, code=code,
                                            companion_pid=os.getpid())
    ledger.claim_pairing(code=code, account_id=account, role=role, session_hash=session)
    return ledger.confirm_pairing(request["id"], companion_pid=os.getpid())


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

    def _confirmed(self, key: str) -> tuple[dict, dict]:
        """A browser order quoted at the seller's price and confirmed by the buyer."""
        quote = priced_quote(self.quote, 1000)
        buyer = pair(self.ledger, "buyer", "buyer", "b" * 86, "BUYERCODE2")
        seller = pair(self.ledger, "seller", "seller", "c" * 86, "SELLRCODE2")
        price = self.ledger.set_price(seller_id="seller", offer_id=self.offer["id"],
                                      price_zat_per_unit=1000)
        order = self.ledger.request_purchase(
            offer_id=self.offer["id"], buyer_id="buyer", quantity=quote["quantity"],
            idempotency_key=key, chain_genesis="a" * 64)
        self.ledger.attach_quote(order["id"], quote, observed_height=quote["targetHeight"] - 1, meta={
            "termsSha256": "9" * 64, "priceZatPerUnit": 1000, "priceVersion": price["version"],
            "buyerPairingId": buyer["id"], "sellerPairingId": seller["id"],
            "validUntil": int(time.time()) + 600})
        self.ledger.confirm_quote(order["id"], buyer_id="buyer", quote_digest=quote["quoteDigest"],
                                  pairing_id=buyer["id"])
        return order, quote

    def test_exact_order_lifecycle_replay_and_observed_wallet_effects(self):
        order, quote = self._confirmed("lifecycle")
        digest = quote["quoteDigest"]
        evidence = [
            ("BUYER_CONFIRMED", "PROOF_VERIFIED", {"quoteDigest":digest,
                "tradeCommitment":"123", "verifierResultSha256":"c"*64}),
            ("PROOF_VERIFIED", "TRADE_AUTHORIZED", {"quoteDigest":digest,
                "assetBase":quote["assetId"], "buyerId":"buyer", "sellerId":"seller",
                "tipHeight":quote["targetHeight"]-1}),
            ("TRADE_AUTHORIZED", "BUYER_APPROVED", {"quoteDigest":digest,
                "role":"buyer", "approvalMode":"SCRIPTED_TEST_MODE", "signerLogSha256":"d"*64}),
            ("BUYER_APPROVED", "SELLER_APPROVED", {"quoteDigest":digest,
                "role":"seller", "approvalMode":"SCRIPTED_TEST_MODE", "signerLogSha256":"e"*64}),
            ("SELLER_APPROVED", "SUBMITTED", {"quoteDigest":digest,"txid":"f"*64}),
            ("SUBMITTED", "INCLUDED", {"txid":"f"*64,"height":quote["targetHeight"],
                "blockHash":"1"*64}),
            ("INCLUDED", "RECONCILED", {"txid":"f"*64,
                "buyerUnitsBefore":0,"buyerUnitsAfter":quote["quantity"],
                "sellerUnitsBefore":7,"sellerUnitsAfter":7-quote["quantity"],
                "sellerZatBefore":0,"sellerZatAfter":quote["paymentAmount"]}),
            ("RECONCILED", "RECEIPT_AVAILABLE", {"quoteDigest":digest,"txid":"f"*64,
                "height":quote["targetHeight"],"blockHash":"1"*64,
                "approvalMode":"SCRIPTED_TEST_MODE"}),
        ]
        for prior, state, observed in evidence:
            if state == "BUYER_APPROVED":
                self.ledger.start_execution(order["id"], approval_mode="SCRIPTED_TEST_MODE")
            result = self.ledger.transition(order["id"], expected=prior,
                                            state=state, evidence=observed)
            self.assertEqual(result["state"], state)
            self.assertEqual(self.ledger.transition(order["id"], expected=prior,
                                                    state=state, evidence=observed)["state"],state)
            with self.assertRaises(OrderError):
                self.ledger.transition(order["id"], expected=prior,
                                       state=state, evidence={**observed,"replay":True})
        with self.assertRaises(OrderError):
            self.ledger.close_before_approval(order["id"], buyer_id="buyer", state="CANCELLED")
        detail = self.ledger.get_order(order["id"], participant_id="seller")
        self.assertEqual(len(detail["events"]),11)
        self.assertEqual(len(detail["evidence"]),9)
        self.assertEqual(detail["reserved"],0)
        self.assertEqual(detail["sold"],quote["quantity"])
        with self.assertRaises(OrderError):
            self.ledger.get_order(order["id"], participant_id="stranger")

    def test_rejects_wrong_approval_and_unreconciled_effects(self):
        order, quote = self._confirmed("negative")
        with self.assertRaises(OrderError):
            # A server quote cannot skip the buyer's explicit confirmation.
            self.ledger.transition(order["id"],expected="QUOTE_READY",state="PROOF_VERIFIED",
                                   evidence={"quoteDigest":quote["quoteDigest"],
                                             "tradeCommitment":"123","verifierResultSha256":"c"*64})
        self.ledger.transition(order["id"],expected="BUYER_CONFIRMED",state="PROOF_VERIFIED",
                               evidence={"quoteDigest":quote["quoteDigest"],
                                         "tradeCommitment":"123","verifierResultSha256":"c"*64})
        with self.assertRaises(OrderError):
            self.ledger.transition(order["id"],expected="PROOF_VERIFIED",state="TRADE_AUTHORIZED",
                                   evidence={"quoteDigest":quote["quoteDigest"],
                                             "assetBase":"0"*64,"buyerId":"buyer",
                                             "sellerId":"seller","tipHeight":quote["targetHeight"]-1})
        self.assertEqual(self.ledger.get_order(order["id"],participant_id="buyer")["state"],
                         "PROOF_VERIFIED")
        self.ledger.transition(order["id"],expected="PROOF_VERIFIED",state="TRADE_AUTHORIZED",
                               evidence={"quoteDigest":quote["quoteDigest"],"assetBase":quote["assetId"],
                                         "buyerId":"buyer","sellerId":"seller",
                                         "tipHeight":quote["targetHeight"]-1})
        self.ledger.start_execution(order["id"], approval_mode="HUMAN_APPROVED")
        with self.assertRaises(OrderError):
            # A scripted signer cannot stand in for a human-mode execution.
            self.ledger.transition(order["id"],expected="TRADE_AUTHORIZED",state="BUYER_APPROVED",
                                   evidence={"quoteDigest":quote["quoteDigest"],"role":"buyer",
                                             "approvalMode":"SCRIPTED_TEST_MODE","signerLogSha256":"d"*64})


if __name__ == "__main__":
    unittest.main()
