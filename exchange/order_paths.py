"""Locations of per-order private evidence inside an exchange root."""

from __future__ import annotations

from pathlib import Path
import re

# The Phase 4E-B order predates per-order quote directories. Its evidence is
# read in place rather than copied, so historical files stay unchanged.
LEGACY_QUOTE_DIRS = {"ec27f52d2e7ad69287f7d4d4357025af": "order-quotes/gold-1"}
APPROVAL_MODES = ("SCRIPTED_TEST_MODE", "HUMAN_APPROVED")


def order_dir(exchange: Path, order_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", order_id):
        raise ValueError("invalid order ID")
    return exchange / "orders" / order_id


def quote_dir(exchange: Path, order_id: str) -> Path:
    legacy = LEGACY_QUOTE_DIRS.get(order_id)
    return exchange / legacy if legacy else order_dir(exchange, order_id) / "quote"
