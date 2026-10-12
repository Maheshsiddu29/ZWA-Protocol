"""Reconcile several actual wallet scans on one anchored regtest chain.

The output is private participant data and belongs only in the operator's
mode-0700 session root. A web route must recheck chain identity and enforce
wallet ownership before returning any part of it.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from checkout.api import read_scans, read_session
from checkout.scans import parse
from .order_paths import APPROVAL_MODES, quote_dir


def build(exchange: Path, chain_identity: Path, *, restart: bool = False,
          scan_dir: Path | None = None) -> dict:
    registry = json.loads((exchange / "instrument-registry.json").read_text())
    chain = json.loads(chain_identity.read_text())
    if registry.get("network") != "regtest" or chain.get("version") != 1:
        raise ValueError("unsupported registry or chain identity")
    instruments = registry["instruments"]
    if len(instruments) != 3 or len({item["asset_base"] for item in instruments}) != 3:
        raise ValueError("three distinct onchain assets required")
    suffix = "-restart" if restart else ""
    if scan_dir is not None:
        suffix = ""
    scans = scan_dir or exchange / "portfolio-scans"
    holdings = []
    receipts = []
    buyer_native = set()
    seller_native = set()
    scan_heights = set()
    order_receipts = []
    orders_dir = exchange / "orders"
    if orders_dir.is_dir():
        from .order_ledger import OrderLedger
        for path in sorted(orders_dir.glob("*/order-receipt.json")):
            if path.is_symlink() or path.stat().st_size > 8192:
                raise ValueError("unsafe order receipt")
            order_receipt = json.loads(path.read_text())
            if (order_receipt.get("version") != 1 or
                    order_receipt.get("approvalMode") not in APPROVAL_MODES or
                    order_receipt.get("rootedNoteLineage") != "UNSUPPORTED" or
                    order_receipt.get("orderId") != path.parent.name):
                raise ValueError("unverified order receipt")
            ledger = OrderLedger(exchange / "exchange.sqlite")
            order = ledger.get_order(path.parent.name, participant_id=order_receipt["buyerId"])
            if (order["state"] != "RECEIPT_AVAILABLE" or
                    order["seller_id"] != order_receipt["sellerId"] or
                    order["asset_base"] != order_receipt["assetBase"] or
                    order["quantity"] != order_receipt["quantity"] or
                    order["quote_digest"] != order_receipt["quoteDigest"]):
                raise ValueError("order receipt differs from durable ledger")
            session = read_session(quote_dir(exchange, path.parent.name) / "session-result.json")
            per_order = read_scans(path.parent / "public-scans.json", session)
            if (session is None or per_order is None or
                    session["receipt"]["txid"] != order_receipt["txid"] or
                    session["receipt"]["height"] != order_receipt["height"] or
                    session["receipt"]["block_hash"] != order_receipt["blockHash"] or
                    session["quote"]["paymentAmount"] != order_receipt["paymentZat"]):
                raise ValueError("order receipt differs from actual checkout")
            order_receipts.append(order_receipt)
    for item in instruments:
        instrument_id = item["id"]
        asset = item["asset_base"]
        trade = exchange / "trades" / instrument_id
        session = read_session(trade / "session-result.json")
        if session is None or session["receipt"] is None or session["quote"]["assetId"] != asset:
            raise ValueError("accepted trade for registry AssetBase required")
        per_trade = read_scans(trade / "public-scans.json", session)
        if per_trade is None:
            raise ValueError("reconciled trade scans required")
        authorization = json.loads((trade / "authorization-result.json").read_text())
        if any(authorization.get(key) != "VERIFIED" for key in (
                "issuerAuthentication", "issuanceProvenance",
                "credentialAuthorityAuthentication", "investorEligibility",
                "exactTradeAuthorization")):
            raise ValueError("trade authorization is not fully verified")
        buyer = parse(scans / f"{instrument_id}-buyer{suffix}.log", "Buyer", asset)
        seller = parse(scans / f"{instrument_id}-seller{suffix}.log", "Seller", asset)
        extra = [r for r in order_receipts if r["assetBase"] == asset]
        extra_units = sum(r["quantity"] for r in extra)
        if (buyer["assetUnits"] != per_trade["buyerAfter"]["assetUnits"] + extra_units
                or seller["assetUnits"] != per_trade["sellerAfter"]["assetUnits"] - extra_units
                or buyer["height"] < session["receipt"]["height"]
                or seller["height"] < session["receipt"]["height"]):
            raise ValueError("combined wallet scans differ from settled trade")
        buyer_native.add(buyer["nativeZat"])
        seller_native.add(seller["nativeZat"])
        scan_heights.update((buyer["height"], seller["height"]))
        holdings.append({"instrumentId": instrument_id, "assetBase": asset,
                         "buyerUnits": buyer["assetUnits"],
                         "sellerUnits": seller["assetUnits"],
                         "issuanceTxid": item["issuance_txid"]})
        receipts.append({"instrumentId": instrument_id,
                         "quoteDigest": session["sessionId"],
                         "txid": session["receipt"]["txid"],
                         "height": session["receipt"]["height"],
                         "paymentZat": session["quote"]["paymentAmount"],
                         "approvalMode": "SCRIPTED_TEST_MODE"})
    receipts.extend({"instrumentId": r["instrumentId"],
                     "quoteDigest": r["quoteDigest"], "txid": r["txid"],
                     "height": r["height"], "paymentZat": r["paymentZat"],
                     "approvalMode": r["approvalMode"]} for r in order_receipts)
    if len(buyer_native) != 1 or len(seller_native) != 1 or len(scan_heights) != 1:
        raise ValueError("wallet scans are not from one common chain tip")
    height = scan_heights.pop()
    if chain["anchorHeight"] < max(receipt["height"] for receipt in receipts):
        raise ValueError("accepted trades are not covered by the chain anchor")
    if height < chain["anchorHeight"]:
        raise ValueError("wallet scan is older than the chain anchor")
    expected_seller = sum(receipt["paymentZat"] for receipt in receipts)
    if seller_native.pop() != expected_seller:
        raise ValueError("seller scanned ZEC proceeds do not equal purchases")
    return {"version": 1, "network": "regtest", "state": "CHAIN_RESTARTED" if restart else "CHAIN_ACTIVE",
            "genesisHash": chain["genesisHash"], "anchorHash": chain["anchorHash"],
            "anchorHeight": chain["anchorHeight"], "scanHeight": height,
            "buyerNativeZat": buyer_native.pop(), "sellerNativeZat": expected_seller,
            "holdings": holdings, "receipts": receipts,
            "privacy": "OPERATOR_PRIVATE_WALLET_SCAN"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exchange", type=Path)
    parser.add_argument("chain_identity", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--restart", action="store_true")
    parser.add_argument("--scan-dir", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("refusing to overwrite existing portfolio scan")
    result = build(args.exchange, args.chain_identity,
                   restart=args.restart, scan_dir=args.scan_dir)
    os.umask(0o077)
    with args.output.open("x") as out:
        json.dump(result, out, indent=2)
        out.write("\n")
    print("PHASE4E_COMBINED_WALLET_SCAN holdings=" + str(len(result["holdings"])) +
          " seller_native_zat=" + str(result["sellerNativeZat"]))


if __name__ == "__main__":
    main()
