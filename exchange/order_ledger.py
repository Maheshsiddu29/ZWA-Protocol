"""Private, durable offers, reservations, and observed settlement events.

Only trusted local service code may write this module. It does not authenticate
HTTP callers or sign wallets. A reservation is not a Zcash note lock; the
wallet-level commitment guard below serializes quotes per wallet instead,
because the independent signers select their own notes.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
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
ADDRESS = re.compile(r"[0-9a-f]{86}\Z")
PAIRING_CODE = re.compile(r"[A-Z2-7]{10}\Z")
EXECUTION_STATES = (
    "PROOF_VERIFIED", "TRADE_AUTHORIZED", "BUYER_APPROVED", "SELLER_APPROVED",
    "SUBMITTED", "INCLUDED", "RECONCILED", "RECEIPT_AVAILABLE",
)
FAILURE_STATES = ("CANCELLED", "EXPIRED", "REJECTED", "EXECUTION_FAILED", "UNCERTAIN")
ALL_STATES = ("BUYER_REQUESTED", "QUOTE_READY", "BUYER_CONFIRMED", *EXECUTION_STATES,
              *FAILURE_STATES)
# While an order is in one of these states its exact quote may consume the
# buyer's and seller's notes. UNCERTAIN keeps both wallets blocked until an
# operator reconciles it against the chain.
WALLET_COMMITTED_STATES = ("QUOTE_READY", "BUYER_CONFIRMED", "PROOF_VERIFIED",
                           "TRADE_AUTHORIZED", "BUYER_APPROVED", "SELLER_APPROVED",
                           "SUBMITTED", "INCLUDED", "RECONCILED", "UNCERTAIN")
PRE_EXECUTION_STATES = ("BUYER_REQUESTED", "QUOTE_READY", "BUYER_CONFIRMED",
                        "PROOF_VERIFIED", "TRADE_AUTHORIZED")
PRICE_CLASSIFICATION = "SELLER_FIXED_EXPERIMENTAL"
ROLES = ("buyer", "seller")
APPROVAL_MODES = ("HUMAN_APPROVED", "SCRIPTED_TEST_MODE", "CUSTODIAL_DEMO_POLICY")
# OPERATOR_CONFIRMED_LOCAL_COMPANION: a human-run companion confirmed PAIR.
# CUSTODIAL_DEMO_WALLET: a disposable server-held judge wallet (schema 4).
# DEMO_DEALER_STANDING_POLICY: the demo dealer's bounded standing policy.
PAIRING_METHODS = ("OPERATOR_CONFIRMED_LOCAL_COMPANION", "CUSTODIAL_DEMO_WALLET",
                   "DEMO_DEALER_STANDING_POLICY")
SCHEMA_VERSION = 4
# Ledgers holding owner-reviewed evidence are never migrated implicitly.
# Kept in sync with exchange.config.PROTECTED_ROOTS.
PROTECTED_LEDGERS = frozenset({"/private/tmp/zwa4e-phase4e-b/exchange/exchange.sqlite"})


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


def _role(value: str) -> str:
    if value not in ROLES:
        raise OrderError("invalid wallet role")
    return value


def _positive(value: object, limit: int = 1 << 62) -> int:
    if type(value) is not int or not 0 < value <= limit:
        raise OrderError("invalid positive integer")
    return value


def pairing_code_hash(code: str) -> str:
    if not isinstance(code, str) or not PAIRING_CODE.fullmatch(code):
        raise OrderError("invalid pairing code")
    return hashlib.sha256(b"ZWA4E_PAIRING_CODE_V1\0" + code.encode()).hexdigest()


class OrderLedger:
    def __init__(self, database: Path):
        if database.is_symlink():
            raise OrderError("database cannot be a symlink")
        database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            descriptor = os.open(database, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
        except FileExistsError:
            pass  # Another local process created it first.
        if database.stat().st_mode & 0o077:
            raise OrderError("database must be owner-only")
        self.database = database
        with self._connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, SCHEMA_VERSION):
                raise OrderError("unsupported order database schema")
            if version < 3:
                self._migrate(db, version)
                version = 3
            if version == 3 and str(database) not in PROTECTED_LEDGERS:
                self._migrate_v4(db)
                version = SCHEMA_VERSION
        # A protected v3 ledger stays v3: it supports the human companion flow
        # only. Demo tables exist from schema 4.
        self.schema = version

    def _migrate_v4(self, db: sqlite3.Connection) -> None:
        """Add custodial demo wallets, wallet scans and settings (v3 -> v4).

        Every existing row is copied unchanged; wallet_pairings only gains two
        method values. Nothing is inferred about existing pairings.
        """
        methods = ",".join("'" + method + "'" for method in PAIRING_METHODS)
        script = f"""
            CREATE TABLE wallet_pairings_v4 (
              id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
              role TEXT NOT NULL CHECK(role IN ('buyer','seller')),
              wallet_address TEXT NOT NULL, session_hash TEXT NOT NULL,
              method TEXT NOT NULL CHECK(method IN ({methods})),
              created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, revoked_at INTEGER
            );
            INSERT INTO wallet_pairings_v4 SELECT id,account_id,role,wallet_address,session_hash,
              method,created_at,expires_at,revoked_at FROM wallet_pairings;
            DROP TABLE wallet_pairings;
            ALTER TABLE wallet_pairings_v4 RENAME TO wallet_pairings;
            CREATE TABLE demo_wallets (
              id TEXT PRIMARY KEY, account_id TEXT NOT NULL UNIQUE,
              session_hash TEXT NOT NULL UNIQUE, client_hash TEXT NOT NULL,
              state TEXT NOT NULL CHECK(state IN ('REQUESTED','CREATED','FUNDING','READY',
                                                  'EXPIRED','RETIRED','FAILED')),
              wallet_address TEXT UNIQUE, pairing_id TEXT UNIQUE REFERENCES wallet_pairings(id),
              funding_txid TEXT UNIQUE, funding_height INTEGER, failure TEXT,
              created_at INTEGER NOT NULL, ready_at INTEGER, expires_at INTEGER NOT NULL,
              retired_at INTEGER
            );
            CREATE INDEX demo_wallets_created ON demo_wallets(created_at);
            CREATE TABLE wallet_scans (
              wallet_key TEXT NOT NULL, asset_base TEXT NOT NULL,
              scan_height INTEGER NOT NULL CHECK(scan_height > 0), block_hash TEXT NOT NULL,
              native_zat INTEGER NOT NULL CHECK(native_zat >= 0),
              asset_units INTEGER NOT NULL CHECK(asset_units >= 0), scanned_at INTEGER NOT NULL,
              PRIMARY KEY(wallet_key,asset_base,scan_height)
            );
            CREATE TABLE spent_coinbases (
              txid TEXT PRIMARY KEY, purpose TEXT NOT NULL CHECK(purpose IN ('DEMO_FUNDING')),
              recorded_at INTEGER NOT NULL
            );
            CREATE TABLE demo_settings (
              key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL
            )
        """
        db.execute("PRAGMA foreign_keys=OFF")
        try:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION:
                db.execute("COMMIT")
                return
            for statement in script.split(";"):
                if statement.strip():
                    db.execute(statement)
            if db.execute("PRAGMA foreign_key_check").fetchall():
                raise OrderError("ledger migration would break references")
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            db.execute("COMMIT")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.execute("PRAGMA foreign_keys=ON")

    def _migrate(self, db: sqlite3.Connection, version: int) -> None:
        """Bring any schema before v3 to v3 in one transaction.

        Existing offers, order IDs, reservations, event sequences, evidence
        and settlements are copied unchanged; only the orders state constraint
        widens and new tables are added.
        """
        allowed = ",".join("'" + state + "'" for state in ALL_STATES)
        db.execute("PRAGMA foreign_keys=OFF")
        try:
            db.execute("BEGIN IMMEDIATE")
            # Another process may have migrated while this one waited for the lock.
            if db.execute("PRAGMA user_version").fetchone()[0] >= 3:
                db.execute("COMMIT")
                return
            script = ("""
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
                  state TEXT NOT NULL,
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
            """ + f"""
                CREATE TABLE orders_v3 (
                  id TEXT PRIMARY KEY, offer_id TEXT NOT NULL REFERENCES offers(id),
                  buyer_id TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity > 0),
                  state TEXT NOT NULL CHECK(state IN ({allowed})),
                  quote_digest TEXT UNIQUE, quote_expiry_height INTEGER,
                  idempotency_key TEXT NOT NULL, created_at INTEGER NOT NULL,
                  updated_at INTEGER NOT NULL, UNIQUE(buyer_id,idempotency_key)
                );
                INSERT INTO orders_v3 SELECT id,offer_id,buyer_id,quantity,state,quote_digest,
                  quote_expiry_height,idempotency_key,created_at,updated_at FROM orders;
                DROP TABLE orders;
                ALTER TABLE orders_v3 RENAME TO orders;
                CREATE TABLE IF NOT EXISTS order_evidence (
                  order_id TEXT NOT NULL REFERENCES orders(id), sequence INTEGER NOT NULL,
                  kind TEXT NOT NULL, value_json TEXT NOT NULL,
                  PRIMARY KEY(order_id,sequence),
                  FOREIGN KEY(order_id,sequence) REFERENCES order_events(order_id,sequence)
                );
                CREATE TABLE IF NOT EXISTS order_settlements (
                  order_id TEXT PRIMARY KEY REFERENCES orders(id),
                  txid TEXT NOT NULL UNIQUE, quote_digest TEXT NOT NULL UNIQUE
                );
                CREATE TABLE offer_prices (
                  offer_id TEXT PRIMARY KEY REFERENCES offers(id),
                  seller_id TEXT NOT NULL,
                  price_zat_per_unit INTEGER NOT NULL CHECK(price_zat_per_unit > 0),
                  classification TEXT NOT NULL CHECK(classification='{PRICE_CLASSIFICATION}'),
                  version INTEGER NOT NULL CHECK(version > 0), updated_at INTEGER NOT NULL
                );
                CREATE TABLE pairing_requests (
                  id TEXT PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('buyer','seller')),
                  wallet_address TEXT NOT NULL, code_hash TEXT NOT NULL UNIQUE,
                  companion_pid INTEGER NOT NULL, created_at INTEGER NOT NULL,
                  expires_at INTEGER NOT NULL,
                  state TEXT NOT NULL CHECK(state IN ('PENDING','CLAIMED','CONFIRMED','REJECTED')),
                  account_id TEXT, session_hash TEXT, pairing_id TEXT
                );
                CREATE TABLE wallet_pairings (
                  id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                  role TEXT NOT NULL CHECK(role IN ('buyer','seller')),
                  wallet_address TEXT NOT NULL, session_hash TEXT NOT NULL,
                  method TEXT NOT NULL CHECK(method='OPERATOR_CONFIRMED_LOCAL_COMPANION'),
                  created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, revoked_at INTEGER
                );
                CREATE TABLE companion_presence (
                  pairing_id TEXT PRIMARY KEY REFERENCES wallet_pairings(id),
                  pid INTEGER NOT NULL, mode TEXT NOT NULL, last_seen INTEGER NOT NULL
                );
                CREATE TABLE order_quotes (
                  order_id TEXT PRIMARY KEY REFERENCES orders(id),
                  quote_digest TEXT NOT NULL UNIQUE, terms_sha256 TEXT NOT NULL,
                  price_zat_per_unit INTEGER NOT NULL, payment_zat INTEGER NOT NULL,
                  classification TEXT NOT NULL CHECK(classification='{PRICE_CLASSIFICATION}'),
                  price_version INTEGER NOT NULL,
                  buyer_pairing_id TEXT NOT NULL REFERENCES wallet_pairings(id),
                  seller_pairing_id TEXT NOT NULL REFERENCES wallet_pairings(id),
                  sponsor_txid TEXT NOT NULL, target_height INTEGER NOT NULL,
                  valid_until INTEGER NOT NULL, created_at INTEGER NOT NULL
                );
                CREATE TABLE order_executions (
                  order_id TEXT PRIMARY KEY REFERENCES orders(id),
                  quote_digest TEXT NOT NULL UNIQUE, approval_mode TEXT NOT NULL,
                  coordinator_pid INTEGER, started_at INTEGER NOT NULL,
                  finished_at INTEGER, outcome TEXT
                );
                CREATE TABLE order_notes (
                  order_id TEXT PRIMARY KEY REFERENCES orders(id),
                  note TEXT NOT NULL, updated_at INTEGER NOT NULL
                )
            """)
            # execute() keeps the explicit transaction open; executescript()
            # would commit it first. The DDL contains no literal semicolons.
            for statement in script.split(";"):
                if statement.strip():
                    db.execute(statement)
            if db.execute("PRAGMA foreign_key_check").fetchall():
                raise OrderError("ledger migration would break references")
            db.execute("PRAGMA user_version=3")
            db.execute("COMMIT")
        except Exception:
            if db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            db.execute("PRAGMA foreign_keys=ON")

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

    @staticmethod
    def _append_event(db, order_id: str, from_state: str | None, to_state: str,
                      now: int, evidence: dict | None = None) -> int:
        sequence = db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM order_events WHERE order_id=?",
                              (order_id,)).fetchone()[0]
        db.execute("INSERT INTO order_events VALUES (?,?,?,?,?)",
                   (order_id, sequence, from_state, to_state, now))
        if evidence is not None:
            db.execute("INSERT INTO order_evidence VALUES (?,?,?,?)",
                       (order_id, sequence, to_state,
                        json.dumps(evidence, sort_keys=True, separators=(",", ":"))))
        return sequence

    # ------------------------------------------------------------------ offers

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

    def set_price(self, *, seller_id: str, offer_id: str, price_zat_per_unit: int) -> dict:
        """Record the seller's fixed experimental price for future quotes only.

        Quotes already issued keep the price bound into their exact digest.
        """
        _identifier(seller_id); _record_id(offer_id)
        price = _positive(price_zat_per_unit, 21_000_000 * 100_000_000)
        now = int(time.time())
        with self._transaction() as db:
            offer = db.execute("SELECT * FROM offers WHERE id=?", (offer_id,)).fetchone()
            if not offer or offer["seller_id"] != seller_id or offer["state"] != "OFFER_CREATED":
                raise OrderError("offer is unavailable to seller")
            prior = db.execute("SELECT * FROM offer_prices WHERE offer_id=?", (offer_id,)).fetchone()
            if prior and prior["price_zat_per_unit"] == price:
                return dict(prior)
            version = prior["version"] + 1 if prior else 1
            db.execute("""INSERT INTO offer_prices VALUES (?,?,?,?,?,?)
                          ON CONFLICT(offer_id) DO UPDATE SET price_zat_per_unit=excluded.price_zat_per_unit,
                          version=excluded.version, updated_at=excluded.updated_at""",
                       (offer_id, seller_id, price, PRICE_CLASSIFICATION, version, now))
            return dict(db.execute("SELECT * FROM offer_prices WHERE offer_id=?", (offer_id,)).fetchone())

    def offer_price(self, offer_id: str) -> dict | None:
        _record_id(offer_id)
        with self._connection() as db:
            row = db.execute("SELECT * FROM offer_prices WHERE offer_id=?", (offer_id,)).fetchone()
            return dict(row) if row else None

    def get_offer(self, offer_id: str) -> dict | None:
        _record_id(offer_id)
        with self._connection() as db:
            row = db.execute("SELECT * FROM offers WHERE id=?", (offer_id,)).fetchone()
            return dict(row) if row else None

    # ----------------------------------------------------------------- pairing

    def create_pairing_request(self, *, role: str, wallet_address: str, code: str,
                               companion_pid: int, ttl_seconds: int = 300) -> dict:
        """Called by the wallet companion; the code is shown only in its terminal."""
        _role(role)
        if not isinstance(wallet_address, str) or not ADDRESS.fullmatch(wallet_address):
            raise OrderError("invalid wallet receiving address")
        _positive(companion_pid); _positive(ttl_seconds, 900)
        now = int(time.time())
        request_id = secrets.token_hex(16)
        with self._transaction() as db:
            db.execute("INSERT INTO pairing_requests VALUES (?,?,?,?,?,?,?,'PENDING',NULL,NULL,NULL)",
                       (request_id, role, wallet_address, pairing_code_hash(code),
                        companion_pid, now, now + ttl_seconds))
            return dict(db.execute("SELECT * FROM pairing_requests WHERE id=?", (request_id,)).fetchone())

    def claim_pairing(self, *, code: str, account_id: str, role: str, session_hash: str) -> dict:
        """Called for an authenticated portal session that typed the terminal code."""
        _identifier(account_id); _role(role); _hex32(session_hash)
        digest = pairing_code_hash(code)
        now = int(time.time())
        with self._transaction() as db:
            row = db.execute("SELECT * FROM pairing_requests WHERE code_hash=?", (digest,)).fetchone()
            if (not row or row["state"] != "PENDING" or row["expires_at"] <= now
                    or row["role"] != role):
                raise OrderError("pairing code is invalid, expired, or for another wallet role")
            db.execute("""UPDATE pairing_requests SET state='CLAIMED',account_id=?,session_hash=?
                          WHERE id=?""", (account_id, session_hash, row["id"]))
            return {"requestId": row["id"], "role": role, "state": "CLAIMED",
                    "walletAddress": row["wallet_address"]}

    def pairing_request(self, request_id: str) -> dict:
        _record_id(request_id)
        with self._connection() as db:
            row = db.execute("SELECT * FROM pairing_requests WHERE id=?", (request_id,)).fetchone()
            if not row:
                raise OrderError("pairing request does not exist")
            return dict(row)

    def confirm_pairing(self, request_id: str, *, companion_pid: int,
                        lifetime_seconds: int = 4 * 60 * 60) -> dict:
        """Called by the companion after its operator confirms the claiming account."""
        _record_id(request_id); _positive(lifetime_seconds, 12 * 60 * 60)
        now = int(time.time())
        with self._transaction() as db:
            row = db.execute("SELECT * FROM pairing_requests WHERE id=?", (request_id,)).fetchone()
            if (not row or row["state"] != "CLAIMED" or row["expires_at"] <= now
                    or row["companion_pid"] != companion_pid):
                raise OrderError("pairing request is not awaiting this companion's confirmation")
            # A wallet or an account has at most one active pairing.
            db.execute("""UPDATE wallet_pairings SET revoked_at=? WHERE revoked_at IS NULL AND
                          ((account_id=? AND role=?) OR wallet_address=?)""",
                       (now, row["account_id"], row["role"], row["wallet_address"]))
            pairing_id = secrets.token_hex(16)
            db.execute("""INSERT INTO wallet_pairings VALUES
                          (?,?,?,?,?,'OPERATOR_CONFIRMED_LOCAL_COMPANION',?,?,NULL)""",
                       (pairing_id, row["account_id"], row["role"], row["wallet_address"],
                        row["session_hash"], now, now + lifetime_seconds))
            db.execute("UPDATE pairing_requests SET state='CONFIRMED',pairing_id=? WHERE id=?",
                       (pairing_id, request_id))
            return dict(db.execute("SELECT * FROM wallet_pairings WHERE id=?", (pairing_id,)).fetchone())

    def reject_pairing(self, request_id: str, *, companion_pid: int) -> None:
        _record_id(request_id)
        with self._transaction() as db:
            db.execute("""UPDATE pairing_requests SET state='REJECTED'
                          WHERE id=? AND companion_pid=? AND state IN ('PENDING','CLAIMED')""",
                       (request_id, companion_pid))

    def active_pairing(self, *, account_id: str, role: str,
                       session_hash: str | None = None) -> dict | None:
        _identifier(account_id); _role(role)
        now = int(time.time())
        with self._connection() as db:
            row = db.execute("""SELECT * FROM wallet_pairings WHERE account_id=? AND role=?
                                AND revoked_at IS NULL AND expires_at>? ORDER BY created_at DESC
                                LIMIT 1""", (account_id, role, now)).fetchone()
        if not row or (session_hash is not None and row["session_hash"] != _hex32(session_hash)):
            return None
        return dict(row)

    def pairing(self, pairing_id: str) -> dict | None:
        _record_id(pairing_id)
        with self._connection() as db:
            row = db.execute("SELECT * FROM wallet_pairings WHERE id=?", (pairing_id,)).fetchone()
            return dict(row) if row else None

    def pairing_usable(self, pairing_id: str) -> bool:
        row = self.pairing(pairing_id)
        return bool(row and row["revoked_at"] is None and row["expires_at"] > time.time())

    def revoke_pairing(self, *, account_id: str, role: str, session_hash: str) -> None:
        _identifier(account_id); _role(role); _hex32(session_hash)
        with self._transaction() as db:
            db.execute("""UPDATE wallet_pairings SET revoked_at=? WHERE account_id=? AND role=?
                          AND session_hash=? AND revoked_at IS NULL""",
                       (int(time.time()), account_id, role, session_hash))

    def heartbeat(self, pairing_id: str, *, pid: int, mode: str) -> None:
        _record_id(pairing_id); _positive(pid)
        if mode not in APPROVAL_MODES:
            raise OrderError("invalid companion mode")
        with self._transaction() as db:
            db.execute("""INSERT INTO companion_presence VALUES (?,?,?,?)
                          ON CONFLICT(pairing_id) DO UPDATE SET pid=excluded.pid,
                          mode=excluded.mode,last_seen=excluded.last_seen""",
                       (pairing_id, pid, mode, int(time.time())))

    def companion_online(self, pairing_id: str, *, stale_seconds: int) -> dict | None:
        _record_id(pairing_id)
        with self._connection() as db:
            row = db.execute("SELECT * FROM companion_presence WHERE pairing_id=?",
                             (pairing_id,)).fetchone()
        if not row or row["last_seen"] < time.time() - stale_seconds or not self.pairing_usable(pairing_id):
            return None
        return dict(row)

    # ------------------------------------------------------------------ orders

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

    @staticmethod
    def _wallets_committed(db, seller_id: str, buyer_id: str, order_id: str) -> bool:
        states = ",".join("?" for _ in WALLET_COMMITTED_STATES)
        row = db.execute(f"""SELECT o.id FROM orders o JOIN offers f ON f.id=o.offer_id
                             WHERE o.id<>? AND o.state IN ({states})
                             AND (o.buyer_id=? OR f.seller_id=?) LIMIT 1""",
                         (order_id, *WALLET_COMMITTED_STATES, buyer_id, seller_id)).fetchone()
        return row is not None

    def wallets_committed(self, order_id: str) -> bool:
        """True if another order's live quote may consume either wallet's notes."""
        _record_id(order_id)
        with self._connection() as db:
            row = db.execute("""SELECT o.buyer_id,f.seller_id FROM orders o JOIN offers f
                                ON f.id=o.offer_id WHERE o.id=?""", (order_id,)).fetchone()
            if not row:
                raise OrderError("order does not exist")
            return self._wallets_committed(db, row["seller_id"], row["buyer_id"], order_id)

    def spent_sponsor_txids(self) -> set[str]:
        """Sponsor coinbases this exchange has committed or already spent.

        Pinned Zebra's getaddressutxos can keep listing a coinbase output that
        was spent in its non-finalized chain, so the address index alone must
        never decide that a sponsor input is still free.
        """
        states = (*WALLET_COMMITTED_STATES, "RECEIPT_AVAILABLE")
        marks = ",".join("?" for _ in states)
        with self._connection() as db:
            spent = {row[0] for row in db.execute(
                f"""SELECT q.sponsor_txid FROM order_quotes q JOIN orders o ON o.id=q.order_id
                    WHERE o.state IN ({marks})""", states)}
            if self.schema >= 4:
                spent |= {row[0] for row in db.execute("SELECT txid FROM spent_coinbases")}
        return spent

    def committed_sponsor_txids(self) -> set[str]:
        states = ",".join("?" for _ in WALLET_COMMITTED_STATES)
        with self._connection() as db:
            return {row[0] for row in db.execute(
                f"""SELECT q.sponsor_txid FROM order_quotes q JOIN orders o ON o.id=q.order_id
                    WHERE o.state IN ({states})""", WALLET_COMMITTED_STATES)}

    def attach_quote(self, order_id: str, quote: dict, *, observed_height: int,
                     meta: dict | None = None) -> dict:
        _record_id(order_id)
        try:
            validate_quote(quote)
        except ValueError as exc:
            raise OrderError("invalid exact checkout quote") from exc
        if (type(observed_height) is not int or observed_height < 0
                or observed_height + 1 != quote["targetHeight"]
                or observed_height >= quote["transactionExpiry"]):
            raise OrderError("quote is not fresh at the observed chain height")
        if meta is not None:
            expected = {"termsSha256", "priceZatPerUnit", "priceVersion", "buyerPairingId",
                        "sellerPairingId", "validUntil"}
            if type(meta) is not dict or set(meta) != expected:
                raise OrderError("quote metadata schema")
            _hex32(meta["termsSha256"]); _positive(meta["priceZatPerUnit"])
            _positive(meta["priceVersion"]); _positive(meta["validUntil"])
            _record_id(meta["buyerPairingId"]); _record_id(meta["sellerPairingId"])
        with self._transaction() as db:
            order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if order and order["state"] == "QUOTE_READY" and order["quote_digest"] == quote["quoteDigest"]:
                return dict(order)
            if not order or order["state"] != "BUYER_REQUESTED":
                raise OrderError("purchase is not awaiting a quote")
            offer = db.execute("SELECT * FROM offers WHERE id=?", (order["offer_id"],)).fetchone()
            if (quote["assetId"] != offer["asset_base"] or quote["quantity"] != order["quantity"]):
                raise OrderError("quote does not match reserved asset or quantity")
            if self._wallets_committed(db, offer["seller_id"], order["buyer_id"], order_id):
                raise OrderError("a wallet's notes are committed to another live quote")
            if meta is not None:
                price = db.execute("SELECT * FROM offer_prices WHERE offer_id=?",
                                   (order["offer_id"],)).fetchone()
                if (not price or price["price_zat_per_unit"] != meta["priceZatPerUnit"]
                        or price["version"] != meta["priceVersion"]
                        or quote["paymentAmount"] != order["quantity"] * meta["priceZatPerUnit"]):
                    raise OrderError("quote payment differs from the seller's fixed price")
                for key, role, account in (("buyerPairingId", "buyer", order["buyer_id"]),
                                           ("sellerPairingId", "seller", offer["seller_id"])):
                    pairing = db.execute("SELECT * FROM wallet_pairings WHERE id=?",
                                         (meta[key],)).fetchone()
                    if (not pairing or pairing["role"] != role or pairing["account_id"] != account
                            or pairing["revoked_at"] is not None):
                        raise OrderError("quote receivers are not the paired wallets")
                states = ",".join("?" for _ in WALLET_COMMITTED_STATES)
                if db.execute(f"""SELECT 1 FROM order_quotes q JOIN orders o ON o.id=q.order_id
                                  WHERE q.sponsor_txid=? AND o.state IN ({states})""",
                              (quote["sponsorOutpointTxid"], *WALLET_COMMITTED_STATES)).fetchone():
                    raise OrderError("sponsor input is committed to another quote")
            now = int(time.time())
            db.execute("""UPDATE orders SET state='QUOTE_READY',quote_digest=?,
                          quote_expiry_height=?,updated_at=? WHERE id=?""",
                       (quote["quoteDigest"],quote["transactionExpiry"],now,order_id))
            if meta is not None:
                db.execute("INSERT INTO order_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (order_id, quote["quoteDigest"], meta["termsSha256"],
                            meta["priceZatPerUnit"], quote["paymentAmount"], PRICE_CLASSIFICATION,
                            meta["priceVersion"], meta["buyerPairingId"], meta["sellerPairingId"],
                            quote["sponsorOutpointTxid"], quote["targetHeight"],
                            meta["validUntil"], now))
            self._append_event(db, order_id, "BUYER_REQUESTED", "QUOTE_READY", now)
            return dict(db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone())

    def order_quote(self, order_id: str) -> dict | None:
        _record_id(order_id)
        with self._connection() as db:
            row = db.execute("SELECT * FROM order_quotes WHERE order_id=?", (order_id,)).fetchone()
            return dict(row) if row else None

    def confirm_quote(self, order_id: str, *, buyer_id: str, quote_digest: str,
                      pairing_id: str) -> dict:
        """Record the buyer's portal confirmation of one server-generated quote.

        This is a purchase request, not a wallet signature. The browser can only
        echo the digest the server generated; any other value is rejected.
        """
        _record_id(order_id); _identifier(buyer_id); _hex32(quote_digest); _record_id(pairing_id)
        now = int(time.time())
        with self._transaction() as db:
            order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            quote = db.execute("SELECT * FROM order_quotes WHERE order_id=?", (order_id,)).fetchone()
            if not order or order["buyer_id"] != buyer_id or not quote:
                raise OrderError("order is unavailable to buyer")
            if order["quote_digest"] != quote_digest or quote["quote_digest"] != quote_digest:
                raise OrderError("confirmation does not match the current exact quote")
            if quote["buyer_pairing_id"] != pairing_id:
                raise OrderError("quote belongs to another wallet pairing")
            if order["state"] == "BUYER_CONFIRMED":
                return dict(order)
            if order["state"] != "QUOTE_READY":
                raise OrderError("quote is no longer awaiting buyer confirmation")
            if quote["valid_until"] <= now:
                raise OrderError("quote has expired")
            db.execute("UPDATE orders SET state='BUYER_CONFIRMED',updated_at=? WHERE id=?",
                       (now, order_id))
            self._append_event(db, order_id, "QUOTE_READY", "BUYER_CONFIRMED", now,
                               {"quoteDigest": quote_digest, "buyerPairingId": pairing_id,
                                "confirmation": "PORTAL_PURCHASE_REQUEST_NOT_A_WALLET_SIGNATURE"})
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
            started = db.execute("SELECT 1 FROM order_executions WHERE order_id=?",
                                 (order_id,)).fetchone()
            if order["state"] not in PRE_EXECUTION_STATES or started:
                raise OrderError("reservation cannot be released after approval starts")
            now = int(time.time())
            db.execute("UPDATE offers SET reserved=reserved-? WHERE id=?", (order["quantity"],order["offer_id"]))
            db.execute("UPDATE orders SET state=?,updated_at=? WHERE id=?", (state,now,order_id))
            self._append_event(db, order_id, order["state"], state, now)
            return dict(db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone())

    def fail(self, order_id: str, *, expected: str, state: str, evidence: dict,
             release: bool) -> dict:
        """Close an order fail-closed from a trusted service observation.

        UNCERTAIN never releases inventory: an accepted transaction may exist.
        Nothing after submission can be reported as an ordinary failure.
        """
        _record_id(order_id)
        if state not in ("EXPIRED", "REJECTED", "EXECUTION_FAILED", "UNCERTAIN"):
            raise OrderError("unsupported failure state")
        if type(evidence) is not dict or "reason" not in evidence:
            raise OrderError("failure requires an observed reason")
        if state == "UNCERTAIN" and release:
            raise OrderError("uncertain execution cannot release inventory")
        allowed = PRE_EXECUTION_STATES + ("BUYER_APPROVED", "SELLER_APPROVED")
        if state == "UNCERTAIN":
            allowed += ("SUBMITTED", "INCLUDED", "RECONCILED")
        if expected not in allowed:
            raise OrderError("order cannot fail from this state")
        payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        if len(payload) > 8192:
            raise OrderError("evidence is too large")
        with self._transaction() as db:
            order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if not order:
                raise OrderError("order does not exist")
            if order["state"] == state:
                return dict(order)
            if order["state"] != expected:
                raise OrderError("order state changed")
            now = int(time.time())
            if release:
                db.execute("UPDATE offers SET reserved=reserved-? WHERE id=?",
                           (order["quantity"], order["offer_id"]))
            db.execute("UPDATE orders SET state=?,updated_at=? WHERE id=?", (state, now, order_id))
            self._append_event(db, order_id, expected, state, now, json.loads(payload))
            return dict(db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone())

    def note(self, order_id: str, text: str) -> None:
        """A non-authoritative progress hint for the participant UI."""
        _record_id(order_id)
        if not isinstance(text, str) or not 0 < len(text) <= 240:
            raise OrderError("invalid note")
        with self._transaction() as db:
            db.execute("""INSERT INTO order_notes VALUES (?,?,?) ON CONFLICT(order_id)
                          DO UPDATE SET note=excluded.note,updated_at=excluded.updated_at""",
                       (order_id, text, int(time.time())))

    # -------------------------------------------------------------- execution

    def start_execution(self, order_id: str, *, approval_mode: str) -> tuple[dict, bool]:
        """Insert the single-execution guard. Returns (row, created)."""
        _record_id(order_id)
        if approval_mode not in APPROVAL_MODES:
            raise OrderError("invalid approval mode")
        with self._transaction() as db:
            prior = db.execute("SELECT * FROM order_executions WHERE order_id=?", (order_id,)).fetchone()
            if prior:
                return dict(prior), False
            order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if not order or order["state"] != "TRADE_AUTHORIZED":
                raise OrderError("only an authorized trade can start execution")
            db.execute("INSERT INTO order_executions VALUES (?,?,?,NULL,?,NULL,NULL)",
                       (order_id, order["quote_digest"], approval_mode, int(time.time())))
            return dict(db.execute("SELECT * FROM order_executions WHERE order_id=?",
                                   (order_id,)).fetchone()), True

    def set_execution_pid(self, order_id: str, pid: int) -> None:
        _record_id(order_id); _positive(pid)
        with self._transaction() as db:
            db.execute("UPDATE order_executions SET coordinator_pid=? WHERE order_id=? AND coordinator_pid IS NULL",
                       (pid, order_id))

    def finish_execution(self, order_id: str, outcome: str) -> None:
        _record_id(order_id)
        if outcome not in ("ACCEPTED", "FAILED", "UNCERTAIN"):
            raise OrderError("invalid execution outcome")
        with self._transaction() as db:
            db.execute("""UPDATE order_executions SET finished_at=?,outcome=?
                          WHERE order_id=? AND finished_at IS NULL""",
                       (int(time.time()), outcome, order_id))

    def execution(self, order_id: str) -> dict | None:
        _record_id(order_id)
        with self._connection() as db:
            row = db.execute("SELECT * FROM order_executions WHERE order_id=?", (order_id,)).fetchone()
            return dict(row) if row else None

    def orders_in(self, states: tuple[str, ...]) -> list[dict]:
        marks = ",".join("?" for _ in states)
        with self._connection() as db:
            return [dict(row) for row in db.execute(
                f"""SELECT o.*,f.seller_id,f.instrument_id,f.asset_base,f.chain_genesis
                    FROM orders o JOIN offers f ON f.id=o.offer_id
                    WHERE o.state IN ({marks}) ORDER BY o.created_at,o.rowid""", states)]

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
            note = db.execute("SELECT note,updated_at FROM order_notes WHERE order_id=?",
                              (order_id,)).fetchone()
            result["note"] = dict(note) if note else None
            return result

    def transition(self, order_id: str, *, expected: str, state: str,
                   evidence: dict) -> dict:
        """Append one observed event atomically; never infer success from intent.

        The trusted operator adapter validates the external observation before
        calling this method. Exact required fields here prevent empty claims.
        """
        _record_id(order_id)
        next_state = {
            "BUYER_CONFIRMED": "PROOF_VERIFIED",
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
            if evidence["role"] != role or evidence["approvalMode"] not in APPROVAL_MODES:
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
            if state in ("BUYER_APPROVED", "SELLER_APPROVED"):
                execution = db.execute("SELECT * FROM order_executions WHERE order_id=?",
                                       (order_id,)).fetchone()
                quote = db.execute("SELECT 1 FROM order_quotes WHERE order_id=?",
                                   (order_id,)).fetchone()
                # Browser-initiated orders must have an execution guard whose
                # mode matches the recorded approval mode.
                if quote and (not execution or execution["approval_mode"] != evidence["approvalMode"]):
                    raise OrderError("approval mode differs from the guarded execution")
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
