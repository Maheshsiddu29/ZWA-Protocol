"""Strict public registry; chain evidence is required to elevate availability."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re

CATEGORIES = frozenset({"PRECIOUS_METALS", "COMMODITIES", "TREASURY_BILLS",
                        "GOVERNMENT_BONDS", "CORPORATE_BONDS", "MONEY_MARKET_FUNDS",
                        "INVESTMENT_FUNDS", "REAL_ESTATE", "PRIVATE_CREDIT",
                        "EQUITIES", "TRADE_FINANCE_RECEIVABLES", "OTHER_RWA"})
STATES = ("CATALOG_ONLY", "EXPERIMENTAL_ISSUED", "EXPERIMENTAL_TRADABLE", "EXTERNALLY_VERIFIED")
HEX32 = re.compile(r"[0-9a-f]{64}\Z")


class InvalidInstrument(ValueError):
    pass


@dataclass(frozen=True)
class Instrument:
    id: str
    name: str
    category: str
    issuer_id: str
    description: str
    underlying_rights: str
    rights_evidence: str | None
    unit: str
    precision: int
    denomination: str
    permitted_investors: str
    transfer_restrictions: str
    pricing_model: str
    offer_terms: str
    redemption_terms: str
    compliance_policy: str
    pricing_source: str
    availability: str
    asset_base: str | None = None
    issuance_txid: str | None = None
    seller_inventory_units: int | None = None


def load_catalog(path: Path | None = None) -> dict[str, Instrument]:
    path = path or Path(__file__).with_name("catalog.json")
    data = json.loads(path.read_text())
    result: dict[str, Instrument] = {}
    if type(data) is not list:
        raise InvalidInstrument("catalog schema")
    for item in data:
        if type(item) is not dict or item.get("id") in result:
            raise InvalidInstrument("duplicate or malformed instrument")
        if item.get("category") not in CATEGORIES or item.get("availability") != "CATALOG_ONLY":
            raise InvalidInstrument("unsupported category or unsupported catalog claim")
        if type(item.get("precision")) is not int or not 0 <= item["precision"] <= 18:
            raise InvalidInstrument("unit precision")
        for key in ("id", "name", "issuerId", "description", "underlyingRights", "unit",
                    "denomination", "permittedInvestors", "transferRestrictions", "pricingModel",
                    "offerTerms", "redemptionTerms", "compliancePolicy", "pricingSource"):
            if not isinstance(item.get(key), str) or not item[key]:
                raise InvalidInstrument(f"missing {key}")
        result[item["id"]] = Instrument(item["id"], item["name"], item["category"],
                                         item["issuerId"], item["description"],
                                         item["underlyingRights"], item.get("rightsEvidence"),
                                         item["unit"], item["precision"], item["denomination"],
                                         item["permittedInvestors"], item["transferRestrictions"],
                                         item["pricingModel"], item["offerTerms"],
                                         item["redemptionTerms"], item["compliancePolicy"],
                                         item["pricingSource"], "CATALOG_ONLY")
    return result


def with_issuance(instrument: Instrument, issuance: dict, *, observed_on_chain: bool,
                  seller_inventory_units: int | None = None) -> Instrument:
    """Bind a catalog item to an issuance independently observed on this regtest.

    Callers must authenticate the issuing wallet and chain before setting
    observed_on_chain. This function never infers issuance from metadata alone.
    """
    if not observed_on_chain or type(issuance) is not dict:
        raise InvalidInstrument("chain-observed issuance required")
    asset = issuance.get("asset_base")
    txid = issuance.get("txid")
    if not isinstance(asset, str) or not HEX32.fullmatch(asset) or asset == "0" * 64:
        raise InvalidInstrument("invalid AssetBase")
    if not isinstance(txid, str) or not HEX32.fullmatch(txid):
        raise InvalidInstrument("invalid issuance txid")
    if seller_inventory_units is not None and (type(seller_inventory_units) is not int or seller_inventory_units < 0):
        raise InvalidInstrument("invalid seller inventory")
    from dataclasses import replace
    state = "EXPERIMENTAL_TRADABLE" if seller_inventory_units and seller_inventory_units > 0 else "EXPERIMENTAL_ISSUED"
    return replace(instrument, availability=state, asset_base=asset,
                   issuance_txid=txid, seller_inventory_units=seller_inventory_units)


def assert_distinct_assets(instruments: list[Instrument]) -> None:
    bases = [item.asset_base for item in instruments if item.asset_base is not None]
    if len(bases) != len(set(bases)):
        raise InvalidInstrument("one AssetBase cannot label multiple instruments")
