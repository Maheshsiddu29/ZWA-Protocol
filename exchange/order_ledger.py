"""Private, durable offers, reservations, and observed settlement events.

Only trusted local service code may write this module. It does not authenticate
HTTP callers or sign wallets. A reservation is not a Zcash note lock.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
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
EXECUTION_STATES = (
    "PROOF_VERIFIED", "TRADE_AUTHORIZED", "BUYER_APPROVED", "SELLER_APPROVED",
    "SUBMITTED", "INCLUDED", "RECONCILED", "RECEIPT_AVAILABLE",
)
ALL_STATES = ("BUYER_REQUESTED", "QUOTE_READY", *EXECUTION_STATES,
              "CANCELLED", "EXPIRED", "REJECTED", "EXECUTION_FAILED", "UNCERTAIN")


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
            if version not in (0, 1, 2):
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
            if version < 2:
                # Rebuild only the orders table to widen its state constraint.
                # Existing IDs, reservations, and event sequences are preserved.
                db.execute("PRAGMA foreign_keys=OFF")
                allowed = ",".join("'" + state + "'" for state in ALL_STATES)
                db.executescript(f"""
                    BEGIN IMMEDIATE;
                    CREATE TABLE orders_v2 (
                      id TEXT PRIMARY KEY, offer_id TEXT NOT NULL REFERENCES offers(id),
                      buyer_id TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity > 0),
                      state TEXT NOT NULL CHECK(state IN ({allowed})),
                      quote_digest TEXT UNIQUE, quote_expiry_height INTEGER,
                      idempotency_key TEXT NOT NULL, created_at INTEGER NOT NULL,
                      updated_at INTEGER NOT NULL, UNIQUE(buyer_id,idempotency_key)
                    );
                    INSERT INTO orders_v2 SELECT * FROM orders;
                    DROP TABLE orders;
                    ALTER TABLE orders_v2 RENAME TO orders;
                    CREATE TABLE order_evidence (
                      order_id TEXT NOT NULL REFERENCES orders(id), sequence INTEGER NOT NULL,
                      kind TEXT NOT NULL, value_json TEXT NOT NULL,
                      PRIMARY KEY(order_id,sequence),
                      FOREIGN KEY(order_id,sequence) REFERENCES order_events(order_id,sequence)
                    );
                    CREATE TABLE order_settlements (
                      order_id TEXT PRIMARY KEY REFERENCES orders(id),
                      txid TEXT NOT NULL UNIQUE, quote_digest TEXT NOT NULL UNIQUE
                    );
                    PRAGMA user_version=2;
                    COMMIT;
                """)
                db.execute("PRAGMA foreign_keys=ON")
            else:
                db.execute("""CREATE TABLE IF NOT EXISTS order_evidence (
                      order_id TEXT NOT NULL REFERENCES orders(id), sequence INTEGER NOT NULL,
                      kind TEXT NOT NULL, value_json TEXT NOT NULL,
                      PRIMARY KEY(order_id,sequence),
                      FOREIGN KEY(order_id,sequence) REFERENCES order_events(order_id,sequence)
                    )""")
                db.execute("""CREATE TABLE IF NOT EXISTS order_settlements (
                      order_id TEXT PRIMARY KEY REFERENCES orders(id),
                      txid TEXT NOT NULL UNIQUE, quote_digest TEXT NOT NULL UNIQUE
                    )""")

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

    def get_order(self, order_id: str, *, participant_id: str) -> dict:
        """Return one private order only to its buyer or seller."""
        _record_id(order_id); _identifier(participant_id)
        with self._connection() as db:
            row = db.execute("""SELECT o.*, f.seller_id, f.instrument_id, f.asset_base,
                               f.chain_genesis, f.reserved, f.sold FROM orders o
                               JOIN offers f ON f.id=o.offer_id WHERE o.id=?""",
                             (order_id,)).fetchone()
            if not row or participant_id not in (row["buyer_id"], row["seller_id"]):
                raise OrderError("order is unavailable to participant")
            result = dict(row)
            result["events"] = [dict(e) for e in db.execute(
                "SELECT * FROM order_events WHERE order_id=? ORDER BY sequence", (order_id,))]
            result["evidence"] = [dict(e) for e in db.execute(
                "SELECT * FROM order_evidence WHERE order_id=? ORDER BY sequence", (order_id,))]
            return result

    def transition(self, order_id: str, *, expected: str, state: str,
                   evidence: dict) -> dict:
        """Append one observed event atomically; never infer success from intent.

        The trusted operator adapter validates the external observation before
        calling this method. Exact required fields here prevent empty claims.
        """
        _record_id(order_id)
        next_state = {
            "QUOTE_READY": "PROOF_VERIFIED",
            "PROOF_VERIFIED": "TRADE_AUTHORIZED",
            "TRADE_AUTHORIZED": "BUYER_APPROVED",
            "BUYER_APPROVED": "SELLER_APPROVED",
            "SELLER_APPROVED": "SUBMITTED",
            "SUBMITTED": "INCLUDED",
            "INCLUDED": "RECONCILED",
            "RECONCILED": "RECEIPT_AVAILABLE",
        }
        if next_state.get(expected) != state or type(evidence) is not dict:
            raise OrderError("unsupported execution transition")
        required = {
            "PROOF_VERIFIED": ("quoteDigest", "tradeCommitment", "verifierResultSha256"),
            "TRADE_AUTHORIZED": ("quoteDigest", "assetBase", "buyerId", "sellerId", "tipHeight"),
            "BUYER_APPROVED": ("quoteDigest", "role", "approvalMode", "signerLogSha256"),
            "SELLER_APPROVED": ("quoteDigest", "role", "approvalMode", "signerLogSha256"),
            "SUBMITTED": ("txid", "quoteDigest"),
            "INCLUDED": ("txid", "height", "blockHash"),
            "RECONCILED": ("txid", "buyerUnitsBefore", "buyerUnitsAfter",
                           "sellerUnitsBefore", "sellerUnitsAfter", "sellerZatBefore", "sellerZatAfter"),
            "RECEIPT_AVAILABLE": ("txid", "height", "blockHash", "approvalMode"),
        }[state]
        if not all(key in evidence for key in required):
            raise OrderError("transition lacks required observation")
        if state in ("BUYER_APPROVED", "SELLER_APPROVED"):
            role = "buyer" if state == "BUYER_APPROVED" else "seller"
            if evidence["role"] != role or evidence["approvalMode"] not in (
                    "SCRIPTED_TEST_MODE", "HUMAN_APPROVED"):
                raise OrderError("signer identity or approval mode mismatch")
        if state == "RECONCILED":
            numeric = ("buyerUnitsBefore", "buyerUnitsAfter", "sellerUnitsBefore",
                       "sellerUnitsAfter", "sellerZatBefore", "sellerZatAfter")
            if any(type(evidence[key]) is not int or evidence[key] < 0 for key in numeric):
                raise OrderError("invalid scan number")
            units = (evidence["buyerUnitsAfter"] - evidence["buyerUnitsBefore"],
                     evidence["sellerUnitsBefore"] - evidence["sellerUnitsAfter"])
            if units[0] != units[1] or units[0] <= 0 or (
                    evidence["sellerZatAfter"] <= evidence["sellerZatBefore"]):
                raise OrderError("wallet effects do not reconcile")
        for key, value in evidence.items():
            if key.endswith("Sha256") or key in ("quoteDigest", "assetBase", "txid", "blockHash"):
                _hex32(value)
        payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        if len(payload) > 8192:
            raise OrderError("evidence is too large")
        with self._transaction() as db:
            order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if not order:
                raise OrderError("order does not exist")
            if order["state"] == state:
                prior = db.execute("""SELECT value_json FROM order_evidence
                                     WHERE order_id=? ORDER BY sequence DESC LIMIT 1""", (order_id,)).fetchone()
                if prior and prior[0] == payload:
                    return dict(order)
                raise OrderError("duplicate transition has different evidence")
            if order["state"] != expected or evidence.get("quoteDigest", order["quote_digest"]) != order["quote_digest"]:
                raise OrderError("order state or quote digest changed")
            offer = db.execute("SELECT * FROM offers WHERE id=?", (order["offer_id"],)).fetchone()
            if offer["reserved"] < order["quantity"]:
                raise OrderError("order reservation is missing")
            if state == "TRADE_AUTHORIZED" and (
                    evidence["assetBase"] != offer["asset_base"] or
                    evidence["buyerId"] != order["buyer_id"] or
                    evidence["sellerId"] != offer["seller_id"] or
                    evidence["tipHeight"] + 1 != order["quote_expiry_height"] - 40):
                raise OrderError("authorized trade differs from reserved quote or chain tip")
            if state == "INCLUDED":
                submitted = db.execute("SELECT txid FROM order_settlements WHERE order_id=?",
                                       (order_id,)).fetchone()
                if not submitted or submitted["txid"] != evidence["txid"]:
                    raise OrderError("included transaction differs from submission")
            if state == "RECONCILED" and units[0] != order["quantity"]:
                raise OrderError("wallet unit delta differs from order quantity")
            now = int(time.time())
            sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM order_events WHERE order_id=?",
                                  (order_id,)).fetchone()[0]
            db.execute("UPDATE orders SET state=?,updated_at=? WHERE id=?", (state,now,order_id))
            db.execute("INSERT INTO order_events VALUES (?,?,?,?,?)", (order_id,sequence,expected,state,now))
            db.execute("INSERT INTO order_evidence VALUES (?,?,?,?)", (order_id,sequence,state,payload))
            if state == "SUBMITTED":
                db.execute("INSERT INTO order_settlements VALUES (?,?,?)",
                           (order_id,evidence["txid"],order["quote_digest"]))
            if state == "RECEIPT_AVAILABLE":
                db.execute("UPDATE offers SET reserved=reserved-?,sold=sold+? WHERE id=?",
                           (order["quantity"],order["quantity"],order["offer_id"]))
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
