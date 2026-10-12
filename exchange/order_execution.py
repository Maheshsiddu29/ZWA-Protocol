"""Trusted local bridge from one funded order to observed checkout evidence.

This is an operator CLI, never an HTTP signer. Its outputs contain no wallet
seeds or signed action groups. The pinned coordinator and independent wallet
signers remain the transaction authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from checkout.api import read_authorization, read_scans, read_session
from checkout.quote import from_terms
from checkout.scans import parse
from .order_ledger import OrderError, OrderLedger
from .persistent_chain import _rpc, status as chain_status

ORDER_ID = "ec27f52d2e7ad69287f7d4d4357025af"
ROOT = Path("/private/tmp/zwa4e-phase4e-b")
CONTAINER = "zwa-phase4e-b"
PORT = 18321
AUTH_SCRIPT = Path(__file__).resolve().parents[1] / "checkout/authorization/native_zec_v2.js"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def _small_json(path: Path, limit: int = 65536) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise OrderError(f"unsafe or missing evidence: {path.name}")
    data = json.loads(path.read_text())
    if type(data) is not dict:
        raise OrderError("evidence must be an object")
    return data


def _sha(path: Path) -> str:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise OrderError("unsafe evidence file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ExistingGoldOrder:
    def __init__(self, root: Path = ROOT, order_id: str = ORDER_ID):
        if root != ROOT or order_id != ORDER_ID:
            raise OrderError("this operator bridge is pinned to the approved gold order")
        self.root = root
        self.order_id = order_id
        self.exchange = root / "exchange"
        self.work = self.exchange / "orders" / order_id
        self.quote_dir = self.exchange / "order-quotes/gold-1"
        self.ledger = OrderLedger(self.exchange / "exchange.sqlite")
        self.terms_path = self.quote_dir / "terms.json"
        self.session_path = self.quote_dir / "session-result.json"
        self.terms = _small_json(self.terms_path)
        self.session = read_session(self.session_path)
        if self.session is None:
            raise OrderError("prepared checkout session is missing")
        self.quote = from_terms(self.terms)
        self.order = self.ledger.get_order(order_id, participant_id="buyer")
        if (self.order["buyer_id"] != "buyer" or self.order["seller_id"] != "seller"
                or self.order["state"] in ("CANCELLED", "EXPIRED")
                or self.order["quote_digest"] != self.quote["quoteDigest"]
                or self.session["sessionId"] != self.quote["quoteDigest"]
                or self.session["quote"] != self.quote
                or self.order["quote_expiry_height"] != self.quote["transactionExpiry"]
                or self.order["asset_base"] != self.terms["asset_base"]
                or self.order["quantity"] != self.terms["quantity"]):
            raise OrderError("reserved order, prepared session, or exact quote differs")
        registry = _small_json(self.exchange / "instrument-registry.json")
        if not any(item.get("id") == "zwa-gold-reference" and
                   item.get("asset_base") == self.terms["asset_base"]
                   for item in registry.get("instruments", [])):
            raise OrderError("AssetBase is not the issued gold-reference instrument")

    def _live(self) -> int:
        state = chain_status(self.root, CONTAINER, PORT)
        if state.get("state") not in ("CHAIN_ACTIVE", "CHAIN_RESTARTED"):
            raise OrderError("protected chain is unavailable or reset")
        identity = _small_json(self.root / "chain-identity.json", 8192)
        if self.order["chain_genesis"] != identity["genesisHash"]:
            raise OrderError("order chain genesis differs")
        return state["height"]

    def _before(self, tip: int) -> tuple[dict, dict]:
        asset = self.terms["asset_base"]
        buyer = parse(self.work / "buyer-before.log", "Buyer", asset)
        seller = parse(self.work / "seller-before.log", "Seller", asset)
        if buyer["height"] != tip or seller["height"] != tip:
            raise OrderError("wallet scans must match current chain tip")
        if (seller["assetUnits"] < self.order["reserved"] or
                buyer["nativeZat"] < self.quote["paymentAmount"]):
            raise OrderError("wallet inputs or reserved inventory are unavailable")
        return buyer, seller

    def preflight(self) -> dict:
        if self.order["state"] != "QUOTE_READY" or self.session["outcome"] != "QUOTE_READY":
            raise OrderError("gold order is not an unexecuted quote")
        tip = self._live()
        if tip + 1 != self.quote["targetHeight"] or tip >= self.quote["transactionExpiry"]:
            raise OrderError("quote target or expiry is stale")
        buyer, seller = self._before(tip)
        return {"orderId": self.order_id, "quoteDigest": self.quote["quoteDigest"],
                "assetBase": self.terms["asset_base"], "tipHeight": tip,
                "buyerUnits": buyer["assetUnits"], "sellerUnits": seller["assetUnits"],
                "reserved": self.order["reserved"], "termsSha256": _sha(self.terms_path)}

    def proof(self, node: str) -> dict:
        pre = self.preflight()
        auth_dir = self.work / "authorization"
        result = subprocess.run([node, str(AUTH_SCRIPT), "verify", str(self.terms_path),
                                 self.quote["quoteDigest"], str(auth_dir / "trust.json"),
                                 str(auth_dir / "authorization.json")],
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise OrderError("Groth16 and authority verification rejected")
        verified = json.loads(result.stdout)
        output = self.work / "preflight-authorization.json"
        if output.exists():
            if _small_json(output, 8192) != verified:
                raise OrderError("preflight verifier output changed")
        else:
            output.write_text(json.dumps(verified, sort_keys=True) + "\n")
            output.chmod(0o600)
        checked = read_authorization(output, self.quote["quoteDigest"])
        if checked is None:
            raise OrderError("verified proof result is absent")
        evidence = {"quoteDigest": self.quote["quoteDigest"],
                    "tradeCommitment": checked["tradeCommitment"],
                    "verifierResultSha256": _sha(output), "termsSha256": pre["termsSha256"],
                    "trustSha256": _sha(auth_dir / "trust.json"),
                    "authorizationSha256": _sha(auth_dir / "authorization.json"),
                    "issuerAuthentication": checked["issuerAuthentication"],
                    "credentialAuthorityAuthentication": checked["credentialAuthorityAuthentication"],
                    "investorEligibility": checked["investorEligibility"]}
        return self.ledger.transition(self.order_id, expected="QUOTE_READY",
                                      state="PROOF_VERIFIED", evidence=evidence)

    def authorize(self) -> dict:
        if self.order["state"] != "PROOF_VERIFIED":
            raise OrderError("proof has not been recorded")
        tip = self._live()
        if tip + 1 != self.quote["targetHeight"]:
            raise OrderError("quote target is stale")
        self._before(tip)
        proof = next(e for e in self.order["evidence"] if e["kind"] == "PROOF_VERIFIED")
        prior = json.loads(proof["value_json"])
        if prior["termsSha256"] != _sha(self.terms_path) or (
                prior["authorizationSha256"] != _sha(self.work / "authorization/authorization.json") or
                prior["trustSha256"] != _sha(self.work / "authorization/trust.json")):
            raise OrderError("proof or exact trade terms changed")
        return self.ledger.transition(self.order_id, expected="PROOF_VERIFIED",
                                      state="TRADE_AUTHORIZED", evidence={
                                          "quoteDigest": self.quote["quoteDigest"],
                                          "assetBase": self.terms["asset_base"],
                                          "buyerId": "buyer", "sellerId": "seller",
                                          "tipHeight": tip, "termsSha256": prior["termsSha256"]})

    def approval(self, role: str) -> dict:
        if role not in ("buyer", "seller"):
            raise OrderError("invalid signer role")
        path = self.work / f"{role}-signer.log"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 131072:
            raise OrderError("missing signer observation")
        lines = [line for line in path.read_text().splitlines()
                 if line.startswith("PHASE35F_AUTHORIZED ")]
        if len(lines) != 1:
            raise OrderError("exactly one successful signer authorization required")
        line = lines[0]
        expected_role = role.title()
        effects = (f"{self.terms['asset_base']}:{self.order['quantity']}",
                   f"native:-{self.quote['paymentAmount']}") if role == "buyer" else (
                       f"{self.terms['asset_base']}:-{self.order['quantity']}",
                       f"native:{self.quote['paymentAmount']}")
        if (f"role={expected_role} " not in line or
                f"terms_hash={self.quote['quoteDigest']} " not in line or
                " proof=verified spend_auth=verified" not in line or
                not all(effect in line for effect in effects)):
            raise OrderError("signer did not authorize exact role and trade effects")
        prior = "TRADE_AUTHORIZED" if role == "buyer" else "BUYER_APPROVED"
        target = "BUYER_APPROVED" if role == "buyer" else "SELLER_APPROVED"
        return self.ledger.transition(self.order_id, expected=prior, state=target,
                                      evidence={"quoteDigest": self.quote["quoteDigest"],
                                                "role": role, "approvalMode": "SCRIPTED_TEST_MODE",
                                                "signerLogSha256": _sha(path)})

    def submitted(self) -> dict:
        session = read_session(self.session_path)
        receipt = _small_json(self.work / "receipt.json", 8192)
        if session is None or session["outcome"] != "ACCEPTED" or session["receipt"] != receipt:
            raise OrderError("coordinator did not publish accepted exact-quote receipt")
        log = self.work / "coordinator.log"
        if (log.is_symlink() or not log.is_file() or
                f"PHASE35G_EXECUTED txid={receipt['txid']} height={receipt['height']} "
                not in log.read_text()):
            raise OrderError("coordinator did not report executed transaction")
        return self.ledger.transition(self.order_id, expected="SELLER_APPROVED",
                                      state="SUBMITTED", evidence={
                                          "quoteDigest": self.quote["quoteDigest"],
                                          "txid": receipt["txid"],
                                          "coordinatorLogSha256": _sha(log)})

    def included(self) -> dict:
        receipt = read_session(self.session_path)["receipt"]
        block = _rpc(PORT, "getblock", [str(receipt["height"]), 1])
        tx = _rpc(PORT, "getrawtransaction", [receipt["txid"], 1])
        if (receipt["txid"] not in block.get("tx", []) or
                block.get("hash") != receipt["block_hash"] or
                tx.get("height") != receipt["height"] or
                len(tx.get("hex", "")) // 2 != receipt["tx_bytes"]):
            raise OrderError("transaction inclusion differs from coordinator receipt")
        return self.ledger.transition(self.order_id, expected="SUBMITTED",
                                      state="INCLUDED", evidence={
                                          "txid": receipt["txid"],
                                          "height": receipt["height"],
                                          "blockHash": receipt["block_hash"]})

    def reconciled(self) -> dict:
        session = read_session(self.session_path)
        scans = read_scans(self.work / "public-scans.json", session)
        if scans is None:
            raise OrderError("independent wallet scans are absent")
        b0,b1,s0,s1 = (scans[key] for key in (
            "buyerBefore", "buyerAfter", "sellerBefore", "sellerAfter"))
        if (b1["height"] < session["receipt"]["height"] or
                s1["height"] < session["receipt"]["height"] or
                b1["assetUnits"] - b0["assetUnits"] != self.order["quantity"] or
                s0["assetUnits"] - s1["assetUnits"] != self.order["quantity"] or
                s1["nativeZat"] - s0["nativeZat"] != self.quote["paymentAmount"] or
                b0["nativeZat"] - b1["nativeZat"] != self.quote["paymentAmount"]):
            raise OrderError("wallet scans do not match exact purchase effects")
        r = session["receipt"]
        return self.ledger.transition(self.order_id, expected="INCLUDED",
                                      state="RECONCILED", evidence={
                                          "txid": r["txid"],
                                          "buyerUnitsBefore": b0["assetUnits"],
                                          "buyerUnitsAfter": b1["assetUnits"],
                                          "sellerUnitsBefore": s0["assetUnits"],
                                          "sellerUnitsAfter": s1["assetUnits"],
                                          "sellerZatBefore": s0["nativeZat"],
                                          "sellerZatAfter": s1["nativeZat"],
                                          "scanSha256": _sha(self.work / "public-scans.json")})

    def receipt(self) -> dict:
        session = read_session(self.session_path)
        r = session["receipt"]
        self._live()
        block = _rpc(PORT, "getblock", [str(r["height"]), 1])
        if r["txid"] not in block.get("tx", []) or block.get("hash") != r["block_hash"]:
            raise OrderError("transaction is no longer included in protected chain")
        record = {"version": 1, "orderId": self.order_id,
                  "buyerId": "buyer", "sellerId": "seller",
                  "instrumentId": "zwa-gold-reference", "assetBase": self.terms["asset_base"],
                  "quantity": self.order["quantity"], "paymentZat": self.quote["paymentAmount"],
                  "quoteDigest": self.quote["quoteDigest"], "txid": r["txid"],
                  "height": r["height"], "blockHash": r["block_hash"],
                  "approvalMode": "SCRIPTED_TEST_MODE",
                  "rootedNoteLineage": "UNSUPPORTED"}
        path = self.work / "order-receipt.json"
        if path.exists() and _small_json(path, 8192) != record:
            raise OrderError("existing receipt differs")
        self.ledger.transition(self.order_id, expected="RECONCILED",
                               state="RECEIPT_AVAILABLE", evidence={
                                   "txid": r["txid"], "height": r["height"],
                                   "blockHash": r["block_hash"],
                                   "approvalMode": "SCRIPTED_TEST_MODE",
                                   "quoteDigest": self.quote["quoteDigest"]})
        if not path.exists():
            path.write_text(json.dumps(record, indent=2) + "\n")
            path.chmod(0o600)
        return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "proof", "authorize", "buyer",
                                           "seller", "submitted", "included", "reconciled", "receipt"))
    parser.add_argument("--node", default="node")
    args = parser.parse_args()
    order = ExistingGoldOrder()
    action = {"preflight": order.preflight, "proof": lambda: order.proof(args.node),
              "authorize": order.authorize, "buyer": lambda: order.approval("buyer"),
              "seller": lambda: order.approval("seller"), "submitted": order.submitted,
              "included": order.included, "reconciled": order.reconciled,
              "receipt": order.receipt}[args.action]
    print(json.dumps(action(), sort_keys=True))


if __name__ == "__main__":
    main()
