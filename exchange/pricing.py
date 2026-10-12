"""Typed price provenance for disposable regtest instruments.

Pyth Core data is read over authenticated HTTPS transport. This module does
not verify a Wormhole VAA, so its output is an indicative reference only.
Signed NAV and private ZEC offers use operator-provisioned HMAC keys and are
also off-chain statements, not legal valuation or reserve evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import hmac
import json
import re
import time
from typing import Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

XAU_USD_FEED_ID = "765d2ba906dbc32ca17cc11f5310a89e9ee1f6420508c63861f2f8ba4ee34bb2"
PYTH_HERMES = "https://pyth.dourolabs.app/hermes/v2/updates/price/latest"


class InvalidPrice(ValueError):
    pass


@dataclass(frozen=True)
class PriceRecord:
    kind: str
    instrument_id: str
    numerator: int
    exponent: int
    unit: str
    source: str
    observed_at: int
    received_at: int
    reference: str
    confidence: int | None
    trust: str

    @property
    def decimal_value(self) -> Decimal:
        return Decimal(self.numerator) * (Decimal(10) ** self.exponent)


def _integer(value: object, label: str, *, positive: bool = False) -> int:
    if type(value) is not int or (positive and value <= 0):
        raise InvalidPrice(f"{label}: invalid integer")
    return value


def _timestamp(value: object, now: int, max_age: int) -> int:
    timestamp = _integer(value, "timestamp", positive=True)
    if timestamp > now + 30 or now - timestamp > max_age:
        raise InvalidPrice("price observation is future dated or stale")
    return timestamp


def parse_pyth_xau(payload: dict, *, now: int | None = None, max_age: int = 3600) -> PriceRecord:
    """Validate one *indicative* XAU/USD Core response from the pinned feed.

    The feed identifier was checked against Pyth's published XAU/USD prefix and
    suffix. Response ID matching alone authenticates the HTTP server, not the
    underlying Pyth signature. A VAA verifier must be added before claiming it.
    """
    now = int(time.time()) if now is None else now
    if type(payload) is not dict or type(payload.get("parsed")) is not list or len(payload["parsed"]) != 1:
        raise InvalidPrice("exactly one parsed Pyth feed is required")
    feed = payload["parsed"][0]
    if type(feed) is not dict or feed.get("id") != XAU_USD_FEED_ID:
        raise InvalidPrice("wrong Pyth feed identity")
    point = feed.get("price")
    if type(point) is not dict:
        raise InvalidPrice("missing Pyth price")
    try:
        price = int(point["price"])
        confidence = int(point["conf"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidPrice("invalid Pyth decimal encoding") from exc
    if not isinstance(point.get("price"), str) or not re.fullmatch(r"[1-9][0-9]*", point["price"]):
        raise InvalidPrice("noncanonical positive Pyth price")
    if not isinstance(point.get("conf"), str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)", point["conf"]):
        raise InvalidPrice("noncanonical Pyth confidence")
    exponent = _integer(point.get("expo"), "exponent")
    if not -12 <= exponent <= 0 or confidence < 0 or confidence * 10 > price:
        raise InvalidPrice("unsupported exponent or low confidence")
    observed_at = _timestamp(point.get("publish_time"), now, max_age)
    return PriceRecord("MARKET_REFERENCE", "zwa-gold-reference", price, exponent,
                       "USD_PER_TROY_OUNCE", "PYTH_CORE_XAU_USD", observed_at,
                       now, XAU_USD_FEED_ID, confidence, "HTTPS_TRANSPORT_ONLY")


class MarketPriceProvider:
    def __init__(self, api_key: str, *, transport: Callable | None = None):
        if not api_key:
            raise InvalidPrice("Pyth API key is required")
        self.api_key = api_key
        self.transport = transport or urlopen

    def xau_usd(self, *, now: int | None = None) -> PriceRecord:
        url = PYTH_HERMES + "?" + urlencode({"ids[]": "0x" + XAU_USD_FEED_ID})
        request = Request(url, headers={"Authorization": "Bearer " + self.api_key,
                                        "Accept": "application/json"})
        with self.transport(request, timeout=5) as response:
            raw = response.read(262145)
        if len(raw) > 262144:
            raise InvalidPrice("oversize oracle response")
        return parse_pyth_xau(json.loads(raw), now=now)


def _canonical(data: dict) -> bytes:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sign_statement(statement: dict, key: bytes) -> str:
    if len(key) < 32:
        raise InvalidPrice("authority key is too short")
    return hmac.new(key, _canonical(statement), hashlib.sha256).hexdigest()


def _verify_statement(statement: dict, signature: str, key: bytes) -> None:
    if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
        raise InvalidPrice("invalid authority signature")
    if not hmac.compare_digest(signature, sign_statement(statement, key)):
        raise InvalidPrice("authority signature mismatch")


class IssuerNAVProvider:
    def __init__(self, issuer_keys: dict[str, bytes]):
        self.issuer_keys = issuer_keys

    def read(self, statement: dict, signature: str, *, now: int | None = None) -> PriceRecord:
        now = int(time.time()) if now is None else now
        if type(statement) is not dict or set(statement) != {"version", "instrumentId", "issuerId", "navMicrousdPerUnit", "observedAt", "expiresAt", "nonce"}:
            raise InvalidPrice("NAV schema")
        issuer = statement["issuerId"]
        if statement["version"] != 1 or issuer not in self.issuer_keys:
            raise InvalidPrice("unauthorized issuer")
        _verify_statement(statement, signature, self.issuer_keys[issuer])
        observed = _timestamp(statement["observedAt"], now, 86400)
        expires = _integer(statement["expiresAt"], "expiry", positive=True)
        if expires <= now or expires <= observed or expires - observed > 86400:
            raise InvalidPrice("expired or excessive NAV lifetime")
        value = _integer(statement["navMicrousdPerUnit"], "NAV", positive=True)
        if not isinstance(statement["nonce"], str) or not re.fullmatch(r"[0-9a-f]{32}", statement["nonce"]):
            raise InvalidPrice("NAV nonce")
        return PriceRecord("ISSUER_NAV", statement["instrumentId"], value, -6,
                           "USD_PER_SYNTHETIC_UNIT", issuer, observed, now,
                           hashlib.sha256(_canonical(statement)).hexdigest(), None,
                           "OPERATOR_PINNED_HMAC_ISSUER")


class PrivateQuoteProvider:
    def __init__(self, seller_keys: dict[str, bytes]):
        self.seller_keys = seller_keys

    def read(self, statement: dict, signature: str, *, now: int | None = None) -> PriceRecord:
        now = int(time.time()) if now is None else now
        expected = {"version", "instrumentId", "assetBase", "sellerId", "quantity", "totalZat", "observedAt", "expiresAt", "nonce"}
        if type(statement) is not dict or set(statement) != expected:
            raise InvalidPrice("private quote schema")
        seller = statement["sellerId"]
        if statement["version"] != 1 or seller not in self.seller_keys:
            raise InvalidPrice("unauthorized seller")
        _verify_statement(statement, signature, self.seller_keys[seller])
        observed = _timestamp(statement["observedAt"], now, 300)
        expires = _integer(statement["expiresAt"], "expiry", positive=True)
        if expires <= now or expires <= observed or expires - observed > 300:
            raise InvalidPrice("expired or excessive quote lifetime")
        if not isinstance(statement["assetBase"], str) or not re.fullmatch(r"[0-9a-f]{64}", statement["assetBase"]):
            raise InvalidPrice("asset base")
        quantity = _integer(statement["quantity"], "quantity", positive=True)
        value = _integer(statement["totalZat"], "ZEC payment", positive=True)
        if quantity > 2**63 - 1 or value > 21_000_000 * 100_000_000:
            raise InvalidPrice("quote exceeds checkout bounds")
        if not isinstance(statement["nonce"], str) or not re.fullmatch(r"[0-9a-f]{32}", statement["nonce"]):
            raise InvalidPrice("quote nonce")
        return PriceRecord("SELLER_EXECUTION_QUOTE", statement["instrumentId"], value, -8,
                           "ZEC_TOTAL_PAYMENT", seller, observed, now,
                           hashlib.sha256(_canonical(statement)).hexdigest(), None,
                           "OPERATOR_PINNED_HMAC_SELLER")
