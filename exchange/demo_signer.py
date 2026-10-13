"""Restricted policy signer for the hosted judge sandbox (CUSTODIAL_DEMO_POLICY).

Run as its own process, next to the order service:

    python3 -m exchange.demo_signer serve --root /private/tmp/zwa4e-judge-...

It serves two kinds of disposable regtest wallets that the server holds:

* each judge's demo wallet, which signs only the exact quote that judge
  confirmed in the browser, inside the published per-order limits; and
* the demo dealer's seller wallet, which signs only under its standing policy:
  listed assets, the dealer's current listed price, bounded size, proceeds to
  its own address, and a buyer that is a live demo wallet.

Browser confirmation is a purchase request recorded by the server. It is not
an independent cryptographic signature by the judge, and this mode is never
reported as human wallet approval. The signer has no network listener, reads
no browser input, and never signs bytes: the pinned wallet signer builds its
own Action Group from structured terms and checks them against its own keys.
Every check below reads authoritative state: the ledger, the exact terms file
whose hash the quote recorded, the Groth16 evidence, and the live chain tip.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from checkout.quote import from_terms
from .config import ExchangeConfig, load as load_config
from .demo_ledger import DemoLedger
from .order_ledger import OrderError
from .order_paths import order_dir
from .tools import ToolError, Tools, phase_lines, write_lines, write_new_json

MODE = "CUSTODIAL_DEMO_POLICY"
NETWORK_FEE_ZAT = 5000
VERIFIED_KEYS = ("issuerAuthentication", "issuanceProvenance", "credentialAuthorityAuthentication",
                 "investorEligibility", "exactTradeAuthorization")


class PolicyRejected(Exception):
    """The order is outside the standing policy; nothing is signed."""


def _sha256(path: Path) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise PolicyRejected("unsafe terms file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DemoSigner:
    def __init__(self, config: ExchangeConfig, *, ledger: DemoLedger | None = None,
                 tools: Tools | None = None, log=None):
        if not config.demo_mode or config.demo is None:
            raise SystemExit("the demo signer serves only CUSTODIAL_DEMO_POLICY roots")
        if config.protected:
            raise SystemExit("the demo signer never serves a protected root")
        self.config = config
        self.policy = config.demo
        self.ledger = ledger or DemoLedger(config.ledger_path)
        self.tools = tools or Tools(config)
        self.log = log or (lambda event: print(json.dumps(event, sort_keys=True), flush=True))
        registry = json.loads((config.exchange / "instrument-registry.json").read_text())
        self.assets = {item["asset_base"] for item in registry["instruments"]}
        self.pid = os.getpid()
        self._dealer_address: str | None = None

    def dealer_address(self) -> str:
        if self._dealer_address is None:
            self._dealer_address = self.tools.wallet_address("seller")
        return self._dealer_address

    # -------------------------------------------------------------- presence

    def heartbeat(self) -> None:
        """Mark the pairings this signer serves as online."""
        for row in self.ledger.wallets_in(("READY",)):
            if row["pairing_id"] and row["expires_at"] > time.time() and self.ledger.pairing_usable(row["pairing_id"]):
                self.ledger.heartbeat(row["pairing_id"], pid=self.pid, mode=MODE)
        dealer = self.ledger.active_pairing(account_id=self.policy.dealer_account_id, role="seller")
        if dealer and dealer["method"] == "DEMO_DEALER_STANDING_POLICY":
            self.ledger.heartbeat(dealer["id"], pid=self.pid, mode=MODE)

    # ---------------------------------------------------------------- policy

    def _common(self, order: dict) -> tuple[dict, dict, dict, Path]:
        """Exact terms, quote and evidence shared by both policies."""
        quote_row = self.ledger.order_quote(order["id"])
        if not quote_row or quote_row["quote_digest"] != order["quote_digest"]:
            raise PolicyRejected("order has no server-generated quote")
        terms_path = order_dir(self.config.exchange, order["id"]) / "quote" / "terms.json"
        if _sha256(terms_path) != quote_row["terms_sha256"]:
            raise PolicyRejected("terms differ from the quoted terms")
        terms = json.loads(terms_path.read_text())
        quote = from_terms(terms)
        if quote["quoteDigest"] != order["quote_digest"]:
            raise PolicyRejected("terms do not hash to the quote digest")
        if terms["network"] != "regtest" or quote["network"] != "regtest":
            raise PolicyRejected("only the experimental regtest network is allowed")
        if terms["asset_base"] not in self.assets or terms["asset_base"] != order["asset_base"]:
            raise PolicyRejected("asset is not a listed demo asset")
        if terms["quantity"] != order["quantity"] or not 1 <= terms["quantity"] <= self.policy.max_units_per_order:
            raise PolicyRejected("quantity is outside the per-order limit")
        payment = terms["quantity"] * quote_row["price_zat_per_unit"]
        if terms["price_zat"] != payment or quote_row["payment_zat"] != payment or \
                payment > self.policy.max_payment_zat_per_order:
            raise PolicyRejected("payment is not quantity times the quoted price, or exceeds the limit")
        if terms["matcher_fee_zat"] != 0 or terms["network_fee_zat"] != NETWORK_FEE_ZAT or \
                quote["feePayer"] != "DISPOSABLE_REGTEST_SPONSOR" or quote["maximumTotalDebit"] != payment:
            raise PolicyRejected("fee terms differ from the sponsored demo fee")
        if terms["seller_proceeds_address"] != self.dealer_address():
            raise PolicyRejected("proceeds do not go to the demo dealer")
        proof = self.ledger.evidence_of(order["id"], "PROOF_VERIFIED")
        if not proof or proof.get("quoteDigest") != order["quote_digest"] or \
                any(proof.get(key) != "VERIFIED" for key in VERIFIED_KEYS):
            raise PolicyRejected("Groth16 authorization is not verified for this quote")
        authorized = self.ledger.evidence_of(order["id"], "TRADE_AUTHORIZED")
        if not authorized or authorized.get("quoteDigest") != order["quote_digest"]:
            raise PolicyRejected("trade is not authorized by the exchange")
        chain = self.tools.chain()
        if chain.get("state") not in ("CHAIN_ACTIVE", "CHAIN_RESTARTED") or \
                chain["height"] + 1 != terms["target_height"] or chain["height"] >= terms["tx_expiry_height"]:
            raise PolicyRejected("quote is not executable at the current chain tip")
        buyer = self.ledger.wallet_for_pairing(quote_row["buyer_pairing_id"])
        pairing = self.ledger.pairing(quote_row["buyer_pairing_id"])
        if (not buyer or not pairing or pairing["method"] != "CUSTODIAL_DEMO_WALLET"
                or buyer["state"] != "READY" or buyer["expires_at"] <= time.time()
                or buyer["account_id"] != order["buyer_id"] or pairing["revoked_at"] is not None
                or buyer["wallet_address"] != pairing["wallet_address"]
                or terms["buyer_receive_address"] != buyer["wallet_address"]):
            raise PolicyRejected("buyer is not this order's live demo wallet")
        return quote_row, terms, buyer, terms_path

    def check_buyer(self, order: dict) -> dict:
        quote_row, terms, buyer, terms_path = self._common(order)
        confirmation = self.ledger.confirmation(order["id"])
        if (not confirmation or confirmation.get("quoteDigest") != order["quote_digest"]
                or confirmation.get("buyerPairingId") != quote_row["buyer_pairing_id"]
                or confirmation["occurredAt"] > quote_row["valid_until"]):
            raise PolicyRejected("the judge did not confirm this exact quote in time")
        if self.ledger.counted_orders(order["buyer_id"]) > self.policy.max_orders_per_wallet:
            raise PolicyRejected("this demo wallet reached its order limit")
        if self.ledger.sponsored_last_hour() >= self.policy.max_sponsored_orders_per_hour:
            raise PolicyRejected("the hourly sponsored-fee budget is used up")
        return {"wallet": buyer["id"], "pairingId": quote_row["buyer_pairing_id"], "terms": terms_path,
                "checks": ["exact-terms-hash", "browser-confirmation", "regtest", "listed-asset",
                           "per-order-limits", "dealer-recipient", "sponsored-fee", "groth16-verified",
                           "fresh-tip", "wallet-order-limit", "hourly-fee-budget"]}

    def check_dealer(self, order: dict) -> dict:
        if self.ledger.setting("dealerPolicy") != "ACTIVE":
            raise PolicyRejected("the dealer has paused its standing policy")
        quote_row, terms, _buyer, terms_path = self._common(order)
        pairing = self.ledger.pairing(quote_row["seller_pairing_id"])
        if (not pairing or pairing["method"] != "DEMO_DEALER_STANDING_POLICY"
                or pairing["account_id"] != self.policy.dealer_account_id
                or order["seller_id"] != self.policy.dealer_account_id
                or pairing["wallet_address"] != self.dealer_address()):
            raise PolicyRejected("seller is not the demo dealer's standing policy")
        price = self.ledger.offer_price(order["offer_id"])
        if (not price or price["version"] != quote_row["price_version"]
                or price["price_zat_per_unit"] != quote_row["price_zat_per_unit"]):
            raise PolicyRejected("the dealer's listed price changed after this quote")
        approved = self.ledger.evidence_of(order["id"], "BUYER_APPROVED")
        if not approved or approved.get("quoteDigest") != order["quote_digest"]:
            raise PolicyRejected("the buyer wallet has not approved this quote")
        return {"wallet": None, "pairingId": quote_row["seller_pairing_id"], "terms": terms_path,
                "checks": ["policy-active", "exact-terms-hash", "regtest", "listed-asset",
                           "current-listed-price", "per-order-limits", "own-proceeds-address",
                           "live-demo-buyer", "groth16-verified", "buyer-approved-first", "fresh-tip"]}

    # ---------------------------------------------------------------- signing

    def pending(self, role: str) -> list[dict]:
        turn = "TRADE_AUTHORIZED" if role == "buyer" else "BUYER_APPROVED"
        result = []
        for order in self.ledger.orders_in((turn,)):
            execution = self.ledger.execution(order["id"])
            if (not execution or execution["finished_at"] is not None
                    or execution["approval_mode"] != MODE):
                continue
            if (order_dir(self.config.exchange, order["id"]) / f"{role}-approval.json").exists():
                continue
            if self.config.socket_path(order["id"]).is_socket():
                result.append(order)
        return result

    def approve(self, order: dict, role: str) -> str:
        work = order_dir(self.config.exchange, order["id"])
        try:
            checked = self.check_buyer(order) if role == "buyer" else self.check_dealer(order)
        except (PolicyRejected, OrderError, ToolError, KeyError, ValueError, OSError) as exc:
            reason = str(exc)[:200] or type(exc).__name__
            pairing = (self.ledger.order_quote(order["id"]) or {}).get(f"{role}_pairing_id")
            write_lines(work / f"{role}-signer.log",
                        [f"PHASE35F_SIGNER_REJECTED role={role.title()} reason=demo-policy"])
            write_new_json(work / f"{role}-approval.json", {
                "version": 1, "orderId": order["id"], "role": role,
                "quoteDigest": order["quote_digest"], "pairingId": pairing, "mode": MODE,
                "signerCommand": "not started: outside demo policy", "controllingTerminal": False,
                "result": "REJECTED", "policyReason": reason, "signerExit": None,
                "finishedAt": int(time.time())})
            self.log({"orderId": order["id"], "role": role, "event": "POLICY_REJECTED", "reason": reason})
            return "REJECTED"
        command, env, home = self.tools.signer_command(
            role, "authorize-live", "--terms", str(checked["terms"]), "--approve", order["quote_digest"],
            "--socket", str(self.config.socket_path(order["id"])), "--scripted-test-mode",
            wallet=checked["wallet"])
        result = subprocess.run(command, cwd=home, env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=self.config.approval_wait_seconds + 60)
        lines = phase_lines(result.stdout + "\n" + result.stderr)
        write_lines(work / f"{role}-signer.log", lines)
        authorized = result.returncode == 0 and any(line.startswith("PHASE35F_AUTHORIZED ") for line in lines)
        write_new_json(work / f"{role}-approval.json", {
            "version": 1, "orderId": order["id"], "role": role, "quoteDigest": order["quote_digest"],
            "pairingId": checked["pairingId"], "mode": MODE,
            "signerCommand": "authorize-live under custodial demo policy",
            "controllingTerminal": False, "policyChecks": checked["checks"],
            "result": "AUTHORIZED" if authorized else "REJECTED",
            "signerExit": result.returncode, "finishedAt": int(time.time())})
        self.log({"orderId": order["id"], "role": role, "event": "POLICY_SIGNED" if authorized else "SIGNER_FAILED"})
        return "AUTHORIZED" if authorized else "REJECTED"

    def run_once(self) -> None:
        self.heartbeat()
        for role in ("buyer", "seller"):
            for order in self.pending(role):
                self.approve(order, role)


def serve(config: ExchangeConfig, interval: float) -> None:
    config.check_tool_revision()
    Tools(config).check_isolation()
    lock_path = config.exchange / "demo-signer.lock"
    descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("another demo signer already serves this exchange root")
    signer = DemoSigner(config)
    signer.log({"event": "DEMO_SIGNER_STARTED", "root": str(config.root), "mode": MODE})
    while True:
        try:
            signer.run_once()
        except (OrderError, ToolError, OSError, ValueError) as exc:
            signer.log({"event": "DEMO_SIGNER_ERROR", "error": str(exc)[:300]})
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("serve",))
    parser.add_argument("--root", required=True)
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()
    os.umask(0o077)
    serve(load_config(args.root), args.interval)


if __name__ == "__main__":
    sys.exit(main())
