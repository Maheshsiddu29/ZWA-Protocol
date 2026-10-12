"""Private, durable pre-approval offers and inventory reservations.

Only trusted local service code may call this module. It does not authenticate
HTTP callers, authorize proofs, sign wallets, or mark orders executable. A
seller reservation here is a coordination guard, not a Zcash note lock.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

from checkout.quote import validate as validate_quote
from .persistent_chain import status as chain_status
from .portfolio_scans import build as build_portfolio

HEX32 = re.compile(r"[0-9a-f]{64}\Z")
HEX16 = re.compile(r"[0-9a-f]{32}\Z")
IDENT = re.compile(r"[a-zA-Z0-9_-]{1,80}\Z")


class OrderError(ValueError):
    pass


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise OrderError("invalid identifier")
    return value


def _hex32(value: str) -> str:
    if not isinstance(value, str) or not HEX32.fullmatch(value):
        raise OrderError("invalid 32-byte digest")
    return value


def _record_id(value: str) -> str:
    if not isinstance(value, str) or not HEX16.fullmatch(value):
        raise OrderError("invalid record ID")
    return value


class OrderLedger:
    def __init__(self, database: Path):
        if database.is_symlink():
            raise OrderError("database cannot be a symlink")
        database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not database.exists():
            descriptor = os.open(database, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        if database.stat().st_mode & 0o077:
            raise OrderError("database must be owner-only")
        self.database = database
        with self._connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise OrderError("unsupported order database schema")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS offers (
                  id TEXT PRIMARY KEY, seller_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
                  asset_base TEXT NOT NULL, chain_genesis TEXT NOT NULL,
                  scan_height INTEGER NOT NULL, scan_anchor TEXT NOT NULL,
                  inventory_at_scan INTEGER NOT NULL CHECK(inventory_at_scan >= 0),
                  capacity INTEGER NOT NULL CHECK(capacity > 0),
                  reserved INTEGER NOT NULL DEFAULT 0 CHECK(reserved >= 0),
                  sold INTEGER NOT NULL DEFAULT 0 CHECK(sold >= 0),
                  state TEXT NOT NULL CHECK(state IN ('OFFER_CREATED','CLOSED')),
                  idempotency_key TEXT NOT NULL, created_at INTEGER NOT NULL,
                  UNIQUE(seller_id,idempotency_key),
                  CHECK(reserved + sold <= capacity)
                );
                CREATE TABLE IF NOT EXISTS orders (
                  id TEXT PRIMARY KEY, offer_id TEXT NOT NULL REFERENCES offers(id),
                  buyer_id TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity > 0),
                  state TEXT NOT NULL CHECK(state IN
                    ('BUYER_REQUESTED','QUOTE_READY','CANCELLED','EXPIRED')),
                  quote_digest TEXT UNIQUE, quote_expiry_height INTEGER,
                  idempotency_key TEXT NOT NULL, created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL,
                  UNIQUE(buyer_id,idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS order_events (
                  order_id TEXT NOT NULL REFERENCES orders(id), sequence INTEGER NOT NULL,
                  from_state TEXT, to_state TEXT NOT NULL, occurred_at INTEGER NOT NULL,
                  PRIMARY KEY(order_id,sequence)
                );
            """)
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.database, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self):
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.execute("COMMIT")
            except Exception:
                db.execute("ROLLBACK")
                raise

    def create_offer(self, *, seller_id: str, instrument_id: str, asset_base: str,
                     chain_genesis: str, scan_height: int, scan_anchor: str,
                     inventory_units: int, capacity: int, idempotency_key: str) -> dict:
        """Reserve a share of an operator-verified seller wallet scan.

        This internal method must only receive inventory from the live-chain
        `create_live_offer` adapter below, never from a browser request.
        """
        _identifier(seller_id); _identifier(instrument_id); _identifier(idempotency_key)
        _hex32(asset_base); _hex32(chain_genesis); _hex32(scan_anchor)
        if (type(scan_height) is not int or scan_height < 1 or
                type(inventory_units) is not int or inventory_units < 1 or
                type(capacity) is not int or not 1 <= capacity <= inventory_units):
            raise OrderError("invalid funded inventory")
        now = int(time.time())
        with self._transaction() as db:
            prior = db.execute("SELECT * FROM offers WHERE seller_id=? AND idempotency_key=?",
                               (seller_id, idempotency_key)).fetchone()
            if prior:
                if (prior["instrument_id"], prior["asset_base"], prior["capacity"],
                    prior["chain_genesis"], prior["scan_height"], prior["scan_anchor"]) != (
                        instrument_id, asset_base, capacity, chain_genesis, scan_height, scan_anchor):
                    raise OrderError("idempotency key reused for a different offer")
                return dict(prior)
            used = db.execute("""SELECT COALESCE(SUM(capacity - sold),0) FROM offers
                                 WHERE seller_id=? AND asset_base=? AND chain_genesis=?
                                   AND state='OFFER_CREATED'""",
                              (seller_id, asset_base, chain_genesis)).fetchone()[0]
            if used + capacity > inventory_units:
                raise OrderError("seller inventory is already allocated to offers")
            offer_id = secrets.token_hex(16)
            db.execute("""INSERT INTO offers
                (id,seller_id,instrument_id,asset_base,chain_genesis,scan_height,scan_anchor,
                 inventory_at_scan,capacity,state,idempotency_key,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,'OFFER_CREATED',?,?)""",
                (offer_id,seller_id,instrument_id,asset_base,chain_genesis,scan_height,
                 scan_anchor,inventory_units,capacity,idempotency_key,now))
            return dict(db.execute("SELECT * FROM offers WHERE id=?", (offer_id,)).fetchone())

    def request_purchase(self, *, offer_id: str, buyer_id: str, quantity: int,
                         idempotency_key: str, chain_genesis: str) -> dict:
        _record_id(offer_id); _identifier(buyer_id); _identifier(idempotency_key); _hex32(chain_genesis)
        if type(quantity) is not int or quantity <= 0:
            raise OrderError("invalid quantity")
        now = int(time.time())
        with self._transaction() as db:
            prior = db.execute("SELECT * FROM orders WHERE buyer_id=? AND idempotency_key=?",
                               (buyer_id, idempotency_key)).fetchone()
            if prior:
                prior_offer = db.execute("SELECT chain_genesis FROM offers WHERE id=?",
                                         (prior["offer_id"],)).fetchone()
                if (prior["offer_id"] != offer_id or prior["quantity"] != quantity
                        or prior_offer["chain_genesis"] != chain_genesis):
                    raise OrderError("idempotency key reused for a different purchase")
                return dict(prior)
            offer = db.execute("SELECT * FROM offers WHERE id=?", (offer_id,)).fetchone()
            if not offer or offer["state"] != "OFFER_CREATED" or offer["chain_genesis"] != chain_genesis:
                raise OrderError("offer is unavailable on this chain")
            if offer["capacity"] - offer["reserved"] - offer["sold"] < quantity:
                raise OrderError("seller inventory is already reserved")
            order_id = secrets.token_hex(16)
            db.execute("UPDATE offers SET reserved=reserved+? WHERE id=?", (quantity, offer_id))
            db.execute("""INSERT INTO orders
                (id,offer_id,buyer_id,quantity,state,idempotency_key,created_at,updated_at)
                VALUES (?,?,?,?,'BUYER_REQUESTED',?,?,?)""",
                (order_id,offer_id,buyer_id,quantity,idempotency_key,now,now))
            db.execute("INSERT INTO order_events VALUES (?,1,NULL,'BUYER_REQUESTED',?)", (order_id,now))
            return dict(db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone())

    def attach_quote(self, order_id: str, quote: dict, *, observed_height: int) -> dict:
        _record_id(order_id)
        try:
            validate_quote(quote)
        except ValueError as exc:
            raise OrderError("invalid exact checkout quote") from exc
        if (type(observed_height) is not int or observed_height < 0
                or observed_height + 1 != quote["targetHeight"]
                or observed_height >= quote["transactionExpiry"]):
            raise OrderError("quote is not fresh at the observed chain height")
        with self._transaction() as db:
            order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if order and order["state"] == "QUOTE_READY" and order["quote_digest"] == quote["quoteDigest"]:
                return dict(order)
            if not order or order["state"] != "BUYER_REQUESTED":
                raise OrderError("purchase is not awaiting a quote")
            offer = db.execute("SELECT * FROM offers WHERE id=?", (order["offer_id"],)).fetchone()
            if (quote["assetId"] != offer["asset_base"] or quote["quantity"] != order["quantity"]):
                raise OrderError("quote does not match reserved asset or quantity")
            now = int(time.time())
            db.execute("""UPDATE orders SET state='QUOTE_READY',quote_digest=?,
                          quote_expiry_height=?,updated_at=? WHERE id=?""",
                       (quote["quoteDigest"],quote["transactionExpiry"],now,order_id))
            db.execute("INSERT INTO order_events VALUES (?,2,'BUYER_REQUESTED','QUOTE_READY',?)",
                       (order_id,now))
            return dict(db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone())

    def close_before_approval(self, order_id: str, *, buyer_id: str,
                              state: str) -> dict:
        """Cancel or expire only before any wallet authorization starts."""
        _record_id(order_id); _identifier(buyer_id)
        if state not in ("CANCELLED", "EXPIRED"):
            raise OrderError("unsupported pre-approval close state")
        with self._transaction() as db:
            order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if not order or order["buyer_id"] != buyer_id:
                raise OrderError("order is unavailable to buyer")
            if order["state"] == state:
                return dict(order)
            if order["state"] not in ("BUYER_REQUESTED", "QUOTE_READY"):
                raise OrderError("reservation cannot be released after approval starts")
            now = int(time.time())
            db.execute("UPDATE offers SET reserved=reserved-? WHERE id=?", (order["quantity"],order["offer_id"]))
            db.execute("UPDATE orders SET state=?,updated_at=? WHERE id=?", (state,now,order_id))
            sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM order_events WHERE order_id=?",
                                  (order_id,)).fetchone()[0]
            db.execute("INSERT INTO order_events VALUES (?,?,?,?,?)",
                       (order_id,sequence,order["state"],state,now))
            return dict(db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone())


def create_live_offer(root: Path, container: str, port: int, *, seller_id: str,
                      instrument_id: str, capacity: int, idempotency_key: str) -> dict:
    """Create a private offer only from current live scans and chain anchor."""
    state = chain_status(root, container, port)
    if state.get("state") not in ("CHAIN_ACTIVE", "CHAIN_RESTARTED"):
        raise OrderError("chain is not active and anchor-verified")
    exchange = root / "exchange"
    scans = exchange / "rescans"
    candidates = [p for p in scans.glob("scan.*")
                  if p.is_dir() and not p.is_symlink() and (p / "portfolio.json").is_file()]
    if not candidates:
        raise OrderError("a fresh post-restart wallet rescan is required")
    newest = max(candidates, key=lambda p: (p / "portfolio.json").stat().st_mtime_ns)
    portfolio = build_portfolio(exchange, root / "chain-identity.json",
                                restart=True, scan_dir=newest)
    if (portfolio["scanHeight"] != state["height"] or
            portfolio["anchorHash"] != state["anchorHash"]):
        raise OrderError("seller inventory scan is not at the current chain tip")
    holding = next((item for item in portfolio["holdings"]
                    if item["instrumentId"] == instrument_id), None)
    if not holding:
        raise OrderError("instrument has no verified seller inventory")
    ledger = OrderLedger(exchange / "exchange.sqlite")
    return ledger.create_offer(seller_id=seller_id, instrument_id=instrument_id,
                               asset_base=holding["assetBase"],
                               chain_genesis=portfolio["genesisHash"],
                               scan_height=portfolio["scanHeight"],
                               scan_anchor=portfolio["anchorHash"],
                               inventory_units=holding["sellerUnits"],
                               capacity=capacity, idempotency_key=idempotency_key)
