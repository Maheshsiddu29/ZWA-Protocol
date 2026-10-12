"""Local contract and simulated chain tests. No test spends funds."""

from __future__ import annotations

from copy import deepcopy
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest

from .api import Engine, NodeUnavailable, PINNED_GENESIS, RECORDED_HUMAN_ID, RECORDED_ID, SessionError, handler_for, read_session, recorded
from .quote import InvalidQuote, digest, from_terms, validate
from http.server import ThreadingHTTPServer


class FakeRpc:
    def __init__(self, tip=103, block=None, unavailable=False, chain="test", genesis=PINNED_GENESIS):
        self.tip = tip
        self.block = block
        self.unavailable = unavailable
        self.chain = chain
        self.genesis = genesis

    def call(self, method, params=None):
        if self.unavailable:
            raise NodeUnavailable("simulated node outage")
        if method == "getblockchaininfo":
            return {"chain": self.chain, "blocks": self.tip,
                    "consensus": {"nextblock": "88888888"},
                    "upgrades": {"88888888": {"activationheight": 1}}}
        if method == "getblock":
            if params == ["0", 1]:
                return {"hash": self.genesis}
            if self.block is None:
                raise NodeUnavailable("simulated missing block")
            return self.block
        raise AssertionError(method)


def session(quote, receipt=None, outcome=None):
    return {"version": 1, "sessionId": quote["quoteDigest"],
            "source": "LIVE_LOCAL_CLI", "outcome": outcome or ("ACCEPTED" if receipt else "QUOTE_READY"),
            "quote": quote, "receipt": receipt}


def receipt(quote):
    return {"terms_hash": quote["quoteDigest"], "txid": "11" * 32,
            "height": quote["targetHeight"], "block_hash": "22" * 32,
            "tx_bytes": 28060}


class QuoteTests(unittest.TestCase):
    def setUp(self):
        self.quote, _ = recorded()

    def test_cross_language_recorded_vector_and_fee_accounting(self):
        self.assertEqual(self.quote["quoteDigest"],
                         "73403f0c26335be79bdec4213b5119269e02a4e83f4a1f006fd93fb7b9da9271")
        self.assertEqual(self.quote["quantity"], 7)
        self.assertEqual(self.quote["paymentAmount"], 10000)
        self.assertEqual(self.quote["maximumTotalDebit"], 10000)
        self.assertEqual(self.quote["networkFee"], 5000)
        self.assertEqual(self.quote["feePayer"], "DISPOSABLE_REGTEST_SPONSOR")

    def test_quote_mutations_and_noncanonical_encoding(self):
        for field, value in (("quantity", 8), ("paymentAmount", 9999),
                             ("assetId", "11" * 32),
                             ("buyerReceivingCommitment", "33" * 32),
                             ("sellerProceedsCommitment", "44" * 32),
                             ("networkFee", 6000), ("transactionExpiry", 144)):
            with self.subTest(field=field):
                changed = deepcopy(self.quote)
                changed[field] = value
                with self.assertRaises(InvalidQuote):
                    validate(changed)
        for field, value in (("assetId", self.quote["assetId"].upper()),
                             ("quantity", "7"), ("quantity", True),
                             ("assetId", "00" * 32), ("network", "mainnet"),
                             ("maximumTotalDebit", 15000),
                             ("sponsorInput", 625000001),
                             ("sponsorOutpointIndex", 1),
                             ("sponsorChange", 624994000)):
            with self.subTest(field=field, value=value):
                changed = deepcopy(self.quote)
                changed[field] = value
                with self.assertRaises(InvalidQuote):
                    digest({k: v for k, v in changed.items() if k != "quoteDigest"})

    def test_terms_recipient_mutation_changes_digest(self):
        fixture = json.loads((Path(__file__).parent / "recorded-phase35f.json").read_text())
        original = from_terms(fixture["terms"])
        mutated = deepcopy(fixture["terms"])
        mutated["buyer_receive_address"] = fixture["terms"]["seller_proceeds_address"]
        self.assertNotEqual(from_terms(mutated)["quoteDigest"], original["quoteDigest"])


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.quote, _ = recorded()
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "session.json"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, value):
        self.path.write_text(json.dumps(value))

    def test_missing_corrupt_duplicate_and_receipt_mismatch(self):
        self.assertIsNone(read_session(self.path))
        self.path.write_text("{")
        with self.assertRaises(SessionError):
            read_session(self.path)
        self.write(session(self.quote))
        self.assertIsNone(read_session(self.path)["receipt"])
        duplicate = session(self.quote)
        duplicate["extraSession"] = session(self.quote)
        self.write(duplicate)
        with self.assertRaises(SessionError):
            read_session(self.path)
        bad = receipt(self.quote)
        bad["terms_hash"] = "33" * 32
        self.write(session(self.quote, bad))
        with self.assertRaises(SessionError):
            read_session(self.path)

    def test_stale_quote_node_unavailable_and_restart(self):
        self.write(session(self.quote))
        engine = Engine(self.path, FakeRpc(tip=102))
        view, code = engine.route(f"/v1/checkout/sessions/{self.quote['quoteDigest']}")
        self.assertEqual((view["status"], code), ("QUOTE_READY", 200))
        view, _ = Engine(self.path, FakeRpc(tip=103)).route(f"/v1/checkout/sessions/{self.quote['quoteDigest']}")
        self.assertEqual(view["status"], "EXPIRED_LOCALLY")
        view, code = Engine(self.path, FakeRpc(unavailable=True)).route(f"/v1/checkout/sessions/{self.quote['quoteDigest']}")
        self.assertEqual((view["status"], code), (None, 503))
        view, code = Engine(self.path, FakeRpc(chain="main")).route(f"/v1/checkout/sessions/{self.quote['quoteDigest']}")
        self.assertEqual((view["status"], code, view["error"]["code"]),
                         (None, 503, "WRONG_NETWORK"))
        view, code = Engine(self.path, FakeRpc(genesis="00" * 32)).route(f"/v1/checkout/sessions/{self.quote['quoteDigest']}")
        self.assertEqual((view["status"], code), (None, 503))
        self.assertEqual(Engine(self.path, FakeRpc()).route(f"/v1/checkout/sessions/{self.quote['quoteDigest']}/receipt")[1], 404)
        self.write(session(self.quote, outcome="FAILED"))
        view, _ = Engine(self.path, FakeRpc()).route(f"/v1/checkout/sessions/{self.quote['quoteDigest']}")
        self.assertEqual(view["status"], "FAILED")
        self.write(session(self.quote, outcome="UNCERTAIN"))
        view, code = Engine(self.path, FakeRpc()).route(f"/v1/checkout/sessions/{self.quote['quoteDigest']}")
        self.assertEqual((view["status"], code, view["error"]["code"]),
                         (None, 503, "EXECUTION_UNCERTAIN"))

    def test_accepted_tip_change_and_simulated_reorg(self):
        rec = receipt(self.quote)
        self.write(session(self.quote, rec))
        block = {"hash": rec["block_hash"], "tx": [rec["txid"]], "confirmations": 1}
        path = f"/v1/checkout/sessions/{self.quote['quoteDigest']}"
        view, code = Engine(self.path, FakeRpc(tip=103, block=block)).route(path)
        self.assertEqual((code, view["status"], view["receipt"]["finality"]),
                         (200, "PENDING_FINALITY", "PENDING_CONFIRMATIONS_1_OF_3"))
        later = dict(block, confirmations=3)
        view, _ = Engine(self.path, FakeRpc(tip=105, block=later)).route(path)
        self.assertEqual(view["status"], "FINALIZED")
        view, _ = Engine(self.path, FakeRpc(tip=105, block=dict(later, hash="ff" * 32))).route(path)
        self.assertEqual(view["status"], "INVALIDATED_BY_REORG")
        self.assertEqual(view["receipt"]["purchaseSettlement"], "INVALIDATED_BY_REORG")
        view, code = Engine(self.path, FakeRpc(tip=105, block=None)).route(path)
        self.assertEqual((code, view["status"]), (503, None))
        inconsistent = dict(block, confirmations=2)
        view, code = Engine(self.path, FakeRpc(tip=103, block=inconsistent)).route(path)
        self.assertEqual((code, view["status"]), (503, None))

    def test_historical_is_never_live_session(self):
        engine = Engine(None, FakeRpc(unavailable=True))
        for session_id, txid in ((RECORDED_ID, "99d9982716cbbeb1937691fef76511f0485e527c7cce4961e4510c1b9f21fb25"),
                                 (RECORDED_HUMAN_ID, "aaeb15b837d5b44fc6aaf6d88b36ac9b802cd53e6cd2cf292ef8450db6826dfd")):
            with self.subTest(session_id=session_id):
                view, code = engine.route(f"/v1/checkout/sessions/{session_id}")
                self.assertEqual((code, view["status"], view["receipt"]["finality"]),
                                 (200, "RECORDED_REGTEST_EVIDENCE", "UNKNOWN"))
                self.assertEqual(view["receipt"]["sessionId"], session_id)
                self.assertEqual(view["receipt"]["txid"], txid)
                self.assertFalse(view["actionable"])
                receipt_view, receipt_code = engine.route(f"/v1/checkout/sessions/{session_id}/receipt")
                self.assertEqual((receipt_code, receipt_view), (200, view["receipt"]))


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(Engine(None, FakeRpc()), 0))
        # The handler requires the actual Host port. Rebuild it before serving.
        port = self.server.server_address[1]
        self.server.RequestHandlerClass = handler_for(Engine(None, FakeRpc()), port)
        self.port = port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method="GET", path="/v1/checkout/info", headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port)
        connection.request(method, path, body=body,
                           headers={"Host": f"127.0.0.1:{self.port}", **(headers or {})})
        response = connection.getresponse()
        data = json.loads(response.read())
        status = response.status
        cors = response.getheader("Access-Control-Allow-Origin")
        connection.close()
        return status, data, cors

    def test_browser_api_security(self):
        self.assertEqual(self.request()[0], 200)
        self.assertEqual(self.request(headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request(headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request(headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.request(method="POST", body=b"{}")[0], 400)
        self.assertEqual(self.request(method="POST")[0], 405)
        self.assertEqual(self.request(path="/v1/checkout/info?x=1")[0], 400)
        self.assertIsNone(self.request()[2])
        with socket.create_connection(("127.0.0.1", self.port), timeout=2) as raw:
            raw.sendall((f"GET /v1/checkout/info HTTP/1.1\r\n"
                         f"Host: 127.0.0.1:{self.port}\r\n"
                         "Host: evil.example\r\n\r\n").encode())
            self.assertIn(b" 403 ", raw.recv(256))


if __name__ == "__main__":
    unittest.main()
