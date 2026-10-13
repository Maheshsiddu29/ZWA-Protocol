"""Durable records for the hosted judge sandbox (ledger schema 4).

A judge session owns exactly one disposable, server-held regtest wallet. The
browser never chooses a wallet, path, address, balance or signing identity:
the session is bound to the wallet by the hash of its random session ID, and
every lookup below is keyed by that binding. Admission is capped here, inside
one write transaction, so concurrent sign-ups cannot exceed the limits.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time

from .config import DemoPolicy
from .order_ledger import (ADDRESS, FAILURE_STATES, HEX16, OrderError, OrderLedger, _hex32,
                           _identifier, _record_id)

DEMO_ACCOUNT = re.compile(r"judge-[0-9a-f]{20}\Z")
LIVE_WALLET_STATES = ("REQUESTED", "CREATED", "FUNDING", "READY")
WALLET_STATES = (*LIVE_WALLET_STATES, "EXPIRED", "RETIRED", "FAILED")
TRANSITIONS = {
    "REQUESTED": ("CREATED", "FAILED"),
    "CREATED": ("FUNDING", "EXPIRED", "FAILED"),
    "FUNDING": ("READY", "EXPIRED", "FAILED"),
    "READY": ("EXPIRED", "FAILED"),
    "EXPIRED": ("RETIRED",),
    "FAILED": ("RETIRED",),
}
DEALER_KEY = "dealer"
# The dealer's standing policy is not a browser session; its pairing row
# carries a fixed, non-secret marker in place of a session hash.
DEALER_SESSION_MARKER = hashlib.sha256(b"ZWA_DEMO_DEALER_STANDING_POLICY_V1").hexdigest()
SETTINGS = {"dealerPolicy": ("ACTIVE", "PAUSED")}


def is_demo_account(value: object) -> bool:
    return isinstance(value, str) and DEMO_ACCOUNT.fullmatch(value) is not None


class DemoLedger(OrderLedger):
    """OrderLedger plus the demo-only tables; refuses a schema 3 ledger."""

    def __init__(self, database):
        super().__init__(database)
        if self.schema < 4:
            raise OrderError("custodial demo records require ledger schema 4")

    # ------------------------------------------------------------ admission

    def request_wallet(self, *, session_hash: str, client_hash: str,
                       policy: DemoPolicy) -> dict:
        """Admit one new judge session, or refuse if any cap is reached."""
        _hex32(session_hash); _hex32(client_hash)
        now = int(time.time())
        live = ",".join("?" for _ in LIVE_WALLET_STATES)
        with self._transaction() as db:
            active = db.execute(f"SELECT count(*) FROM demo_wallets WHERE state IN ({live})"
                                " AND expires_at>?", (*LIVE_WALLET_STATES, now)).fetchone()[0]
            if active >= policy.max_active_wallets:
                raise OrderError("the demo is at capacity; try again in a few minutes")
            recent = db.execute("SELECT count(*) FROM demo_wallets WHERE created_at>?",
                                (now - 3600,)).fetchone()[0]
            if recent >= policy.max_wallets_per_hour:
                raise OrderError("too many demo sessions were started this hour; try again later")
            mine = db.execute("SELECT count(*) FROM demo_wallets WHERE client_hash=? AND created_at>?",
                              (client_hash, now - 3600)).fetchone()[0]
            if mine >= policy.max_wallets_per_client_per_hour:
                raise OrderError("too many demo sessions from this connection; try again later")
            wallet_id = secrets.token_hex(16)
            account = "judge-" + secrets.token_hex(10)
            db.execute("""INSERT INTO demo_wallets (id,account_id,session_hash,client_hash,state,
                          created_at,expires_at) VALUES (?,?,?,?,'REQUESTED',?,?)""",
                       (wallet_id, account, session_hash, client_hash, now,
                        now + policy.wallet_lifetime_seconds))
            return dict(db.execute("SELECT * FROM demo_wallets WHERE id=?", (wallet_id,)).fetchone())

    def forget_client_hashes(self) -> int:
        """Drop rate-limit buckets once they no longer count toward any cap."""
        with self._transaction() as db:
            return db.execute("UPDATE demo_wallets SET client_hash='forgotten' WHERE created_at<=? "
                              "AND client_hash<>'forgotten'", (int(time.time()) - 3600,)).rowcount

    # --------------------------------------------------------------- lookups

    def wallet_for_session(self, account_id: str, session_hash: str) -> dict | None:
        """The live wallet bound to this exact judge session, else None."""
        if not is_demo_account(account_id):
            return None
        _hex32(session_hash)
        with self._connection() as db:
            row = db.execute("SELECT * FROM demo_wallets WHERE account_id=? AND session_hash=?",
                             (account_id, session_hash)).fetchone()
        if not row or row["state"] not in LIVE_WALLET_STATES or row["expires_at"] <= time.time():
            return None
        return dict(row)

    def session_wallet(self, account_id: str, session_hash: str) -> dict | None:
        """This session's wallet in any state (for an accurate end-of-session reason)."""
        if not is_demo_account(account_id) or not isinstance(session_hash, str):
            return None
        with self._connection() as db:
            row = db.execute("SELECT * FROM demo_wallets WHERE account_id=? AND session_hash=?",
                             (account_id, session_hash)).fetchone()
        return dict(row) if row else None

    def wallet(self, wallet_id: str) -> dict | None:
        _record_id(wallet_id)
        with self._connection() as db:
            row = db.execute("SELECT * FROM demo_wallets WHERE id=?", (wallet_id,)).fetchone()
            return dict(row) if row else None

    def wallet_for_account(self, account_id: str) -> dict | None:
        _identifier(account_id)
        with self._connection() as db:
            row = db.execute("SELECT * FROM demo_wallets WHERE account_id=?", (account_id,)).fetchone()
            return dict(row) if row else None

    def wallet_for_pairing(self, pairing_id: str) -> dict | None:
        _record_id(pairing_id)
        with self._connection() as db:
            row = db.execute("SELECT * FROM demo_wallets WHERE pairing_id=?", (pairing_id,)).fetchone()
            return dict(row) if row else None

    def wallets_in(self, states: tuple[str, ...]) -> list[dict]:
        if not states or any(state not in WALLET_STATES for state in states):
            raise OrderError("invalid wallet state")
        marks = ",".join("?" for _ in states)
        with self._connection() as db:
            return [dict(row) for row in db.execute(
                f"SELECT * FROM demo_wallets WHERE state IN ({marks}) ORDER BY created_at,id", states)]

    # ----------------------------------------------------------- transitions

    def advance(self, wallet_id: str, *, expected: str, state: str, **fields) -> dict:
        """Move one wallet along its lifecycle; fields are validated per state."""
        _record_id(wallet_id)
        if state not in TRANSITIONS.get(expected, ()):
            raise OrderError("unsupported demo wallet transition")
        allowed = {"CREATED": {"wallet_address"}, "FUNDING": {"funding_txid", "funding_height"},
                   "EXPIRED": set(), "RETIRED": set(), "FAILED": {"failure"}}.get(state)
        if allowed is None or set(fields) - allowed:
            raise OrderError("unexpected demo wallet fields")
        if "wallet_address" in fields and not ADDRESS.fullmatch(str(fields["wallet_address"])):
            raise OrderError("invalid wallet address")
        if "funding_txid" in fields:
            _hex32(fields["funding_txid"])
            if type(fields.get("funding_height")) is not int or fields["funding_height"] < 1:
                raise OrderError("invalid funding height")
        if "failure" in fields and (not isinstance(fields["failure"], str)
                                    or not 0 < len(fields["failure"]) <= 200):
            raise OrderError("invalid failure reason")
        now = int(time.time())
        with self._transaction() as db:
            row = db.execute("SELECT * FROM demo_wallets WHERE id=?", (wallet_id,)).fetchone()
            if not row:
                raise OrderError("demo wallet does not exist")
            if row["state"] == state:
                return dict(row)
            if row["state"] != expected:
                raise OrderError("demo wallet state changed")
            if state in ("EXPIRED", "FAILED") and row["pairing_id"]:
                db.execute("UPDATE wallet_pairings SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                           (now, row["pairing_id"]))
            extra = {"RETIRED": {"retired_at": now}}.get(state, {})
            columns = {**fields, **extra, "state": state}
            db.execute("UPDATE demo_wallets SET " + ",".join(f"{key}=?" for key in columns) +
                       " WHERE id=?", (*columns.values(), wallet_id))
            return dict(db.execute("SELECT * FROM demo_wallets WHERE id=?", (wallet_id,)).fetchone())

    def reserve_funding_coinbase(self, coinbase_txid: str) -> None:
        """Record, before mining, a coinbase a funding will spend. Append-only:
        after a crash the coinbase stays excluded even if its funding landed."""
        _hex32(coinbase_txid)
        with self._transaction() as db:
            db.execute("INSERT OR IGNORE INTO spent_coinbases VALUES (?,'DEMO_FUNDING',?)",
                       (coinbase_txid, int(time.time())))

    def resolve_unexecuted(self, order_id: str, evidence: dict) -> dict:
        """UNCERTAIN -> EXECUTION_FAILED once the chain proves no execution.

        Only the demo operator calls this, after the transaction's expiry
        height has passed under empty blocks and fresh scans of both wallets
        equal their before-scans. It releases the reservation.
        """
        _record_id(order_id)
        if type(evidence) is not dict or evidence.get("reason") != "EXPIRED_WITHOUT_EXECUTION":
            raise OrderError("resolution requires chain evidence of non-execution")
        payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        with self._transaction() as db:
            order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            if not order or order["state"] != "UNCERTAIN":
                raise OrderError("order is not uncertain")
            if db.execute("SELECT 1 FROM order_settlements WHERE order_id=?", (order_id,)).fetchone():
                raise OrderError("a submitted settlement is never released automatically")
            now = int(time.time())
            db.execute("UPDATE offers SET reserved=reserved-? WHERE id=?", (order["quantity"], order["offer_id"]))
            db.execute("UPDATE orders SET state='EXECUTION_FAILED',updated_at=? WHERE id=?", (now, order_id))
            self._append_event(db, order_id, "UNCERTAIN", "EXECUTION_FAILED", now, json.loads(payload))
            return dict(db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone())

    def mark_ready(self, wallet_id: str) -> dict:
        """FUNDING -> READY, creating the custodial pairing in the same transaction."""
        _record_id(wallet_id)
        now = int(time.time())
        with self._transaction() as db:
            row = db.execute("SELECT * FROM demo_wallets WHERE id=?", (wallet_id,)).fetchone()
            if not row:
                raise OrderError("demo wallet does not exist")
            if row["state"] == "READY":
                return dict(row)
            if row["state"] != "FUNDING" or not row["wallet_address"] or row["expires_at"] <= now:
                raise OrderError("demo wallet is not awaiting readiness")
            pairing_id = secrets.token_hex(16)
            db.execute("""INSERT INTO wallet_pairings VALUES (?,?,'buyer',?,?,
                          'CUSTODIAL_DEMO_WALLET',?,?,NULL)""",
                       (pairing_id, row["account_id"], row["wallet_address"], row["session_hash"],
                        now, row["expires_at"]))
            db.execute("UPDATE demo_wallets SET state='READY',pairing_id=?,ready_at=? WHERE id=?",
                       (pairing_id, now, wallet_id))
            return dict(db.execute("SELECT * FROM demo_wallets WHERE id=?", (wallet_id,)).fetchone())

    def end_session(self, account_id: str, session_hash: str) -> dict | None:
        """The judge ends their own session early. Funds stay on the regtest chain."""
        row = self.wallet_for_session(account_id, session_hash)
        if not row:
            return None
        with self._transaction() as db:
            db.execute("UPDATE demo_wallets SET expires_at=? WHERE id=? AND expires_at>?",
                       (int(time.time()), row["id"], int(time.time())))
        return row

    # ----------------------------------------------------------------- dealer

    def ensure_dealer_pairing(self, *, account_id: str, wallet_address: str,
                              lifetime_seconds: int = 12 * 60 * 60) -> dict:
        """Keep exactly one live standing-policy pairing for the dealer wallet."""
        _identifier(account_id)
        if not ADDRESS.fullmatch(wallet_address):
            raise OrderError("invalid wallet address")
        now = int(time.time())
        with self._transaction() as db:
            row = db.execute("""SELECT * FROM wallet_pairings WHERE account_id=? AND role='seller'
                                AND method='DEMO_DEALER_STANDING_POLICY' AND revoked_at IS NULL
                                AND expires_at>? ORDER BY created_at DESC LIMIT 1""",
                             (account_id, now + 3600)).fetchone()
            if row and row["wallet_address"] == wallet_address:
                return dict(row)
            # Not used by a quote that is still live: renewing must never
            # invalidate an order bound to the earlier pairing.
            db.execute("""UPDATE wallet_pairings SET revoked_at=? WHERE account_id=? AND role='seller'
                          AND revoked_at IS NULL AND id NOT IN (
                            SELECT q.seller_pairing_id FROM order_quotes q JOIN orders o ON o.id=q.order_id
                            WHERE o.state NOT IN ('CANCELLED','EXPIRED','REJECTED','EXECUTION_FAILED',
                                                  'RECEIPT_AVAILABLE'))""", (now, account_id))
            pairing_id = secrets.token_hex(16)
            db.execute("""INSERT INTO wallet_pairings VALUES (?,?,'seller',?,?,
                          'DEMO_DEALER_STANDING_POLICY',?,?,NULL)""",
                       (pairing_id, account_id, wallet_address, DEALER_SESSION_MARKER,
                        now, now + lifetime_seconds))
            return dict(db.execute("SELECT * FROM wallet_pairings WHERE id=?", (pairing_id,)).fetchone())

    def setting(self, key: str) -> str:
        if key not in SETTINGS:
            raise OrderError("unknown setting")
        with self._connection() as db:
            row = db.execute("SELECT value FROM demo_settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else SETTINGS[key][0]

    def set_setting(self, key: str, value: str) -> str:
        if key not in SETTINGS or value not in SETTINGS[key]:
            raise OrderError("invalid setting")
        with self._transaction() as db:
            db.execute("""INSERT INTO demo_settings VALUES (?,?,?) ON CONFLICT(key)
                          DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                       (key, value, int(time.time())))
        return value

    # ------------------------------------------------------------------ scans

    def record_scan(self, wallet_key: str, *, asset_base: str, height: int, block_hash: str,
                    native_zat: int, asset_units: int) -> None:
        if wallet_key != DEALER_KEY and not HEX16.fullmatch(wallet_key):
            raise OrderError("invalid wallet key")
        if asset_base != "native":
            _hex32(asset_base)
        _hex32(block_hash)
        for value in (height, native_zat, asset_units):
            if type(value) is not int or value < 0:
                raise OrderError("invalid scan number")
        with self._transaction() as db:
            db.execute("""INSERT INTO wallet_scans VALUES (?,?,?,?,?,?,?)
                          ON CONFLICT(wallet_key,asset_base,scan_height) DO UPDATE SET block_hash=excluded.block_hash,native_zat=excluded.native_zat,
                          asset_units=excluded.asset_units,scanned_at=excluded.scanned_at""",
                       (wallet_key, asset_base, height, block_hash, native_zat, asset_units,
                        int(time.time())))

    def latest_scans(self, wallet_key: str, max_height: int | None = None) -> dict[str, dict]:
        """Newest scan per asset at or below the live tip, when one is supplied."""
        if max_height is not None and (type(max_height) is not int or max_height < 0):
            raise OrderError("invalid scan height")
        with self._connection() as db:
            rows = db.execute("""SELECT s.* FROM wallet_scans s JOIN (
                                   SELECT asset_base, MAX(scan_height) AS h FROM wallet_scans
                                   WHERE wallet_key=? AND (? IS NULL OR scan_height<=?)
                                   GROUP BY asset_base) m
                                 ON m.asset_base=s.asset_base AND m.h=s.scan_height
                                 WHERE s.wallet_key=?""",
                              (wallet_key, max_height, max_height, wallet_key)).fetchall()
        return {row["asset_base"]: dict(row) for row in rows}

    # ----------------------------------------------------------------- limits

    def orders_for_buyer(self, buyer_id: str) -> list[dict]:
        _identifier(buyer_id)
        with self._connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM orders WHERE buyer_id=? ORDER BY created_at,id", (buyer_id,))]

    def counted_orders(self, buyer_id: str) -> int:
        """Orders that consumed, or may still consume, a sponsored settlement."""
        closed = ("CANCELLED", "EXPIRED", "REJECTED", "EXECUTION_FAILED")
        return sum(order["state"] not in closed for order in self.orders_for_buyer(buyer_id))

    def open_order(self, buyer_id: str) -> dict | None:
        done = (*FAILURE_STATES, "RECEIPT_AVAILABLE")
        return next((o for o in self.orders_for_buyer(buyer_id) if o["state"] not in done), None)

    def sponsored_last_hour(self) -> int:
        with self._connection() as db:
            return db.execute("""SELECT count(*) FROM order_events WHERE to_state='SUBMITTED'
                                 AND occurred_at>?""", (int(time.time()) - 3600,)).fetchone()[0]

    def confirmation(self, order_id: str) -> dict | None:
        """The buyer's recorded browser confirmation of the exact quote."""
        _record_id(order_id)
        with self._connection() as db:
            row = db.execute("""SELECT e.occurred_at, v.value_json FROM order_events e
                                JOIN order_evidence v ON v.order_id=e.order_id AND v.sequence=e.sequence
                                WHERE e.order_id=? AND e.to_state='BUYER_CONFIRMED'""",
                             (order_id,)).fetchone()
        if not row:
            return None
        return {"occurredAt": row["occurred_at"], **json.loads(row["value_json"])}

    def evidence_of(self, order_id: str, kind: str) -> dict | None:
        _record_id(order_id)
        with self._connection() as db:
            row = db.execute("""SELECT value_json FROM order_evidence WHERE order_id=? AND kind=?
                                ORDER BY sequence DESC LIMIT 1""", (order_id, kind)).fetchone()
        return json.loads(row["value_json"]) if row else None
