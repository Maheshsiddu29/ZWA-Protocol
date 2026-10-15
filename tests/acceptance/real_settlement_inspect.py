#!/usr/bin/env python3
"""Read-only inspection of a running judge-demo Compose project, for the REAL browser purchase acceptance.

    real_settlement_inspect.py --project NAME [--ssh HOST] [--sudo] preflight
    real_settlement_inspect.py --project NAME [--ssh HOST] [--sudo] verify --order ID --preflight FILE \
        --instrument ID --quantity N

`preflight` runs before any purchase. It proves that every cryptographic and chain component is the real one:
the Zebra container runs a pinned image for its platform; portal, order service and demo signer run their real
modules from one engine image; that image holds the pinned tx-tool, the Sapling parameters and the Groth16
verifier byte-identical to this repository; the chain is the regtest genesis; and no order is unresolved.
The test-double backend (tests/acceptance/web_test_double_exchange.py) has none of these and fails here.

`verify` checks one settled order independently of the website: ledger history and evidence, both restricted
signers' attestations for the exact quote, the coordinator's execution line, the transaction and block read
from Zebra (a real v6 transaction, not a stub), durability, reconciliation, a single settlement and a single
order for this buyer.

Nothing is written anywhere: every container program opens the ledger read-only, reads files and calls only
read-only RPC methods. Output is JSON with checks and sanitized facts (no keys, seeds, tokens or sessions).
Exit 0 only when every check passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY))

from benchmark.engine import PROBE, PROBE_MARKER  # noqa: E402
from benchmark.safety import REGTEST_GENESIS  # noqa: E402
from exchange.persistent_chain import PINNED_ZEBRA_IMAGES  # noqa: E402

ENGINE_COMMANDS = {
    "portal": ["python3", "-m", "exchange.portal_server"],
    "order-service": ["python3", "-m", "exchange.order_service", "serve"],
    "demo-signer": ["python3", "-m", "exchange.demo_signer", "serve"],
}
VERIFIER_FILES = ("verification-key-v2.json", "native_zec_v2.js", "checkout_final.zkey", "native_zec_checkout_v2.wasm")
SAPLING = {"sapling-spend.params": "8e48ffd23abb3a5fd9c5589204f32d9c31285a04b78096ba40a79b75677efc13",
           "sapling-output.params": "2f0ebbcbb9bb0bcffe95a397e7eba89c29eb4dde6191c339db88570e3f3fb0e4"}
FORWARD = ["BUYER_REQUESTED", "QUOTE_READY", "BUYER_CONFIRMED", "PROOF_VERIFIED", "TRADE_AUTHORIZED",
           "BUYER_APPROVED", "SELLER_APPROVED", "SUBMITTED", "INCLUDED", "RECONCILED", "RECEIPT_AVAILABLE"]
CLOSED = ("RECEIPT_AVAILABLE", "CANCELLED", "EXPIRED", "REJECTED", "EXECUTION_FAILED")
VERDICTS = ("issuerAuthentication", "issuanceProvenance", "credentialAuthorityAuthentication",
            "investorEligibility", "exactTradeAuthorization")
DURABLE = "RESTART_DURABLE_UNDER_PINNED_ZEBRA_POLICY_NOT_FINALITY"
MIN_TX_BYTES = 10_000  # a real ZSA swap with Orchard proofs is tens of kilobytes

# Container-side programs (stdin to `python3 -`). Read-only by construction.
ENGINE_FACTS = r'''
import hashlib, json, os, sys
root = os.environ["ZWA_PRIVATE_EXCHANGE_ROOT"]
config = json.load(open(os.path.join(root, "exchange", "service-config.json")))
def sha(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""): h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None
from exchange import tools
auth = tools.AUTH_SCRIPT.parent
tx = config["txToolDir"]
params = os.path.join(config["paramsDataHome"], "ZcashParams")
print(json.dumps({
  "config": {k: config.get(k) for k in ("version", "approvalMode", "chainManagement", "isolation", "rpcHost",
                                        "rpcPort", "stabilizationBlocks", "txToolDir", "nodeBin")},
  "demo": config.get("demo"),
  "txToolRevision": open(os.path.join(tx, ".zwa-tx-tool-revision")).read().strip()
                    if os.path.isfile(os.path.join(tx, ".zwa-tx-tool-revision")) else None,
  "txToolBinary": os.access(os.path.join(tx, "target", "release", "zcash_tx_tool"), os.X_OK),
  "nodeBin": os.access(config["nodeBin"], os.X_OK),
  "verifier": {name: sha(str(auth / name)) for name in sys.argv[1:]},
  "sapling": {name: sha(os.path.join(params, name)) for name in ("sapling-spend.params", "sapling-output.params")},
}))
'''

RPC_READ = r'''
import json, os, sys
from exchange.persistent_chain import _rpc
config = json.load(open(os.path.join(os.environ["ZWA_PRIVATE_EXCHANGE_ROOT"], "exchange", "service-config.json")))
ALLOWED = {"getblockchaininfo", "getblockcount", "getblockhash", "getblock", "getrawtransaction", "getrawmempool"}
out = []
for call in json.loads(sys.argv[1]):
    assert call[0] in ALLOWED
    try:
        result = _rpc(config["rpcPort"], call[0], call[1], host=config.get("rpcHost", "127.0.0.1"))
        out.append({"ok": True, "result": result})
    except Exception as exc:
        out.append({"ok": False, "error": type(exc).__name__})
print(json.dumps(out))
'''

ORDER_ARTIFACTS = r'''
import hashlib, json, os, re, sqlite3, sys
root = os.environ["ZWA_PRIVATE_EXCHANGE_ROOT"]
order = sys.argv[1]
assert re.fullmatch(r"[0-9a-f]{32}", order)
d = os.path.join(root, "exchange", "orders", order)
def read_json(name):
    path = os.path.join(d, name)
    return json.load(open(path)) if os.path.isfile(path) and not os.path.islink(path) else None
def phase(name, prefixes):
    path = os.path.join(d, name)
    if not os.path.isfile(path):
        return []
    keep = []
    for line in open(path, errors="replace"):
        for token in re.findall(r"PHASE[0-9A-Z_]+(?: [a-z_]+=[^ \n]+)*", line):
            if token.startswith(prefixes):
                # only public attestation fields; never seeds, keys or addresses
                keep.append(" ".join(t for t in token.split(" ")
                                     if not re.match(r"(seed|key|secret|address|ufvk|usk)", t.split("=")[0])))
    return keep
def sha(name):
    path = os.path.join(d, name)
    return hashlib.sha256(open(path, "rb").read()).hexdigest() if os.path.isfile(path) else None
db = sqlite3.connect("file:" + os.path.join(root, "exchange", "exchange.sqlite") + "?mode=ro", uri=True)
proof = db.execute("SELECT value_json FROM order_evidence WHERE order_id=? AND kind='PROOF_VERIFIED'",
                   (order,)).fetchone()
proof = json.loads(proof[0]) if proof else {}
receipt = read_json("order-receipt.json") or {}
print(json.dumps({
  "stabilization": read_json("stabilization.json"),
  "dropped": os.path.exists(os.path.join(d, "dropped.json")),
  "receipt": {k: receipt.get(k) for k in ("txid", "height", "blockHash", "quoteDigest", "quantity", "paymentZat",
                                         "approvalMode", "instrumentId", "rootedNoteLineage")},
  "proofEvidence": {k: proof.get(k) for k in ("verifierResultSha256", "tradeCommitment", "quoteDigest")},
  "preflightAuthorizationSha256": sha("preflight-authorization.json"),
  "buyerSigner": phase("buyer-signer.log", ("PHASE35F_AUTHORIZED",)),
  "sellerSigner": phase("seller-signer.log", ("PHASE35F_AUTHORIZED",)),
  "coordinator": phase("coordinator.log", ("PHASE35G_EXECUTED",)),
  "proofGeneration": phase("proof-generation.log", ("PHASE4C1_PROOF_GENERATED",)),
}))
'''


class Inspector:
    def __init__(self, project: str, ssh: str | None, sudo: bool):
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", project):
            raise SystemExit("invalid Compose project name")
        self.project, self.ssh, self.sudo = project, ssh, sudo
        self.checks: list[dict] = []

    def docker(self, *args: str, stdin: str | None = None) -> str:
        argv = (["sudo"] if self.sudo else []) + ["docker", *args]
        if self.ssh:
            argv = ["ssh", "-o", "BatchMode=yes", self.ssh, shlex.join(argv)]
        done = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=300)
        if done.returncode:
            raise RuntimeError(f"docker {args[0]} failed: {done.stderr.strip()[-300:]}")
        return done.stdout

    def container(self, service: str) -> dict:
        name = f"{self.project}-{service}-1"
        info = json.loads(self.docker("inspect", name))[0]
        if info["Config"]["Labels"].get("com.docker.compose.project") != self.project:
            raise RuntimeError(f"{name} does not belong to {self.project}")
        return info

    def run_in(self, service: str, program: str, *args: str) -> dict | list:
        info = self.container(service)
        env = dict(item.split("=", 1) for item in info["Config"]["Env"])
        root = env.get("ZWA_PRIVATE_EXCHANGE_ROOT", "")
        if not re.fullmatch(r"/var/lib/zwa/zwa4e-[A-Za-z0-9-]+", root):
            raise RuntimeError("unexpected exchange root in the engine container")
        out = self.docker("exec", "-i", "-e", f"ZWA_PRIVATE_EXCHANGE_ROOT={root}", info["Name"].lstrip("/"),
                          "python3", "-", *args, stdin=program)
        return json.loads(out)

    def rpc(self, *calls: list) -> list:
        return self.run_in("portal", RPC_READ, json.dumps(list(calls)))

    def probe(self, *orders: str) -> dict:
        return self.run_in("portal", PROBE, PROBE_MARKER, *orders)

    def check(self, name: str, ok: bool, detail: object = "") -> bool:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return bool(ok)

    def result(self, facts: dict) -> dict:
        return {"project": self.project, "ok": all(c["ok"] for c in self.checks), "checks": self.checks, "facts": facts}

    # ------------------------------------------------------------------ preflight

    def preflight(self) -> dict:
        facts: dict = {}
        services = {}
        for service in ("zebra", "portal", "order-service", "demo-signer", "web", "caddy"):
            try:
                services[service] = self.container(service)
            except Exception as exc:  # noqa: BLE001
                self.check(f"container {service} exists in {self.project}", False, str(exc)[:200])
        if len(services) != 6:
            return self.result(facts)
        for service, info in services.items():
            self.check(f"{service} running", info["State"]["Running"])
        facts["images"] = {service: info["Image"] for service, info in services.items()}
        zebra = services["zebra"]
        platform = None
        try:
            image = json.loads(self.docker("image", "inspect", zebra["Image"]))[0]
            platform = f"{image['Os']}/{image['Architecture']}"
        except Exception as exc:  # noqa: BLE001
            self.check("zebra image is inspectable", False, str(exc)[:200])
        self.check("zebra runs the image pinned for its platform",
                   platform in PINNED_ZEBRA_IMAGES and PINNED_ZEBRA_IMAGES[platform] == zebra["Image"],
                   {"platform": platform, "image": zebra["Image"]})
        engines = {services[s]["Image"] for s in ENGINE_COMMANDS}
        self.check("portal, order service and demo signer share one engine image", len(engines) == 1, sorted(engines))
        for service, prefix in ENGINE_COMMANDS.items():
            command = services[service]["Config"]["Cmd"] or []
            self.check(f"{service} runs {' '.join(prefix[2:])}", command[:len(prefix)] == prefix, command[:4])
        for service in ("zebra", *ENGINE_COMMANDS):
            ports = services[service]["HostConfig"].get("PortBindings") or {}
            self.check(f"{service} publishes no host port", not ports, ports)
        caddy_ports = services["caddy"]["HostConfig"].get("PortBindings") or {}
        facts["caddyPorts"] = {k: [f"{b.get('HostIp')}:{b.get('HostPort')}" for b in v]
                               for k, v in caddy_ports.items()}

        engine = self.run_in("order-service", ENGINE_FACTS, *VERIFIER_FILES)
        config = engine["config"]
        facts["engineConfig"] = config
        facts["demoLimits"] = engine["demo"]
        self.check("custodial demo policy, external chain, Landlock isolation",
                   (config["approvalMode"], config["chainManagement"], config["isolation"])
                   == ("CUSTODIAL_DEMO_POLICY", "external", "linux-landlock"), config)
        pinned = re.search(r"^ARG TX_TOOL_REVISION=([0-9a-f]{40})$",
                           (REPOSITORY / "deploy/engine/Dockerfile").read_text(), re.M).group(1)
        self.check("tx-tool is the pinned revision and its binary is executable",
                   engine["txToolRevision"] == pinned and engine["txToolBinary"],
                   {"revision": engine["txToolRevision"], "pinned": pinned})
        self.check("node binary for the Groth16 verifier is executable", engine["nodeBin"])
        local = {name: hashlib.sha256((REPOSITORY / "checkout/authorization" / name).read_bytes()).hexdigest()
                 for name in VERIFIER_FILES}
        self.check("Groth16 verifier, key and circuit are byte-identical to this repository",
                   engine["verifier"] == local, {n: (engine["verifier"][n] or "")[:16] for n in VERIFIER_FILES})
        self.check("Sapling parameters match their pinned hashes", engine["sapling"] == SAPLING)

        info, genesis = self.rpc(["getblockchaininfo", []], ["getblockhash", [0]])
        self.check("Zebra RPC answers", info["ok"] and genesis["ok"])
        if info["ok"] and genesis["ok"]:
            facts["chain"] = {"tip": info["result"].get("blocks"), "genesisHash": genesis["result"]}
            self.check("chain is the regtest genesis", genesis["result"] == REGTEST_GENESIS, genesis["result"])
        probe = self.probe()
        self.check("ledger integrity", probe["integrity"] == "ok", probe["integrity"])
        self.check("ledger chain identity matches the node", probe["identity"]["genesisHash"] == REGTEST_GENESIS
                   and probe["chain"].get("live") is True)
        open_orders = [o for o in probe["orderIndex"] if o["state"] not in CLOSED]
        self.check("no unresolved or in-flight order", not open_orders, open_orders)
        self.check("no order is UNCERTAIN", not [o for o in probe["orderIndex"] if o["state"] == "UNCERTAIN"])
        self.check("no reserved inventory", all(o["reserved"] == 0 for o in probe["offers"]),
                   [(o["instrument_id"], o["reserved"]) for o in probe["offers"]])
        self.check("dealer policy is ACTIVE", probe["dealerPolicy"] == "ACTIVE", probe["dealerPolicy"])
        self.check("dealer inventory scans exist", bool(probe["dealerScans"]))
        facts["offers"] = [{k: o[k] for k in ("id", "instrument_id", "capacity", "reserved", "sold")}
                           for o in probe["offers"]]
        facts["orderIds"] = [o["id"] for o in probe["orderIndex"]]
        facts["settlementTxids"] = [s["txid"] for s in probe["settlements"]]
        return self.result(facts)

    # --------------------------------------------------------------------- verify

    def verify(self, order_id: str, before: dict, instrument: str, quantity: int) -> dict:
        if not re.fullmatch(r"[0-9a-f]{32}", order_id):
            raise SystemExit("invalid order id")
        if not before.get("ok"):
            raise SystemExit("the preflight record did not pass; refusing to verify against it")
        probe = self.probe(order_id)
        order = probe["orders"].get(order_id)
        if not self.check("order exists in the ledger", order is not None):
            return self.result({})
        events = [e["to_state"] for e in order["events"]]
        evidence = order["evidence"]
        self.check("order reached RECEIPT_AVAILABLE through the full forward path, each state once",
                   events == FORWARD, events)
        quote_digest = order["quote_digest"]
        offer = next((o for o in probe["offers"] if o["id"] == order["offer_id"]), None)
        payment = (order.get("quote") or {}).get("payment_zat")
        self.check("order is the requested instrument and quantity",
                   offer and offer["instrument_id"] == instrument and order["quantity"] == quantity,
                   {"instrument": offer and offer["instrument_id"], "quantity": order["quantity"]})
        new_orders = [o for o in probe["orderIndex"] if o["id"] not in set(before["facts"]["orderIds"])]
        mine = [o for o in probe["orders"].values() if o["buyer_id"] == order["buyer_id"]]
        self.check("exactly one new order since preflight", [o["id"] for o in new_orders] == [order_id],
                   [o["id"] for o in new_orders])
        everyone = self.probe()
        buyer_orders = [o for o in everyone["orders"].values() if o["buyer_id"] == order["buyer_id"]]
        self.check("this buyer holds exactly one order", len(buyer_orders) == 1 and len(mine) == 1, len(buyer_orders))

        proof = evidence.get("PROOF_VERIFIED", {})
        self.check("all five Groth16 verdicts VERIFIED for this quote",
                   all(proof.get(k) == "VERIFIED" for k in VERDICTS) and proof.get("quoteDigest") == quote_digest,
                   {k: proof.get(k) for k in VERDICTS})
        artifacts = self.run_in("portal", ORDER_ARTIFACTS, order_id)
        self.check("the verifier's saved result is the one the ledger recorded",
                   artifacts["preflightAuthorizationSha256"] is not None
                   and artifacts["preflightAuthorizationSha256"] == artifacts["proofEvidence"]["verifierResultSha256"])
        self.check("a Groth16 proof was generated for this exact quote",
                   any(f"quote={quote_digest}" in line for line in artifacts["proofGeneration"]))
        for role, lines in (("buyer", artifacts["buyerSigner"]), ("seller", artifacts["sellerSigner"])):
            line = next((l for l in lines if f"terms_hash={quote_digest}" in l), "")
            self.check(f"{role} signer authorized this quote with verified proofs and spend authorization",
                       "proof=verified" in line and "spend_auth=verified" in line, line[:160])
        included = evidence.get("INCLUDED", {})
        txid, height, block_hash = included.get("txid"), included.get("height"), included.get("blockHash")
        self.check("coordinator executed exactly this transaction",
                   any(f"txid={txid} height={height} rpc_block_hash={block_hash}" in line
                       for line in artifacts["coordinator"]))
        self.check("settlement ledger has one row for this order and its txid is unique",
                   [s["txid"] for s in everyone["settlements"] if s["order_id"] == order_id] == [txid]
                   and [s["txid"] for s in everyone["settlements"]].count(txid) == 1)
        self.check("receipt file matches the inclusion evidence and quote",
                   (artifacts["receipt"]["txid"], artifacts["receipt"]["height"], artifacts["receipt"]["blockHash"],
                    artifacts["receipt"]["quoteDigest"]) == (txid, height, block_hash, quote_digest))

        tx, chain_hash, block, tip = self.rpc(["getrawtransaction", [txid, 1]], ["getblockhash", [height]],
                                              ["getblock", [str(height), 1]], ["getblockcount", []])
        raw = tx["result"].get("hex", "") if tx["ok"] else ""
        self.check("Zebra returns the transaction at the receipt height",
                   tx["ok"] and tx["result"].get("height") == height, tx.get("result", {}).get("height"))
        self.check("it is a real v6 (ZSA) transaction of realistic size",
                   raw.startswith("06000080") and len(raw) // 2 >= MIN_TX_BYTES,
                   {"header": raw[:16], "bytes": len(raw) // 2})
        self.check("the main chain's block at that height has the receipt's hash and contains the transaction",
                   chain_hash["ok"] and chain_hash["result"] == block_hash and block["ok"]
                   and block["result"].get("hash") == block_hash and txid in block["result"].get("tx", []))
        stabilization = artifacts["stabilization"] or {}
        blocks_needed = before["facts"]["engineConfig"]["stabilizationBlocks"]
        confirmations = tip["result"] - height + 1 if tip["ok"] else 0
        self.check("durable: stabilization recorded under the pinned-Zebra policy and not dropped",
                   stabilization.get("policy") == DURABLE and stabilization.get("minedEmptyBlocks") == blocks_needed
                   and type(stabilization.get("anchorHeight")) is int and stabilization["anchorHeight"] > height
                   and not artifacts["dropped"],
                   {k: stabilization.get(k) for k in ("policy", "minedEmptyBlocks", "anchorHeight", "tipHeight")})
        self.check("confirmations cover the stabilization blocks", confirmations >= blocks_needed, confirmations)

        reconciled = evidence.get("RECONCILED", {})
        self.check("reconciliation: buyer +units, dealer -units and +payment, exactly the quote",
                   reconciled.get("txid") == txid
                   and reconciled["buyerUnitsAfter"] - reconciled["buyerUnitsBefore"] == quantity
                   and reconciled["sellerUnitsBefore"] - reconciled["sellerUnitsAfter"] == quantity
                   and reconciled["sellerZatAfter"] - reconciled["sellerZatBefore"] == payment,
                   reconciled)
        sold_before = {o["id"]: o["sold"] for o in before["facts"]["offers"]}
        self.check("inventory: this offer sold exactly the quantity; nothing is reserved anywhere",
                   all(o["reserved"] == 0 for o in everyone["offers"])
                   and all(o["sold"] - sold_before.get(o["id"], 0) == (quantity if o["id"] == order["offer_id"] else 0)
                           for o in everyone["offers"]))
        self.check("no order is unresolved after the purchase",
                   not [o for o in everyone["orderIndex"] if o["state"] not in CLOSED])
        facts = {"orderId": order_id, "instrument": instrument, "quantity": quantity, "paymentZat": payment,
                 "quoteDigest": quote_digest, "events": events, "proof": {k: proof.get(k) for k in VERDICTS},
                 "transaction": {"txid": txid, "height": height, "blockHash": block_hash, "header": raw[:16],
                                 "bytes": len(raw) // 2, "confirmations": confirmations},
                 "durability": {k: stabilization.get(k) for k in ("policy", "minedEmptyBlocks", "anchorHeight")},
                 "reconciliation": reconciled, "approvalMode": artifacts["receipt"]["approvalMode"],
                 "rootedNoteLineage": artifacts["receipt"]["rootedNoteLineage"]}
        return self.result(facts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--ssh", help="run docker on this SSH host (read-only commands only)")
    parser.add_argument("--sudo", action="store_true", help="prefix docker with sudo")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight")
    verify = sub.add_parser("verify")
    verify.add_argument("--order", required=True)
    verify.add_argument("--preflight", required=True, type=Path)
    verify.add_argument("--instrument", required=True)
    verify.add_argument("--quantity", required=True, type=int)
    args = parser.parse_args()
    inspector = Inspector(args.project, args.ssh, args.sudo)
    try:
        if args.command == "preflight":
            result = inspector.preflight()
        else:
            before = json.loads(args.preflight.read_text())
            result = inspector.verify(args.order, before, args.instrument, args.quantity)
    except Exception as exc:  # noqa: BLE001 - reported, never a pass
        inspector.check("inspection completed", False, f"{type(exc).__name__}: {str(exc)[:300]}")
        result = inspector.result({})
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
