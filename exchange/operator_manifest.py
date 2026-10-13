"""Record public per-instrument issuance and seller inventory observations."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .registry import assert_distinct_assets, load_catalog, with_issuance
from checkout.scans import parse


def build(folder: Path, expected_units: int = 100) -> dict:
    catalog = load_catalog()
    instruments = []
    for instrument_id, item in catalog.items():
        issuance = json.loads((folder / f"{instrument_id}-issuance.json").read_text())
        observed = json.loads((folder / f"{instrument_id}-chain.json").read_text())
        transaction = observed.get("result")
        if (not isinstance(transaction, dict) or not isinstance(transaction.get("height"), int)
                or transaction["height"] <= 0 or not isinstance(transaction.get("hex"), str)
                or len(transaction["hex"]) < 100):
            raise ValueError("issuance not observed in chain RPC")
        balance = parse(folder / f"{instrument_id}-inventory.log", "Seller", issuance["asset_base"])
        if balance["assetUnits"] != expected_units or balance["height"] < transaction["height"]:
            raise ValueError("seller inventory is not reconciled with issuance")
        bound = with_issuance(item, issuance, observed_on_chain=True,
                              seller_inventory_units=balance["assetUnits"])
        public = asdict(bound)
        public.pop("rights_evidence")
        public["issuance_height"] = transaction["height"]
        public["inventory_scan_height"] = balance["height"]
        instruments.append((bound, public))
    assert_distinct_assets([bound for bound, _ in instruments])
    return {"version": 1, "network": "regtest", "trustSource": "EXPERIMENTAL_LOCAL_OBSERVATION",
            "instruments": [public for _, public in instruments]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--expected-units", type=int, default=100)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("refusing to overwrite manifest")
    result = build(args.folder, args.expected_units)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print("PHASE4D_DISTINCT_ISSUANCES=" + str(len(result["instruments"])))


if __name__ == "__main__":
    main()
