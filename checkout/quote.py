"""Phase 3.5G local checkout quote codec; mirrors the pinned tx-tool QuoteV1.

This digest is an off-chain approval token. It is not a consensus sighash.
"""

from __future__ import annotations

import hashlib
import re
import struct

PERSON = b"ZWA35G_QUOTE_V1\0"
HEX32 = re.compile(r"[0-9a-f]{64}\Z")
KNOWN_BRANCH = 2290649224
MONEY_MAX = 21_000_000 * 100_000_000

FIELDS = frozenset({
    "version", "quoteId", "network", "assetId", "quantity", "paymentAsset",
    "paymentAmount", "buyerReceivingCommitment", "sellerProceedsCommitment",
    "feePayer", "networkFee", "matcherFee", "maximumTotalDebit",
    "transactionExpiry", "consensusBranchId", "targetHeight", "groupExpiry",
    "sponsorOutpointTxid", "sponsorOutpointIndex", "sponsorInput",
    "sponsorChange", "quoteDigest",
})


class InvalidQuote(ValueError):
    pass


def _hex32(value: object) -> bytes:
    if not isinstance(value, str) or not HEX32.fullmatch(value):
        raise InvalidQuote("noncanonical 32-byte hexadecimal field")
    return bytes.fromhex(value)


def _number(value: object, bits: int) -> int:
    if type(value) is not int or not 0 <= value < 1 << bits:
        raise InvalidQuote("integer out of range")
    return value


def _u32(value: object) -> bytes:
    return struct.pack("<I", _number(value, 32))


def _u64(value: object) -> bytes:
    return struct.pack("<Q", _number(value, 64))


def _receiver(domain: bytes, address_hex: str) -> str:
    if not isinstance(address_hex, str) or not re.fullmatch(r"[0-9a-f]{86}", address_hex):
        raise InvalidQuote("noncanonical recipient")
    return hashlib.sha256(domain + bytes.fromhex(address_hex)).hexdigest()


def from_terms(terms: dict) -> dict:
    """Build public quote fields from already reviewed Phase 3.5F terms."""
    expected = {
        "version", "network", "consensus_branch_id", "asset_base", "quantity",
        "price_zat", "matcher_fee_zat", "network_fee_zat", "buyer_receive_address",
        "seller_proceeds_address", "fee_sponsor_txid", "fee_sponsor_index",
        "fee_sponsor_input_zat", "fee_sponsor_change_zat", "target_height",
        "tx_expiry_height", "group_expiry_height", "nonce",
    }
    if type(terms) is not dict or set(terms) != expected:
        raise InvalidQuote("terms schema")
    quote = {
        "version": terms["version"], "quoteId": terms["nonce"],
        "network": terms["network"], "assetId": terms["asset_base"],
        "quantity": terms["quantity"], "paymentAsset": "ZEC",
        "paymentAmount": terms["price_zat"],
        "buyerReceivingCommitment": _receiver(b"ZWA35G_BUYER_RECEIVER_V1\0", terms["buyer_receive_address"]),
        "sellerProceedsCommitment": _receiver(b"ZWA35G_SELLER_RECEIVER_V1\0", terms["seller_proceeds_address"]),
        "feePayer": "DISPOSABLE_REGTEST_SPONSOR",
        "networkFee": terms["network_fee_zat"], "matcherFee": terms["matcher_fee_zat"],
        "maximumTotalDebit": terms["price_zat"],
        "transactionExpiry": terms["tx_expiry_height"],
        "consensusBranchId": terms["consensus_branch_id"],
        "targetHeight": terms["target_height"], "groupExpiry": terms["group_expiry_height"],
        "sponsorOutpointTxid": terms["fee_sponsor_txid"],
        "sponsorOutpointIndex": terms["fee_sponsor_index"],
        "sponsorInput": terms["fee_sponsor_input_zat"],
        "sponsorChange": terms["fee_sponsor_change_zat"],
    }
    quote["quoteDigest"] = digest(quote)
    return quote


def digest(quote: dict) -> str:
    if type(quote) is not dict or set(quote) != FIELDS - {"quoteDigest"}:
        raise InvalidQuote("quote schema")
    if quote["version"] != 1 or quote["network"] != "regtest":
        raise InvalidQuote("unsupported quote version or network")
    if quote["paymentAsset"] != "ZEC" or quote["feePayer"] != "DISPOSABLE_REGTEST_SPONSOR":
        raise InvalidQuote("unsupported payment or fee payer")
    if quote["consensusBranchId"] != KNOWN_BRANCH:
        raise InvalidQuote("wrong consensus branch")
    asset = _hex32(quote["assetId"])
    if asset == bytes(32):
        raise InvalidQuote("unsupported asset shape")
    quantity = _number(quote["quantity"], 64)
    payment = _number(quote["paymentAmount"], 64)
    fee = _number(quote["networkFee"], 64)
    if not 0 < quantity <= (1 << 63) - 1 or not 0 < payment <= MONEY_MAX:
        raise InvalidQuote("unsupported quantity or payment")
    if quote["matcherFee"] != 0 or quote["maximumTotalDebit"] != payment:
        raise InvalidQuote("buyer total is inconsistent")
    if fee == 0 or fee % 5 or fee > MONEY_MAX:
        raise InvalidQuote("unsupported network fee")
    if quote["sponsorOutpointIndex"] != 0 or quote["sponsorInput"] != 625_000_000:
        raise InvalidQuote("unsupported sponsor input")
    if quote["sponsorChange"] != 625_000_000 - fee:
        raise InvalidQuote("sponsor fee arithmetic")
    target = _number(quote["targetHeight"], 32)
    if target > (1 << 32) - 41 or quote["transactionExpiry"] != target + 40 or quote["groupExpiry"] != target + 10:
        raise InvalidQuote("expiry arithmetic")
    payload = b"".join((
        _u32(quote["version"]), b"\x01\x00\x01", asset,
        _u64(quantity), _u64(payment),
        _hex32(quote["buyerReceivingCommitment"]),
        _hex32(quote["sellerProceedsCommitment"]),
        _u64(fee), _u64(quote["matcherFee"]), _u64(quote["maximumTotalDebit"]),
        _u32(quote["transactionExpiry"]), _u32(quote["consensusBranchId"]),
        _u32(target), _u32(quote["groupExpiry"]),
        _hex32(quote["sponsorOutpointTxid"]),
        _u32(quote["sponsorOutpointIndex"]),
        _u64(quote["sponsorInput"]), _u64(quote["sponsorChange"]),
        _hex32(quote["quoteId"]),
    ))
    return hashlib.blake2b(payload, digest_size=32, person=PERSON).hexdigest()


def validate(quote: dict) -> None:
    if type(quote) is not dict or set(quote) != FIELDS:
        raise InvalidQuote("quote schema")
    claimed = _hex32(quote["quoteDigest"])
    actual = digest({key: value for key, value in quote.items() if key != "quoteDigest"})
    if claimed != bytes.fromhex(actual):
        raise InvalidQuote("quote digest mismatch")
