"""Regression tests for durable chain identity and stale-balance fail closure."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from exchange import persistent_chain as chain


class ChainIdentityTests(unittest.TestCase):
    def test_anchor_requires_same_chain_prefix(self):
        meta = {"anchorHeight": 113, "anchorHash": "a" * 64}
        chain.verify_anchor(meta, lambda height: "a" * 64, 113)
        with self.assertRaisesRegex(chain.ChainError, "CHAIN_RESET"):
            chain.verify_anchor(meta, lambda height: "a" * 64, 14)
        with self.assertRaisesRegex(chain.ChainError, "CHAIN_RESET"):
            chain.verify_anchor(meta, lambda height: "b" * 64, 113)

    def test_chain_reset_remains_visible_after_node_stops(self):
        with tempfile.TemporaryDirectory(prefix="zwa4e-test-", dir="/private/tmp") as raw:
            root = Path(raw)
            identity = {"version": 1, "image": chain.IMAGE,
                        "configSha256": chain.CONFIG_SHA256,
                        "genesisHash": "0" * 64, "anchorHeight": 113,
                        "anchorHash": "a" * 64, "lastStartState": "CHAIN_ACTIVE"}
            (root / "chain-identity.json").write_text(json.dumps(identity))
            info = {"State": {"Running": True}}
            with (patch.object(chain, "_paths", return_value=(root / "chain", root / "chain" / "config")),
                  patch.object(chain, "_state_exists", return_value=True),
                  patch.object(chain, "_inspect", return_value=info),
                  patch.object(chain, "_validate_container"),
                  patch.object(chain, "_rpc", side_effect=[14, "0" * 64])):
                result = chain.status(root, "test", 18320)
            self.assertEqual(result["state"], "CHAIN_RESET")
            with (patch.object(chain, "_paths", return_value=(root / "chain", root / "chain" / "config")),
                  patch.object(chain, "_state_exists", return_value=True)):
                stopped = chain.status(root, "test", 18320)
            self.assertEqual(stopped["state"], "CHAIN_RESET")

    def test_rpc_outage_does_not_poison_chain_identity(self):
        with tempfile.TemporaryDirectory(prefix="zwa4e-test-", dir="/private/tmp") as raw:
            root = Path(raw)
            identity = {"version": 1, "image": chain.IMAGE,
                        "configSha256": chain.CONFIG_SHA256,
                        "genesisHash": "0" * 64, "anchorHeight": 113,
                        "anchorHash": "a" * 64, "lastStartState": "CHAIN_ACTIVE"}
            path = root / "chain-identity.json"
            path.write_text(json.dumps(identity))
            with (patch.object(chain, "_paths", return_value=(root / "chain", root / "chain" / "config")),
                  patch.object(chain, "_state_exists", return_value=True),
                  patch.object(chain, "_inspect", return_value={"State": {"Running": True}}),
                  patch.object(chain, "_validate_container"),
                  patch.object(chain, "_rpc", side_effect=URLError("offline"))):
                result = chain.status(root, "test", 18320)
            self.assertEqual(result["state"], "CHAIN_STOPPED")
            self.assertNotIn("detectedReset", json.loads(path.read_text()))


if __name__ == "__main__":
    unittest.main()
