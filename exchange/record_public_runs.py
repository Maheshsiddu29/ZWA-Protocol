"""Export only public, internally reconciled historical demo evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from checkout.quote import validate as validate_quote
from .registry import load_catalog


def record(root: Path, instrument_id: str) -> dict:
    if instrument_id not in load_catalog():
        raise ValueError("unknown instrument")
    folder = root / "exchange"
    session = json.loads((folder / "api-depth3.json").read_text())
    independent_block = json.loads((folder / "independent-block.json").read_text())["result"]
    independent_transaction = json.loads((folder / "independent-transaction.json").read_text())["result"]
    registry = json.loads((folder / "instrument-registry.json").read_text())
    items = {item["id"]: item for item in registry["instruments"]}
    if len(items) != 3 or len({item["asset_base"] for item in items.values()}) != 3:
        raise ValueError("three distinct issuance identities required")
    item = items[instrument_id]
    quote = session["quote"]
    validate_quote(quote)
    receipt = session["receipt"]
    scans = session["walletScans"]
    auth = session["authorization"]
    if (session["status"] != "FINALIZED" or session["approvalEvidence"] != "SCRIPTED_TEST_MODE"
            or session["sessionId"] != quote["quoteDigest"] or quote["assetId"] != item["asset_base"]
            or quote["network"] != "regtest" or receipt["txid"] not in independent_block["tx"]
            or receipt["blockHash"] != independent_block["hash"]
            or independent_transaction["height"] != receipt["height"]
            or len(independent_transaction["hex"]) // 2 != receipt["txBytes"]
            or receipt["confirmations"] != 3):
        raise ValueError("historical session, chain observation, and issuance do not reconcile")
    if not all(auth.get(field) == "VERIFIED" for field in
               ("issuerAuthentication", "issuanceProvenance", "credentialAuthorityAuthentication",
                "investorEligibility", "exactTradeAuthorization")):
        raise ValueError("combined authorization was not verified")
    if (scans["buyerAfter"]["assetUnits"] - scans["buyerBefore"]["assetUnits"] != quote["quantity"]
            or scans["sellerBefore"]["assetUnits"] - scans["sellerAfter"]["assetUnits"] != quote["quantity"]
            or scans["buyerBefore"]["nativeZat"] - scans["buyerAfter"]["nativeZat"] != quote["paymentAmount"]
            or scans["sellerAfter"]["nativeZat"] - scans["sellerBefore"]["nativeZat"] != quote["paymentAmount"]):
        raise ValueError("wallet scans do not reconcile")
    return {"instrumentId": instrument_id, "assetBase": quote["assetId"],
            "issuanceTxid": item["issuance_txid"], "quoteDigest": quote["quoteDigest"],
            "quantity": quote["quantity"], "paymentZat": quote["paymentAmount"],
            "networkFeeZat": quote["networkFee"], "txid": receipt["txid"],
            "blockHash": receipt["blockHash"], "height": receipt["height"],
            "confirmationsObservedBeforeShutdown": receipt["confirmations"],
            "buyerAssetBefore": scans["buyerBefore"]["assetUnits"],
            "buyerAssetAfter": scans["buyerAfter"]["assetUnits"],
            "sellerAssetBefore": scans["sellerBefore"]["assetUnits"],
            "sellerAssetAfter": scans["sellerAfter"]["assetUnits"],
            "sellerNativeBeforeZat": scans["sellerBefore"]["nativeZat"],
            "sellerNativeAfterZat": scans["sellerAfter"]["nativeZat"],
            "authorization": "GROTH16_COMBINED_VERIFIED_IN_RUN",
            "walletApproval": "SCRIPTED_TEST_MODE", "currentFinality": "UNKNOWN_CHAIN_STOPPED"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--treasury", type=Path, required=True)
    parser.add_argument("--credit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runs = [record(args.gold, "zwa-gold-reference"),
            record(args.treasury, "zwa-treasury-style"),
            record(args.credit, "zwa-credit-participation")]
    args.output.write_text(json.dumps({"version": 1, "network": "regtest",
                                       "source": "RECORDED_SCRIPTED_TEST_EVIDENCE",
                                       "runs": runs}, indent=2) + "\n")


if __name__ == "__main__":
    main()
