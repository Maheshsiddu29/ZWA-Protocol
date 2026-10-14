"""Regression tests for durable chain identity and stale-balance fail closure."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from exchange import persistent_chain as chain


class ChainIdentityTests(unittest.TestCase):
    def test_checkpoint_never_anchors_volatile_zebra_tip(self):
        with tempfile.TemporaryDirectory(prefix="zwa4e-test-", dir="/private/tmp") as raw:
            root = Path(raw)
            identity = {"version":1,"image":chain.IMAGE,
                        "configSha256":chain.CONFIG_SHA256,
                        "genesisHash":"0"*64,"anchorHeight":113,
                        "anchorHash":"a"*64,"lastStartState":"CHAIN_RESTARTED"}
            (root/"chain-identity.json").write_text(json.dumps(identity))
            def fake_rpc(_port,method,params=None,host="127.0.0.1"):
                if method == "getblockcount": return 2215
                if method == "getblockhash":
                    return "a"*64 if params == [113] else "b"*64
                raise AssertionError(method)
            with (patch.object(chain,"_paths",return_value=(root/"chain",root/"chain/config")),
                  patch.object(chain,"_rpc",side_effect=fake_rpc)):
                result=chain.checkpoint(root,18321)
            self.assertEqual(result["height"],2215)
            self.assertEqual(result["anchorHeight"],2115)
            self.assertEqual(json.loads((root/"chain-identity.json").read_text())["anchorHeight"],2115)

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


AWS_GENESIS = "029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327"


def legacy_local_identity() -> dict:
    """Shape written by start() for the local arm64 docker-managed workflow."""
    return {"version": 1, "image": chain.IMAGE, "configSha256": chain.CONFIG_SHA256,
            "genesisHash": "0" * 64, "anchorHeight": 113, "anchorHash": "a" * 64,
            "lastStartState": "CHAIN_RESTARTED", "lastObservedHeight": 200}


def aws_external_identity() -> dict:
    """Shape of the live AWS identity: adopted externally, arm64 label recorded."""
    return {"version": 1, "image": chain.PINNED_ZEBRA_IMAGES["linux/arm64"],
            "configSha256": chain.CONFIG_SHA256, "genesisHash": AWS_GENESIS,
            "anchorHeight": 335, "anchorHash": "c" * 64, "lastStartState": "CHAIN_ACTIVE",
            "lastObservedHeight": 436, "management": "external"}


def attested_runtime(meta: dict) -> dict:
    return {"schema": 2, "imageId": chain.PINNED_ZEBRA_IMAGES["linux/amd64"], "platform": "linux/amd64",
            "declaredSourceRevision": chain.ZEBRA_SOURCE_REVISION,
            "evidence": "POINT_IN_TIME_HOST_ATTESTATION", "attestedAt": 1_760_000_000,
            "containerId": "b" * 64, "composeProject": "zwa-prov-stage", "recordedImage": meta["image"],
            "verifiedChain": {"genesisHash": meta["genesisHash"], "anchorHeight": meta["anchorHeight"],
                              "anchorHash": meta["anchorHash"], "observedHeight": 436},
            "migratedFromSha256": "d" * 64}


class IdentitySchemaTests(unittest.TestCase):
    def write(self, root: Path, data: dict) -> Path:
        path = root / "chain-identity.json"
        path.write_text(json.dumps(data))
        return path

    def test_pins_cover_both_platforms_and_keep_the_legacy_label(self):
        self.assertEqual(chain.PINNED_ZEBRA_IMAGES, {
            "linux/arm64": "sha256:540a3429ef14ff3c575258255e4310951606d16c86ec5e1c37e285ea95178185",
            "linux/amd64": "sha256:2c3f3dde0230f572911d70b7f74cc8f4587c1a139b0756d904e3b09eba2e32b9"})
        self.assertEqual(chain.IMAGE, chain.PINNED_ZEBRA_IMAGES["linux/arm64"])
        self.assertEqual(chain.RECORDED_IMAGE, chain.IMAGE)

    def test_legacy_local_and_aws_external_identities_remain_readable(self):
        for identity in (legacy_local_identity(), aws_external_identity()):
            with tempfile.TemporaryDirectory(prefix="zwa4e-test-", dir="/private/tmp") as raw:
                self.write(Path(raw), identity)
                self.assertEqual(chain._meta(Path(raw)), identity)

    def test_migrated_identity_is_readable_and_keeps_version_one(self):
        meta = aws_external_identity()
        meta[chain.RUNTIME_KEY] = attested_runtime(meta)
        self.assertEqual(chain.validate_identity(meta)["version"], 1)
        self.assertEqual(chain.validate_runtime(meta)["platform"], "linux/amd64")

    def test_malformed_or_unpinned_runtime_fails_closed(self):
        def mutate(change):
            meta = aws_external_identity()
            meta[chain.RUNTIME_KEY] = attested_runtime(meta)
            change(meta, meta[chain.RUNTIME_KEY])
            return meta
        cases = {
            "unknown image": lambda m, r: r.update(imageId="sha256:" + "e" * 64),
            "arm64 id claimed as amd64": lambda m, r: r.update(imageId=chain.PINNED_ZEBRA_IMAGES["linux/arm64"]),
            "amd64 id claimed as arm64": lambda m, r: r.update(platform="linux/arm64"),
            "unhashable platform": lambda m, r: r.update(platform=["linux/amd64"]),
            "other source": lambda m, r: r.update(declaredSourceRevision="0" * 40),
            "undeclared source key": lambda m, r: r.update(sourceRevision=r.pop("declaredSourceRevision")),
            "label evidence": lambda m, r: r.update(evidence="LABEL"),
            "current-image claim": lambda m, r: r.update(evidence="HOST_ATTESTED"),
            "schema 1 (never deployed)": lambda m, r: r.update(schema=1),
            "boolean schema": lambda m, r: r.update(schema=True),
            "short container id": lambda m, r: r.update(containerId="b" * 12),
            "bad project": lambda m, r: r.update(composeProject="../zwa-demo"),
            "missing project": lambda m, r: r.pop("composeProject"),
            "extra field": lambda m, r: r.update(note="x"),
            "missing field": lambda m, r: r.pop("migratedFromSha256"),
            "rewritten label": lambda m, r: r.update(recordedImage=r["imageId"]),
            "other genesis": lambda m, r: r["verifiedChain"].update(genesisHash="f" * 64),
            "anchor ahead": lambda m, r: r["verifiedChain"].update(anchorHeight=336),
            "other anchor hash": lambda m, r: r["verifiedChain"].update(anchorHash="f" * 64),
            "observed below anchor": lambda m, r: r["verifiedChain"].update(observedHeight=1),
            "docker-local chain": lambda m, r: m.pop("management"),
            "not an object": lambda m, r: m.update({chain.RUNTIME_KEY: "linux/amd64"}),
            "null": lambda m, r: m.update({chain.RUNTIME_KEY: None}),
        }
        for name, change in cases.items():
            with self.subTest(name), self.assertRaises(chain.ChainError):
                chain.validate_identity(mutate(change))

    def test_identity_label_is_not_rewritten_to_another_image(self):
        for image in (chain.PINNED_ZEBRA_IMAGES["linux/amd64"], "sha256:" + "e" * 64):
            meta = aws_external_identity()
            meta["image"] = image
            with self.assertRaises(chain.ChainError):
                chain.validate_identity(meta)
        meta = aws_external_identity()
        meta["management"] = "docker"
        with self.assertRaises(chain.ChainError):
            chain.validate_identity(meta)
        with self.assertRaises(chain.ChainError):
            chain.validate_identity([aws_external_identity()])

    def test_status_reports_label_only_provenance_honestly(self):
        meta = aws_external_identity()
        hashes = {0: AWS_GENESIS, 335: "c" * 64}
        def fake_rpc(_port, method, params=None, host="127.0.0.1"):
            return 436 if method == "getblockcount" else hashes[params[0]]
        with tempfile.TemporaryDirectory(prefix="zwa4e-test-", dir="/private/tmp") as raw:
            root = Path(raw)
            path = self.write(root, meta)
            before = path.read_bytes()
            with (patch.object(chain, "_paths", return_value=(root / "chain", root / "chain/config")),
                  patch.object(chain, "_rpc", side_effect=fake_rpc)):
                result = chain.status(root, "zebra", 18232, host="zebra", managed="external")
                self.assertEqual(result["provenance"], {"recordedImage": chain.RECORDED_IMAGE,
                                                        "evidence": "LABEL_ONLY",
                                                        "currentRuntimeVerified": False,
                                                        "currentRuntime": chain.FRESH_HOST_CHECK})
                self.assertEqual(result["state"], "CHAIN_ACTIVE")
                # An ordinary status check never rewrites the identity.
                self.assertEqual(path.read_bytes(), before)
                meta[chain.RUNTIME_KEY] = attested_runtime(meta)
                self.write(root, meta)
                result = chain.status(root, "zebra", 18232, host="zebra", managed="external")
            provenance = result["provenance"]
            # A recorded attestation is point-in-time evidence, never the current image.
            self.assertEqual(provenance["evidence"], "POINT_IN_TIME_HOST_ATTESTATION")
            self.assertIs(provenance["currentRuntimeVerified"], False)
            self.assertNotIn("currentImage", provenance)
            self.assertIn("zebra-runtime-attest.py verify", provenance["currentRuntime"])
            self.assertEqual(provenance["attestedImage"], chain.PINNED_ZEBRA_IMAGES["linux/amd64"])
            self.assertEqual(provenance["attestedContainerId"], "b" * 64)
            self.assertEqual(provenance["recordedImage"], chain.RECORDED_IMAGE)

    def test_docker_local_status_reports_the_inspected_container(self):
        with tempfile.TemporaryDirectory(prefix="zwa4e-test-", dir="/private/tmp") as raw:
            root = Path(raw)
            self.write(root, legacy_local_identity())
            hashes = {0: "0" * 64, 113: "a" * 64}
            with (patch.object(chain, "_paths", return_value=(root / "chain", root / "chain/config")),
                  patch.object(chain, "_state_exists", return_value=True),
                  patch.object(chain, "_inspect", return_value={"State": {"Running": True}}),
                  patch.object(chain, "_validate_container"),
                  patch.object(chain, "_rpc", side_effect=lambda _p, m, params=None, host="127.0.0.1":
                               200 if m == "getblockcount" else hashes[params[0]])):
                result = chain.status(root, "test", 18320)
            self.assertEqual(result["state"], "CHAIN_RESTARTED")
            self.assertEqual(result["provenance"]["evidence"], "CONTAINER_INSPECTED")
            self.assertIs(result["provenance"]["currentRuntimeVerified"], True)
            self.assertEqual(result["provenance"]["currentImage"], chain.PINNED_ZEBRA_IMAGES["linux/arm64"])


if __name__ == "__main__":
    unittest.main()
