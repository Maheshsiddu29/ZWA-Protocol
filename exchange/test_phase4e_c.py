"""Phase 4E-C regressions for pairing, quoting, authorization and execution.

Every test uses its own disposable /private/tmp/zwa4e-test-* root with fake
tool runners. No test touches a real chain, wallet, or the protected root.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from checkout.quote import from_terms
from exchange import config as config_module, portal as portal_module
from exchange.config import ConfigError, ExchangeConfig
from exchange.order_ledger import OrderError, OrderLedger
from exchange.order_service import OrderService, ServiceError
from exchange.portal import Portal, PortalError
from exchange.tools import ToolError

ASSETS = {"zwa-gold-reference": "1" * 64, "zwa-treasury-style": "2" * 64,
          "zwa-credit-participation": "3" * 64}
BUYER_ADDR, SELLER_ADDR = "b" * 86, "c" * 86
SESSION = "5" * 64
GENESIS = "a" * 64


def make_terms(asset, quantity, payment, buyer, seller, sponsor, target):
    return {"version": 1, "network": "regtest", "consensus_branch_id": 2290649224,
            "asset_base": asset, "quantity": quantity, "price_zat": payment,
            "matcher_fee_zat": 0, "network_fee_zat": 5000, "buyer_receive_address": buyer,
            "seller_proceeds_address": seller, "fee_sponsor_txid": sponsor,
            "fee_sponsor_index": 0, "fee_sponsor_input_zat": 625_000_000,
            "fee_sponsor_change_zat": 624_995_000, "target_height": target,
            "tx_expiry_height": target + 40, "group_expiry_height": target + 10,
            "nonce": secrets.token_hex(32)}


class FakeTools:
    """Deterministic stand-in for the pinned tool, verifier and RPC."""

    def __init__(self):
        self.height = 200
        self.state = "CHAIN_ACTIVE"
        self.wallets = {"buyer": [1_000_000, 0], "seller": [0, 100]}
        self.verify_rc = 0
        self.review_fail = None
        self.asset_override = None
        self.spawned = []
        self.block = None

    def chain(self):
        return {"state": self.state, "height": self.height, "anchorHeight": 100, "anchorHash": "d" * 64}

    def signer(self, role, *args, timeout=0):
        if args[0] == "balances":
            native, units = self.wallets[role]
            return [f"PHASE35F_BALANCES role={role.title()} scanned_height={self.height} "
                    f"native_zat={native} asset={args[2]} asset_units={units}"]
        if args[0] == "review":
            if self.review_fail == role:
                raise ToolError(f"{role} wallet review failed")
            return [f"PHASE35F_REVIEW role={role.title()} terms_hash=x"]
        raise ToolError("unexpected signer action")

    def coordinator(self, *args, timeout=0):
        if args[0] == "prepare-sale":
            opts = dict(zip(args[1::2], args[2::2]))
            asset = self.asset_override or opts["--asset"]
            sponsor = {"6": "6" * 64, "7": "7" * 64}[opts["--fee-coinbase-height"]]
            terms = make_terms(asset, int(opts["--quantity"]), int(opts["--price-zat"]),
                               opts["--buyer-address"], opts["--seller-address"], sponsor, self.height + 1)
            Path(opts["--out"]).write_text(json.dumps(terms))
            quote = from_terms(terms)
            Path(opts["--session-result"]).write_text(json.dumps({
                "version": 1, "sessionId": quote["quoteDigest"], "source": "LIVE_LOCAL_CLI",
                "outcome": "QUOTE_READY", "quote": quote, "receipt": None}))
            return ["PHASE35F_TERMS_PREPARED terms_hash=" + quote["quoteDigest"]]
        if args[0] == "mine-empty":
            self.height += int(args[2])
            return ["PHASE35F_MINED_EMPTY"]
        raise ToolError("unexpected coordinator action")

    def authorization(self, action, *args, timeout=0):
        if action == "generate":
            out = Path(args[3])
            out.mkdir(mode=0o700)
            (out / "trust.json").write_text('{"trust":1}')
            (out / "authorization.json").write_text('{"proof":1}')
            return subprocess.CompletedProcess([], 0, "PHASE4C1_PROOF_GENERATED trade=1\n", "")
        digest = args[1]
        result = {"version": 2, "issuerAuthentication": "VERIFIED", "issuanceProvenance": "VERIFIED",
                  "credentialAuthorityAuthentication": "VERIFIED", "investorEligibility": "VERIFIED",
                  "exactTradeAuthorization": "VERIFIED", "quoteDigest": digest,
                  "tradeCommitment": "12345", "rootedNoteLineage": "UNSUPPORTED"}
        if self.verify_rc:
            return subprocess.CompletedProcess([], 1, "", "PHASE4C1_AUTHORIZATION_REJECTED: Groth16 proof rejected")
        return subprocess.CompletedProcess([], 0, json.dumps(result), "")

    def mature_sponsor_coinbases(self, tip):
        return [(6, "6" * 64), (7, "7" * 64)]

    def coinbase_txid(self, height):
        return {6: "6" * 64, 7: "7" * 64}[height]

    def spawn_execute_live(self, args, log):
        log.write_text("")
        self.spawned.append(args)
        return SimpleNamespace(pid=4242)

    def rpc(self, method, params=None):
        if method == "getblock":
            return {"tx": [self.block["txid"]], "hash": self.block["hash"]}
        if method == "getrawtransaction":
            return {"height": self.block["height"], "hex": "00" * self.block["bytes"]}
        raise ToolError("unexpected RPC")


class Phase4ECBase(unittest.TestCase):
    def setUp(self):
        self.root = Path("/private/tmp/zwa4e-test-" + secrets.token_hex(6))
        self.root.mkdir(mode=0o700)
        self.addCleanup(shutil.rmtree, self.root, True)
        exchange = self.root / "exchange"
        exchange.mkdir(mode=0o700)
        (exchange / "instrument-registry.json").write_text(json.dumps({
            "version": 1, "network": "regtest", "instruments": [
                {"id": key, "asset_base": value, "name": key, "issuer_id": "zwa-disposable-issuer",
                 "issuance_txid": "e" * 64, "issuance_height": 5} for key, value in ASSETS.items()]}))
        (self.root / "chain-identity.json").write_text(json.dumps({
            "version": 1, "genesisHash": GENESIS, "anchorHeight": 100, "anchorHash": "d" * 64}))
        self.config = ExchangeConfig(root=self.root, container="zwa-test", rpc_port=18999,
                                     approval_mode="SCRIPTED_TEST_MODE",
                                     tx_tool_dir=Path("/nonexistent"), params_data_home=Path("/nonexistent"),
                                     node_bin=Path("/usr/bin/false"))
        self.ledger = OrderLedger(self.config.ledger_path)
        self.tools = FakeTools()
        self.events = []
        self.service = OrderService(self.config, ledger=self.ledger, tools=self.tools, log=self.events.append)
        self.alive = True
        self.service._coordinator_alive = lambda pid: self.alive
        self.service._terminate = lambda pid: setattr(self, "terminated", pid)
        self.offer = self.ledger.create_offer(
            seller_id="seller", instrument_id="zwa-gold-reference", asset_base=ASSETS["zwa-gold-reference"],
            chain_genesis=GENESIS, scan_height=100, scan_anchor="d" * 64, inventory_units=100,
            capacity=100, idempotency_key="seller-gold")

    def pair(self, role, account, address, code, session=SESSION):
        request = self.ledger.create_pairing_request(role=role, wallet_address=address, code=code,
                                                     companion_pid=os.getpid())
        self.ledger.claim_pairing(code=code, account_id=account, role=role, session_hash=session)
        return self.ledger.confirm_pairing(request["id"], companion_pid=os.getpid())

    def ready(self, key="order-1", quantity=1, price=1000):
        self.buyer = self.pair("buyer", "buyer", BUYER_ADDR, "BUYERAAAA2")
        self.seller = self.pair("seller", "seller", SELLER_ADDR, "SELLRAAAA2")
        self.ledger.set_price(seller_id="seller", offer_id=self.offer["id"], price_zat_per_unit=price)
        order = self.ledger.request_purchase(offer_id=self.offer["id"], buyer_id="buyer", quantity=quantity,
                                             idempotency_key=key, chain_genesis=GENESIS)
        self.service.tick()
        return self.ledger.get_order(order["id"], participant_id="buyer")

    def state(self, order_id):
        return self.ledger.get_order(order_id, participant_id="buyer")["state"]

    def reserved(self):
        return self.ledger.get_offer(self.offer["id"])["reserved"]

    def authorized(self):
        order = self.ready()
        self.ledger.confirm_quote(order["id"], buyer_id="buyer", quote_digest=order["quote_digest"],
                                  pairing_id=self.buyer["id"])
        self.service.tick()
        return self.ledger.get_order(order["id"], participant_id="buyer")

    def online(self):
        for pairing in (self.buyer, self.seller):
            self.ledger.heartbeat(pairing["id"], pid=os.getpid(), mode="SCRIPTED_TEST_MODE")

    def attest(self, order_id, role, result="AUTHORIZED", mode="SCRIPTED_TEST_MODE"):
        work = self.config.order_dir(order_id)
        terms = json.loads((work / "quote/terms.json").read_text())
        quote = from_terms(terms)
        effects = (f"{quote['assetId']}:{quote['quantity']},native:-{quote['paymentAmount']}" if role == "buyer"
                   else f"{quote['assetId']}:-{quote['quantity']},native:{quote['paymentAmount']}")
        line = (f"PHASE35F_AUTHORIZED role={role.title()} terms_hash={quote['quoteDigest']} "
                f"action_group_commitment={'9' * 64} actions=2 own_inputs_spent=1 own_effects={effects} "
                "group_native_balance_zat=0 group_expiry_height=211 proof=verified spend_auth=verified")
        (work / f"{role}-signer.log").write_text(line + "\n" if result == "AUTHORIZED" else
                                                 f"PHASE35F_SIGNER_REJECTED role={role.title()} reason=x\n")
        pairing = self.buyer if role == "buyer" else self.seller
        (work / f"{role}-approval.json").write_text(json.dumps({
            "version": 1, "orderId": order_id, "role": role, "quoteDigest": quote["quoteDigest"],
            "pairingId": pairing["id"], "mode": mode, "signerCommand": "authorize-live --scripted-test-mode",
            "controllingTerminal": False, "result": result, "signerExit": 0 if result == "AUTHORIZED" else 2,
            "finishedAt": int(time.time())}))
        return quote

    def coordinator_exit(self, order_id, outcome, quote):
        work = self.config.order_dir(order_id)
        receipt = None
        if outcome == "ACCEPTED":
            receipt = {"terms_hash": quote["quoteDigest"], "txid": "f" * 64, "height": quote["targetHeight"],
                       "block_hash": "0" * 63 + "1", "tx_bytes": 3000}
            (work / "receipt.json").write_text(json.dumps(receipt))
            (work / "coordinator.log.raw").write_text(
                f"noise\nPHASE35G_EXECUTED txid={'f' * 64} height={quote['targetHeight']} "
                f"rpc_block_hash={'0' * 63}1 signed_groups_persisted=false\n")
            self.tools.block = {"txid": "f" * 64, "hash": "0" * 63 + "1", "height": quote["targetHeight"], "bytes": 3000}
            self.tools.height = quote["targetHeight"]
            self.tools.wallets["buyer"] = [1_000_000 - quote["paymentAmount"], quote["quantity"]]
            self.tools.wallets["seller"] = [quote["paymentAmount"], 100 - quote["quantity"]]
        (work / "quote/session-result.json").write_text(json.dumps({
            "version": 1, "sessionId": quote["quoteDigest"], "source": "LIVE_LOCAL_CLI",
            "outcome": outcome, "quote": quote, "receipt": receipt}))
        self.alive = False


class PairingTests(Phase4ECBase):
    def test_pairing_requires_matching_role_live_code_and_companion_confirmation(self):
        request = self.ledger.create_pairing_request(role="buyer", wallet_address=BUYER_ADDR,
                                                     code="ABCDEFGHIJ", companion_pid=os.getpid())
        with self.assertRaises(OrderError):  # wrong wallet role for this account
            self.ledger.claim_pairing(code="ABCDEFGHIJ", account_id="seller", role="seller", session_hash=SESSION)
        with self.assertRaises(OrderError):  # unknown code
            self.ledger.claim_pairing(code="ZZZZZZZZZZ", account_id="buyer", role="buyer", session_hash=SESSION)
        self.ledger.claim_pairing(code="ABCDEFGHIJ", account_id="buyer", role="buyer", session_hash=SESSION)
        with self.assertRaises(OrderError):  # single use
            self.ledger.claim_pairing(code="ABCDEFGHIJ", account_id="buyer", role="buyer", session_hash=SESSION)
        with self.assertRaises(OrderError):  # another process cannot confirm
            self.ledger.confirm_pairing(request["id"], companion_pid=os.getpid() + 1)
        pairing = self.ledger.confirm_pairing(request["id"], companion_pid=os.getpid())
        self.assertEqual(pairing["wallet_address"], BUYER_ADDR)
        self.assertIsNotNone(self.ledger.active_pairing(account_id="buyer", role="buyer", session_hash=SESSION))
        # A different browser session does not inherit the pairing.
        self.assertIsNone(self.ledger.active_pairing(account_id="buyer", role="buyer", session_hash="6" * 64))

    def test_expired_code_and_repairing_revokes_previous_wallet(self):
        request = self.ledger.create_pairing_request(role="buyer", wallet_address=BUYER_ADDR,
                                                     code="EXPIREDAA2", companion_pid=os.getpid())
        with self.ledger._transaction() as db:
            db.execute("UPDATE pairing_requests SET expires_at=? WHERE id=?", (int(time.time()) - 1, request["id"]))
        with self.assertRaises(OrderError):
            self.ledger.claim_pairing(code="EXPIREDAA2", account_id="buyer", role="buyer", session_hash=SESSION)
        first = self.pair("buyer", "buyer", BUYER_ADDR, "FIRSTAAAA2")
        second = self.pair("buyer", "buyer", "d" * 86, "SECONDAAA2")
        self.assertFalse(self.ledger.pairing_usable(first["id"]))
        self.assertTrue(self.ledger.pairing_usable(second["id"]))

    def test_scripted_mode_is_refused_for_protected_root(self):
        root = Path("/private/tmp/zwa4e-phase4e-b")
        with patch.object(config_module, "_root", return_value=root), \
             patch.object(Path, "is_file", return_value=True), \
             patch.object(Path, "is_symlink", return_value=False), \
             patch.object(Path, "stat", return_value=SimpleNamespace(st_size=10, st_mode=0o100600)), \
             patch.object(Path, "read_text", return_value=json.dumps({
                 "version": 1, "root": str(root), "container": "zwa-phase4e-b", "rpcPort": 18321,
                 "approvalMode": "SCRIPTED_TEST_MODE", "txToolDir": "/a", "paramsDataHome": "/b",
                 "nodeBin": "/c", "quoteValiditySeconds": 600, "approvalWaitSeconds": 300,
                 "companionStaleSeconds": 15, "stabilizationBlocks": 110})):
            with self.assertRaisesRegex(ConfigError, "forbidden on a protected root"):
                config_module.load(root)


class QuoteTests(Phase4ECBase):
    def test_server_quote_binds_paired_wallets_seller_price_and_fresh_tip(self):
        order = self.ready(quantity=2)
        self.assertEqual(order["state"], "QUOTE_READY")
        quote = self.ledger.order_quote(order["id"])
        terms = json.loads((self.config.order_dir(order["id"]) / "quote/terms.json").read_text())
        self.assertEqual((terms["buyer_receive_address"], terms["seller_proceeds_address"]), (BUYER_ADDR, SELLER_ADDR))
        self.assertEqual((terms["price_zat"], quote["price_zat_per_unit"], quote["classification"]),
                         (2000, 1000, "SELLER_FIXED_EXPERIMENTAL"))
        self.assertEqual(terms["target_height"], self.tools.height + 1)

    def test_quote_waits_for_price_and_both_pairings(self):
        order = self.ledger.request_purchase(offer_id=self.offer["id"], buyer_id="buyer", quantity=1,
                                             idempotency_key="no-price", chain_genesis=GENESIS)
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "BUYER_REQUESTED")
        self.assertIn("price", self.ledger.get_order(order["id"], participant_id="buyer")["note"]["note"])
        self.ledger.set_price(seller_id="seller", offer_id=self.offer["id"], price_zat_per_unit=1000)
        self.pair("buyer", "buyer", BUYER_ADDR, "BUYERBBBB2")
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "BUYER_REQUESTED")
        self.assertIn("seller", self.ledger.get_order(order["id"], participant_id="buyer")["note"]["note"])

    def test_insufficient_spendable_inventory_and_buyer_funds_reject_and_release(self):
        self.tools.wallets["seller"] = [0, 0]
        order = self.ready()
        self.assertEqual(order["state"], "REJECTED")
        self.assertEqual(self.reserved(), 0)
        self.tools.wallets["seller"] = [0, 100]
        self.tools.wallets["buyer"] = [500, 0]
        second = self.ledger.request_purchase(offer_id=self.offer["id"], buyer_id="buyer", quantity=1,
                                              idempotency_key="poor", chain_genesis=GENESIS)
        self.service.tick()
        self.assertEqual(self.state(second["id"]), "REJECTED")

    def test_overlapping_note_commitments_queue_second_order(self):
        first = self.ready("first")
        second = self.ledger.request_purchase(offer_id=self.offer["id"], buyer_id="buyer", quantity=1,
                                              idempotency_key="second", chain_genesis=GENESIS)
        self.service.tick()
        self.assertEqual(self.state(first["id"]), "QUOTE_READY")
        self.assertEqual(self.state(second["id"]), "BUYER_REQUESTED")
        self.ledger.close_before_approval(first["id"], buyer_id="buyer", state="CANCELLED")
        self.service.tick()
        self.assertEqual(self.state(second["id"]), "QUOTE_READY")

    def test_wrong_asset_from_preparation_is_never_attached(self):
        self.tools.asset_override = "4" * 64
        order = self.ready()
        self.assertEqual(order["state"], "BUYER_REQUESTED")
        self.assertIsNone(self.ledger.order_quote(order["id"]))

    def test_payment_must_equal_quantity_times_seller_price(self):
        order = self.ready()
        terms_path = self.config.order_dir(order["id"]) / "quote/terms.json"
        quote = from_terms(json.loads(terms_path.read_text()))
        other = self.ledger.request_purchase(offer_id=self.offer["id"], buyer_id="other", quantity=1,
                                             idempotency_key="k", chain_genesis=GENESIS)
        tampered = from_terms({**json.loads(terms_path.read_text()), "price_zat": 999,
                               "fee_sponsor_txid": "7" * 64})
        with self.assertRaisesRegex(OrderError, "committed|seller's fixed price"):
            self.ledger.attach_quote(other["id"], tampered, observed_height=self.tools.height, meta={
                "termsSha256": "9" * 64, "priceZatPerUnit": 1000, "priceVersion": 1,
                "buyerPairingId": self.buyer["id"], "sellerPairingId": self.seller["id"],
                "validUntil": int(time.time()) + 60})
        self.assertEqual(quote["paymentAmount"], 1000)

    def test_stale_quote_expires_and_releases(self):
        order = self.ready()
        self.tools.height += 1  # Another block makes the quote target stale.
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "EXPIRED")
        self.assertEqual(self.reserved(), 0)

    def test_buyer_confirmation_is_bound_to_digest_buyer_pairing_and_expiry(self):
        order = self.ready()
        for kwargs in ({"buyer_id": "buyer", "quote_digest": "0" * 64, "pairing_id": self.buyer["id"]},
                       {"buyer_id": "intruder", "quote_digest": order["quote_digest"], "pairing_id": self.buyer["id"]},
                       {"buyer_id": "buyer", "quote_digest": order["quote_digest"], "pairing_id": self.seller["id"]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(OrderError):
                self.ledger.confirm_quote(order["id"], **kwargs)
        with self.ledger._transaction() as db:
            db.execute("UPDATE order_quotes SET valid_until=? WHERE order_id=?", (int(time.time()) - 1, order["id"]))
        with self.assertRaisesRegex(OrderError, "expired"):
            self.ledger.confirm_quote(order["id"], buyer_id="buyer", quote_digest=order["quote_digest"],
                                      pairing_id=self.buyer["id"])


class AuthorizationTests(Phase4ECBase):
    def test_invalid_groth16_ends_purchase_before_any_signing_request(self):
        self.tools.verify_rc = 1
        order = self.authorized()
        self.assertEqual(order["state"], "REJECTED")
        self.assertIsNone(self.ledger.execution(order["id"]))
        self.assertEqual(self.tools.spawned, [])
        self.assertEqual(self.reserved(), 0)

    def test_wallet_review_rejection_prevents_execution(self):
        self.tools.review_fail = "seller"
        order = self.authorized()
        self.assertEqual(order["state"], "REJECTED")
        self.assertEqual(self.tools.spawned, [])

    def test_changed_terms_after_quote_block_authorization(self):
        order = self.ready()
        terms = self.config.order_dir(order["id"]) / "quote/terms.json"
        data = json.loads(terms.read_text())
        data["price_zat"] = 1
        terms.write_text(json.dumps(data))
        self.ledger.confirm_quote(order["id"], buyer_id="buyer", quote_digest=order["quote_digest"],
                                  pairing_id=self.buyer["id"])
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "BUYER_CONFIRMED")
        self.assertTrue(any("changed" in e.get("error", "") for e in self.events))

    def test_disconnected_companions_never_start_execution(self):
        order = self.authorized()
        self.assertEqual(order["state"], "TRADE_AUTHORIZED")
        self.service.tick()
        self.assertIsNone(self.ledger.execution(order["id"]))
        self.assertIn("companion", self.ledger.get_order(order["id"], participant_id="buyer")["note"]["note"])

    def test_unavailable_rpc_pauses_every_order(self):
        order = self.authorized()
        self.online()
        self.tools.state = "CHAIN_STOPPED"
        self.service.tick()
        self.assertIsNone(self.ledger.execution(order["id"]))


class ExecutionTests(Phase4ECBase):
    def started(self):
        order = self.authorized()
        self.online()
        self.service.tick()
        self.assertEqual(len(self.tools.spawned), 1)
        self.service.tick()
        self.assertEqual(len(self.tools.spawned), 1, "single execution guard")
        return order

    def test_complete_scripted_execution_reaches_receipt_and_durability(self):
        order = self.started()
        quote = self.attest(order["id"], "buyer")
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "BUYER_APPROVED")
        self.attest(order["id"], "seller")
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "SELLER_APPROVED")
        self.coordinator_exit(order["id"], "ACCEPTED", quote)
        with patch("exchange.order_service.persistent_chain.checkpoint",
                   return_value={"height": 311, "anchorHeight": 211, "anchorHash": "8" * 64}), \
             patch.object(OrderService, "rescan", return_value=self.root / "exchange/rescans/scan.x/portfolio.json"):
            self.service.tick()
        detail = self.ledger.get_order(order["id"], participant_id="seller")
        self.assertEqual(detail["state"], "RECEIPT_AVAILABLE")
        self.assertEqual((detail["reserved"], detail["sold"]), (0, 1))
        receipt = json.loads((self.config.order_dir(order["id"]) / "order-receipt.json").read_text())
        self.assertEqual((receipt["approvalMode"], receipt["priceClassification"]),
                         ("SCRIPTED_TEST_MODE", "SELLER_FIXED_EXPERIMENTAL"))
        stable = json.loads((self.config.order_dir(order["id"]) / "stabilization.json").read_text())
        self.assertEqual(stable["policy"], "RESTART_DURABLE_UNDER_PINNED_ZEBRA_POLICY_NOT_FINALITY")
        self.assertEqual(self.ledger.execution(order["id"])["outcome"], "ACCEPTED")

    def test_legacy_receipt_without_execution_never_triggers_mining(self):
        order = self.ready()
        with self.ledger._transaction() as db:  # A pre-4E-C settled order shape.
            db.execute("UPDATE orders SET state='RECEIPT_AVAILABLE' WHERE id=?", (order["id"],))
        (self.config.order_dir(order["id"]) / "order-receipt.json").write_text("{}")
        height = self.tools.height
        self.service.tick()
        self.assertEqual(self.tools.height, height)
        self.assertFalse((self.config.order_dir(order["id"]) / "stabilization.json").exists())

    def test_buyer_rejection_terminates_window_and_releases(self):
        order = self.started()
        self.attest(order["id"], "buyer", result="REJECTED")
        self.service.tick()
        self.assertEqual(self.terminated, 4242)
        self.alive = False
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "EXECUTION_FAILED")
        self.assertEqual(self.reserved(), 0)

    def test_seller_rejection_records_buyer_group_only_held_by_coordinator(self):
        order = self.started()
        self.attest(order["id"], "buyer")
        self.service.tick()
        self.attest(order["id"], "seller", result="REJECTED")
        self.service.tick()
        self.alive = False
        self.service.tick()
        detail = self.ledger.get_order(order["id"], participant_id="buyer")
        self.assertEqual(detail["state"], "EXECUTION_FAILED")
        failure = json.loads(detail["evidence"][-1]["value_json"])
        self.assertEqual(failure["reason"], "SELLER_WALLET_REJECTED")
        self.assertTrue(failure["buyerGroupDisclosedToCoordinatorOnly"])

    def test_signer_timeout_fails_closed(self):
        order = self.started()
        (self.config.order_dir(order["id"]) / "authorization-result.json").write_text("{}")
        quote = from_terms(json.loads((self.config.order_dir(order["id"]) / "quote/terms.json").read_text()))
        self.coordinator_exit(order["id"], "FAILED", quote)
        self.service.tick()
        detail = self.ledger.get_order(order["id"], participant_id="buyer")
        self.assertEqual(detail["state"], "EXECUTION_FAILED")
        self.assertEqual(json.loads(detail["evidence"][-1]["value_json"])["reason"],
                         "WALLET_APPROVAL_TIMEOUT_OR_COORDINATOR_FAILURE")

    def test_coordinator_crash_after_both_approvals_is_uncertain_and_keeps_reservation(self):
        order = self.started()
        quote = self.attest(order["id"], "buyer"); self.service.tick()
        self.attest(order["id"], "seller"); self.service.tick()
        self.coordinator_exit(order["id"], "QUOTE_READY", quote)
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "UNCERTAIN")
        self.assertEqual(self.reserved(), 1)
        with self.assertRaises(OrderError):
            self.ledger.close_before_approval(order["id"], buyer_id="buyer", state="CANCELLED")

    def test_attestation_for_another_mode_or_pairing_is_not_recorded(self):
        order = self.started()
        self.attest(order["id"], "buyer", mode="HUMAN_APPROVED")
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "TRADE_AUTHORIZED")
        self.assertTrue(any("another trade or mode" in e.get("error", "") for e in self.events))

    def test_missing_receipt_never_advances(self):
        order = self.started()
        quote = self.attest(order["id"], "buyer"); self.service.tick()
        self.attest(order["id"], "seller"); self.service.tick()
        self.coordinator_exit(order["id"], "ACCEPTED", quote)
        (self.config.order_dir(order["id"]) / "receipt.json").unlink()
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "SELLER_APPROVED")

    def test_wallet_scan_mismatch_after_inclusion_is_uncertain(self):
        order = self.started()
        quote = self.attest(order["id"], "buyer"); self.service.tick()
        self.attest(order["id"], "seller"); self.service.tick()
        self.coordinator_exit(order["id"], "ACCEPTED", quote)
        self.tools.wallets["buyer"][1] = 0  # Buyer did not actually receive the unit.
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "UNCERTAIN")
        self.assertEqual(self.reserved(), 1)

    def test_post_submission_failure_cannot_release_inventory(self):
        order = self.started()
        with self.assertRaises(OrderError):
            self.ledger.fail(order["id"], expected="SUBMITTED", state="EXECUTION_FAILED", release=True,
                             evidence={"reason": "x"})
        with self.assertRaises(OrderError):
            self.ledger.fail(order["id"], expected="TRADE_AUTHORIZED", state="UNCERTAIN", release=True,
                             evidence={"reason": "x"})


class PortalTests(Phase4ECBase):
    def portal(self):
        with patch.object(Portal, "__init__", lambda s, c: (setattr(s, "config", c), setattr(s, "ledger", self.ledger))[0]):
            portal = Portal(self.config)
        portal.live = lambda: (True, self.tools.height)
        return portal

    def test_unpaired_buyer_cannot_request_purchase(self):
        portal = self.portal()
        with self.assertRaisesRegex(PortalError, "pair your wallet"):
            portal.buy("buyer", SESSION, {"offerId": self.offer["id"], "quantity": 1, "idempotencyKey": "abcdefgh"})

    def test_cross_user_and_cross_role_order_access_is_rejected(self):
        order = self.ready()
        portal = self.portal()
        self.assertEqual(portal.order(order["id"], "buyer", "buyer")["viewer"], "buyer")
        self.assertEqual(portal.order(order["id"], "seller", "seller")["viewer"], "seller")
        with self.assertRaises(OrderError):
            portal.order(order["id"], "intruder", "buyer")
        with self.assertRaises(OrderError):
            portal.order(order["id"], "buyer", "seller")
        with self.assertRaises(OrderError):
            portal.cancel("seller", {"orderId": order["id"]})

    def test_bridge_rejects_role_action_mismatch_and_bad_shapes(self):
        for request in ({"action": "buy", "actor": "seller", "role": "seller", "sessionHash": SESSION, "payload": {}},
                        {"action": "set-price", "actor": "buyer", "role": "buyer", "sessionHash": SESSION, "payload": {}},
                        {"action": "order", "actor": "../x", "role": "buyer", "sessionHash": SESSION, "payload": {}},
                        {"action": "order", "actor": "buyer", "role": "buyer", "sessionHash": "x", "payload": {}},
                        {"action": "shell", "actor": "buyer", "role": "buyer", "sessionHash": SESSION, "payload": {}, "extra": 1}):
            with self.subTest(request=request["action"]), \
                 patch.object(portal_module, "load_config", return_value=self.config), \
                 patch.object(portal_module.Portal, "__init__", lambda s, c: (setattr(s, "config", c), setattr(s, "ledger", self.ledger))[0]):
                with self.assertRaises((PortalError, OrderError)):
                    portal_module.handle(request)

    def test_duplicate_buy_click_returns_same_order(self):
        self.ledger.set_price(seller_id="seller", offer_id=self.offer["id"], price_zat_per_unit=1000)
        self.pair("buyer", "buyer", BUYER_ADDR, "BUYERCCCC2")
        portal = self.portal()
        portal._portfolio_at_tip = lambda tip: {"holdings": [{"assetBase": ASSETS["zwa-gold-reference"], "sellerUnits": 100}]}
        payload = {"offerId": self.offer["id"], "quantity": 1, "idempotencyKey": "click-once-1"}
        first, second = portal.buy("buyer", SESSION, payload), portal.buy("buyer", SESSION, payload)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.reserved(), 1)


class MigrationTests(unittest.TestCase):
    def test_concurrent_first_open_creates_and_migrates_once(self):
        from concurrent.futures import ThreadPoolExecutor
        root = Path("/private/tmp/zwa4e-test-" + secrets.token_hex(6))
        root.mkdir(mode=0o700)
        self.addCleanup(shutil.rmtree, root, True)
        path = root / "exchange.sqlite"
        with ThreadPoolExecutor(max_workers=6) as pool:
            ledgers = list(pool.map(lambda _: OrderLedger(path), range(6)))
        self.assertEqual(len(ledgers), 6)
        db = sqlite3.connect(path)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)
        self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")

    def test_v2_ledger_rows_and_history_survive_v3_migration(self):
        root = Path("/private/tmp/zwa4e-test-" + secrets.token_hex(6))
        root.mkdir(mode=0o700)
        self.addCleanup(shutil.rmtree, root, True)
        path = root / "exchange.sqlite"
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        db = sqlite3.connect(path)
        db.executescript("""
            CREATE TABLE offers (id TEXT PRIMARY KEY, seller_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
              asset_base TEXT NOT NULL, chain_genesis TEXT NOT NULL, scan_height INTEGER NOT NULL,
              scan_anchor TEXT NOT NULL, inventory_at_scan INTEGER NOT NULL, capacity INTEGER NOT NULL,
              reserved INTEGER NOT NULL DEFAULT 0, sold INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL,
              idempotency_key TEXT NOT NULL, created_at INTEGER NOT NULL, UNIQUE(seller_id,idempotency_key));
            CREATE TABLE orders (id TEXT PRIMARY KEY, offer_id TEXT NOT NULL REFERENCES offers(id),
              buyer_id TEXT NOT NULL, quantity INTEGER NOT NULL, state TEXT NOT NULL CHECK(state IN
              ('BUYER_REQUESTED','QUOTE_READY','RECEIPT_AVAILABLE')), quote_digest TEXT UNIQUE,
              quote_expiry_height INTEGER, idempotency_key TEXT NOT NULL, created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL, UNIQUE(buyer_id,idempotency_key));
            CREATE TABLE order_events (order_id TEXT NOT NULL REFERENCES orders(id), sequence INTEGER NOT NULL,
              from_state TEXT, to_state TEXT NOT NULL, occurred_at INTEGER NOT NULL, PRIMARY KEY(order_id,sequence));
            CREATE TABLE order_evidence (order_id TEXT NOT NULL REFERENCES orders(id), sequence INTEGER NOT NULL,
              kind TEXT NOT NULL, value_json TEXT NOT NULL, PRIMARY KEY(order_id,sequence));
            CREATE TABLE order_settlements (order_id TEXT PRIMARY KEY REFERENCES orders(id),
              txid TEXT NOT NULL UNIQUE, quote_digest TEXT NOT NULL UNIQUE);
            INSERT INTO offers VALUES ('o','seller','gold','1','a',1,'b',10,10,0,1,'OFFER_CREATED','k',1);
            INSERT INTO orders VALUES ('x','o','buyer',1,'RECEIPT_AVAILABLE','q',41,'k',1,2);
            INSERT INTO order_events VALUES ('x',1,NULL,'BUYER_REQUESTED',1);
            INSERT INTO order_evidence VALUES ('x',1,'BUYER_REQUESTED','{}');
            INSERT INTO order_settlements VALUES ('x','t','q');
            PRAGMA user_version=2;
        """)
        db.close()
        OrderLedger(path)
        db = sqlite3.connect(path)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)
        self.assertEqual(db.execute("SELECT state,quote_digest FROM orders").fetchall(), [("RECEIPT_AVAILABLE", "q")])
        self.assertEqual(db.execute("SELECT count(*) FROM order_events").fetchone()[0], 1)
        self.assertEqual(db.execute("SELECT txid FROM order_settlements").fetchone()[0], "t")
        self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
