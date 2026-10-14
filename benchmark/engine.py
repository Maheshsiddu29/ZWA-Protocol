"""Talking to the benchmark project's engine: the portal relay and the read-only probe.

Both are fixed programs passed to `docker compose exec -T portal python3 -c`
of the *benchmark* project. Neither is installed in the engine image, so the
reviewed engine files stay unchanged.

* RELAY forwards fixed-shape requests to the real portal HTTP API
  (POST /v1/portal, GET /readyz) on the container's loopback, with the token
  from the container's own environment. The token never crosses the host
  command line or the harness's memory.
* PROBE reads the ledger through a read-only SQLite URI, file modification
  times with os.stat, and a fixed set of read-only RPC methods. It opens no
  wallet home, prints no session or client hash, wallet address, seed or key,
  and writes nothing.
"""

from __future__ import annotations

import itertools
import json
import queue
import threading
import time

from .docker import Compose
from .watchdog import StopController

RELAY_MARKER = "zwa-bench-relay"
PROBE_MARKER = "zwa-bench-probe"

RELAY = r'''
import json, os, sys, urllib.error, urllib.request
# argv: zwa-bench-relay [port]. The harness never passes a port (the portal's fixed 8790 in its container);
# the integration test passes the ephemeral port of a real portal_server it started.
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8790
TOKEN = os.environ.get("ZWA_PORTAL_TOKEN", "")
if len(TOKEN) < 32:
    sys.stdout.write(json.dumps({"id": None, "status": 0, "body": {"error": "portal token unavailable"}}) + "\n")
    sys.exit(2)
def call(method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path), data=data, method=method,
                                     headers={"Authorization": "Bearer " + TOKEN,
                                              "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")
for line in sys.stdin:
    rid = None
    try:
        request = json.loads(line)
        rid = request["id"]
        if request["kind"] == "portal" and set(request["body"]) == {"action", "actor", "role", "sessionHash", "payload"}:
            status, body = call("POST", "/v1/portal", request["body"])
        elif request["kind"] == "readyz":
            status, body = call("GET", "/readyz")
        else:
            status, body = 0, {"error": "unsupported relay request"}
    except Exception as exc:
        status, body = 0, {"error": "relay: " + type(exc).__name__}
    sys.stdout.write(json.dumps({"id": rid, "status": status, "body": body}) + "\n")
    sys.stdout.flush()
'''

PROBE = r'''
import json, os, sqlite3, sys
from pathlib import Path
from exchange.demo_ledger import SETTINGS
from exchange.persistent_chain import _rpc
ROOT = Path(os.environ["ZWA_PRIVATE_EXCHANGE_ROOT"])
EXCHANGE = ROOT / "exchange"
ARTIFACTS = ("buyer-before.log", "seller-before.log", "quote/terms.json", "quote/session-result.json",
             "proof-generation.log", "authorization/authorization.json", "preflight-authorization.json",
             "buyer-review.log", "seller-review.log", "coordinator.log.raw", "buyer-signer.log",
             "buyer-approval.json", "seller-signer.log", "seller-approval.json", "authorization-result.json",
             "receipt.json", "coordinator.log", "buyer-after.log", "seller-after.log", "public-scans.json",
             "order-receipt.json", "stabilization.json", "dropped.json")
EVIDENCE_FIELDS = {
    "PROOF_VERIFIED": ("issuerAuthentication", "issuanceProvenance", "credentialAuthorityAuthentication",
                       "investorEligibility", "exactTradeAuthorization", "quoteDigest"),
    "TRADE_AUTHORIZED": ("tipHeight",), "BUYER_APPROVED": ("approvalMode",), "SELLER_APPROVED": ("approvalMode",),
    "SUBMITTED": ("txid",), "INCLUDED": ("txid", "height", "blockHash"),
    "RECONCILED": ("txid", "buyerUnitsBefore", "buyerUnitsAfter", "sellerUnitsBefore", "sellerUnitsAfter",
                   "sellerZatBefore", "sellerZatAfter"),
    "RECEIPT_AVAILABLE": ("txid", "height", "blockHash", "approvalMode"),
    "REJECTED": ("reason", "transactionSubmitted"), "EXPIRED": ("reason",), "CANCELLED": ("reason",),
    "EXECUTION_FAILED": ("reason", "transactionSubmitted", "sessionOutcome"),
    "UNCERTAIN": ("reason", "sessionOutcome"),
}
RPC_ALLOWED = ("getblockcount", "getblockhash", "getblock")
config = json.loads((EXCHANGE / "service-config.json").read_text())
def rpc(method, params=None):
    assert method in RPC_ALLOWED
    return _rpc(config["rpcPort"], method, params or [], host=config.get("rpcHost", "127.0.0.1"))
db = sqlite3.connect("file:" + str(EXCHANGE / "exchange.sqlite") + "?mode=ro", uri=True)
db.row_factory = sqlite3.Row
q = lambda sql, args=(): [dict(row) for row in db.execute(sql, args)]
wanted = set(sys.argv[2:])
out = {"probe": 1, "approvalMode": config.get("approvalMode"), "integrity": db.execute("PRAGMA integrity_check").fetchone()[0]}
identity = json.loads((ROOT / "chain-identity.json").read_text())
out["identity"] = {key: identity.get(key) for key in ("genesisHash", "anchorHeight", "anchorHash")}
try:
    tip = rpc("getblockcount")
    out["chain"] = {"live": True, "tip": tip, "genesisHash": rpc("getblockhash", [0]),
                    "tipHash": rpc("getblockhash", [tip])}
except Exception as exc:
    out["chain"] = {"live": False, "error": type(exc).__name__}
out["offers"] = q("SELECT id,instrument_id,asset_base,capacity,reserved,sold,state FROM offers ORDER BY id")
out["prices"] = q("SELECT offer_id,price_zat_per_unit,version FROM offer_prices ORDER BY offer_id")
# An absent row means the exchange's default (exchange.demo_ledger.SETTINGS), exactly as DemoLedger.setting reads it.
out["dealerPolicy"] = (q("SELECT value FROM demo_settings WHERE key='dealerPolicy'")
                       or [{"value": SETTINGS["dealerPolicy"][0]}])[0]["value"]
out["dealerScans"] = q("""SELECT s.asset_base,s.scan_height,s.asset_units,s.native_zat,s.scanned_at FROM wallet_scans s
                          JOIN (SELECT asset_base,MAX(scan_height) h FROM wallet_scans WHERE wallet_key='dealer'
                          GROUP BY asset_base) m ON m.asset_base=s.asset_base AND m.h=s.scan_height
                          WHERE s.wallet_key='dealer' ORDER BY s.asset_base""")
out["wallets"] = q("""SELECT account_id,state,funding_txid,funding_height,failure,created_at,ready_at,expires_at
                      FROM demo_wallets ORDER BY created_at,account_id""")
out["settlements"] = q("SELECT order_id,txid FROM order_settlements ORDER BY order_id")
orders = q("""SELECT id,offer_id,buyer_id,quantity,state,quote_digest,created_at,updated_at FROM orders
              ORDER BY created_at,id""")
out["orderIndex"] = [{key: row[key] for key in ("id", "offer_id", "quantity", "state")} for row in orders]
out["orders"] = {}
for row in orders:
    if wanted and row["id"] not in wanted:
        continue
    oid = row["id"]
    detail = dict(row)
    detail["events"] = q("SELECT sequence,from_state,to_state,occurred_at FROM order_events WHERE order_id=? ORDER BY sequence", (oid,))
    evidence = {}
    for item in q("SELECT kind,value_json FROM order_evidence WHERE order_id=? ORDER BY sequence", (oid,)):
        fields = EVIDENCE_FIELDS.get(item["kind"])
        if fields:
            value = json.loads(item["value_json"])
            evidence[item["kind"]] = {key: value.get(key) for key in fields}
    detail["evidence"] = evidence
    execution = q("SELECT started_at,finished_at,outcome,approval_mode FROM order_executions WHERE order_id=?", (oid,))
    detail["execution"] = execution[0] if execution else None
    quote = q("SELECT price_zat_per_unit,payment_zat,target_height,valid_until,created_at FROM order_quotes WHERE order_id=?", (oid,))
    detail["quote"] = quote[0] if quote else None
    directory = EXCHANGE / "orders" / oid
    mtimes = {}
    for name in ARTIFACTS:
        path = directory / name
        try:
            info = os.lstat(path)
            mtimes[name] = info.st_mtime_ns
        except FileNotFoundError:
            pass
    detail["artifactMtimeNs"] = mtimes
    included = evidence.get("INCLUDED")
    if included and out["chain"].get("live"):
        try:
            block = rpc("getblock", [str(included["height"]), 1])
            detail["onChain"] = {"txInBlock": included["txid"] in block.get("tx", []),
                                 "blockHashMatches": block.get("hash") == included["blockHash"],
                                 "confirmations": out["chain"]["tip"] - included["height"] + 1}
        except Exception as exc:
            detail["onChain"] = {"error": type(exc).__name__}
    out["orders"][oid] = detail
print(json.dumps(out, sort_keys=True))
'''

# Exchange order states (exchange/order_ledger.py), repeated here so the
# host-side harness need not import the ledger.
TERMINAL_FAILURE = ("CANCELLED", "EXPIRED", "REJECTED", "EXECUTION_FAILED", "UNCERTAIN")
SETTLED = "RECEIPT_AVAILABLE"
PRE_CONFIRM = ("BUYER_REQUESTED", "QUOTE_READY")
VERDICT_KEYS = ("issuerAuthentication", "issuanceProvenance", "credentialAuthorityAuthentication",
                "investorEligibility", "exactTradeAuthorization")


class EngineError(RuntimeError):
    pass


class PortalRefused(EngineError):
    """The portal answered {"ok": false}: an application refusal, not a transport error."""


class PortalRelay:
    """One relay process in the benchmark portal container, shared by all workers."""

    def __init__(self, compose: Compose, *, timeout: float = 90.0, spawn=None):
        self.compose = compose
        self.timeout = timeout
        self._spawn = spawn or (lambda: compose.spawn_exec("python3", "-c", RELAY, RELAY_MARKER))
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._process = None
        self._lines: queue.Queue = queue.Queue()
        self.restarts = 0
        self.calls = 0

    def _start(self) -> None:
        self._process = self._spawn()
        self._lines = queue.Queue()
        stream = self._process.stdout

        def pump():
            for raw in iter(stream.readline, b""):
                self._lines.put(raw)
            self._lines.put(None)
        threading.Thread(target=pump, daemon=True, name="relay-reader").start()

    def _request(self, message: dict) -> dict:
        with self._lock:
            for attempt in range(2):
                if self._process is None or self._process.poll() is not None:
                    if self._process is not None:
                        self.restarts += 1
                    self._start()
                rid = next(self._ids)
                try:
                    self._process.stdin.write((json.dumps({**message, "id": rid}) + "\n").encode())
                    self._process.stdin.flush()
                except (BrokenPipeError, OSError):
                    self._process = None
                    continue
                deadline = time.monotonic() + self.timeout
                while True:
                    try:
                        raw = self._lines.get(timeout=max(0.1, deadline - time.monotonic()))
                    except queue.Empty:
                        raise EngineError("portal relay did not answer in time")
                    if raw is None:
                        self._process = None
                        break
                    reply = json.loads(raw)
                    if reply.get("id") == rid:
                        self.calls += 1
                        return reply
            raise EngineError("portal relay is unavailable")

    def portal(self, action: str, *, actor: str = "anonymous", role: str = "anonymous",
               session: str, payload: dict | None = None) -> dict:
        reply = self._request({"kind": "portal", "body": {"action": action, "actor": actor, "role": role,
                                                          "sessionHash": session, "payload": payload or {}}})
        body = reply.get("body")
        if reply.get("status") != 200 or not isinstance(body, dict):
            raise EngineError(f"portal {action}: HTTP {reply.get('status')}")
        if not body.get("ok"):
            raise PortalRefused(f"portal {action} refused: {str(body.get('error'))[:200]}")
        return body["data"]

    def ready(self) -> dict:
        reply = self._request({"kind": "readyz"})
        return {"status": reply.get("status"), **(reply.get("body") or {})}

    def close(self) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                try:
                    self._process.stdin.close()  # the relay exits at end of input
                    self._process.wait(timeout=10)
                except Exception:
                    pass  # a stuck relay is left to the container; never killed here
            self._process = None


def probe(compose: Compose, order_ids: list[str] | tuple[str, ...] = ()) -> dict:
    """One read-only consistency snapshot of the benchmark exchange."""
    out = compose.ok("exec", "-T", "portal", "python3", "-c", PROBE, PROBE_MARKER, *order_ids,
                     what="read-only probe", timeout=300)
    data = json.loads(out)
    if not isinstance(data, dict) or data.get("probe") != 1:
        raise EngineError("unexpected probe output")
    return data


def wait_ready(relay: PortalRelay, controller: StopController, timeout: float, clock=time.monotonic) -> dict:
    deadline = clock() + timeout
    last = {}
    while clock() < deadline:
        try:
            last = relay.ready()
            if last.get("status") == 200 and last.get("ready"):
                return last
        except EngineError as exc:
            last = {"error": str(exc)}
        if controller.wait(2.0):
            break
    raise EngineError(f"portal is not ready: {last}")
