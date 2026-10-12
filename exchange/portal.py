"""Fixed-action JSON bridge between the local exchange portal and the ledger.

The Next.js server authenticates the participant and passes the account ID,
role and a hash of its session identifier on stdin. This bridge never signs,
generates proofs, starts processes, or accepts a path, command, RPC method, or
monetary digest of its own choosing from the browser. Order execution happens
only in the separate operator service and the participants' wallet companions.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import time

from checkout.quote import from_terms
from .config import ConfigError, ExchangeConfig, load as load_config
from .order_ledger import OrderError, OrderLedger, PRICE_CLASSIFICATION, create_live_offer
from .order_paths import order_dir, quote_dir
from .persistent_chain import _rpc
from .portfolio_scans import build as build_portfolio

HEX16 = re.compile(r"[0-9a-f]{32}\Z")
HEX32 = re.compile(r"[0-9a-f]{64}\Z")
IDENT = re.compile(r"[a-zA-Z0-9_-]{1,80}\Z")
KEY = re.compile(r"[a-zA-Z0-9_-]{8,80}\Z")
STAGES = ("BUYER_REQUESTED", "QUOTE_READY", "BUYER_CONFIRMED", "PROOF_VERIFIED",
          "TRADE_AUTHORIZED", "BUYER_APPROVED", "SELLER_APPROVED", "SUBMITTED", "INCLUDED",
          "RECONCILED", "RECEIPT_AVAILABLE")


class PortalError(ValueError):
    pass


class Portal:
    def __init__(self, config: ExchangeConfig):
        self.config = config
        self.ledger = OrderLedger(config.ledger_path)

    # ------------------------------------------------------------ chain/scans

    def _identity(self) -> dict:
        return json.loads(self.config.chain_identity.read_text())

    def live(self) -> tuple[bool, int | None]:
        identity = self._identity()
        if identity.get("detectedReset"):
            return False, None
        port = self.config.rpc_port
        try:
            height = _rpc(port, "getblockcount")
            if (_rpc(port, "getblockhash", [0]) != identity["genesisHash"] or
                    _rpc(port, "getblockhash", [identity["anchorHeight"]]) != identity["anchorHash"]):
                return False, None
            return True, height
        except Exception:
            return False, None

    def _portfolio_at_tip(self, tip: int) -> dict | None:
        scans = [p for p in (self.config.exchange / "rescans").glob("scan.*/portfolio.json")
                 if p.is_file() and not p.is_symlink()]
        if not scans:
            return None
        newest = max(scans, key=lambda p: p.stat().st_mtime_ns)
        portfolio = build_portfolio(self.config.exchange, self.config.chain_identity,
                                    restart=True, scan_dir=newest.parent)
        return portfolio if portfolio["scanHeight"] == tip else None

    def _registry(self) -> dict[str, dict]:
        data = json.loads((self.config.exchange / "instrument-registry.json").read_text())
        return {item["id"]: item for item in data["instruments"]}

    # --------------------------------------------------------------- wallets

    def wallet(self, actor: str, role: str, session: str) -> dict:
        pairing = self.ledger.active_pairing(account_id=actor, role=role, session_hash=session)
        if not pairing:
            return {"paired": False, "role": role, "approvalMode": self.config.approval_mode}
        online = self.ledger.companion_online(pairing["id"], stale_seconds=self.config.companion_stale_seconds)
        address = pairing["wallet_address"]
        return {"paired": True, "role": role, "method": pairing["method"],
                "walletAddress": f"{address[:10]}…{address[-6:]}",
                "pairedAt": pairing["created_at"], "expiresAt": pairing["expires_at"],
                "companionOnline": online is not None, "approvalMode": self.config.approval_mode}

    def pair(self, actor: str, role: str, session: str, payload: dict) -> dict:
        if set(payload) != {"code"} or not isinstance(payload["code"], str):
            raise PortalError("pairing request schema")
        code = payload["code"].replace("-", "").replace(" ", "").upper()
        claimed = self.ledger.claim_pairing(code=code, account_id=actor, role=role, session_hash=session)
        return {"state": claimed["state"], "role": role,
                "next": "Confirm this account in your wallet companion terminal."}

    # ----------------------------------------------------------------- market

    def market(self, actor: str) -> dict:
        active, tip = self.live()
        registry = self._registry()
        if not active:
            return {"chainActive": False, "instruments": []}
        portfolio = self._portfolio_at_tip(tip)
        units = {h["assetBase"]: h["sellerUnits"] for h in portfolio["holdings"]} if portfolio else {}
        with self.ledger._connection() as db:
            rows = db.execute("""SELECT f.*,p.price_zat_per_unit,p.classification,p.version
                                 FROM offers f LEFT JOIN offer_prices p ON p.offer_id=f.id
                                 WHERE f.state='OFFER_CREATED' ORDER BY f.created_at,f.id""").fetchall()
        result = []
        for row in rows:
            item = registry.get(row["instrument_id"])
            if not item or item["asset_base"] != row["asset_base"]:
                continue
            available = min(row["capacity"] - row["reserved"] - row["sold"],
                            units.get(row["asset_base"], 0) - row["reserved"]) if portfolio else 0
            result.append({"offerId": row["id"], "instrumentId": row["instrument_id"],
                           "name": item["name"], "issuerId": item["issuer_id"],
                           "assetBase": row["asset_base"], "issuanceTxid": item["issuance_txid"],
                           "issuanceHeight": item["issuance_height"],
                           "availableUnits": max(available, 0),
                           "inventoryVerifiedAtTip": portfolio is not None,
                           "priceZatPerUnit": row["price_zat_per_unit"],
                           "priceClassification": row["classification"],
                           "priceVersion": row["version"],
                           "sellerId": row["seller_id"] if row["seller_id"] == actor else None})
        return {"chainActive": True, "chainHeight": tip, "instruments": result}

    def set_price(self, actor: str, payload: dict) -> dict:
        if set(payload) != {"offerId", "priceZatPerUnit"} or not HEX16.fullmatch(str(payload["offerId"])):
            raise PortalError("price request schema")
        row = self.ledger.set_price(seller_id=actor, offer_id=payload["offerId"],
                                    price_zat_per_unit=payload["priceZatPerUnit"])
        return {"offerId": row["offer_id"], "priceZatPerUnit": row["price_zat_per_unit"],
                "priceClassification": row["classification"], "priceVersion": row["version"]}

    def create_offer(self, actor: str, payload: dict) -> dict:
        if set(payload) != {"instrumentId", "capacity", "idempotencyKey"}:
            raise PortalError("offer request schema")
        offer = create_live_offer(self.config.root, self.config.container, self.config.rpc_port,
                                  seller_id=actor, instrument_id=payload["instrumentId"],
                                  capacity=payload["capacity"], idempotency_key=payload["idempotencyKey"])
        return {"offerId": offer["id"], "instrumentId": offer["instrument_id"], "capacity": offer["capacity"]}

    # ----------------------------------------------------------------- orders

    def buy(self, actor: str, session: str, payload: dict) -> dict:
        if (set(payload) != {"offerId", "quantity", "idempotencyKey"}
                or not HEX16.fullmatch(str(payload["offerId"]))
                or not KEY.fullmatch(str(payload["idempotencyKey"]))
                or type(payload["quantity"]) is not int or not 1 <= payload["quantity"] <= 1000):
            raise PortalError("purchase request schema")
        if not self.ledger.active_pairing(account_id=actor, role="buyer", session_hash=session):
            raise PortalError("pair your wallet before requesting a purchase")
        active, tip = self.live()
        if not active:
            raise PortalError("experimental chain is unavailable")
        offer = self.ledger.get_offer(payload["offerId"])
        price = self.ledger.offer_price(payload["offerId"]) if offer else None
        if not offer or not price:
            raise PortalError("offer has no seller price")
        portfolio = self._portfolio_at_tip(tip)
        if portfolio is None:
            raise PortalError("seller inventory scan is stale")
        holding = next((h for h in portfolio["holdings"] if h["assetBase"] == offer["asset_base"]), None)
        if not holding or holding["sellerUnits"] < offer["reserved"] + payload["quantity"]:
            raise PortalError("seller spendable inventory is unavailable")
        order = self.ledger.request_purchase(offer_id=offer["id"], buyer_id=actor,
                                             quantity=payload["quantity"],
                                             idempotency_key=payload["idempotencyKey"],
                                             chain_genesis=self._identity()["genesisHash"])
        return self.order(order["id"], actor, "buyer")

    def confirm(self, actor: str, session: str, payload: dict) -> dict:
        if (set(payload) != {"orderId", "quoteDigest"} or not HEX16.fullmatch(str(payload["orderId"]))
                or not HEX32.fullmatch(str(payload["quoteDigest"]))):
            raise PortalError("confirmation schema")
        pairing = self.ledger.active_pairing(account_id=actor, role="buyer", session_hash=session)
        if not pairing:
            raise PortalError("pair your wallet before confirming")
        self.ledger.confirm_quote(payload["orderId"], buyer_id=actor,
                                  quote_digest=payload["quoteDigest"], pairing_id=pairing["id"])
        return self.order(payload["orderId"], actor, "buyer")

    def cancel(self, actor: str, payload: dict) -> dict:
        if set(payload) != {"orderId"} or not HEX16.fullmatch(str(payload["orderId"])):
            raise PortalError("cancel request schema")
        self.ledger.close_before_approval(payload["orderId"], buyer_id=actor, state="CANCELLED")
        return self.order(payload["orderId"], actor, "buyer")

    def _quote_view(self, order_id: str, digest: str | None) -> dict | None:
        if not digest:
            return None
        terms_path = quote_dir(self.config.exchange, order_id) / "terms.json"
        if terms_path.is_symlink() or not terms_path.is_file():
            return None
        quote = from_terms(json.loads(terms_path.read_text()))
        if quote["quoteDigest"] != digest:
            return None
        row = self.ledger.order_quote(order_id)
        return {"digest": quote["quoteDigest"], "assetBase": quote["assetId"],
                "quantity": quote["quantity"], "paymentZat": quote["paymentAmount"],
                "priceZatPerUnit": row["price_zat_per_unit"] if row else None,
                "priceClassification": row["classification"] if row else "OPERATOR_SELECTED_TEST_PRICE",
                "networkFeeZat": quote["networkFee"], "feePayer": quote["feePayer"],
                "matcherFeeZat": quote["matcherFee"], "network": quote["network"],
                "targetHeight": quote["targetHeight"], "expiryHeight": quote["transactionExpiry"],
                "groupExpiryHeight": quote["groupExpiry"],
                "buyerReceiverCommitment": quote["buyerReceivingCommitment"],
                "sellerProceedsCommitment": quote["sellerProceedsCommitment"],
                "validUntil": row["valid_until"] if row else None}

    def order(self, order_id: str, actor: str, role: str) -> dict:
        if not HEX16.fullmatch(order_id):
            raise PortalError("invalid order")
        detail = self.ledger.get_order(order_id, participant_id=actor)
        viewer = "buyer" if detail["buyer_id"] == actor else "seller"
        if viewer != role:
            raise OrderError("order is unavailable to participant")
        evidence = {e["kind"]: json.loads(e["value_json"]) for e in detail["evidence"]}
        active, tip = self.live()
        if detail["chain_genesis"] != self._identity()["genesisHash"]:
            active = False
        quote = self._quote_view(order_id, detail["quote_digest"])
        quote_row = self.ledger.order_quote(order_id)
        execution = self.ledger.execution(order_id)
        state = detail["state"]
        awaiting = None
        if execution and execution["finished_at"] is None:
            awaiting = {"TRADE_AUTHORIZED": "buyer", "BUYER_APPROVED": "seller"}.get(state)
        own_companion = None
        if quote_row:
            own_companion = self.ledger.companion_online(
                quote_row[f"{viewer}_pairing_id"], stale_seconds=self.config.companion_stale_seconds) is not None
        proof = evidence.get("PROOF_VERIFIED", {})
        failure = next((evidence[s] for s in ("REJECTED", "EXPIRED", "EXECUTION_FAILED", "UNCERTAIN")
                        if s in evidence), None)
        receipt = None
        if state == "RECEIPT_AVAILABLE" and active:
            path = order_dir(self.config.exchange, order_id) / "order-receipt.json"
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= 8192:
                saved = json.loads(path.read_text())
                included = evidence.get("INCLUDED", {})
                if (saved.get("txid") == included.get("txid") and
                        saved.get("blockHash") == included.get("blockHash") and
                        saved.get("quoteDigest") == detail["quote_digest"]):
                    try:
                        block = _rpc(self.config.rpc_port, "getblock", [str(saved["height"]), 1])
                        if block["hash"] == saved["blockHash"] and saved["txid"] in block["tx"]:
                            receipt = {**saved, "confirmations": tip - saved["height"] + 1,
                                       "durability": self._durability(order_id)}
                    except Exception:
                        receipt = None
        reconciled = evidence.get("RECONCILED")
        effects = None
        if reconciled:
            effects = ({"assetUnits": reconciled["buyerUnitsAfter"] - reconciled["buyerUnitsBefore"],
                        "nativeZat": -quote["paymentZat"] if quote else None} if viewer == "buyer" else
                       {"assetUnits": reconciled["sellerUnitsAfter"] - reconciled["sellerUnitsBefore"],
                        "nativeZat": reconciled["sellerZatAfter"] - reconciled["sellerZatBefore"]})
        return {"id": detail["id"], "state": state, "viewer": viewer,
                "buyerId": detail["buyer_id"], "sellerId": detail["seller_id"],
                "instrumentId": detail["instrument_id"], "assetBase": detail["asset_base"],
                "quantity": detail["quantity"], "quote": quote,
                "events": [{"sequence": e["sequence"], "from_state": e["from_state"],
                            "to_state": e["to_state"], "occurred_at": e["occurred_at"]}
                           for e in detail["events"]],
                "note": detail["note"]["note"] if detail["note"] else None,
                "proofVerified": "PROOF_VERIFIED" in evidence,
                "proof": {key: proof.get(key) for key in (
                    "tradeCommitment", "issuerAuthentication", "issuanceProvenance",
                    "credentialAuthorityAuthentication", "investorEligibility",
                    "exactTradeAuthorization", "verifierResultSha256")} if proof else None,
                "buyerApprovalMode": evidence.get("BUYER_APPROVED", {}).get("approvalMode"),
                "sellerApprovalMode": evidence.get("SELLER_APPROVED", {}).get("approvalMode"),
                "executionMode": execution["approval_mode"] if execution else None,
                "awaitingWallet": awaiting, "ownCompanionOnline": own_companion,
                "failure": failure, "effects": effects,
                "chainActive": active, "chainHeight": tip, "receipt": receipt}

    def _durability(self, order_id: str) -> str | None:
        path = order_dir(self.config.exchange, order_id) / "stabilization.json"
        if path.is_file() and not path.is_symlink():
            return json.loads(path.read_text()).get("policy")
        return None

    def orders(self, actor: str, role: str) -> dict:
        with self.ledger._connection() as db:
            ids = [r[0] for r in db.execute(
                """SELECT o.id FROM orders o JOIN offers f ON f.id=o.offer_id
                   WHERE """ + ("o.buyer_id=?" if role == "buyer" else "f.seller_id=?") +
                " ORDER BY o.created_at DESC,o.id DESC", (actor,))]
        return {"orders": [self.order(order_id, actor, role) for order_id in ids]}


def handle(request: dict) -> dict:
    if type(request) is not dict or set(request) != {"action", "actor", "role", "sessionHash", "payload"}:
        raise PortalError("bridge request schema")
    action, actor, role, session, payload = (request[k] for k in
                                             ("action", "actor", "role", "sessionHash", "payload"))
    if (not isinstance(actor, str) or not IDENT.fullmatch(actor) or role not in ("buyer", "seller")
            or not isinstance(session, str) or not HEX32.fullmatch(session) or type(payload) is not dict):
        raise PortalError("invalid participant")
    portal = Portal(load_config())
    buyer_only = {"buy", "confirm", "cancel"}
    seller_only = {"set-price", "create-offer"}
    if (action in buyer_only and role != "buyer") or (action in seller_only and role != "seller"):
        raise PortalError("role or action unavailable")
    if action == "wallet":
        return portal.wallet(actor, role, session)
    if action == "pair":
        return portal.pair(actor, role, session, payload)
    if action == "unpair":
        portal.ledger.revoke_pairing(account_id=actor, role=role, session_hash=session)
        return portal.wallet(actor, role, session)
    if action == "market":
        return portal.market(actor)
    if action == "orders":
        return portal.orders(actor, role)
    if action == "order":
        if set(payload) != {"orderId"} or not isinstance(payload["orderId"], str):
            raise PortalError("order request schema")
        return portal.order(payload["orderId"], actor, role)
    if action == "buy":
        return portal.buy(actor, session, payload)
    if action == "confirm":
        return portal.confirm(actor, session, payload)
    if action == "cancel":
        return portal.cancel(actor, payload)
    if action == "set-price":
        return portal.set_price(actor, payload)
    if action == "create-offer":
        return portal.create_offer(actor, payload)
    raise PortalError("role or action unavailable")


def main() -> None:
    raw = sys.stdin.read(8193)
    if len(raw) > 8192:
        print(json.dumps({"ok": False, "error": "request is too large"}))
        return
    try:
        result = handle(json.loads(raw))
        print(json.dumps({"ok": True, "data": result}, separators=(",", ":")))
    except (PortalError, OrderError, ConfigError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:200]}))
    except Exception:
        print(json.dumps({"ok": False, "error": "request could not be completed"}))


if __name__ == "__main__":
    main()
