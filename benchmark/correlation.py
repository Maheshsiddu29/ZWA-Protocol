"""Protected purchase correlation: recover a buy the exchange accepted but whose answer was lost.

Before a purchase is submitted, a record is written to <workdir>/private/correlations/ (directory 0700,
files 0600, fsynced) holding what is needed to find that purchase again: the disposable demo account, its
session hash, the idempotency key, and the offer, instrument and quantity. Session hash and key are bearer
material for one regtest demo session, so they stay in private/ like the portal token. They are registered with
the redactor and never reach events, summaries, reports or logs.

Rules (docs/benchmark-runbook.md, "Ambiguous purchases"):

* one logical buy has exactly one key; a retry resends the same key and payload, never a new one;
* an unknown result is resolved only by reading the buyer's orders, never by submitting another purchase;
* each demo account places exactly one logical purchase, so its order list is the complete set of candidates:
  none means NOT_ACCEPTED, exactly one that matches means ACCEPTED, anything else stays AMBIGUOUS for the owner.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time

STATUSES = ("SUBMITTING", "ACCEPTED", "NOT_ACCEPTED", "AMBIGUOUS")
UNRESOLVED = ("SUBMITTING", "AMBIGUOUS")
RUN_ID = re.compile(r"[0-9A-Za-z-]{1,64}\Z")
LABEL = re.compile(r"P[1-3]\Z")
LOOKUP_ATTEMPTS = 3


class CorrelationError(RuntimeError):
    pass


def _fsync_dir(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class CorrelationStore:
    def __init__(self, directory: Path, run_id: str):
        if not RUN_ID.fullmatch(run_id):
            raise CorrelationError("invalid run id for a correlation record")
        self.directory = directory
        self.run_id = run_id
        directory.mkdir(mode=0o700, parents=False, exist_ok=True)
        if directory.is_symlink() or not directory.is_dir():
            raise CorrelationError("the correlation directory must be a real directory")
        os.chmod(directory, 0o700)

    def path(self, label: str) -> Path:
        if not LABEL.fullmatch(label):
            raise CorrelationError("invalid purchase label")
        return self.directory / f"{self.run_id}-{label}.json"

    def _write(self, path: Path, record: dict, *, exclusive: bool) -> None:
        text = json.dumps(record, sort_keys=True, indent=2) + "\n"
        target = path if exclusive else path.with_name(path.name + ".tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags, 0o600)
        with os.fdopen(descriptor, "w") as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        if not exclusive:
            os.replace(target, path)
        _fsync_dir(self.directory)

    def begin(self, label: str, *, account: str, session: str, key: str, offer_id: str, instrument: str,
              quantity: int) -> dict:
        """Durably record the logical buy before it is submitted. A label is begun at most once per run."""
        now = round(time.time(), 3)
        record = {"version": 1, "runId": self.run_id, "label": label, "account": account, "sessionHash": session,
                  "idempotencyKey": key, "offerId": offer_id, "instrument": instrument, "quantity": quantity,
                  "status": "SUBMITTING", "orderId": None, "createdAt": now, "updatedAt": now, "history": []}
        self._write(self.path(label), record, exclusive=True)
        return record

    def update(self, label: str, *, status: str, order_id: str | None = None, reason: str = "") -> dict:
        if status not in STATUSES:
            raise CorrelationError("invalid correlation status")
        record = self.load(self.path(label))
        if record["status"] == "ACCEPTED" and status != "ACCEPTED":
            raise CorrelationError("an accepted purchase cannot be downgraded")
        if record["orderId"] and order_id and order_id != record["orderId"]:
            raise CorrelationError("a correlation record cannot change its order")
        record["history"].append({"at": round(time.time(), 3), "from": record["status"], "to": status,
                                  "reason": reason[:200]})
        record.update(status=status, orderId=order_id or record["orderId"], updatedAt=round(time.time(), 3))
        self._write(self.path(label), record, exclusive=False)
        return record

    @staticmethod
    def load(path: Path) -> dict:
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
            raise CorrelationError(f"correlation record {path.name} is not an owner-only regular file")
        record = json.loads(path.read_text())
        if type(record) is not dict or record.get("version") != 1 or record.get("status") not in STATUSES:
            raise CorrelationError(f"correlation record {path.name} is malformed")
        return record

    @classmethod
    def all_records(cls, directory: Path) -> list[dict]:
        """Every record of every run (a missing directory means none were ever written)."""
        if not directory.exists():
            return []
        if directory.is_symlink() or not directory.is_dir():
            raise CorrelationError("the correlation directory must be a real directory")
        leftovers = sorted(p.name for p in directory.glob("*.tmp"))
        if leftovers:
            raise CorrelationError(f"interrupted correlation update {leftovers}; owner review needed")
        return [cls.load(path) for path in sorted(directory.glob("*.json"))]


def classify(orders: list[dict] | None, *, instrument: str, quantity: int) -> tuple[str, str | None, str]:
    """(status, order ID, reason) from the buyer's complete order list, or None if it could not be read."""
    if orders is None:
        return "AMBIGUOUS", None, "the buyer's orders could not be read"
    if not orders:
        return "NOT_ACCEPTED", None, "the exchange holds no order for this buyer"
    matching = [o for o in orders if o.get("quantity") == quantity and o.get("instrumentId") == instrument
                and isinstance(o.get("id"), str) and re.fullmatch(r"[0-9a-f]{32}", o["id"])]
    if len(orders) == 1 and len(matching) == 1:
        return "ACCEPTED", matching[0]["id"], "exactly one matching order"
    return "AMBIGUOUS", None, f"{len(orders)} order(s), {len(matching)} matching; owner review needed"


def lookup(relay, record: dict, sleep=time.sleep) -> list[dict] | None:
    """The buyer's orders through the portal's read-only `orders` action, or None. Never submits anything."""
    from .engine import EngineError, PortalRefused

    for attempt in range(LOOKUP_ATTEMPTS):
        try:
            data = relay.portal("orders", actor=record["account"], role="buyer", session=record["sessionHash"],
                                payload={})
            orders = data.get("orders") if isinstance(data, dict) else None
            return orders if isinstance(orders, list) else None
        except PortalRefused:
            return None  # e.g. the demo session expired: the result stays unknown
        except EngineError:
            if attempt + 1 < LOOKUP_ATTEMPTS:
                sleep(1.0)
    return None


def recover(store_directory: Path, run_id: str, relay, sleep=time.sleep) -> list[dict]:
    """Resolve this run's unresolved records by lookup only. Returns public results (no session or key)."""
    results = []
    for record in CorrelationStore.all_records(store_directory):
        if record["runId"] != run_id:
            continue
        status, order_id, reason = record["status"], record["orderId"], "already resolved"
        if status in UNRESOLVED:
            status, order_id, reason = classify(lookup(relay, record, sleep), instrument=record["instrument"],
                                                quantity=record["quantity"])
            CorrelationStore(store_directory, run_id).update(record["label"], status=status, order_id=order_id,
                                                             reason="reconcile: " + reason)
        results.append({"label": record["label"], "account": record["account"], "status": status,
                        "orderId": order_id, "reason": reason})
    return results
