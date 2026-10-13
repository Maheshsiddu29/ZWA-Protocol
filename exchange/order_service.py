"""Operator-run order execution service for browser-initiated purchases.

One locked process advances every live order from durable ledger state:

  BUYER_REQUESTED  -> real wallet scans + prepare-sale       -> QUOTE_READY
  BUYER_CONFIRMED  -> fresh Groth16 proof + verifier         -> PROOF_VERIFIED
  PROOF_VERIFIED   -> live tip, scans, signer review          -> TRADE_AUTHORIZED
  TRADE_AUTHORIZED -> both paired companions online           -> execute-live
  signer evidence  -> BUYER_APPROVED -> SELLER_APPROVED
  coordinator receipt + RPC + wallet scans -> SUBMITTED ... RECEIPT_AVAILABLE

The service never signs. It cannot release a wallet group; only the wallet
companion's signer can, and only after its own review. The coordinator it
starts re-verifies the Groth16 authorization before opening its approval
socket and again before assembling the transaction.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import secrets
import string
import subprocess
import sys
import time

from checkout.api import SessionError, read_authorization, read_scans, read_session
from checkout.quote import from_terms
from checkout.scans import LINE as SCAN_LINE, parse, write as write_public_scans
from . import persistent_chain
from .config import ExchangeConfig, load as load_config
from .demo_ledger import DemoLedger
from .demo_wallets import DemoError, DemoOperator
from .order_ledger import OrderError, OrderLedger, WALLET_COMMITTED_STATES
from .order_paths import order_dir
from .portfolio_scans import build as build_portfolio
from .tools import ToolError, Tools, phase_lines, write_lines, write_new_json

ACTIVE_STATES = ("BUYER_REQUESTED", "QUOTE_READY", "BUYER_CONFIRMED", "PROOF_VERIFIED",
                 "TRADE_AUTHORIZED", "BUYER_APPROVED", "SELLER_APPROVED", "SUBMITTED",
                 "INCLUDED", "RECONCILED")
INSTRUMENTS = ("zwa-gold-reference", "zwa-treasury-style", "zwa-credit-participation")
DURABILITY_POLICY = "RESTART_DURABLE_UNDER_PINNED_ZEBRA_POLICY_NOT_FINALITY"


class ServiceError(RuntimeError):
    pass


def sha256_file(path: Path, limit: int = 262144) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ServiceError(f"unsafe evidence file: {path.name}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def small_json(path: Path, limit: int = 65536) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ServiceError(f"unsafe or missing evidence: {path.name}")
    data = json.loads(path.read_text())
    if type(data) is not dict:
        raise ServiceError("evidence must be an object")
    return data


def set_aside(path: Path) -> None:
    """Keep evidence from an interrupted attempt instead of deleting it."""
    if path.exists():
        path.rename(path.with_name(f"{path.name}.superseded-{time.time_ns()}"))


class OrderService:
    def __init__(self, config: ExchangeConfig, *, ledger: OrderLedger | None = None,
                 tools: Tools | None = None, log=None):
        self.config = config
        self.ledger = ledger or (DemoLedger(config.ledger_path) if config.demo_mode
                                 else OrderLedger(config.ledger_path))
        self.tools = tools or Tools(config)
        self.log = log or (lambda event: print(json.dumps(event, sort_keys=True), flush=True))
        self.registry = small_json(config.exchange / "instrument-registry.json")
        self.assets = {item["id"]: item["asset_base"] for item in self.registry["instruments"]}
        # Custodial judge sandbox only; never present for a human-approved root.
        self.demo = DemoOperator(self) if config.demo_mode else None

    # ---------------------------------------------------------------- helpers

    def _note(self, order_id: str, text: str) -> None:
        self.ledger.note(order_id, text)

    def _work(self, order_id: str) -> Path:
        work = order_dir(self.config.exchange, order_id)
        work.mkdir(mode=0o700, parents=True, exist_ok=True)
        return work

    def _instrument(self, order: dict) -> str:
        instrument = order["instrument_id"]
        if instrument not in INSTRUMENTS or self.assets.get(instrument) != order["asset_base"]:
            raise ServiceError("order instrument is not an issued registry asset")
        return instrument

    def _scan(self, role: str, asset: str, path: Path, wallet: str | None = None) -> dict:
        lines = [line for line in self.tools.signer(role, "balances", "--asset", asset, wallet=wallet)
                 if line.startswith("PHASE35F_BALANCES ")]
        write_lines(path, lines)
        return parse(path, role.title(), asset)

    @staticmethod
    def parse_scan(lines: list[str], role: str, asset: str) -> dict:
        matches = [m for line in lines if (m := SCAN_LINE.fullmatch(line))]
        if len(matches) != 1 or matches[0].group(1) != role.title() or matches[0].group(4) != asset:
            raise ServiceError("scanner output does not match role and asset")
        return {"height": int(matches[0].group(2)), "nativeZat": int(matches[0].group(3)),
                "assetUnits": int(matches[0].group(5))}

    def _wallet(self, pairing_id: str) -> str | None:
        """Demo wallet behind a buyer pairing; None means the role's own wallet."""
        return self.demo.wallet_for_pairing(pairing_id) if self.demo else None

    def _quote(self, order_id: str) -> tuple[dict, dict, dict, Path]:
        quote_row = self.ledger.order_quote(order_id)
        if not quote_row:
            raise ServiceError("order has no server-generated quote")
        qdir = self._work(order_id) / "quote"
        terms_path = qdir / "terms.json"
        if sha256_file(terms_path) != quote_row["terms_sha256"]:
            raise ServiceError("exact sale terms changed after quoting")
        terms = small_json(terms_path)
        quote = from_terms(terms)
        if quote["quoteDigest"] != quote_row["quote_digest"]:
            raise ServiceError("sale terms no longer match the quote digest")
        return quote_row, terms, quote, qdir

    def _fresh_tip(self, quote: dict) -> int:
        chain = self.tools.chain()
        if chain.get("state") not in ("CHAIN_ACTIVE", "CHAIN_RESTARTED"):
            raise ServiceError("protected chain is not active and anchor-verified")
        tip = chain["height"]
        if tip + 1 != quote["targetHeight"] or tip >= quote["transactionExpiry"]:
            return -1
        return tip

    # ------------------------------------------------------------------ steps

    def tick(self) -> None:
        chain = self.tools.chain()
        if chain.get("state") not in ("CHAIN_ACTIVE", "CHAIN_RESTARTED"):
            for order in self.ledger.orders_in(ACTIVE_STATES):
                self._note(order["id"], "Paused: the experimental chain is unavailable.")
            return
        if self.demo:
            # Provisioning may mine blocks, but only while no quote is live.
            self.demo.tick(chain["height"])
            chain = self.tools.chain()
            if chain.get("state") not in ("CHAIN_ACTIVE", "CHAIN_RESTARTED"):
                return
        for order in self.ledger.orders_in(ACTIVE_STATES):
            # Advance one order through every step its current evidence allows.
            for _ in range(len(ACTIVE_STATES)):
                try:
                    self.step(order, chain["height"])
                except (OrderError, ServiceError, ToolError, SessionError, DemoError,
                        subprocess.TimeoutExpired, OSError, ValueError) as exc:
                    self.log({"orderId": order["id"], "state": order["state"], "error": str(exc)[:300]})
                    try:
                        self._note(order["id"], "Waiting: " + str(exc)[:200])
                    except OrderError:
                        pass
                    break
                current = next((row for row in self.ledger.orders_in(ACTIVE_STATES)
                                if row["id"] == order["id"]), None)
                if current is None or current["state"] == order["state"]:
                    break
                order = current
        self.stabilize_if_due()

    def step(self, order: dict, tip: int) -> None:
        state = order["state"]
        if state == "BUYER_REQUESTED":
            self.prepare_quote(order, tip)
        elif state == "QUOTE_READY":
            self.expire_stale_quote(order)
        elif state == "BUYER_CONFIRMED":
            self.prove(order)
        elif state == "PROOF_VERIFIED":
            self.authorize(order)
        elif state in ("TRADE_AUTHORIZED", "BUYER_APPROVED", "SELLER_APPROVED"):
            if self.ledger.execution(order["id"]) is None:
                if state == "TRADE_AUTHORIZED":
                    self.start(order)
            else:
                self.monitor(order)
        elif state in ("SUBMITTED", "INCLUDED", "RECONCILED"):
            self.settle(order)

    def prepare_quote(self, order: dict, tip: int) -> None:
        order_id = order["id"]
        asset = self.assets[self._instrument(order)]
        if self.ledger.wallets_committed(order_id):
            self._note(order_id, "Queued: another live quote is using these wallets.")
            return
        if self.demo and self.demo.funding_pending():
            # Funding mines a block; it must not land between quote and execution.
            self._note(order_id, "Queued: a new demo wallet is being funded. Your quote follows.")
            return
        price = self.ledger.offer_price(order["offer_id"])
        if not price:
            self._note(order_id, "Waiting for the seller to set a fixed experimental price.")
            return
        buyer = self.ledger.active_pairing(account_id=order["buyer_id"], role="buyer")
        seller = self.ledger.active_pairing(account_id=order["seller_id"], role="seller")
        if not buyer:
            self._note(order_id, "Waiting for the buyer's wallet pairing.")
            return
        if not seller:
            self._note(order_id, "Waiting for the seller's wallet pairing.")
            return
        work = self._work(order_id)
        buyer_scan = self._scan("buyer", asset, work / "buyer-before.log", self._wallet(buyer["id"]))
        seller_scan = self._scan("seller", asset, work / "seller-before.log")
        if buyer_scan["height"] != tip or seller_scan["height"] != tip:
            raise ServiceError("wallet scans are not at the current chain tip")
        offer = self.ledger.get_offer(order["offer_id"])
        payment = order["quantity"] * price["price_zat_per_unit"]
        if seller_scan["assetUnits"] < offer["reserved"]:
            self.ledger.fail(order_id, expected="BUYER_REQUESTED", state="REJECTED", release=True,
                             evidence={"reason": "SELLER_SPENDABLE_INVENTORY_INSUFFICIENT",
                                       "sellerUnits": seller_scan["assetUnits"],
                                       "reservedUnits": offer["reserved"], "tipHeight": tip})
            return
        if buyer_scan["nativeZat"] < payment:
            self.ledger.fail(order_id, expected="BUYER_REQUESTED", state="REJECTED", release=True,
                             evidence={"reason": "BUYER_NATIVE_ZEC_INSUFFICIENT",
                                       "paymentZat": payment, "tipHeight": tip})
            return
        committed = self.ledger.committed_sponsor_txids()
        sponsor = next(((height, txid) for height, txid in self.tools.mature_sponsor_coinbases(tip)
                        if txid not in committed and self.tools.coinbase_txid(height) == txid), None)
        if sponsor is None:
            raise ServiceError("no unspent mature sponsor coinbase is available")
        qdir = work / "quote"
        set_aside(qdir)
        qdir.mkdir(mode=0o700)
        self.tools.coordinator(
            "prepare-sale", "--asset", asset, "--quantity", str(order["quantity"]),
            "--price-zat", str(payment), "--buyer-address", buyer["wallet_address"],
            "--seller-address", seller["wallet_address"],
            "--fee-coinbase-height", str(sponsor[0]), "--out", str(qdir / "terms.json"),
            "--session-result", str(qdir / "session-result.json"))
        terms = small_json(qdir / "terms.json")
        session = read_session(qdir / "session-result.json")
        quote = from_terms(terms)
        if (session is None or session["outcome"] != "QUOTE_READY" or session["quote"] != quote
                or terms["asset_base"] != asset or terms["quantity"] != order["quantity"]
                or terms["price_zat"] != payment
                or terms["buyer_receive_address"] != buyer["wallet_address"]
                or terms["seller_proceeds_address"] != seller["wallet_address"]
                or terms["fee_sponsor_txid"] != sponsor[1] or terms["target_height"] != tip + 1):
            raise ServiceError("prepared sale terms differ from the requested order")
        self.ledger.attach_quote(order_id, quote, observed_height=tip, meta={
            "termsSha256": sha256_file(qdir / "terms.json"),
            "priceZatPerUnit": price["price_zat_per_unit"], "priceVersion": price["version"],
            "buyerPairingId": buyer["id"], "sellerPairingId": seller["id"],
            "validUntil": int(time.time()) + self.config.quote_validity_seconds})
        self._note(order_id, "Exact quote ready for the buyer's review.")
        self.log({"orderId": order_id, "state": "QUOTE_READY", "quoteDigest": quote["quoteDigest"]})

    def expire_stale_quote(self, order: dict) -> None:
        quote_row = self.ledger.order_quote(order["id"])
        if quote_row is None:
            return  # A pre-4E-C order is never advanced by this service.
        _, _, quote, _ = self._quote(order["id"])
        if quote_row["valid_until"] <= time.time() or self._fresh_tip(quote) < 0:
            self.ledger.fail(order["id"], expected="QUOTE_READY", state="EXPIRED", release=True,
                             evidence={"reason": "QUOTE_NOT_CONFIRMED_BEFORE_EXPIRY",
                                       "quoteDigest": quote["quoteDigest"]})

    def _expire_before_signing(self, order: dict, reason: str) -> None:
        self.ledger.fail(order["id"], expected=order["state"], state="EXPIRED", release=True,
                         evidence={"reason": reason, "quoteDigest": order["quote_digest"]})

    def prove(self, order: dict) -> None:
        order_id = order["id"]
        quote_row, terms, quote, qdir = self._quote(order_id)
        if self._fresh_tip(quote) < 0:
            self._expire_before_signing(order, "CHAIN_ADVANCED_BEFORE_AUTHORIZATION")
            return
        work = self._work(order_id)
        auth = work / "authorization"
        set_aside(auth)
        issuance = self.config.exchange / "instruments" / f"{self._instrument(order)}-issuance.json"
        generated = self.tools.authorization("generate", str(qdir / "terms.json"),
                                             str(qdir / "session-result.json"),
                                             str(issuance), str(auth))
        write_lines(work / "proof-generation.log", phase_lines(generated.stdout) or
                    [line for line in generated.stdout.splitlines() if line.startswith("PHASE4C1_")])
        if generated.returncode:
            self.ledger.fail(order_id, expected="BUYER_CONFIRMED", state="REJECTED", release=True,
                             evidence={"reason": "GROTH16_PROOF_GENERATION_FAILED",
                                       "quoteDigest": quote["quoteDigest"]})
            return
        verified = self.tools.authorization("verify", str(qdir / "terms.json"), quote["quoteDigest"],
                                            str(auth / "trust.json"), str(auth / "authorization.json"))
        if verified.returncode:
            # A failed proof ends the purchase before any signing request exists.
            self.ledger.fail(order_id, expected="BUYER_CONFIRMED", state="REJECTED", release=True,
                             evidence={"reason": "GROTH16_AUTHORIZATION_REJECTED",
                                       "quoteDigest": quote["quoteDigest"],
                                       "verifier": verified.stderr.strip()[-200:]})
            return
        output = work / "preflight-authorization.json"
        set_aside(output)
        write_new_json(output, json.loads(verified.stdout))
        checked = read_authorization(output, quote["quoteDigest"])
        if checked is None:
            raise ServiceError("verified authorization result is absent")
        self.ledger.transition(order_id, expected="BUYER_CONFIRMED", state="PROOF_VERIFIED", evidence={
            "quoteDigest": quote["quoteDigest"], "tradeCommitment": checked["tradeCommitment"],
            "verifierResultSha256": sha256_file(output), "termsSha256": quote_row["terms_sha256"],
            "trustSha256": sha256_file(auth / "trust.json"),
            "authorizationSha256": sha256_file(auth / "authorization.json"),
            "issuerAuthentication": checked["issuerAuthentication"],
            "issuanceProvenance": checked["issuanceProvenance"],
            "credentialAuthorityAuthentication": checked["credentialAuthorityAuthentication"],
            "investorEligibility": checked["investorEligibility"],
            "exactTradeAuthorization": checked["exactTradeAuthorization"]})
        self.log({"orderId": order_id, "state": "PROOF_VERIFIED", "quoteDigest": quote["quoteDigest"]})

    def authorize(self, order: dict) -> None:
        order_id = order["id"]
        quote_row, _, quote, qdir = self._quote(order_id)
        tip = self._fresh_tip(quote)
        if tip < 0:
            self._expire_before_signing(order, "CHAIN_ADVANCED_BEFORE_AUTHORIZATION")
            return
        work = self._work(order_id)
        asset = quote["assetId"]
        buyer = parse(work / "buyer-before.log", "Buyer", asset)
        seller = parse(work / "seller-before.log", "Seller", asset)
        if buyer["height"] != tip or seller["height"] != tip:
            raise ServiceError("wallet scans are not at the authorized tip")
        detail = self.ledger.get_order(order_id, participant_id=order["buyer_id"])
        proof = json.loads(next(e for e in detail["evidence"] if e["kind"] == "PROOF_VERIFIED")["value_json"])
        auth = work / "authorization"
        if (proof["termsSha256"] != sha256_file(qdir / "terms.json") or
                proof["trustSha256"] != sha256_file(auth / "trust.json") or
                proof["authorizationSha256"] != sha256_file(auth / "authorization.json")):
            raise ServiceError("proof or exact trade terms changed")
        try:
            for role in ("buyer", "seller"):
                wallet = self._wallet(quote_row["buyer_pairing_id"]) if role == "buyer" else None
                write_lines(work / f"{role}-review.log",
                            self.tools.signer(role, "review", "--terms", str(qdir / "terms.json"),
                                              wallet=wallet))
        except ToolError as exc:
            self.ledger.fail(order_id, expected="PROOF_VERIFIED", state="REJECTED", release=True,
                             evidence={"reason": "WALLET_REVIEW_REJECTED_TERMS",
                                       "quoteDigest": quote["quoteDigest"], "detail": str(exc)[:200]})
            return
        self.ledger.transition(order_id, expected="PROOF_VERIFIED", state="TRADE_AUTHORIZED", evidence={
            "quoteDigest": quote["quoteDigest"], "assetBase": asset,
            "buyerId": order["buyer_id"], "sellerId": order["seller_id"], "tipHeight": tip,
            "termsSha256": quote_row["terms_sha256"]})
        self._note(order_id, "Authorized. Waiting for both paired wallet companions.")

    def start(self, order: dict) -> None:
        order_id = order["id"]
        quote_row, _, quote, qdir = self._quote(order_id)
        if self._fresh_tip(quote) < 0:
            self._expire_before_signing(order, "CHAIN_ADVANCED_BEFORE_SIGNING")
            return
        online = [self.ledger.companion_online(quote_row[key], stale_seconds=self.config.companion_stale_seconds)
                  for key in ("buyer_pairing_id", "seller_pairing_id")]
        if not all(online):
            if quote_row["valid_until"] + self.config.quote_validity_seconds <= time.time():
                self._expire_before_signing(order, "WALLET_COMPANIONS_NOT_CONNECTED")
            else:
                missing = " and ".join(role for role, ok in zip(("buyer", "seller"), online) if not ok)
                self._note(order_id, f"Waiting for the {missing} wallet companion to connect.")
            return
        if any(row["mode"] != self.config.approval_mode for row in online):
            raise ServiceError("wallet companion approval mode differs from service mode")
        execution, created = self.ledger.start_execution(order_id, approval_mode=self.config.approval_mode)
        if not created:
            return
        work = self._work(order_id)
        socket = self.config.socket_path(order_id)
        socket.parent.mkdir(mode=0o700, exist_ok=True)
        if socket.exists():
            raise ServiceError("approval socket already exists")
        auth = work / "authorization"
        process = self.tools.spawn_execute_live([
            "--terms", str(qdir / "terms.json"), "--socket", str(socket),
            "--wait-secs", str(self.config.approval_wait_seconds),
            "--out", str(work / "receipt.json"),
            "--session-result", str(qdir / "session-result.json"),
            "--trust", str(auth / "trust.json"), "--authorization", str(auth / "authorization.json"),
            "--authorization-verifier", str(Path(__file__).resolve().parents[1] /
                                            "checkout/authorization/native_zec_v2.js"),
            "--authorization-node", str(self.config.node_bin),
            "--authorization-result", str(work / "authorization-result.json")],
            work / "coordinator.log.raw")
        self.ledger.set_execution_pid(order_id, process.pid)
        self._note(order_id, "Approval window open. Buyer: review and approve in your wallet companion.")
        self.log({"orderId": order_id, "event": "EXECUTION_STARTED", "pid": process.pid,
                  "approvalMode": self.config.approval_mode})

    @staticmethod
    def _coordinator_alive(pid: int | None) -> bool:
        if not pid:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        try:
            waited, _ = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                return False
        except ChildProcessError:
            pass
        command = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                 capture_output=True, text=True).stdout
        return "purchase-coordinator" in command and "execute-live" in command

    def _signer_result(self, order_id: str, role: str) -> dict | None:
        path = self._work(order_id) / f"{role}-approval.json"
        if not path.exists():
            return None
        attestation = small_json(path, 8192)
        if (attestation.get("orderId") != order_id or attestation.get("role") != role
                or attestation.get("result") not in ("AUTHORIZED", "REJECTED")):
            raise ServiceError("malformed wallet companion attestation")
        return attestation

    def _record_approval(self, order: dict, role: str, quote: dict) -> bool:
        order_id = order["id"]
        attestation = self._signer_result(order_id, role)
        if attestation is None or attestation["result"] != "AUTHORIZED":
            return False
        quote_row = self.ledger.order_quote(order_id)
        execution = self.ledger.execution(order_id)
        if (attestation.get("quoteDigest") != quote["quoteDigest"]
                or attestation.get("pairingId") != quote_row[f"{role}_pairing_id"]
                or attestation.get("mode") != execution["approval_mode"]
                or attestation.get("mode") != self.config.approval_mode):
            raise ServiceError("wallet companion attestation is for another trade or mode")
        log = self._work(order_id) / f"{role}-signer.log"
        lines = [line for line in log.read_text().splitlines() if line.startswith("PHASE35F_AUTHORIZED ")]
        if len(lines) != 1:
            raise ServiceError("exactly one successful signer authorization required")
        line = lines[0]
        payment = quote["paymentAmount"]
        effects = ((f"{quote['assetId']}:{quote['quantity']}", f"native:-{payment}") if role == "buyer"
                   else (f"{quote['assetId']}:-{quote['quantity']}", f"native:{payment}"))
        if (f"role={role.title()} " not in line or f"terms_hash={quote['quoteDigest']} " not in line
                or " proof=verified spend_auth=verified" not in line
                or not all(effect in line for effect in effects)):
            raise ServiceError("signer did not authorize exact role and trade effects")
        prior, target = (("TRADE_AUTHORIZED", "BUYER_APPROVED") if role == "buyer"
                         else ("BUYER_APPROVED", "SELLER_APPROVED"))
        self.ledger.transition(order_id, expected=prior, state=target, evidence={
            "quoteDigest": quote["quoteDigest"], "role": role,
            "approvalMode": attestation["mode"], "signerLogSha256": sha256_file(log),
            "attestationSha256": sha256_file(self._work(order_id) / f"{role}-approval.json")})
        self.log({"orderId": order_id, "state": target, "approvalMode": attestation["mode"]})
        return True

    def _terminate(self, pid: int) -> None:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass

    def monitor(self, order: dict) -> None:
        order_id = order["id"]
        _, _, quote, qdir = self._quote(order_id)
        execution = self.ledger.execution(order_id)
        state = order["state"]
        if self._coordinator_alive(execution["coordinator_pid"]):
            role = {"TRADE_AUTHORIZED": "buyer", "BUYER_APPROVED": "seller"}.get(state)
            if role is None:
                return
            result = self._signer_result(order_id, role)
            if result and result["result"] == "REJECTED":
                # The other wallet must never be asked to sign a trade that
                # this wallet declined. End the approval window now.
                self._terminate(execution["coordinator_pid"])
                return
            if self._record_approval(order, role, quote) and role == "buyer":
                self._note(order_id, "Buyer approved. Seller: review and approve in your wallet companion.")
            return
        socket = self.config.socket_path(order_id)
        if socket.is_socket():
            socket.unlink()
        session = read_session(qdir / "session-result.json")
        outcome = session["outcome"] if session else "MISSING"
        if outcome == "ACCEPTED":
            for role in ("buyer", "seller"):
                current = self.ledger.get_order(order_id, participant_id=order["buyer_id"])["state"]
                if (role == "buyer" and current == "TRADE_AUTHORIZED") or (
                        role == "seller" and current == "BUYER_APPROVED"):
                    if not self._record_approval(dict(order, state=current), role, quote):
                        self.ledger.fail(order_id, expected=current, state="UNCERTAIN", release=False,
                                         evidence={"reason": "ACCEPTED_WITHOUT_ATTESTED_APPROVAL",
                                                   "quoteDigest": quote["quoteDigest"]})
                        return
            self.settle(order)
            return
        rejected = [role for role in ("buyer", "seller")
                    if (self._signer_result(order_id, role) or {}).get("result") == "REJECTED"]
        both_approved = state == "SELLER_APPROVED"
        if outcome == "UNCERTAIN" or (both_approved and outcome != "FAILED"):
            self.ledger.fail(order_id, expected=state, state="UNCERTAIN", release=False,
                             evidence={"reason": "COORDINATOR_STOPPED_AFTER_BOTH_APPROVALS",
                                       "sessionOutcome": outcome, "quoteDigest": quote["quoteDigest"]})
            self.ledger.finish_execution(order_id, "UNCERTAIN")
            return
        reason = (f"{rejected[0].upper()}_WALLET_REJECTED" if rejected else
                  "COORDINATOR_AUTHORIZATION_REJECTED" if not (self._work(order_id) / "authorization-result.json").exists()
                  else "WALLET_APPROVAL_TIMEOUT_OR_COORDINATOR_FAILURE")
        evidence = {"reason": reason, "sessionOutcome": outcome, "quoteDigest": quote["quoteDigest"],
                    "transactionSubmitted": False}
        if state == "BUYER_APPROVED":
            # The buyer's signed group existed only in the stopped coordinator's
            # memory; it was never persisted or submitted.
            evidence["buyerGroupDisclosedToCoordinatorOnly"] = True
        self.ledger.fail(order_id, expected=state, state="EXECUTION_FAILED", release=True, evidence=evidence)
        self.ledger.finish_execution(order_id, "FAILED")
        self.log({"orderId": order_id, "state": "EXECUTION_FAILED", "reason": reason})

    def settle(self, order: dict) -> None:
        order_id = order["id"]
        quote_row, _, quote, qdir = self._quote(order_id)
        work = self._work(order_id)
        session = read_session(qdir / "session-result.json")
        if session is None or session["outcome"] != "ACCEPTED":
            raise ServiceError("coordinator did not publish an accepted receipt")
        receipt = small_json(work / "receipt.json", 8192)
        if session["receipt"] != receipt:
            raise ServiceError("coordinator receipt differs from session")
        state = self.ledger.get_order(order_id, participant_id=order["buyer_id"])["state"]
        if state == "SELLER_APPROVED":
            raw = (work / "coordinator.log.raw").read_text()
            executed = [line for line in raw.splitlines() if line.startswith(
                f"PHASE35G_EXECUTED txid={receipt['txid']} height={receipt['height']} ")]
            if len(executed) != 1:
                raise ServiceError("coordinator did not report the executed transaction")
            write_lines(work / "coordinator.log", executed)
            self.ledger.transition(order_id, expected="SELLER_APPROVED", state="SUBMITTED", evidence={
                "quoteDigest": quote["quoteDigest"], "txid": receipt["txid"],
                "coordinatorLogSha256": sha256_file(work / "coordinator.log")})
            self.ledger.finish_execution(order_id, "ACCEPTED")
            state = "SUBMITTED"
        if state == "SUBMITTED":
            block = self.tools.rpc("getblock", [str(receipt["height"]), 1])
            tx = self.tools.rpc("getrawtransaction", [receipt["txid"], 1])
            if (receipt["txid"] not in block.get("tx", []) or block.get("hash") != receipt["block_hash"]
                    or tx.get("height") != receipt["height"]
                    or len(tx.get("hex", "")) // 2 != receipt["tx_bytes"]):
                raise ServiceError("transaction inclusion differs from coordinator receipt")
            self.ledger.transition(order_id, expected="SUBMITTED", state="INCLUDED", evidence={
                "txid": receipt["txid"], "height": receipt["height"], "blockHash": receipt["block_hash"]})
            state = "INCLUDED"
        if state == "INCLUDED":
            asset = quote["assetId"]
            buyer_wallet = self._wallet(quote_row["buyer_pairing_id"])
            for role in ("buyer", "seller"):
                after = self._scan(role, asset, work / f"{role}-after.log",
                                   buyer_wallet if role == "buyer" else None)
                if after["height"] < receipt["height"]:
                    raise ServiceError("wallet scan predates the accepted block")
            b0, b1, s0, s1 = (parse(work / f"{name}.log", name.split("-")[0].title(), asset)
                              for name in ("buyer-before", "buyer-after", "seller-before", "seller-after"))
            if (b1["assetUnits"] - b0["assetUnits"] != quote["quantity"]
                    or s0["assetUnits"] - s1["assetUnits"] != quote["quantity"]
                    or s1["nativeZat"] - s0["nativeZat"] != quote["paymentAmount"]
                    or b0["nativeZat"] - b1["nativeZat"] != quote["paymentAmount"]):
                # An included transaction whose wallet effects differ from the
                # exact quote is held, never reported as a settled purchase.
                self.ledger.fail(order_id, expected="INCLUDED", state="UNCERTAIN", release=False,
                                 evidence={"reason": "WALLET_SCANS_DO_NOT_MATCH_EXACT_EFFECTS",
                                           "txid": receipt["txid"]})
                return
            if not (work / "public-scans.json").exists():
                write_public_scans(qdir / "session-result.json", work / "public-scans.json",
                                   work / "buyer-before.log", work / "buyer-after.log",
                                   work / "seller-before.log", work / "seller-after.log")
            read_scans(work / "public-scans.json", session)
            if self.demo and buyer_wallet:
                self.demo.record_settlement_scans(buyer_wallet, asset, b1, s1)
            self.ledger.transition(order_id, expected="INCLUDED", state="RECONCILED", evidence={
                "txid": receipt["txid"], "buyerUnitsBefore": b0["assetUnits"],
                "buyerUnitsAfter": b1["assetUnits"], "sellerUnitsBefore": s0["assetUnits"],
                "sellerUnitsAfter": s1["assetUnits"], "sellerZatBefore": s0["nativeZat"],
                "sellerZatAfter": s1["nativeZat"], "scanSha256": sha256_file(work / "public-scans.json")})
            state = "RECONCILED"
        if state == "RECONCILED":
            block = self.tools.rpc("getblock", [str(receipt["height"]), 1])
            if receipt["txid"] not in block.get("tx", []) or block.get("hash") != receipt["block_hash"]:
                raise ServiceError("transaction is no longer included in the chain")
            mode = self.ledger.execution(order_id)["approval_mode"]
            record = {"version": 1, "orderId": order_id, "buyerId": order["buyer_id"],
                      "sellerId": order["seller_id"], "instrumentId": self._instrument(order),
                      "assetBase": quote["assetId"], "quantity": quote["quantity"],
                      "paymentZat": quote["paymentAmount"],
                      "priceZatPerUnit": quote_row["price_zat_per_unit"],
                      "priceClassification": quote_row["classification"],
                      "quoteDigest": quote["quoteDigest"], "txid": receipt["txid"],
                      "height": receipt["height"], "blockHash": receipt["block_hash"],
                      "approvalMode": mode, "rootedNoteLineage": "UNSUPPORTED"}
            self.ledger.transition(order_id, expected="RECONCILED", state="RECEIPT_AVAILABLE", evidence={
                "txid": receipt["txid"], "height": receipt["height"], "blockHash": receipt["block_hash"],
                "approvalMode": mode, "quoteDigest": quote["quoteDigest"]})
            path = work / "order-receipt.json"
            if path.exists():
                if small_json(path, 8192) != record:
                    raise ServiceError("existing order receipt differs")
            else:
                write_new_json(path, record)
            self._note(order_id, "Settled. Waiting for restart-durability blocks and a fresh wallet rescan.")
            self.log({"orderId": order_id, "state": "RECEIPT_AVAILABLE", "txid": receipt["txid"]})

    # ---------------------------------------------------------- durability

    def stabilize_if_due(self) -> None:
        # Only orders this service executed; pre-4E-C receipts are never
        # reprocessed and never trigger mining on their own.
        due = [order for order in self.ledger.orders_in(("RECEIPT_AVAILABLE",))
               if self.ledger.execution(order["id"]) is not None
               and (order_dir(self.config.exchange, order["id"]) / "order-receipt.json").exists()
               and not (order_dir(self.config.exchange, order["id"]) / "stabilization.json").exists()]
        if not due or self.ledger.orders_in(WALLET_COMMITTED_STATES):
            return
        if self.demo and len(due) < 3 and all(
                o["updated_at"] + 90 > time.time() for o in due):
            # Batch durability mining: every wallet scan replays the chain, so
            # 110 blocks per purchase would slow every later judge.
            return
        # Pinned Zebra drops its newest ~99 non-finalized blocks on restart.
        # Mining past that window makes the accepted block survive a restart
        # under this node's policy. It is not consensus finality.
        self.tools.coordinator("mine-empty", "--count", str(self.config.stabilization_blocks))
        anchor = persistent_chain.checkpoint(self.config.root, self.config.rpc_port,
                                             host=self.config.rpc_host)
        if anchor["anchorHeight"] < max(small_json(order_dir(self.config.exchange, o["id"]) /
                                                   "order-receipt.json")["height"] for o in due):
            raise ServiceError("checkpoint anchor does not cover the accepted purchase")
        rescan = (self.demo.rescan_after_stabilization(due) if self.demo
                  else self.rescan().parent.name)
        for order in due:
            write_new_json(order_dir(self.config.exchange, order["id"]) / "stabilization.json", {
                "version": 1, "policy": DURABILITY_POLICY,
                "minedEmptyBlocks": self.config.stabilization_blocks,
                "tipHeight": anchor["height"], "anchorHeight": anchor["anchorHeight"],
                "anchorHash": anchor["anchorHash"], "rescan": rescan})
            self._note(order["id"], "Settled and visible in a fresh wallet rescan.")
        self.log({"event": "STABILIZED", "anchorHeight": anchor["anchorHeight"], "rescan": rescan})

    def rescan(self) -> Path:
        """Fresh independent buyer and seller scans for every issued asset."""
        if self.demo:
            raise ServiceError("the custodial demo records wallet scans in the ledger")
        scans = self.config.exchange / "rescans"
        scans.mkdir(mode=0o700, exist_ok=True)
        # Same shape as the operator rescan's mktemp scan.XXXXXX, which the
        # portal requires before it will consider a scan directory.
        alphabet = string.ascii_letters + string.digits
        while True:
            scan_dir = scans / ("scan." + "".join(secrets.choice(alphabet) for _ in range(6)))
            try:
                scan_dir.mkdir(mode=0o700)
                break
            except FileExistsError:
                continue
        for instrument in INSTRUMENTS:
            for role in ("buyer", "seller"):
                self._scan(role, self.assets[instrument], scan_dir / f"{instrument}-{role}.log")
        portfolio = build_portfolio(self.config.exchange, self.config.chain_identity,
                                    restart=True, scan_dir=scan_dir)
        write_new_json(scan_dir / "portfolio.json", portfolio)
        return scan_dir / "portfolio.json"


def serve(config: ExchangeConfig, interval: float) -> None:
    config.check_tool_revision()
    Tools(config).check_isolation()
    lock_path = config.exchange / "order-service.lock"
    descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("another order service already owns this exchange root")
    service = OrderService(config)
    service.log({"event": "SERVICE_STARTED", "root": str(config.root),
                 "approvalMode": config.approval_mode, "protected": config.protected})
    while True:
        service.tick()
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("serve", "tick", "rescan"))
    parser.add_argument("--root", required=True)
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()
    os.umask(0o077)
    config = load_config(args.root)
    if args.action == "serve":
        serve(config, args.interval)
    elif args.action == "tick":
        OrderService(config).tick()
    else:
        print(OrderService(config).rescan())


if __name__ == "__main__":
    sys.exit(main())
