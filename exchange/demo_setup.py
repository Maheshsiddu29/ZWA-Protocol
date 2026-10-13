"""Operator bootstrap of the demo dealer's listings on a fresh judge-demo root.

Run once after the setup script has issued the instruments:

    python3 -m exchange.demo_setup --root /private/tmp/zwa4e-judge-... \\
        --capacity 900 --price zwa-gold-reference=1000 ...

Inventory comes from a live, independent scan of the dealer wallet at the
anchored tip, never from arguments: the capacity is only the share of that
scanned inventory the dealer lists. Re-running is idempotent.
"""

from __future__ import annotations

import argparse
import json
import os

from .config import load as load_config
from .demo_ledger import DEALER_KEY, DemoLedger
from .order_ledger import OrderError
from .order_service import OrderService


def bootstrap(root: str, capacity: int, prices: dict[str, int]) -> dict:
    config = load_config(root)
    if not config.demo_mode:
        raise SystemExit("demo listings belong only on a CUSTODIAL_DEMO_POLICY root")
    service = OrderService(config, log=lambda event: None)
    ledger: DemoLedger = service.ledger
    if set(prices) != set(service.assets):
        raise SystemExit("set a price for every issued instrument")
    chain = service.tools.chain()
    if chain.get("state") not in ("CHAIN_ACTIVE", "CHAIN_RESTARTED"):
        raise SystemExit("chain is not active and anchor-verified")
    scans = service.demo.scan_wallet(DEALER_KEY, list(service.assets.values()))
    identity = json.loads(config.chain_identity.read_text())
    dealer = config.demo.dealer_account_id
    listings = []
    for instrument, asset in service.assets.items():
        scan = scans[asset]
        if scan["height"] != chain["height"] or scan["assetUnits"] < capacity:
            raise OrderError(f"dealer inventory for {instrument} is below the listed capacity")
        offer = ledger.create_offer(seller_id=dealer, instrument_id=instrument, asset_base=asset,
                                    chain_genesis=identity["genesisHash"], scan_height=scan["height"],
                                    scan_anchor=identity["anchorHash"], inventory_units=scan["assetUnits"],
                                    capacity=capacity, idempotency_key=f"demo-dealer-{instrument}")
        price = ledger.set_price(seller_id=dealer, offer_id=offer["id"],
                                 price_zat_per_unit=prices[instrument])
        listings.append({"instrumentId": instrument, "offerId": offer["id"], "capacity": offer["capacity"],
                         "scannedUnits": scan["assetUnits"], "scanHeight": scan["height"],
                         "priceZatPerUnit": price["price_zat_per_unit"], "priceVersion": price["version"]})
    return {"dealer": dealer, "listings": listings}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", required=True)
    parser.add_argument("--capacity", type=int, required=True)
    parser.add_argument("--price", action="append", default=[], metavar="INSTRUMENT=ZATOSHIS")
    args = parser.parse_args()
    prices = {}
    for item in args.price:
        name, _, value = item.partition("=")
        if not value.isdigit() or int(value) <= 0:
            parser.error(f"invalid price: {item}")
        prices[name] = int(value)
    os.umask(0o077)
    print(json.dumps(bootstrap(args.root, args.capacity, prices), indent=2))


if __name__ == "__main__":
    main()
