"""Fixed, validated local order mutations for authenticated portal requests.

There is no wallet signing, proof generation, shell dispatch, or arbitrary file
path in this bridge. A new request remains non-executable until the trusted
operator prepares and verifies a fresh quote and both signers approve.
"""

import argparse
import json
from pathlib import Path

from .order_ledger import OrderError, OrderLedger, create_live_offer
from .portfolio_scans import build as build_portfolio
from .persistent_chain import status

ROOT = Path("/private/tmp/zwa4e-phase4e-b")
CONTAINER = "zwa-phase4e-b"
PORT = 18321


def mutate(action: str, role: str, actor: str, payload: dict) -> dict:
    if type(payload) is not dict:
        raise OrderError("request must be an object")
    chain = status(ROOT, CONTAINER, PORT)
    if chain.get("state") not in ("CHAIN_ACTIVE", "CHAIN_RESTARTED"):
        raise OrderError("protected chain is unavailable")
    ledger = OrderLedger(ROOT / "exchange/exchange.sqlite")
    if action == "create-offer" and role == "seller" and actor == "seller":
        if set(payload) != {"instrumentId", "capacity", "idempotencyKey"}:
            raise OrderError("offer request schema")
        return create_live_offer(ROOT, CONTAINER, PORT, seller_id=actor,
                                 instrument_id=payload["instrumentId"],
                                 capacity=payload["capacity"],
                                 idempotency_key=payload["idempotencyKey"])
    if action == "request" and role == "buyer" and actor == "buyer":
        if set(payload) != {"offerId", "quantity", "idempotencyKey"}:
            raise OrderError("purchase request schema")
        scans = [p for p in (ROOT / "exchange/rescans").glob("scan.*/portfolio.json")
                 if p.is_file() and not p.is_symlink()]
        if not scans:
            raise OrderError("fresh seller wallet scan required")
        newest = max(scans, key=lambda p:p.stat().st_mtime_ns)
        portfolio = build_portfolio(ROOT / "exchange", ROOT / "chain-identity.json",
                                    restart=True, scan_dir=newest.parent)
        if portfolio["scanHeight"] != chain["height"]:
            raise OrderError("seller inventory scan is stale")
        with ledger._connection() as db:
            offer = db.execute("SELECT * FROM offers WHERE id=?", (payload["offerId"],)).fetchone()
        if not offer:
            raise OrderError("offer unavailable")
        holding = next((h for h in portfolio["holdings"] if h["assetBase"] == offer["asset_base"]), None)
        if (not holding or type(payload["quantity"]) is not int or payload["quantity"] < 1 or
                holding["sellerUnits"] < offer["reserved"] + payload["quantity"]):
            raise OrderError("seller spendable inventory is unavailable")
        return ledger.request_purchase(offer_id=payload["offerId"], buyer_id=actor,
                                       quantity=payload["quantity"],
                                       idempotency_key=payload["idempotencyKey"],
                                       chain_genesis=json.loads((ROOT / "chain-identity.json").read_text())["genesisHash"])
    if action == "cancel" and role == "buyer" and actor == "buyer":
        if set(payload) != {"orderId"}:
            raise OrderError("cancel request schema")
        return ledger.close_before_approval(payload["orderId"], buyer_id=actor,
                                            state="CANCELLED")
    raise OrderError("role or action unavailable")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create-offer", "request", "cancel"))
    parser.add_argument("role", choices=("buyer", "seller"))
    parser.add_argument("actor")
    parser.add_argument("payload_json")
    args = parser.parse_args()
    if len(args.payload_json) > 2048:
        raise OrderError("request is too large")
    result = mutate(args.action, args.role, args.actor, json.loads(args.payload_json))
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
