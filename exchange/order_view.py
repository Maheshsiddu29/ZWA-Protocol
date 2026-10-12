"""Read-only, participant-scoped JSON bridge for the local exchange portal."""

import argparse
import json
from pathlib import Path
import re
import sqlite3

from checkout.quote import from_terms
from .persistent_chain import _rpc

ROOT = Path("/private/tmp/zwa4e-phase4e-b")
HEX16 = re.compile(r"[0-9a-f]{32}\Z")
IDENT = re.compile(r"[a-zA-Z0-9_-]{1,80}\Z")


def _db():
    path = ROOT / "exchange/exchange.sqlite"
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise ValueError("unsafe private ledger")
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def _live() -> tuple[bool, int | None]:
    identity = json.loads((ROOT / "chain-identity.json").read_text())
    if identity.get("detectedReset"):
        return False, None
    try:
        height = _rpc(18321, "getblockcount")
        if (_rpc(18321, "getblockhash", [0]) != identity["genesisHash"] or
                _rpc(18321, "getblockhash", [identity["anchorHeight"]]) != identity["anchorHash"]):
            return False, None
        return True, height
    except Exception:
        return False, None


def order_view(order_id: str, actor: str) -> dict:
    if not HEX16.fullmatch(order_id) or not IDENT.fullmatch(actor):
        raise ValueError("invalid order or participant")
    with _db() as db:
        row = db.execute("""SELECT o.*,f.seller_id,f.instrument_id,f.asset_base,
                           f.chain_genesis FROM orders o JOIN offers f ON f.id=o.offer_id
                           WHERE o.id=?""", (order_id,)).fetchone()
        if row is None or actor not in (row["buyer_id"], row["seller_id"]):
            raise ValueError("order unavailable")
        events = [dict(e) for e in db.execute(
            "SELECT sequence,from_state,to_state,occurred_at FROM order_events WHERE order_id=? ORDER BY sequence",
            (order_id,))]
        evidence = {e["kind"]: json.loads(e["value_json"]) for e in db.execute(
            "SELECT kind,value_json FROM order_evidence WHERE order_id=?", (order_id,))}
    active, tip = _live()
    identity = json.loads((ROOT / "chain-identity.json").read_text())
    if row["chain_genesis"] != identity["genesisHash"]:
        active = False
    quote = None
    if order_id == "ec27f52d2e7ad69287f7d4d4357025af":
        terms = json.loads((ROOT / "exchange/order-quotes/gold-1/terms.json").read_text())
        candidate = from_terms(terms)
        if candidate["quoteDigest"] == row["quote_digest"]:
            quote = {"digest": candidate["quoteDigest"], "assetBase": candidate["assetId"],
                     "quantity": candidate["quantity"], "paymentZat": candidate["paymentAmount"],
                     "networkFeeZat": candidate["networkFee"],
                     "expiryHeight": candidate["transactionExpiry"]}
    receipt = None
    if row["state"] == "RECEIPT_AVAILABLE" and active:
        path = ROOT / "exchange/orders" / order_id / "order-receipt.json"
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= 8192:
            saved = json.loads(path.read_text())
            included = evidence.get("INCLUDED", {})
            if (saved.get("txid") == included.get("txid") and
                    saved.get("blockHash") == included.get("blockHash") and
                    saved.get("quoteDigest") == row["quote_digest"]):
                try:
                    block = _rpc(18321, "getblock", [str(saved["height"]), 1])
                    if block["hash"] == saved["blockHash"] and saved["txid"] in block["tx"]:
                        receipt = saved
                except Exception:
                    pass
    return {"id": row["id"], "state": row["state"],
            "buyerId": row["buyer_id"], "sellerId": row["seller_id"],
            "instrumentId": row["instrument_id"], "assetBase": row["asset_base"],
            "quantity": row["quantity"], "quote": quote, "events": events,
            "proofVerified": "PROOF_VERIFIED" in evidence,
            "buyerApprovalMode": evidence.get("BUYER_APPROVED", {}).get("approvalMode"),
            "sellerApprovalMode": evidence.get("SELLER_APPROVED", {}).get("approvalMode"),
            "chainActive": active, "chainHeight": tip, "receipt": receipt}


def list_orders(actor: str) -> dict:
    if not IDENT.fullmatch(actor):
        raise ValueError("invalid participant")
    with _db() as db:
        ids = [r[0] for r in db.execute("""SELECT o.id FROM orders o
                 JOIN offers f ON f.id=o.offer_id
                 WHERE o.buyer_id=? OR f.seller_id=? ORDER BY o.created_at DESC,o.id DESC""",
                 (actor, actor))]
    return {"orders": [order_view(order_id, actor) for order_id in ids]}


def list_offers(actor: str) -> dict:
    if not IDENT.fullmatch(actor):
        raise ValueError("invalid participant")
    active, tip = _live()
    if not active:
        return {"offers": [], "chainActive": False}
    from .portfolio_scans import build as build_portfolio
    scan_paths = [p for p in (ROOT / "exchange/rescans").glob("scan.*/portfolio.json")
                  if p.is_file() and not p.is_symlink()]
    if not scan_paths:
        return {"offers": [], "chainActive": True, "chainHeight": tip}
    newest = max(scan_paths, key=lambda p:p.stat().st_mtime_ns)
    portfolio = build_portfolio(ROOT / "exchange", ROOT / "chain-identity.json",
                                restart=True, scan_dir=newest.parent)
    if portfolio["scanHeight"] != tip:
        return {"offers": [], "chainActive": True, "chainHeight": tip}
    units = {h["assetBase"]:h["sellerUnits"] for h in portfolio["holdings"]}
    with _db() as db:
        rows = db.execute("""SELECT id,instrument_id,asset_base,capacity,reserved,sold
                             FROM offers WHERE state='OFFER_CREATED'
                             ORDER BY created_at,id""").fetchall()
    return {"chainActive": True, "chainHeight": tip,
            "offers": [{"id": r["id"], "instrumentId": r["instrument_id"],
                        "assetBase": r["asset_base"],
                        "availableUnits": min(r["capacity"] - r["reserved"] - r["sold"],
                                              units.get(r["asset_base"],0) - r["reserved"])}
                       for r in rows if min(r["capacity"] - r["reserved"] - r["sold"],
                                            units.get(r["asset_base"],0) - r["reserved"]) > 0]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("get", "list", "offers"))
    parser.add_argument("actor")
    parser.add_argument("order_id", nargs="?")
    args = parser.parse_args()
    if args.action == "get" and args.order_id:
        result = order_view(args.order_id, args.actor)
    elif args.action == "list" and args.order_id is None:
        result = list_orders(args.actor)
    elif args.action == "offers" and args.order_id is None:
        result = list_offers(args.actor)
    else:
        parser.error("invalid action arguments")
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
