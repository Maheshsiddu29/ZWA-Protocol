#!/usr/bin/env python3
"""Read-only consistency report for a disposable M10 Compose portal.

Run from the host with: docker compose exec -T portal python3 - < deploy/scripts/m10-state-report.py
The script runs inside the portal container and prints no wallet keys, session
hashes, cookie values, portal token, or recovery material.
"""
import json
import os
import time

from exchange.config import load
from exchange.portal import Portal


def holdings(view):
    return {item["instrumentId"]: item["units"] for item in view.get("holdings", [])}


def main():
    root = os.environ["ZWA_PRIVATE_EXCHANGE_ROOT"]
    portal = Portal(load(root))
    live, tip = portal.live()
    if not live:
        raise SystemExit("chain identity or RPC is not live")
    with portal.ledger._connection() as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SystemExit("ledger integrity check failed")
        orders = [dict(row) for row in db.execute(
            "SELECT id,buyer_id,state FROM orders ORDER BY id")]
        wallets = [dict(row) for row in db.execute(
            "SELECT account_id,session_hash,state FROM demo_wallets "
            "WHERE state='READY' AND expires_at>? ORDER BY account_id", (int(time.time()),))]
        settlements = [dict(row) for row in db.execute(
            "SELECT order_id,txid FROM order_settlements ORDER BY order_id")]
        offers = [dict(row) for row in db.execute(
            "SELECT id,reserved,sold,capacity FROM offers ORDER BY id")]

    buyer_views = {}
    for row in wallets:
        view = portal.demo_wallet(row["account_id"], "buyer", row["session_hash"])
        if view["nativeZat"] is None or len(view.get("holdings", [])) != len(portal.assets()):
            raise SystemExit(f"live wallet has no complete canonical scan: {row['account_id']}")
        buyer_views[row["account_id"]] = {
            "state": view["state"], "nativeZat": view["nativeZat"],
            "holdings": holdings(view),
        }
    dealer = portal.dealer_desk(portal.demo.dealer_account_id)
    receipts = {}
    for row in orders:
        if row["state"] != "RECEIPT_AVAILABLE":
            continue
        view = portal.order(row["id"], row["buyer_id"], "buyer")
        receipt = view.get("receipt")
        if not receipt or not view["proofVerified"] or not view["effects"]:
            raise SystemExit(f"receipt, proof or wallet effects unavailable for order {row['id']}")
        receipts[row["id"]] = {
            "txid": receipt["txid"], "height": receipt["height"],
            "blockHash": receipt["blockHash"], "effects": view["effects"],
            "proofVerified": view["proofVerified"],
        }
    if {(item["order_id"], item["txid"]) for item in settlements} != {
            (order_id, view["txid"]) for order_id, view in receipts.items()}:
        raise SystemExit("settlements and live receipts differ")
    print(json.dumps({
        "genesisHash": portal._rpc("getblockhash", [0]),
        "chainHeight": tip,
        "ledgerIntegrity": "ok",
        "orders": {row["id"]: row["state"] for row in orders},
        "settlements": settlements,
        "receipts": receipts,
        "buyers": buyer_views,
        "dealer": {"nativeZat": dealer["nativeZat"], "holdings": holdings(dealer)},
        "offers": offers,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
