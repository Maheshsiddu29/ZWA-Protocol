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
        if (buyer["assetUnits"] != per_trade["buyerAfter"]["assetUnits"]
                or seller["assetUnits"] != per_trade["sellerAfter"]["assetUnits"]
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
