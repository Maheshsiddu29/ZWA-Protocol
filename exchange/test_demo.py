"""Hosted judge sandbox (CUSTODIAL_DEMO_POLICY) regressions.

Every test uses its own disposable /private/tmp/zwa4e-test-* root with fake
tool runners. No test touches a real chain, wallet, or the protected root.
"""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import time
import unittest
from unittest.mock import patch

from checkout.quote import from_terms
from exchange import order_ledger as ledger_module, portal as portal_module
from exchange.config import ConfigError, DemoPolicy, ExchangeConfig, load as load_config
from exchange.demo_ledger import DEALER_KEY, DemoLedger
from exchange.demo_signer import DemoSigner
from exchange.order_ledger import OrderError, OrderLedger
from exchange.order_service import OrderService
from exchange.portal import Portal, PortalError
from exchange.test_phase4e_c import ASSETS, GENESIS, FakeTools, make_terms
from exchange.tools import ToolError
from exchange.wallet_companion import Companion

DEALER_ADDR = "c" * 86
SESSION_A, SESSION_B = "a1" * 32, "b2" * 32
CLIENT = "c3" * 32
FUNDED_ZAT = 624_990_000


def block_hash(height: int) -> str:
    return format(height, "064x")


class DemoTools(FakeTools):
    """FakeTools with demo wallets, funding and per-height block hashes."""

    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self.wallets = {"seller": [0, 1000]}
        self.addresses = {"seller": DEALER_ADDR}
        self.coinbases = 40
        self.mined = []
        self.blocks = {}

    def chain(self):
        return {"state": self.state, "height": self.height, "anchorHeight": 100, "anchorHash": "d" * 64}

    def create_demo_home(self, wallet):
        home = self.root / "demo-wallets" / wallet
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
        return home

    def remove_demo_home(self, wallet):
        shutil.rmtree(self.root / "demo-wallets" / wallet, ignore_errors=True)

    def wallet_address(self, role, wallet=None):
        key = wallet or role
        if key not in self.addresses:
            raise ToolError("wallet has no seed")
        return self.addresses[key]

    def signer(self, role, *args, wallet=None, timeout=0):
        key = wallet or role
        if args[0] == "init":
            self.addresses[key] = secrets.token_hex(43)
            self.wallets[key] = [0, 0]
            return [f"PHASE35F_SIGNER_INIT role={role.title()} seed_file_created=owner_only"]
        if args[0] == "balances":
            native, units = self.wallets[key]
            if wallet:  # demo wallets hold the gold units this test tracks
                units = units if args[2] == ASSETS["zwa-gold-reference"] else 0
            return [f"PHASE35F_BALANCES role={role.title()} scanned_height={self.height} "
                    f"native_zat={native} asset={args[2]} asset_units={units}"]
        return super().signer(role, *args, timeout=timeout)

    def signer_command(self, role, *args, wallet=None):
        return [role, wallet or role, *args], {}, self.root

    def coordinator(self, *args, timeout=0):
        if args[0] == "fund-address":
            opts = dict(zip(args[1::2], args[2::2]))
            wallet = next(key for key, address in self.addresses.items() if address == opts["--address"])
            self.height += 1
            txid = secrets.token_hex(32)
            self.blocks[self.height] = {"tx": [txid], "hash": block_hash(self.height)}
            self.wallets[wallet] = [FUNDED_ZAT, 0]
            self.coinbases -= 1
            self.mined.append(("fund", wallet))
            return [f"PHASE35F_FUNDED recipient={opts['--address']} native_zat=625000000 "
                    f"txid={txid} height={self.height}"]
        if args[0] == "mine-empty":
            self.mined.append(("empty", int(args[2])))
            self.coinbases += int(args[2])
        if args[0] == "prepare-sale":
            opts = dict(zip(args[1::2], args[2::2]))
            terms = make_terms(opts["--asset"], int(opts["--quantity"]), int(opts["--price-zat"]),
                               opts["--buyer-address"], opts["--seller-address"],
                               self.coinbase_txid(int(opts["--fee-coinbase-height"])), self.height + 1)
            Path(opts["--out"]).write_text(json.dumps(terms))
            quote = from_terms(terms)
            Path(opts["--session-result"]).write_text(json.dumps({
                "version": 1, "sessionId": quote["quoteDigest"], "source": "LIVE_LOCAL_CLI",
                "outcome": "QUOTE_READY", "quote": quote, "receipt": None}))
            return ["PHASE35F_TERMS_PREPARED terms_hash=" + quote["quoteDigest"]]
        return super().coordinator(*args, timeout=timeout)

    def mature_sponsor_coinbases(self, tip):
        # Like pinned Zebra, the index keeps listing coinbases we already spent.
        return [(100 + i, self.coinbase_txid(100 + i)) for i in range(max(self.coinbases, 0) + len(self.mined))]

    def coinbase_txid(self, height):
        return format(height, "064x")

    def rpc(self, method, params=None):
        if method == "getblockhash":
            return block_hash(params[0])
        if method == "getblock" and int(params[0]) in self.blocks:
            return self.blocks[int(params[0])]
        return super().rpc(method, params)


class DemoBase(unittest.TestCase):
    policy = DemoPolicy(dealer_account_id="dealer", max_active_wallets=5, wallet_lifetime_seconds=3600,
                        max_wallets_per_hour=10, max_wallets_per_client_per_hour=3, max_orders_per_wallet=2,
                        max_units_per_order=5, max_payment_zat_per_order=100_000,
                        max_sponsored_orders_per_hour=10, sponsor_reserve=4)

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
                                     approval_mode="CUSTODIAL_DEMO_POLICY",
                                     tx_tool_dir=Path("/nonexistent"), params_data_home=Path("/nonexistent"),
                                     node_bin=Path("/usr/bin/false"), quote_validity_seconds=90,
                                     approval_wait_seconds=60, demo=self.policy)
        self.ledger = DemoLedger(self.config.ledger_path)
        self.tools = DemoTools(self.root)
        self.events = []
        self.service = OrderService(self.config, ledger=self.ledger, tools=self.tools, log=self.events.append)
        self.alive = True
        self.service._coordinator_alive = lambda pid: self.alive
        self.service._terminate = lambda pid: setattr(self, "terminated", pid)
        self.signer = DemoSigner(self.config, ledger=self.ledger, tools=self.tools, log=self.events.append)
        self.offer = self.ledger.create_offer(
            seller_id="dealer", instrument_id="zwa-gold-reference", asset_base=ASSETS["zwa-gold-reference"],
            chain_genesis=GENESIS, scan_height=100, scan_anchor="d" * 64, inventory_units=1000,
            capacity=1000, idempotency_key="dealer-gold")
        self.ledger.set_price(seller_id="dealer", offer_id=self.offer["id"], price_zat_per_unit=1000)
        self.sockets = []
        self.addCleanup(lambda: [s.close() for s in self.sockets])

    # ---------------------------------------------------------------- helpers

    def portal(self) -> Portal:
        portal = Portal(self.config)
        portal.live = lambda: (True, self.tools.height)
        portal._rpc = lambda method, params=None: self.tools.rpc(method, params)
        return portal

    def judge(self, session=SESSION_A, client=CLIENT) -> dict:
        row = self.ledger.request_wallet(session_hash=session, client_hash=client, policy=self.policy)
        for _ in range(4):
            self.service.tick()
        wallet = self.ledger.wallet(row["id"])
        self.assertEqual(wallet["state"], "READY")
        return wallet

    def state(self, order_id):
        return self.ledger.get_order(order_id, participant_id="dealer")["state"]

    def buy(self, wallet, session=SESSION_A, quantity=1, key=None):
        return self.portal().buy(wallet["account_id"], session, {
            "offerId": self.offer["id"], "quantity": quantity,
            "idempotencyKey": key or "buy-" + secrets.token_hex(6)})

    def open_socket(self, order_id):
        path = self.config.socket_path(order_id)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        listener = socket.socket(socket.AF_UNIX)
        listener.bind(str(path))
        self.sockets.append(listener)

    def executing(self, wallet, session=SESSION_A, quantity=1) -> dict:
        """Drive one judge order to TRADE_AUTHORIZED with execution started."""
        order = self.buy(wallet, session, quantity)
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "QUOTE_READY")
        detail = self.ledger.get_order(order["id"], participant_id="dealer")
        self.portal().confirm(wallet["account_id"], session, {"orderId": order["id"],
                                                               "quoteDigest": detail["quote_digest"]})
        self.signer.heartbeat()
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "TRADE_AUTHORIZED")
        self.assertIsNotNone(self.ledger.execution(order["id"]))
        self.open_socket(order["id"])
        return self.ledger.get_order(order["id"], participant_id="dealer")

    def signed(self, command, **_kwargs):
        """Stand-in for the pinned signer's authorize-live output."""
        role, terms = command[0], json.loads(Path(command[command.index("--terms") + 1]).read_text())
        quote = from_terms(terms)
        self.assertEqual(command[command.index("--approve") + 1], quote["quoteDigest"])
        self.assertIn("--scripted-test-mode", command)
        effects = (f"{quote['assetId']}:{quote['quantity']},native:-{quote['paymentAmount']}" if role == "buyer"
                   else f"{quote['assetId']}:-{quote['quantity']},native:{quote['paymentAmount']}")
        self.signed_roles.append((role, command[1]))
        line = (f"PHASE35F_AUTHORIZED role={role.title()} terms_hash={quote['quoteDigest']} "
                f"action_group_commitment={'9' * 64} actions=2 own_inputs_spent=1 own_effects={effects} "
                "group_native_balance_zat=0 group_expiry_height=211 proof=verified spend_auth=verified")
        return subprocess.CompletedProcess(command, 0, line + "\n", "")

    def sign_round(self):
        with patch("exchange.demo_signer.subprocess.run", side_effect=self.signed):
            self.signer.run_once()

    def accept(self, order, wallet_id):
        work = self.config.order_dir(order["id"])
        quote = from_terms(json.loads((work / "quote/terms.json").read_text()))
        receipt = {"terms_hash": quote["quoteDigest"], "txid": "f" * 64, "height": quote["targetHeight"],
                   "block_hash": block_hash(quote["targetHeight"]), "tx_bytes": 3000}
        (work / "receipt.json").write_text(json.dumps(receipt))
        (work / "coordinator.log.raw").write_text(
            f"PHASE35G_EXECUTED txid={'f' * 64} height={quote['targetHeight']} "
            f"rpc_block_hash={receipt['block_hash']} signed_groups_persisted=false\n")
        self.tools.block = {"txid": "f" * 64, "hash": receipt["block_hash"], "height": quote["targetHeight"],
                            "bytes": 3000}
        self.tools.blocks[quote["targetHeight"]] = {"tx": ["f" * 64], "hash": receipt["block_hash"]}
        self.tools.height = quote["targetHeight"]
        native, units = self.tools.wallets[wallet_id]
        self.tools.wallets[wallet_id] = [native - quote["paymentAmount"], units + quote["quantity"]]
        dealer_native, dealer_units = self.tools.wallets["seller"]
        self.tools.wallets["seller"] = [dealer_native + quote["paymentAmount"], dealer_units - quote["quantity"]]
        (work / "quote/session-result.json").write_text(json.dumps({
            "version": 1, "sessionId": quote["quoteDigest"], "source": "LIVE_LOCAL_CLI",
            "outcome": "ACCEPTED", "quote": quote, "receipt": receipt}))
        self.alive = False


class ProvisioningTests(DemoBase):
    def test_judge_wallet_is_created_funded_scanned_and_paired(self):
        row = self.ledger.request_wallet(session_hash=SESSION_A, client_hash=CLIENT, policy=self.policy)
        self.assertEqual(row["state"], "REQUESTED")
        self.assertRegex(row["account_id"], r"^judge-[0-9a-f]{20}$")
        self.service.tick()
        wallet = self.ledger.wallet(row["id"])
        self.assertEqual(wallet["state"], "READY")
        self.assertTrue((self.root / "demo-wallets" / row["id"]).is_dir())
        pairing = self.ledger.pairing(wallet["pairing_id"])
        self.assertEqual((pairing["method"], pairing["role"], pairing["account_id"], pairing["wallet_address"]),
                         ("CUSTODIAL_DEMO_WALLET", "buyer", row["account_id"], wallet["wallet_address"]))
        self.assertEqual(pairing["session_hash"], SESSION_A)
        scans = self.ledger.latest_scans(row["id"])
        self.assertEqual(set(scans), set(ASSETS.values()))
        self.assertTrue(all(scan["native_zat"] == FUNDED_ZAT for scan in scans.values()))
        self.assertIn(("fund", row["id"]), self.tools.mined)

    def test_two_judges_get_distinct_wallets_and_addresses(self):
        first, second = self.judge(SESSION_A), self.judge(SESSION_B)
        self.assertNotEqual(first["id"], second["id"])
        self.assertNotEqual(first["wallet_address"], second["wallet_address"])
        self.assertNotEqual(first["account_id"], second["account_id"])

    def test_funding_never_mines_while_a_quote_is_live_and_quotes_wait_for_funding(self):
        judge = self.judge(SESSION_A)
        order = self.buy(judge)
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "QUOTE_READY")
        late = self.ledger.request_wallet(session_hash=SESSION_B, client_hash=CLIENT, policy=self.policy)
        height = self.tools.height
        self.service.tick()
        self.assertEqual(self.ledger.wallet(late["id"])["state"], "CREATED")
        self.assertEqual(self.tools.height, height)  # no block under a live quote
        self.ledger.close_before_approval(order["id"], buyer_id=judge["account_id"], state="CANCELLED")
        self.service.tick()
        self.assertEqual(self.ledger.wallet(late["id"])["state"], "READY")

    def test_sponsor_coinbases_are_topped_up_when_idle(self):
        self.tools.coinbases = 2
        self.judge(SESSION_A)
        self.assertTrue(any(kind == "empty" for kind, _ in self.tools.mined))

    def test_unfinished_creation_fails_after_timeout(self):
        row = self.ledger.request_wallet(session_hash=SESSION_A, client_hash=CLIENT, policy=self.policy)
        with self.ledger._transaction() as db:
            db.execute("UPDATE demo_wallets SET created_at=? WHERE id=?", (int(time.time()) - 3600, row["id"]))
        self.service.tick()
        failed = self.ledger.wallet(row["id"])
        # A failed wallet with no orders is cleaned up immediately.
        self.assertEqual((failed["state"], failed["failure"]), ("RETIRED", "wallet creation did not complete"))
        self.assertIsNone(self.ledger.wallet_for_session(row["account_id"], SESSION_A))

    def test_expiry_cancels_pre_execution_order_revokes_pairing_and_retires_home(self):
        judge = self.judge()
        order = self.buy(judge)
        self.service.tick()
        with self.ledger._transaction() as db:
            db.execute("UPDATE demo_wallets SET expires_at=? WHERE id=?", (int(time.time()) - 1, judge["id"]))
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "EXPIRED")
        self.assertEqual(self.ledger.get_offer(self.offer["id"])["reserved"], 0)
        expired = self.ledger.wallet(judge["id"])
        self.assertEqual(expired["state"], "EXPIRED")
        self.assertFalse(self.ledger.pairing_usable(judge["pairing_id"]))
        self.assertTrue((self.root / "demo-wallets" / judge["id"]).exists())  # grace period
        with self.ledger._transaction() as db:
            db.execute("UPDATE demo_wallets SET expires_at=? WHERE id=?", (int(time.time()) - 3600, judge["id"]))
        self.service.tick()
        self.assertEqual(self.ledger.wallet(judge["id"])["state"], "RETIRED")
        self.assertFalse((self.root / "demo-wallets" / judge["id"]).exists())

    def test_expiry_waits_for_settlement_in_flight(self):
        judge = self.judge()
        order = self.executing(judge)
        with self.ledger._transaction() as db:
            db.execute("UPDATE demo_wallets SET expires_at=? WHERE id=?", (int(time.time()) - 1, judge["id"]))
        self.service.tick()
        self.assertEqual(self.ledger.wallet(judge["id"])["state"], "READY")
        self.assertEqual(self.state(order["id"]), "TRADE_AUTHORIZED")


class LifecycleTests(DemoBase):
    def setUp(self):
        super().setUp()
        self.signed_roles = []

    def test_confirmed_purchase_settles_through_both_policies_and_reconciles(self):
        judge = self.judge()
        order = self.executing(judge, quantity=2)
        self.sign_round()
        self.assertEqual(self.signed_roles, [("buyer", judge["id"])])
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "BUYER_APPROVED")
        self.sign_round()
        self.assertEqual(self.signed_roles[-1], ("seller", "seller"))
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "SELLER_APPROVED")
        self.accept(order, judge["id"])
        self.service.tick()
        detail = self.ledger.get_order(order["id"], participant_id=judge["account_id"])
        self.assertEqual(detail["state"], "RECEIPT_AVAILABLE")
        approvals = [json.loads(e["value_json"]) for e in detail["evidence"] if e["kind"].endswith("_APPROVED")]
        self.assertEqual({a["approvalMode"] for a in approvals}, {"CUSTODIAL_DEMO_POLICY"})
        receipt = json.loads((self.config.order_dir(order["id"]) / "order-receipt.json").read_text())
        self.assertEqual((receipt["approvalMode"], receipt["quantity"], receipt["rootedNoteLineage"]),
                         ("CUSTODIAL_DEMO_POLICY", 2, "UNSUPPORTED"))
        gold = self.ledger.latest_scans(judge["id"])[ASSETS["zwa-gold-reference"]]
        self.assertEqual((gold["asset_units"], gold["native_zat"]), (2, FUNDED_ZAT - 2000))
        dealer = self.ledger.latest_scans(DEALER_KEY)[ASSETS["zwa-gold-reference"]]
        self.assertEqual(dealer["asset_units"], 998)
        view = self.portal().wallet(judge["account_id"], "buyer", SESSION_A)
        held = next(h for h in view["holdings"] if h["instrumentId"] == "zwa-gold-reference")
        self.assertEqual(held["units"], 2)
        attestation = json.loads((self.config.order_dir(order["id"]) / "buyer-approval.json").read_text())
        self.assertFalse(attestation["controllingTerminal"])
        self.assertIn("browser-confirmation", attestation["policyChecks"])

    def test_dealer_pause_rejects_after_buyer_and_nothing_is_submitted(self):
        judge = self.judge()
        order = self.executing(judge)
        self.sign_round()
        self.service.tick()
        self.ledger.set_setting("dealerPolicy", "PAUSED")
        self.sign_round()
        self.assertEqual([role for role, _ in self.signed_roles], ["buyer"])
        self.service.tick()  # rejected seller ends the approval window
        self.assertEqual(getattr(self, "terminated", None), 4242)
        self.alive = False
        self.service.tick()
        detail = self.ledger.get_order(order["id"], participant_id="dealer")
        self.assertEqual(detail["state"], "EXECUTION_FAILED")
        failure = json.loads(detail["evidence"][-1]["value_json"])
        self.assertEqual((failure["reason"], failure["transactionSubmitted"]), ("SELLER_WALLET_REJECTED", False))
        self.assertEqual(self.ledger.get_offer(self.offer["id"])["reserved"], 0)

    def test_price_change_after_quote_is_refused_by_dealer_policy(self):
        judge = self.judge()
        order = self.executing(judge)
        self.sign_round()
        self.service.tick()
        self.ledger.set_price(seller_id="dealer", offer_id=self.offer["id"], price_zat_per_unit=2000)
        self.sign_round()
        attestation = json.loads((self.config.order_dir(order["id"]) / "seller-approval.json").read_text())
        self.assertEqual(attestation["result"], "REJECTED")
        self.assertIn("price changed", attestation["policyReason"])


class ConcurrencyTests(DemoBase):
    def test_simultaneous_purchases_serialize_on_the_dealer_wallet(self):
        first, second = self.judge(SESSION_A), self.judge(SESSION_B)
        a, b = self.buy(first, SESSION_A), self.buy(second, SESSION_B)
        self.service.tick()
        self.assertEqual((self.state(a["id"]), self.state(b["id"])), ("QUOTE_READY", "BUYER_REQUESTED"))
        note = self.ledger.get_order(b["id"], participant_id="dealer")["note"]["note"]
        self.assertIn("Queued", note)
        self.ledger.close_before_approval(a["id"], buyer_id=first["account_id"], state="CANCELLED")
        self.service.tick()
        self.assertEqual(self.state(b["id"]), "QUOTE_READY")

    def test_parallel_signups_never_exceed_the_active_cap(self):
        from concurrent.futures import ThreadPoolExecutor

        def attempt(index):
            try:
                DemoLedger(self.config.ledger_path).request_wallet(
                    session_hash=format(1000 + index, "064x"), client_hash=format(index, "064x"),
                    policy=self.policy)
                return True
            except OrderError:
                return False
        with ThreadPoolExecutor(max_workers=12) as pool:
            admitted = sum(pool.map(attempt, range(12)))
        self.assertEqual(admitted, self.policy.max_active_wallets)


class RecoveryTests(DemoBase):
    def setUp(self):
        super().setUp()
        self.signed_roles = []

    def test_funding_coinbase_is_never_reused_as_a_sponsor_while_the_index_still_lists_it(self):
        judge = self.judge()
        spent = self.ledger.spent_sponsor_txids()
        self.assertEqual(len(spent), 1)
        order = self.buy(judge)
        self.service.tick()
        terms = json.loads((self.config.order_dir(order["id"]) / "quote/terms.json").read_text())
        self.assertNotIn(terms["fee_sponsor_txid"], spent)
        listed = {txid for _, txid in self.tools.mature_sponsor_coinbases(self.tools.height)}
        self.assertTrue(spent <= listed)  # the stale index still lists it

    def uncertain(self) -> tuple[dict, dict]:
        judge = self.judge()
        order = self.executing(judge)
        self.sign_round()
        self.service.tick()
        self.sign_round()
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "SELLER_APPROVED")
        work = self.config.order_dir(order["id"])
        quote = from_terms(json.loads((work / "quote/terms.json").read_text()))
        (work / "coordinator.log.raw").write_text("PHASE35F_COORDINATOR_REJECTED reason=Block rejected\n")
        (work / "quote/session-result.json").write_text(json.dumps({
            "version": 1, "sessionId": quote["quoteDigest"], "source": "LIVE_LOCAL_CLI",
            "outcome": "UNCERTAIN", "quote": quote, "receipt": None}))
        self.alive = False
        return judge, order

    def test_uncertain_order_is_released_only_after_expiry_with_unchanged_wallets(self):
        judge, order = self.uncertain()
        expiry = json.loads((self.config.order_dir(order["id"]) / "quote/terms.json").read_text())["tx_expiry_height"]
        self.service.tick()  # coordinator stopped after both approvals -> UNCERTAIN
        self.service.tick()  # demo operator mines past expiry, rescans, releases
        detail = self.ledger.get_order(order["id"], participant_id="dealer")
        self.assertEqual(detail["state"], "EXECUTION_FAILED")
        evidence = json.loads(detail["evidence"][-1]["value_json"])
        self.assertEqual(evidence["reason"], "EXPIRED_WITHOUT_EXECUTION")
        self.assertGreater(evidence["checkedHeight"], expiry)
        self.assertEqual(self.ledger.get_offer(self.offer["id"])["reserved"], 0)
        self.assertTrue(any(kind == "empty" for kind, _ in self.tools.mined))  # only empty blocks

    def test_uncertain_order_with_changed_wallets_stays_for_the_operator(self):
        judge, order = self.uncertain()
        self.service.tick()
        self.tools.wallets[judge["id"]][1] += 1  # something did change
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "UNCERTAIN")
        self.assertIn({"event": "UNCERTAIN_NEEDS_OPERATOR", "orderId": order["id"]}, self.events)

    def test_uncertain_order_with_an_executed_line_is_never_released(self):
        judge, order = self.uncertain()
        self.service.tick()
        raw = self.config.order_dir(order["id"]) / "coordinator.log.raw"
        raw.write_text(f"PHASE35G_EXECUTED txid={'f' * 64} height=999 rpc_block_hash=x\n")
        self.service.tick()
        self.assertEqual(self.state(order["id"]), "UNCERTAIN")


class PolicyTests(DemoBase):
    def setUp(self):
        super().setUp()
        self.signed_roles = []

    def assert_buyer_refused(self, order, reason):
        with patch("exchange.demo_signer.subprocess.run", side_effect=self.signed) as run:
            self.signer.run_once()
            run.assert_not_called()
        attestation = json.loads((self.config.order_dir(order["id"]) / "buyer-approval.json").read_text())
        self.assertEqual(attestation["result"], "REJECTED")
        self.assertIn(reason, attestation["policyReason"])

    def test_terms_changed_after_quote_are_never_signed(self):
        judge = self.judge()
        order = self.executing(judge)
        path = self.config.order_dir(order["id"]) / "quote/terms.json"
        terms = json.loads(path.read_text())
        terms["price_zat"] = 1
        path.write_text(json.dumps(terms))
        self.assert_buyer_refused(order, "terms differ")

    def test_confirmation_for_another_digest_is_refused(self):
        judge = self.judge()
        order = self.executing(judge)
        with self.ledger._transaction() as db:
            db.execute("""UPDATE order_evidence SET value_json=? WHERE order_id=? AND kind='BUYER_CONFIRMED'""",
                       (json.dumps({"quoteDigest": "0" * 64, "buyerPairingId": judge["pairing_id"]}), order["id"]))
        self.assert_buyer_refused(order, "did not confirm")

    def test_expired_judge_wallet_never_signs(self):
        judge = self.judge()
        order = self.executing(judge)
        with self.ledger._transaction() as db:
            db.execute("UPDATE demo_wallets SET expires_at=? WHERE id=?", (int(time.time()) - 1, judge["id"]))
        self.assert_buyer_refused(order, "live demo wallet")

    def test_payment_above_policy_limit_is_refused(self):
        judge = self.judge()
        order = self.executing(judge, quantity=3)
        self.signer.policy = replace(self.policy, max_payment_zat_per_order=2000)
        self.assert_buyer_refused(order, "exceeds the limit")

    def test_proceeds_to_another_address_are_refused(self):
        judge = self.judge()
        order = self.executing(judge)
        self.signer._dealer_address = "d" * 86
        self.assert_buyer_refused(order, "demo dealer")

    def test_stale_tip_is_refused(self):
        judge = self.judge()
        order = self.executing(judge)
        self.tools.height += 1
        self.assert_buyer_refused(order, "current chain tip")

    def test_signer_refuses_human_and_protected_roots(self):
        with self.assertRaises(SystemExit):
            DemoSigner(replace(self.config, approval_mode="HUMAN_APPROVED", demo=None), ledger=self.ledger,
                       tools=self.tools)
        with self.assertRaises(SystemExit):
            DemoSigner(replace(self.config, root=Path("/private/tmp/zwa4e-phase4e-b")), ledger=self.ledger,
                       tools=self.tools)

    def test_wallet_companion_refuses_custodial_root(self):
        with patch("exchange.wallet_companion.OrderLedger", return_value=self.ledger), \
             patch("exchange.wallet_companion.Tools", return_value=self.tools):
            with self.assertRaises(SystemExit):
                Companion(self.config, "buyer")


class SessionTests(DemoBase):
    def handle(self, action, actor, role, session, payload=None):
        with patch.object(portal_module, "load_config", return_value=self.config), \
             patch.object(portal_module.Portal, "live", lambda s: (True, self.tools.height)), \
             patch.object(portal_module.Portal, "_rpc", lambda s, m, p=None: self.tools.rpc(m, p)):
            return portal_module.handle({"action": action, "actor": actor, "role": role,
                                         "sessionHash": session, "payload": payload or {}})

    def test_admission_caps_per_client_and_globally(self):
        for index in range(3):
            self.handle("demo-start", "anonymous", "anonymous", format(index, "064x"), {"clientHash": CLIENT})
        with self.assertRaisesRegex(OrderError, "this connection"):
            self.handle("demo-start", "anonymous", "anonymous", "9" * 64, {"clientHash": CLIENT})
        for index in range(2):
            self.handle("demo-start", "anonymous", "anonymous", format(10 + index, "064x"),
                        {"clientHash": format(index + 1, "064x")})
        with self.assertRaisesRegex(OrderError, "capacity"):
            self.handle("demo-start", "anonymous", "anonymous", "8" * 64, {"clientHash": "7" * 64})

    def test_session_binding_rejects_other_sessions_forged_roles_and_accounts(self):
        judge = self.judge(SESSION_A)
        other = self.judge(SESSION_B)
        self.assertEqual(self.handle("wallet", judge["account_id"], "buyer", SESSION_A)["state"], "READY")
        for actor, role, session in ((judge["account_id"], "buyer", SESSION_B),     # another judge's session
                                     (judge["account_id"], "buyer", "e" * 64),      # unknown session
                                     (judge["account_id"], "seller", SESSION_A),    # forged role
                                     ("buyer", "buyer", SESSION_A),                 # password buyer
                                     ("dealer", "buyer", SESSION_A),                # dealer as buyer
                                     ("intruder", "seller", SESSION_A)):            # not the dealer
            with self.subTest(actor=actor, role=role):
                with self.assertRaises((PortalError, OrderError)):
                    self.handle("wallet", actor, role, session)
        order = self.buy(other, SESSION_B)
        with self.assertRaises(OrderError):
            self.handle("order", judge["account_id"], "buyer", SESSION_A, {"orderId": order["id"]})
        with self.assertRaises(OrderError):
            self.handle("cancel", judge["account_id"], "buyer", SESSION_A, {"orderId": order["id"]})

    def test_demo_actor_is_rejected_on_a_non_demo_root(self):
        human = replace(self.config, approval_mode="HUMAN_APPROVED", demo=None)
        with patch.object(portal_module, "load_config", return_value=human):
            with self.assertRaises(PortalError):
                portal_module.handle({"action": "wallet", "actor": "judge-" + "0" * 20, "role": "buyer",
                                      "sessionHash": SESSION_A, "payload": {}})

    def test_purchase_limits_and_single_open_order(self):
        judge = self.judge()
        with self.assertRaisesRegex(PortalError, "5 units"):
            self.buy(judge, quantity=6)
        first = self.buy(judge, key="first-order")
        self.assertEqual(self.buy(judge, key="first-order")["id"], first["id"])  # idempotent retry
        with self.assertRaisesRegex(PortalError, "current order"):
            self.buy(judge, key="second-order")
        self.ledger.close_before_approval(first["id"], buyer_id=judge["account_id"], state="CANCELLED")
        self.ledger.set_setting("dealerPolicy", "PAUSED")
        with self.assertRaisesRegex(PortalError, "paused"):
            self.buy(judge, key="third-order")

    def test_ending_a_session_invalidates_it(self):
        judge = self.judge()
        self.assertTrue(self.handle("demo-end", judge["account_id"], "buyer", SESSION_A)["ended"])
        with self.assertRaises(PortalError):
            self.handle("wallet", judge["account_id"], "buyer", SESSION_A)

    def test_pairing_is_unavailable_in_the_hosted_demo(self):
        judge = self.judge()
        with self.assertRaisesRegex(PortalError, "pairing is not available"):
            self.handle("pair", judge["account_id"], "buyer", SESSION_A, {"code": "ABCDE-FGHIJ"})

    def test_dealer_desk_and_policy_toggle(self):
        self.judge()
        desk = self.handle("wallet", "dealer", "seller", SESSION_A)
        self.assertEqual((desk["mode"], desk["policy"]), ("DEMO_DEALER_STANDING_POLICY", "ACTIVE"))
        self.assertEqual(self.handle("dealer-policy", "dealer", "seller", SESSION_A,
                                     {"policy": "PAUSED"})["policy"], "PAUSED")
        with self.assertRaises(PortalError):
            self.handle("dealer-policy", "dealer", "seller", SESSION_A, {"policy": "ANYTHING"})


class SchemaTests(unittest.TestCase):
    def test_protected_ledger_is_not_migrated_to_demo_schema(self):
        root = Path("/private/tmp/zwa4e-test-" + secrets.token_hex(6))
        root.mkdir(mode=0o700)
        self.addCleanup(shutil.rmtree, root, True)
        path = root / "exchange.sqlite"
        with patch.object(ledger_module, "PROTECTED_LEDGERS", frozenset({str(path)})):
            ledger = OrderLedger(path)
            self.assertEqual(ledger.schema, 3)
            with self.assertRaises(OrderError):
                DemoLedger(path)
        self.assertEqual(sqlite3.connect(path).execute("PRAGMA user_version").fetchone()[0], 3)
        self.assertEqual(OrderLedger(path).schema, 4)  # an unprotected copy migrates

    def test_demo_configuration_requires_v2_and_is_refused_for_protected_root(self):
        root = Path("/private/tmp/zwa4e-test-" + secrets.token_hex(6))
        (root / "exchange").mkdir(mode=0o700, parents=True)
        self.addCleanup(shutil.rmtree, root, True)
        base = {"version": 1, "root": str(root), "container": "zwa-test", "rpcPort": 18999,
                "approvalMode": "CUSTODIAL_DEMO_POLICY", "txToolDir": "/a", "paramsDataHome": "/b",
                "nodeBin": "/c", "quoteValiditySeconds": 90, "approvalWaitSeconds": 60,
                "companionStaleSeconds": 15, "stabilizationBlocks": 110}
        path = root / "exchange/service-config.json"

        def write(data):
            path.write_text(json.dumps(data))
            os.chmod(path, 0o600)
        write(base)
        with self.assertRaisesRegex(ConfigError, "version 2"):
            load_config(root)
        demo = {"dealerAccountId": "dealer", "maxActiveWallets": 40, "walletLifetimeSeconds": 7200,
                "maxWalletsPerHour": 60, "maxWalletsPerClientPerHour": 3, "maxOrdersPerWallet": 5,
                "maxUnitsPerOrder": 5, "maxPaymentZatPerOrder": 1_000_000, "maxSponsoredOrdersPerHour": 60,
                "sponsorReserve": 12}
        write({**base, "version": 2, "rpcHost": "zebra", "chainManagement": "external",
               "isolation": "linux-landlock", "demo": demo})
        config = load_config(root)
        self.assertEqual((config.demo.max_units_per_order, config.rpc_host, config.demo_mode), (5, "zebra", True))
        write({**base, "version": 2, "rpcHost": "zebra", "chainManagement": "external",
               "isolation": "linux-landlock", "demo": {**demo, "maxUnitsPerOrder": 10_000}})
        with self.assertRaisesRegex(ConfigError, "maxUnitsPerOrder"):
            load_config(root)
        write({**base, "version": 2, "rpcHost": "evil.example.com", "chainManagement": "external",
               "isolation": "linux-landlock", "demo": demo})
        with self.assertRaisesRegex(ConfigError, "RPC host"):
            load_config(root)


if __name__ == "__main__":
    unittest.main()
