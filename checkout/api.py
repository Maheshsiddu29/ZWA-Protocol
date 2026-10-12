"""Read-only loopback status bridge for one Phase 3.5G checkout.

The independent wallet signers and pinned transaction coordinator remain CLI
processes. This module never invokes them and never reads signed groups.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import re
from typing import Any

from .quote import InvalidQuote, from_terms, validate

RECORDED_ID = "recorded-phase35f"
RECORDED_HUMAN_ID = "recorded-phase35h"
ROOT = Path(__file__).resolve().parent
MAX_SESSION_BYTES = 16_384
MAX_RPC_BYTES = 262_144
MAX_EVENTS_BYTES = 4096
MAX_SCANS_BYTES = 4096
HEX = re.compile(r"[0-9a-f]{64}\Z")
PINNED_GENESIS = "029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327"
SWAP_BRANCH = "88888888"


class SessionError(ValueError):
    pass


class NodeUnavailable(ConnectionError):
    pass


class Rpc:
    def __init__(self, port: int):
        if not 1 <= port <= 65535:
            raise ValueError("RPC port must be valid")
        self.port = port

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        if method not in {"getblockchaininfo", "getblock"}:
            raise ValueError("unsupported internal RPC method")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=1.5)
        try:
            body = json.dumps({"jsonrpc": "2.0", "id": "zwa35g", "method": method,
                               "params": params or []}).encode()
            connection.request("POST", "/", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                raise NodeUnavailable("RPC returned an error")
            raw = response.read(MAX_RPC_BYTES + 1)
            if len(raw) > MAX_RPC_BYTES:
                raise NodeUnavailable("RPC response too large")
            data = json.loads(raw)
            if type(data) is not dict or data.get("error") is not None or "result" not in data:
                raise NodeUnavailable("RPC result invalid")
            return data["result"]
        except (OSError, TimeoutError, ValueError, http.client.HTTPException) as exc:
            raise NodeUnavailable("regtest node unavailable") from exc
        finally:
            connection.close()


def _hex(value: object) -> str:
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise SessionError("noncanonical digest or transaction id")
    return value


def _uint(value: object, bits: int) -> int:
    if type(value) is not int or not 0 <= value < (1 << bits):
        raise SessionError("invalid unsigned integer")
    return value


def read_session(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    try:
        if path.is_symlink() or path.stat().st_size > MAX_SESSION_BYTES:
            raise SessionError("unsafe session result")
    except OSError as exc:
        raise SessionError("unreadable session result") from exc
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise SessionError("corrupt session result") from exc
    if type(data) is not dict or set(data) != {"version", "sessionId", "source", "outcome", "quote", "receipt"}:
        raise SessionError("session result schema")
    if data["version"] != 1 or data["source"] != "LIVE_LOCAL_CLI":
        raise SessionError("unsupported result source")
    if data["outcome"] not in ("QUOTE_READY", "FAILED", "UNCERTAIN", "ACCEPTED"):
        raise SessionError("unsupported session outcome")
    try:
        validate(data["quote"])
    except InvalidQuote as exc:
        raise SessionError("invalid session quote") from exc
    if _hex(data["sessionId"]) != data["quote"]["quoteDigest"]:
        raise SessionError("session id mismatch")
    receipt = data["receipt"]
    if (data["outcome"] == "ACCEPTED") != (receipt is not None):
        raise SessionError("receipt and outcome disagree")
    if receipt is not None:
        if type(receipt) is not dict or set(receipt) != {"terms_hash", "txid", "height", "block_hash", "tx_bytes"}:
            raise SessionError("receipt schema")
        if _hex(receipt["terms_hash"]) != data["sessionId"]:
            raise SessionError("receipt quote mismatch")
        _hex(receipt["txid"])
        _hex(receipt["block_hash"])
        if _uint(receipt["height"], 32) != data["quote"]["targetHeight"]:
            raise SessionError("receipt height mismatch")
        if not 1 <= _uint(receipt["tx_bytes"], 32) <= 100_000:
            raise SessionError("receipt size")
    return data


def read_authorization(path: Path | None, session_id: str) -> dict | None:
    if path is None or not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 8192:
        raise SessionError("unsafe authorization result")
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise SessionError("invalid authorization result") from exc
    expected = {"version", "issuerAuthentication", "issuanceProvenance",
                "credentialAuthorityAuthentication", "investorEligibility",
                "exactTradeAuthorization", "quoteDigest", "tradeCommitment",
                "rootedNoteLineage"}
    if (type(data) is not dict or set(data) != expected or data["version"] != 2
            or data["quoteDigest"] != session_id
            or any(data[key] != "VERIFIED" for key in expected if key.endswith("Authentication")
                   or key in ("issuanceProvenance", "investorEligibility", "exactTradeAuthorization"))
            or data["rootedNoteLineage"] != "UNSUPPORTED"
            or not isinstance(data["tradeCommitment"], str)
            or not data["tradeCommitment"].isdigit()):
        raise SessionError("authorization result schema or quote mismatch")
    return data


PUBLIC_STAGES = (
    "SESSION_PREPARED", "AWAITING_BUYER_APPROVAL", "BUYER_AUTHORIZED",
    "AWAITING_SELLER_APPROVAL", "SELLER_AUTHORIZED", "TRANSACTION_SUBMITTED",
    "TRANSACTION_INCLUDED",
    "CONFIRMATION_DEPTH_OBSERVED", "RECEIPT_AVAILABLE",
)
FAILURE_STAGES = ("BUYER_APPROVAL_NOT_GRANTED", "SELLER_APPROVAL_NOT_GRANTED", "APPROVAL_TIMEOUT",
                  "SIGNER_UNAVAILABLE", "EXECUTION_FAILED", "EXECUTION_UNCERTAIN")


def read_event_record(path: Path | None, session_id: str) -> tuple[list[str], str]:
    if path is None or not path.exists():
        return [], "LOCAL_OPERATOR_EVENT_ONLY"
    try:
        if path.is_symlink() or path.stat().st_size > MAX_EVENTS_BYTES:
            raise SessionError("unsafe event result")
        data = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise SessionError("invalid event result") from exc
    if (type(data) is not dict or set(data) not in ({"version", "sessionId", "stages"},
                                                     {"version", "sessionId", "stages", "mode"})
            or data["version"] != 1 or data["sessionId"] != session_id
            or type(data["stages"]) is not list or len(data["stages"]) > len(PUBLIC_STAGES) + 1
            or data.get("mode", "LOCAL_OPERATOR_EVENT_ONLY") not in
            ("LOCAL_OPERATOR_EVENT_ONLY", "SCRIPTED_TEST_MODE")):
        raise SessionError("event result schema")
    stages = data["stages"]
    if (not stages or stages[0] != PUBLIC_STAGES[0]
            or any(type(stage) is not str for stage in stages)):
        raise SessionError("event sequence")
    normal = [stage for stage in stages if stage in PUBLIC_STAGES]
    if (normal != list(PUBLIC_STAGES[:len(normal)])
            or len(set(stages)) != len(stages)
            or any(stage not in PUBLIC_STAGES + FAILURE_STAGES for stage in stages)
            or (any(stage in FAILURE_STAGES for stage in stages)
                and (stages[-1] not in FAILURE_STAGES or len(stages) != len(normal) + 1))):
        raise SessionError("event order")
    return stages, data.get("mode", "LOCAL_OPERATOR_EVENT_ONLY")


def read_events(path: Path | None, session_id: str) -> list[str]:
    return read_event_record(path, session_id)[0]


def read_scans(path: Path | None, session: dict) -> dict | None:
    if path is None or not path.exists():
        return None
    try:
        if path.is_symlink() or path.stat().st_size > MAX_SCANS_BYTES:
            raise SessionError("unsafe wallet scans")
        data = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise SessionError("invalid wallet scans") from exc
    if (type(data) is not dict or set(data) != {"version", "sessionId", "assetId", "buyerBefore", "buyerAfter", "sellerBefore", "sellerAfter"}
            or data["version"] != 1 or data["sessionId"] != session["sessionId"]
            or data["assetId"] != session["quote"]["assetId"]):
        raise SessionError("wallet scan identity mismatch")
    for key in ("buyerBefore", "buyerAfter", "sellerBefore", "sellerAfter"):
        scan = data[key]
        if type(scan) is not dict or set(scan) != {"height", "nativeZat", "assetUnits"}:
            raise SessionError("wallet scan schema")
        for field in scan:
            _uint(scan[field], 64 if field != "height" else 32)
    q = session["quote"]
    if (data["buyerBefore"]["nativeZat"] - data["buyerAfter"]["nativeZat"] != q["paymentAmount"]
            or data["buyerAfter"]["assetUnits"] - data["buyerBefore"]["assetUnits"] != q["quantity"]
            or data["sellerAfter"]["nativeZat"] - data["sellerBefore"]["nativeZat"] != q["paymentAmount"]
            or data["sellerBefore"]["assetUnits"] - data["sellerAfter"]["assetUnits"] != q["quantity"]
            or data["buyerAfter"]["height"] < session["receipt"]["height"]
            or data["sellerAfter"]["height"] < session["receipt"]["height"]):
        raise SessionError("wallet scans do not reconcile")
    return data


def recorded() -> tuple[dict, dict]:
    data = json.loads((ROOT / "recorded-phase35f.json").read_bytes())
    quote = from_terms(data["terms"])
    validate(quote)
    return quote, data["receipt"]


def recorded_human() -> tuple[dict, dict]:
    """Public evidence from the owner-operated Phase 3.5H disposable run."""
    session = read_session(ROOT / "recorded-phase35h.json")
    if session is None or session["outcome"] != "ACCEPTED":
        raise SessionError("recorded human receipt unavailable")
    return session["quote"], session["receipt"]


def _receipt_view(quote: dict, receipt: dict, status: str, finality: str,
                  source: str, confirmations: int | None = None,
                  session_id: str | None = None) -> dict:
    return {
        "sessionId": session_id or quote["quoteDigest"], "quoteDigest": quote["quoteDigest"],
        "purchaseSettlement": "ACCEPTED" if status in {"ACCEPTED", "PENDING_FINALITY", "FINALIZED", "RECORDED_REGTEST_EVIDENCE"} else status,
        "rootedLineage": "UNSUPPORTED", "investorEligibility": "NOT_EVALUATED",
        "finality": finality, "trustSource": "EXPERIMENTAL_REGTEST",
        "signingMode": "INDEPENDENT_LOCAL_TEST_WALLETS",
        "source": source, "txid": receipt["txid"], "height": receipt["height"],
        "blockHash": receipt["block_hash"] if "block_hash" in receipt else receipt["blockHash"],
        "txBytes": receipt["tx_bytes"] if "tx_bytes" in receipt else receipt["txBytes"],
        "confirmations": confirmations,
    }


class Engine:
    def __init__(self, session_path: Path | None, rpc: Rpc, event_path: Path | None = None,
                 scans_path: Path | None = None, authorization_path: Path | None = None):
        self.session_path = session_path
        self.rpc = rpc
        self.event_path = event_path
        self.scans_path = scans_path
        self.authorization_path = authorization_path

    def availability(self) -> dict:
        try:
            info = self.rpc.call("getblockchaininfo")
            if type(info) is not dict:
                raise NodeUnavailable("chain identity unavailable")
            genesis = self.rpc.call("getblock", ["0", 1])
            consensus = info.get("consensus")
            upgrades = info.get("upgrades")
            swap = upgrades.get(SWAP_BRANCH) if type(upgrades) is dict else None
            if (info.get("chain") != "test"
                    or type(genesis) is not dict
                    or genesis.get("hash") != PINNED_GENESIS
                    or type(consensus) is not dict or consensus.get("nextblock") != SWAP_BRANCH
                    or type(swap) is not dict or swap.get("activationheight") != 1):
                return {"state": "WRONG_NETWORK", "tipHeight": None}
            return {"state": "AVAILABLE", "tipHeight": _uint(info.get("blocks"), 32)}
        except (NodeUnavailable, SessionError):
            return {"state": "NODE_UNAVAILABLE", "tipHeight": None}

    def live(self, session: dict) -> tuple[dict, int]:
        quote = session["quote"]
        receipt = session["receipt"]
        availability = self.availability()
        if session["outcome"] == "FAILED":
            return {"sessionId": session["sessionId"], "status": "FAILED",
                    "availability": availability, "quote": quote,
                    "authorizationCaveat": "Any released signed group may still be usable until its notes are spent."}, 200
        if session["outcome"] == "UNCERTAIN":
            return {"sessionId": session["sessionId"], "status": None,
                    "availability": availability, "quote": quote,
                    "error": {"code": "EXECUTION_UNCERTAIN"}}, 503
        if availability["state"] != "AVAILABLE":
            return {"sessionId": session["sessionId"], "status": None,
                    "availability": availability, "quote": quote,
                    "error": {"code": availability["state"]}}, 503
        tip = availability["tipHeight"]
        if receipt is None:
            status = "EXPIRED_LOCALLY" if tip + 1 > quote["targetHeight"] else "QUOTE_READY"
            return {"sessionId": session["sessionId"], "status": status,
                    "availability": availability, "quote": quote,
                    "authorizationCaveat": "Released signed groups remain usable until their notes are spent."}, 200
        if tip < receipt["height"]:
            status, confirmations, finality = "INVALIDATED_BY_REORG", 0, "UNKNOWN"
        else:
            try:
                block = self.rpc.call("getblock", [str(receipt["height"]), 1])
            except NodeUnavailable:
                return {"sessionId": session["sessionId"], "status": None,
                        "availability": {"state": "NODE_UNAVAILABLE", "tipHeight": None},
                        "quote": quote, "error": {"code": "NODE_UNAVAILABLE"}}, 503
            if type(block) is not dict or type(block.get("tx")) is not list or not isinstance(block.get("hash"), str):
                return {"sessionId": session["sessionId"], "status": None,
                        "availability": {"state": "NODE_UNAVAILABLE", "tipHeight": None},
                        "quote": quote, "error": {"code": "INVALID_NODE_RESPONSE"}}, 503
            if block["hash"] != receipt["block_hash"] or receipt["txid"] not in block["tx"]:
                status, confirmations, finality = "INVALIDATED_BY_REORG", 0, "UNKNOWN"
            else:
                try:
                    confirmations = _uint(block.get("confirmations"), 32)
                except SessionError:
                    confirmations = 0
                if confirmations != tip - receipt["height"] + 1 or confirmations < 1:
                    return {"sessionId": session["sessionId"], "status": None,
                            "availability": {"state": "NODE_UNAVAILABLE", "tipHeight": None},
                            "quote": quote, "error": {"code": "INCONSISTENT_CHAIN_VIEW"}}, 503
                status = "FINALIZED" if confirmations >= 3 else "PENDING_FINALITY"
                finality = ("DEPTH_3_REACHED_NOT_CONSENSUS_FINALITY" if confirmations >= 3
                            else f"PENDING_CONFIRMATIONS_{confirmations}_OF_3")
        view = {"sessionId": session["sessionId"], "status": status,
                "availability": availability, "quote": quote,
                "receipt": _receipt_view(quote, receipt, status, finality,
                                         "LIVE_LOCAL_CLI", confirmations)}
        return view, 200

    def route(self, path: str) -> tuple[dict, int]:
        if path == "/v1/checkout/info":
            return {"version": 1, "network": "regtest", "demoOnly": True,
                    "signingAvailableOverHttp": False, "activeSessionLimit": 1,
                    "availability": self.availability(),
                    "trustSource": "EXPERIMENTAL_REGTEST"}, 200
        quote, historical_receipt = recorded()
        if path == "/v1/checkout/sample-quote":
            return {"source": "RECORDED_REGTEST_EVIDENCE", "actionable": False,
                    "quote": quote, "status": "RECORDED_REGTEST_EVIDENCE"}, 200
        if path == "/v1/checkout/active":
            try:
                active = read_session(self.session_path)
            except SessionError:
                return {"error": {"code": "INVALID_SESSION_RESULT"}}, 503
            if active is None:
                return {"error": {"code": "NO_ACTIVE_SESSION"}}, 404
            return self._live_with_events(active)
        prefix = "/v1/checkout/sessions/"
        if not path.startswith(prefix):
            return {"error": {"code": "NOT_FOUND"}}, 404
        parts = path[len(prefix):].split("/")
        if len(parts) not in (1, 2) or (len(parts) == 2 and parts[1] != "receipt"):
            return {"error": {"code": "NOT_FOUND"}}, 404
        session_id = parts[0]
        if session_id in (RECORDED_ID, RECORDED_HUMAN_ID):
            if session_id == RECORDED_HUMAN_ID:
                quote, historical_receipt = recorded_human()
            receipt = _receipt_view(quote, historical_receipt, "RECORDED_REGTEST_EVIDENCE",
                                    "UNKNOWN", "RECORDED_REGTEST_EVIDENCE",
                                    session_id=session_id)
            if len(parts) == 2:
                return receipt, 200
            return {"sessionId": session_id, "status": "RECORDED_REGTEST_EVIDENCE",
                    "availability": self.availability(), "quote": quote,
                    "receipt": receipt, "actionable": False}, 200
        if not HEX.fullmatch(session_id):
            return {"error": {"code": "NOT_FOUND"}}, 404
        try:
            session = read_session(self.session_path)
        except SessionError:
            return {"error": {"code": "INVALID_SESSION_RESULT"}}, 503
        if session is None or session["sessionId"] != session_id:
            return {"error": {"code": "NOT_FOUND"}}, 404
        view, code = self._live_with_events(session)
        if len(parts) == 2:
            if code != 200:
                return view, code
            if "receipt" not in view:
                return {"error": {"code": "RECEIPT_NOT_AVAILABLE"}}, 404
            return view["receipt"], 200
        return view, code

    def _live_with_events(self, session: dict) -> tuple[dict, int]:
        try:
            stages, approval_mode = read_event_record(self.event_path, session["sessionId"])
            if (session["receipt"] is None
                    and (any(stage in stages for stage in PUBLIC_STAGES[5:])
                         or self.scans_path is not None and self.scans_path.exists())):
                raise SessionError("settlement observation without receipt")
            if session["receipt"] is not None and any(stage in FAILURE_STAGES for stage in stages):
                raise SessionError("failure event conflicts with accepted receipt")
            scans = read_scans(self.scans_path, session) if session["receipt"] is not None else None
            authorization = read_authorization(self.authorization_path, session["sessionId"])
        except SessionError:
            return {"error": {"code": "INVALID_SESSION_OBSERVATION"}}, 503
        view, code = self.live(session)
        if stages:
            view["operatorStages"] = stages
            view["operatorStage"] = stages[-1]
            view["approvalEvidence"] = approval_mode
        if authorization is not None:
            view["authorization"] = authorization
            if isinstance(view.get("receipt"), dict):
                view["receipt"]["investorEligibility"] = "VERIFIED"
        if scans is not None and code == 200 and view.get("status") in ("PENDING_FINALITY", "FINALIZED"):
            view["walletScans"] = scans
            view["receipt"]["walletScans"] = scans
        return view, code


def handler_for(engine: Engine, port: int):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def _security(self) -> bool:
            origin = self.headers.get("Origin")
            if (len(self.headers.get_all("Host", [])) != 1
                    or len(self.headers.get_all("Origin", [])) > 1
                    or len(self.headers.get_all("Content-Length", [])) > 1
                    or len(self.headers.get_all("Sec-Fetch-Site", [])) > 1
                    or self.headers.get("Host") != f"127.0.0.1:{port}"
                    or origin not in (None, f"http://127.0.0.1:{port}")
                    or self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none")
                    or len(self.headers) > 20
                    or sum(len(k) + len(v) for k, v in self.headers.items()) > 4096
                    or self.headers.get("Transfer-Encoding") is not None):
                self._send(403, {"error": {"code": "BROWSER_REQUEST_REJECTED"}})
                return False
            if self.headers.get("Content-Length") not in (None, "0"):
                self._send(400, {"error": {"code": "BODY_NOT_ALLOWED"}})
                return False
            return True

        def do_GET(self) -> None:  # noqa: N802
            if not self._security():
                return
            if len(self.path) > 160 or "?" in self.path or "#" in self.path:
                self._send(400, {"error": {"code": "INVALID_PATH"}})
                return
            payload, status = engine.route(self.path)
            self._send(status, payload)

        def do_POST(self) -> None:  # noqa: N802
            if self._security():
                self._send(405, {"error": {"code": "READ_ONLY_API"}})

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

        def log_message(self, format: str, *args: object) -> None:
            # Never log attacker-controlled paths or browser headers.
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Disposable regtest checkout status API")
    parser.add_argument("--demo", action="store_true", help="explicitly enable local demo")
    parser.add_argument("--port", type=int, default=18735)
    parser.add_argument("--rpc-port", type=int, default=18243)
    parser.add_argument("--session-result", type=Path)
    parser.add_argument("--event-result", type=Path)
    parser.add_argument("--wallet-scans", type=Path)
    parser.add_argument("--authorization-result", type=Path)
    args = parser.parse_args()
    if not args.demo:
        parser.error("disabled without --demo")
    if not 1 <= args.port <= 65535:
        parser.error("invalid API port")
    engine = Engine(args.session_result, Rpc(args.rpc_port), args.event_result,
                    args.wallet_scans, args.authorization_result)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(engine, args.port))
    print(f"Phase 3.5G read-only demo on http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
