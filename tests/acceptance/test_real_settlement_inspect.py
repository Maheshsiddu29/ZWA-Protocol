"""The real-settlement inspector must refuse anything but real cryptographic and chain components.

    python3 -m unittest discover -s tests/acceptance -p 'test_real_settlement_inspect.py'

A synthetic, real-shaped project passes; each substitution a mock or test double would make fails. No Docker,
chain or network is used: the inspector's container access is replaced by canned answers.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import real_settlement_inspect as inspect  # noqa: E402

REPO = inspect.REPOSITORY
ARM = inspect.PINNED_ZEBRA_IMAGES["linux/arm64"]
ENGINE = "sha256:" + "e" * 64
ORDER, OFFER, OTHER = "a" * 32, "b" * 32, "c" * 32
DIGEST, TXID, BLOCK = "d" * 64, "f" * 64, "9" * 64
HEIGHT, TIP = 436, 546
PINNED_TX_TOOL = "16052323904be8efbb9e0f92d059fa57c9198f8a"


def containers():
    def c(service, image, cmd, ports=None):
        return {"Name": f"/zwa-x-{service}-1", "Image": image, "State": {"Running": True},
                "Config": {"Labels": {"com.docker.compose.project": "zwa-x"}, "Cmd": cmd,
                           "Env": ["ZWA_PRIVATE_EXCHANGE_ROOT=/var/lib/zwa/zwa4e-demo"]},
                "HostConfig": {"PortBindings": ports or {}}}
    return {"zebra": c("zebra", ARM, None), "web": c("web", "sha256:" + "1" * 64, ["node", "server.js"]),
            "caddy": c("caddy", "sha256:" + "2" * 64, None, {"443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "9443"}]}),
            **{s: c(s, ENGINE, [*cmd, "--root", "/var/lib/zwa/zwa4e-demo"])
               for s, cmd in inspect.ENGINE_COMMANDS.items()}}


def engine_facts():
    return {"config": {"version": 2, "approvalMode": "CUSTODIAL_DEMO_POLICY", "chainManagement": "external",
                       "isolation": "linux-landlock", "rpcHost": "zebra", "rpcPort": 18232, "stabilizationBlocks": 110,
                       "txToolDir": "/opt/zwa/tx-tool", "nodeBin": "/usr/local/bin/node"},
            "demo": {}, "txToolRevision": PINNED_TX_TOOL, "txToolBinary": True, "nodeBin": True,
            "verifier": {n: hashlib.sha256((REPO / "checkout/authorization" / n).read_bytes()).hexdigest()
                         for n in inspect.VERIFIER_FILES},
            "sapling": dict(inspect.SAPLING)}


def probe(after: bool):
    offers = [{"id": OFFER, "instrument_id": "zwa-credit-participation", "capacity": 900, "reserved": 0,
               "sold": 1 if after else 0, "asset_base": "", "state": "OFFER_CREATED"},
              {"id": OTHER, "instrument_id": "zwa-gold-reference", "capacity": 900, "reserved": 0, "sold": 0,
               "asset_base": "", "state": "OFFER_CREATED"}]
    order = {"id": ORDER, "offer_id": OFFER, "buyer_id": "judge-0", "quantity": 1, "state": "RECEIPT_AVAILABLE",
             "quote_digest": DIGEST, "quote": {"payment_zat": 850},
             "events": [{"to_state": s} for s in inspect.FORWARD],
             "evidence": {"PROOF_VERIFIED": {**{k: "VERIFIED" for k in inspect.VERDICTS}, "quoteDigest": DIGEST},
                          "INCLUDED": {"txid": TXID, "height": HEIGHT, "blockHash": BLOCK},
                          "RECONCILED": {"txid": TXID, "buyerUnitsBefore": 0, "buyerUnitsAfter": 1,
                                         "sellerUnitsBefore": 1000, "sellerUnitsAfter": 999,
                                         "sellerZatBefore": 0, "sellerZatAfter": 850}}}
    return {"integrity": "ok", "identity": {"genesisHash": inspect.REGTEST_GENESIS},
            "chain": {"live": True, "tip": TIP}, "offers": offers, "dealerPolicy": "ACTIVE",
            "dealerScans": [{"asset_units": 1000}],
            "orderIndex": ([{"id": ORDER, "offer_id": OFFER, "quantity": 1, "state": "RECEIPT_AVAILABLE"}]
                           if after else []),
            "orders": {ORDER: order} if after else {},
            "settlements": [{"order_id": ORDER, "txid": TXID}] if after else []}


def artifacts():
    signed = (f"terms_hash={DIGEST} action_group_commitment={'8' * 64} actions=2 own_inputs_spent=1 "
              "group_native_balance_zat=0 group_expiry_height=476 proof=verified spend_auth=verified")
    return {"stabilization": {"policy": inspect.DURABLE, "minedEmptyBlocks": 110, "anchorHeight": 446,
                              "tipHeight": TIP},
            "dropped": False,
            "receipt": {"txid": TXID, "height": HEIGHT, "blockHash": BLOCK, "quoteDigest": DIGEST, "quantity": 1,
                        "paymentZat": 850, "approvalMode": "CUSTODIAL_DEMO_POLICY",
                        "instrumentId": "zwa-credit-participation", "rootedNoteLineage": "UNSUPPORTED"},
            "proofEvidence": {"verifierResultSha256": "5" * 64, "tradeCommitment": "1", "quoteDigest": DIGEST},
            "preflightAuthorizationSha256": "5" * 64,
            "buyerSigner": [f"PHASE35F_AUTHORIZED role=Buyer {signed}"],
            "sellerSigner": [f"PHASE35F_AUTHORIZED role=Seller {signed}"],
            "coordinator": [f"PHASE35G_EXECUTED txid={TXID} height={HEIGHT} rpc_block_hash={BLOCK}"],
            "proofGeneration": [f"PHASE4C1_PROOF_GENERATED trade=1 quote={DIGEST}"]}


class Canned(inspect.Inspector):
    def __init__(self, world):
        super().__init__("zwa-x", None, False)
        self.world = world

    def container(self, service):
        if service not in self.world["containers"]:
            raise RuntimeError(f"no {service} container")
        return self.world["containers"][service]

    def docker(self, *args, stdin=None):
        assert args[:2] == ("image", "inspect")
        return json.dumps([{"Os": "linux", "Architecture": self.world["zebraArch"]}])

    def run_in(self, service, program, *args):
        return copy.deepcopy(self.world["engine"] if program is inspect.ENGINE_FACTS else self.world["artifacts"])

    def rpc(self, *calls):
        answers = {"getblockchaininfo": {"blocks": TIP}, "getblockhash": None, "getblockcount": TIP,
                   "getrawtransaction": {"height": HEIGHT, "hex": self.world["txHex"]},
                   "getblock": {"hash": BLOCK, "tx": ["0" * 64, TXID]}}
        out = []
        for method, params in calls:
            if method == "getblockhash":
                out.append({"ok": True, "result": inspect.REGTEST_GENESIS if params == [0] else BLOCK})
            else:
                out.append({"ok": True, "result": answers[method]})
        return out

    def probe(self, *orders):
        return copy.deepcopy(self.world["probe"])


def world(after=False):
    return {"containers": containers(), "zebraArch": "arm64", "engine": engine_facts(), "artifacts": artifacts(),
            "txHex": "0600008077777777" + "ab" * 28_000, "probe": probe(after)}


def preflight(w):
    return Canned(w).preflight()


def verify(w, before=None):
    before = before or preflight(world())
    return Canned(w).verify(ORDER, before, "zwa-credit-participation", 1)


def failed(result):
    return [c["name"] for c in result["checks"] if not c["ok"]]


class Preflight(unittest.TestCase):
    def test_real_shaped_project_passes(self):
        self.assertEqual(failed(preflight(world())), [])

    def test_test_double_backend_without_containers_fails(self):
        w = world()
        del w["containers"]["zebra"]
        self.assertFalse(preflight(w)["ok"])

    def test_unpinned_or_emulated_zebra_fails(self):
        w = world()
        w["containers"]["zebra"]["Image"] = "sha256:" + "0" * 64
        self.assertIn("zebra runs the image pinned for its platform", failed(preflight(w)))
        w = world()
        w["zebraArch"] = "amd64"  # the arm64 image ID claimed for another platform
        self.assertIn("zebra runs the image pinned for its platform", failed(preflight(w)))

    def test_engine_services_running_other_programs_fail(self):
        w = world()
        w["containers"]["order-service"]["Config"]["Cmd"] = ["python3", "tests/acceptance/web_test_double_exchange.py"]
        self.assertIn("order-service runs exchange.order_service serve", failed(preflight(w)))
        w = world()
        w["containers"]["demo-signer"]["Image"] = "sha256:" + "3" * 64
        self.assertIn("portal, order service and demo signer share one engine image", failed(preflight(w)))

    def test_substituted_verifier_tool_or_params_fail(self):
        for mutate, name in (
                (lambda e: e["verifier"].update({"verification-key-v2.json": "0" * 64}),
                 "Groth16 verifier, key and circuit are byte-identical to this repository"),
                (lambda e: e.update(txToolRevision="0" * 40),
                 "tx-tool is the pinned revision and its binary is executable"),
                (lambda e: e.update(txToolBinary=False), "tx-tool is the pinned revision and its binary is executable"),
                (lambda e: e["sapling"].update({"sapling-spend.params": None}),
                 "Sapling parameters match their pinned hashes"),
                (lambda e: e["config"].update(approvalMode="SCRIPTED_TEST_MODE"),
                 "custodial demo policy, external chain, Landlock isolation")):
            w = world()
            mutate(w["engine"])
            self.assertIn(name, failed(preflight(w)))

    def test_published_internal_port_fails(self):
        w = world()
        published = {"8790/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8790"}]}
        w["containers"]["portal"]["HostConfig"]["PortBindings"] = published
        self.assertIn("portal publishes no host port", failed(preflight(w)))

    def test_unresolved_or_uncertain_order_refuses_a_new_purchase(self):
        w = world(after=True)
        w["probe"]["orderIndex"][0]["state"] = "UNCERTAIN"
        names = failed(preflight(w))
        self.assertIn("no unresolved or in-flight order", names)
        self.assertIn("no order is UNCERTAIN", names)


DURABILITY = "durable: stabilization recorded under the pinned-Zebra policy and not dropped"


class Verify(unittest.TestCase):
    def test_real_shaped_settlement_passes(self):
        self.assertEqual(failed(verify(world(after=True))), [])

    def test_stub_transaction_fails(self):
        w = world(after=True)
        w["txHex"] = "00" * 3000  # what the test-double chain serves
        self.assertIn("it is a real v6 (ZSA) transaction of realistic size", failed(verify(w)))

    def test_missing_proof_or_signer_attestation_fails(self):
        w = world(after=True)
        w["artifacts"]["buyerSigner"] = [line.replace("proof=verified", "proof=skipped")
                                         for line in w["artifacts"]["buyerSigner"]]
        self.assertIn("buyer signer authorized this quote with verified proofs and spend authorization",
                      failed(verify(w)))
        w = world(after=True)
        w["probe"]["orders"][ORDER]["evidence"]["PROOF_VERIFIED"]["investorEligibility"] = "NOT_EVALUATED"
        self.assertIn("all five Groth16 verdicts VERIFIED for this quote", failed(verify(w)))
        w = world(after=True)
        w["artifacts"]["preflightAuthorizationSha256"] = None
        self.assertIn("the verifier's saved result is the one the ledger recorded", failed(verify(w)))

    def test_not_durable_or_dropped_fails(self):
        w = world(after=True)
        w["artifacts"]["stabilization"] = None
        self.assertIn(DURABILITY, failed(verify(w)))
        w = world(after=True)
        w["artifacts"]["dropped"] = True
        self.assertIn(DURABILITY, failed(verify(w)))

    def test_skipped_state_or_inconsistent_reconciliation_fails(self):
        w = world(after=True)
        events = w["probe"]["orders"][ORDER]["events"]
        w["probe"]["orders"][ORDER]["events"] = [e for e in events if e["to_state"] != "SUBMITTED"]
        self.assertIn("order reached RECEIPT_AVAILABLE through the full forward path, each state once",
                      failed(verify(w)))
        w = world(after=True)
        w["probe"]["orders"][ORDER]["evidence"]["RECONCILED"]["sellerZatAfter"] = 0
        self.assertIn("reconciliation: buyer +units, dealer -units and +payment, exactly the quote", failed(verify(w)))

    def test_duplicate_purchase_fails(self):
        w = world(after=True)
        second = copy.deepcopy(w["probe"]["orders"][ORDER])
        second["id"] = "e" * 32
        w["probe"]["orders"][second["id"]] = second
        w["probe"]["orderIndex"].append({"id": second["id"], "offer_id": OFFER, "quantity": 1, "state": "CANCELLED"})
        names = failed(verify(w))
        self.assertIn("exactly one new order since preflight", names)
        self.assertIn("this buyer holds exactly one order", names)

    def test_block_not_on_main_chain_fails(self):
        w = world(after=True)
        w["probe"]["orders"][ORDER]["evidence"]["INCLUDED"]["blockHash"] = "8" * 64
        self.assertIn("the main chain's block at that height has the receipt's hash and contains the transaction",
                      failed(verify(w)))

    def test_refuses_a_failed_preflight_record(self):
        with self.assertRaises(SystemExit):
            verify(world(after=True), before={"ok": False})


if __name__ == "__main__":
    unittest.main()
