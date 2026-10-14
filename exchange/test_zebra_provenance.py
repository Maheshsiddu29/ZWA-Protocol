"""Adversarial regressions for the explicit Zebra runtime provenance migration.

No Docker, no chain, no wallets: fixture roots under /private/tmp/zwa4e-test-*
and a fake RPC. The protected research root is never opened.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import unittest
from unittest.mock import patch
from urllib.error import URLError

from exchange import persistent_chain as chain
from exchange import zebra_provenance as provenance

NOW = 1_760_000_000
GENESIS = "029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327"
ANCHOR = "3" * 64
AMD64 = chain.PINNED_ZEBRA_IMAGES["linux/amd64"]
ARM64 = chain.PINNED_ZEBRA_IMAGES["linux/arm64"]


def aws_identity() -> dict:
    """The live AWS identity shape: externally adopted, arm64 label recorded."""
    return {"version": 1, "image": ARM64, "configSha256": chain.CONFIG_SHA256, "genesisHash": GENESIS,
            "anchorHeight": 335, "anchorHash": ANCHOR, "lastStartState": "CHAIN_ACTIVE",
            "lastObservedHeight": 436, "management": "external"}


def attestation(**changes) -> dict:
    data = {"schema": provenance.ATTESTATION_SCHEMA, "imageId": AMD64, "platform": "linux/amd64",
            "configuredImage": "qedit/zebra-zsa-swap-pinned:latest", "containerId": "b" * 64,
            "containerImageId": AMD64, "containerRunning": True, "composeProject": "zwa-prov-stage",
            "attestedAt": NOW - 30}
    data.update(changes)
    return data


def legacy_reader_accepts(data: dict) -> bool:
    """The identity check in the engine image deployed at cf7f133 (rollback target)."""
    return not (data.get("version") != 1 or data.get("image") != ARM64
                or data.get("configSha256") != chain.CONFIG_SHA256
                or type(data.get("anchorHeight")) is not int or data["anchorHeight"] < 0
                or not isinstance(data.get("anchorHash"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", data["anchorHash"])
                or not isinstance(data.get("genesisHash"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", data["genesisHash"])
                or data.get("lastStartState") not in ("CHAIN_ACTIVE", "CHAIN_RESTARTED"))


class FakeChain:
    def __init__(self, tip: int = 436, genesis: str = GENESIS, anchor: str = ANCHOR):
        self.tip, self.hashes, self.calls = tip, {0: genesis, 335: anchor}, []

    def rpc(self, method, params=None, *_args, **_kwargs):
        self.calls.append(method)
        if method == "getblockcount":
            return self.tip
        if method == "getblockhash":
            height = params[0]
            if height > self.tip:
                raise chain.ChainError("block height not found")
            return self.hashes.get(height, f"{height:064x}")
        raise AssertionError(method)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = Path("/private/tmp/zwa4e-test-" + secrets.token_hex(6))
        self.root.mkdir(mode=0o700)
        self.addCleanup(shutil.rmtree, self.root, True)
        self.identity = self.root / "chain-identity.json"
        self.write_identity(aws_identity())
        # Ledger, wallet, order and chain-state stand-ins that must never change.
        for relative, content in {"exchange/exchange.sqlite": b"SQLite format 3\x00ledger",
                                  "exchange/orders/o1/order-receipt.json": b'{"txid":"c02b4e6d"}',
                                  "exchange/service-config.json": b'{"version":2}',
                                  "seller/wallet.sqlite": b"dealer wallet",
                                  "demo-wallets/" + "a" * 32 + "/wallet.sqlite": b"judge wallet",
                                  "chain/state/v26/regtest/000001.sst": b"rocksdb"}.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(content)
            path.chmod(0o600)
        self.chain = FakeChain()

    def write_identity(self, data: dict | bytes) -> bytes:
        raw = data if isinstance(data, bytes) else provenance.serialize(data)
        self.identity.write_bytes(raw)
        self.identity.chmod(0o600)
        return raw

    def tree(self) -> dict[str, tuple[str, int]]:
        return {str(path.relative_to(self.root)): (hashlib.sha256(path.read_bytes()).hexdigest(),
                                                   path.stat().st_mode)
                for path in sorted(self.root.rglob("*")) if path.is_file() and not path.is_symlink()}

    def record(self, data=None, *, machine="linux/amd64", **kwargs) -> dict:
        kwargs.setdefault("expect_genesis", GENESIS)
        kwargs.setdefault("expect_anchor_height", 335)
        with patch.object(provenance, "PROTECTED_ROOTS", frozenset({str(self.root) + "-protected"})):
            return provenance.record(self.root, attestation() if data is None else data, rpc=self.chain.rpc,
                                     now=NOW, machine=machine, **kwargs)

    def assert_refused(self, pattern: str = "", **kwargs) -> None:
        before = self.tree()
        with self.assertRaisesRegex(chain.ChainError, pattern):
            self.record(**kwargs)
        self.assertEqual(self.tree(), before, "a refused migration changed the root")


class MigrationSuccessTests(Fixture):
    def test_records_amd64_runtime_and_preserves_everything_else(self):
        original_bytes = self.identity.read_bytes()
        before = self.tree()
        result = self.record()
        self.assertTrue(result["changed"])
        after = self.tree()
        self.assertEqual({k for k in after if after[k] != before.get(k)} | (before.keys() - after.keys()),
                         {"chain-identity.json"})
        data = json.loads(self.identity.read_text())
        runtime = data.pop(chain.RUNTIME_KEY)
        self.assertEqual(data, aws_identity())  # genesis, anchor, heights, label: untouched
        self.assertEqual(runtime, {
            "schema": 1, "imageId": AMD64, "platform": "linux/amd64",
            "sourceRevision": "0aef55cea41b83f17610e6ea708e59995f9e739f", "evidence": "HOST_ATTESTED",
            "attestedAt": NOW - 30, "recordedImage": ARM64,
            "verifiedChain": {"genesisHash": GENESIS, "anchorHeight": 335, "anchorHash": ANCHOR,
                              "observedHeight": 436},
            "migratedFromSha256": hashlib.sha256(original_bytes).hexdigest()})
        self.assertEqual(self.identity.stat().st_mode & 0o777, 0o600)
        self.assertEqual(result["sha256"]["after"], hashlib.sha256(self.identity.read_bytes()).hexdigest())

    def test_migrated_identity_is_read_by_current_and_rollback_readers(self):
        self.record()
        data = json.loads(self.identity.read_text())
        self.assertEqual(chain._meta(self.root), data)
        self.assertTrue(legacy_reader_accepts(data))
        self.assertEqual(data["version"], 1)  # exchange.portfolio_scans requires version 1

    def test_repeated_migration_is_idempotent(self):
        self.record()
        migrated = self.identity.read_bytes()
        again = self.record(attestation(attestedAt=NOW - 5))
        self.assertEqual(again["changed"], False)
        self.assertEqual(again["reason"], "ALREADY_RECORDED")
        self.assertEqual(self.identity.read_bytes(), migrated)

    def test_dry_run_writes_nothing(self):
        before = self.tree()
        result = self.record(dry_run=True)
        self.assertEqual((result["changed"], result["reason"]), (False, "DRY_RUN"))
        self.assertEqual(result["zebraRuntime"]["imageId"], AMD64)
        self.assertEqual(self.tree(), before)

    def test_arm64_host_records_the_arm64_pin(self):
        result = self.record(attestation(imageId=ARM64, containerImageId=ARM64, platform="linux/arm64"),
                             machine="linux/arm64")
        self.assertEqual(result["zebraRuntime"]["platform"], "linux/arm64")


class MigrationRefusalTests(Fixture):
    def test_wrong_image_and_architecture_fail_closed(self):
        unknown = "sha256:" + "e" * 64
        cases = {
            "unknown image": (attestation(imageId=unknown, containerImageId=unknown), "linux/amd64"),
            "arm64 image claimed amd64": (attestation(imageId=ARM64, containerImageId=ARM64), "linux/amd64"),
            "amd64 image claimed arm64": (attestation(platform="linux/arm64"), "linux/arm64"),
            "unknown platform": (attestation(platform="linux/s390x"), "linux/amd64"),
            "attested on another machine": (attestation(), "linux/arm64"),
            "unrecognised engine machine": (attestation(), "linux/riscv64"),
            "container runs other image": (attestation(containerImageId=ARM64), "linux/amd64"),
            "container stopped": (attestation(containerRunning=False), "linux/amd64"),
            "container running as text": (attestation(containerRunning="true"), "linux/amd64"),
        }
        for name, (data, machine) in cases.items():
            with self.subTest(name):
                self.assert_refused(data=data, machine=machine)
        self.assertEqual(self.chain.calls, [])  # refused before any chain read

    def test_malformed_attestation_fails_closed(self):
        extra = attestation()
        extra["note"] = "x"
        missing = attestation()
        missing.pop("containerId")
        cases = {"extra field": extra, "missing field": missing, "not an object": [attestation()],
                 "schema": attestation(schema="zwa-zebra-runtime-attestation/2"),
                 "stale": attestation(attestedAt=NOW - provenance.MAX_ATTESTATION_AGE - 1),
                 "future": attestation(attestedAt=NOW + provenance.MAX_CLOCK_SKEW + 1),
                 "float time": attestation(attestedAt=float(NOW)),
                 "container id": attestation(containerId="b" * 12),
                 "project": attestation(composeProject="../zwa"),
                 "configured image": attestation(configuredImage=""),
                 "unhashable platform": attestation(platform=["linux/amd64"])}
        for name, data in cases.items():
            with self.subTest(name):
                self.assert_refused(data=data)

    def test_malformed_or_unsuitable_identity_fails_closed(self):
        migrated = aws_identity()
        migrated[chain.RUNTIME_KEY] = {"schema": 1, "imageId": AMD64, "platform": "linux/arm64"}
        local = aws_identity()
        local.pop("management")
        reset = aws_identity()
        reset["detectedReset"] = "CHAIN_RESET: genesis hash changed"
        relabelled = aws_identity()
        relabelled["image"] = AMD64
        missing = aws_identity()
        missing.pop("anchorHash")
        cases = {"invalid json": b"{not json", "truncated": provenance.serialize(aws_identity())[:40],
                 "not utf-8": b"\xff\xfe{}", "version 2": {**aws_identity(), "version": 2},
                 "relabelled": relabelled, "missing anchor hash": missing,
                 "malformed runtime": migrated, "docker-local identity": local, "recorded reset": reset,
                 "oversized": b"{" + b" " * 9000 + b"}"}
        for name, data in cases.items():
            with self.subTest(name):
                self.write_identity(data)
                self.assert_refused()

    def test_unsafe_identity_files_fail_closed(self):
        target = self.root / "elsewhere.json"
        target.write_bytes(provenance.serialize(aws_identity()))
        self.identity.unlink()
        self.identity.symlink_to(target)
        self.assert_refused("unsafe")
        self.identity.unlink()
        os.mkfifo(self.identity)
        self.assert_refused("unsafe")
        self.identity.unlink()
        self.identity.mkdir()
        self.assert_refused("unsafe")
        self.identity.rmdir()
        self.assert_refused("missing")

    def test_genesis_and_anchor_mismatch_fail_closed(self):
        cases = {"genesis": FakeChain(genesis="4" * 64), "anchor": FakeChain(anchor="5" * 64),
                 "tip below anchor": FakeChain(tip=300)}
        for name, fake in cases.items():
            with self.subTest(name):
                self.chain = fake
                self.assert_refused("genesis|anchor")
        self.assertNotIn("detectedReset", self.identity.read_text())

    def test_rpc_failures_fail_closed(self):
        class Outage(FakeChain):
            def rpc(self, method, params=None, *_args, **_kwargs):
                raise URLError("offline")

        class Garbage(FakeChain):
            def rpc(self, method, params=None, *_args, **_kwargs):
                return "436" if method == "getblockcount" else GENESIS
        for fake in (Outage(), Garbage()):
            self.chain = fake
            self.assert_refused()

    def test_operator_expectations_must_match(self):
        self.assert_refused("expected", expect_genesis="6" * 64)
        self.assert_refused("expected", expect_anchor_height=336)
        self.assert_refused("expected", expect_anchor_height=0)

    def test_different_recorded_runtime_is_never_overwritten(self):
        self.record()
        self.assert_refused("different Zebra runtime",
                            data=attestation(imageId=ARM64, containerImageId=ARM64, platform="linux/arm64"),
                            machine="linux/arm64")

    def test_protected_and_foreign_roots_are_refused(self):
        with patch.object(provenance, "PROTECTED_ROOTS", frozenset({str(self.root)})), \
             patch.object(provenance, "_read_regular", side_effect=AssertionError("opened")):
            with self.assertRaisesRegex(chain.ChainError, "protected"):
                provenance.record(self.root, attestation(), rpc=self.chain.rpc, expect_genesis=GENESIS,
                                  expect_anchor_height=335, now=NOW, machine="linux/amd64")
        for root in (Path("/private/tmp/other"), Path("/var/lib/zwa/zwa4e-x/../zwa4e-y"), Path("relative")):
            with self.assertRaises(chain.ChainError):
                provenance.record(root, attestation(), rpc=self.chain.rpc, expect_genesis=GENESIS,
                                  expect_anchor_height=335, now=NOW, machine="linux/amd64")


class CrashSafetyTests(Fixture):
    def test_concurrent_identity_change_aborts_without_losing_it(self):
        advanced = dict(aws_identity(), anchorHeight=400, anchorHash="7" * 64)
        real = provenance._write_temp

        def checkpoint_races(*args):
            temp = real(*args)
            chain._atomic_json(self.identity, advanced)  # a writer that was not stopped
            return temp
        with patch.object(provenance, "_write_temp", side_effect=checkpoint_races):
            with self.assertRaisesRegex(chain.ChainError, "changed during migration"):
                self.record()
        self.assertEqual(json.loads(self.identity.read_text()), advanced)
        self.assertEqual(sorted(p.name for p in self.root.iterdir() if p.name.startswith(".chain-identity")), [])

    def test_failures_before_the_rename_leave_original_bytes(self):
        for target in ("replace", "fsync", "fchmod"):
            with self.subTest(target):
                before = self.tree()
                with patch.object(provenance.os, target, side_effect=OSError("disk failure")):
                    with self.assertRaises(OSError):
                        self.record()
                self.assertEqual(self.tree(), before)
                self.assertEqual([p for p in self.root.iterdir() if p.name.endswith(".provenance-tmp")], [])


class RevertTests(Fixture):
    def revert(self, **kwargs) -> dict:
        with patch.object(provenance, "PROTECTED_ROOTS", frozenset()):
            return provenance.revert(self.root, **kwargs)

    def test_revert_restores_exact_pre_migration_bytes(self):
        before = self.tree()
        self.record()
        self.assertEqual(self.revert(dry_run=True)["reason"], "DRY_RUN")
        self.assertNotEqual(self.tree(), before)
        self.assertTrue(self.revert()["changed"])
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.revert(), {"changed": False, "reason": "NOT_RECORDED"})
        self.assertTrue(self.record()["changed"])  # can be recorded again

    def test_revert_refuses_once_the_identity_moved_on(self):
        self.record()
        data = json.loads(self.identity.read_text())
        data["lastObservedHeight"] = 900  # e.g. a later checkpoint or start
        self.write_identity(data)
        before = self.tree()
        with self.assertRaisesRegex(chain.ChainError, "paired pre-migration backup"):
            self.revert()
        self.assertEqual(self.tree(), before)

    def test_revert_refuses_protected_roots(self):
        with patch.object(provenance, "PROTECTED_ROOTS", frozenset({str(self.root)})), \
             patch.object(provenance, "_read_regular", side_effect=AssertionError("opened")):
            with self.assertRaisesRegex(chain.ChainError, "protected"):
                provenance.revert(self.root)


class CompatibilityAfterMigrationTests(Fixture):
    def status(self) -> dict:
        with patch.object(chain, "_rpc", side_effect=lambda _p, m, params=None, host="127.0.0.1":
                          self.chain.rpc(m, params)):
            return chain.status(self.root, "zebra", 18232, host="zebra", managed="external")

    def test_status_checkpoint_and_restart_remain_compatible(self):
        self.record()
        migrated = self.identity.read_bytes()
        result = self.status()
        self.assertEqual((result["state"], result["height"], result["anchorHeight"]), ("CHAIN_ACTIVE", 436, 335))
        self.assertEqual(result["provenance"]["evidence"], "HOST_ATTESTED")
        self.assertEqual(self.identity.read_bytes(), migrated)  # status never rewrites it
        runtime = json.loads(migrated)[chain.RUNTIME_KEY]

        self.chain.tip = 620
        with patch.object(chain, "_rpc", side_effect=lambda _p, m, params=None, host="127.0.0.1":
                          self.chain.rpc(m, params)):
            checkpoint = chain.checkpoint(self.root, 18232, "zebra")
        self.assertEqual(checkpoint["anchorHeight"], 520)
        data = chain._meta(self.root)
        self.assertEqual(data[chain.RUNTIME_KEY], runtime)  # provenance survives checkpoints
        self.assertEqual((data["genesisHash"], data["image"]), (GENESIS, ARM64))

        self.chain.tip = 540  # Zebra restart drops its newest non-finalized blocks
        self.assertEqual(self.status()["state"], "CHAIN_ACTIVE")
        self.chain.tip = 500  # a rollback below the durable anchor is still a reset
        self.assertEqual(self.status()["state"], "CHAIN_RESET")
        self.assertEqual(chain._meta(self.root)[chain.RUNTIME_KEY], runtime)
        self.chain.tip = 620
        self.assert_refused("reset", expect_anchor_height=520)

    def test_local_arm64_docker_workflow_is_unchanged(self):
        local = aws_identity()
        local.pop("management")
        self.write_identity(local)
        (self.root / "chain" / "regtest-config.toml").write_text(chain.CONFIG)
        info = {"Image": chain.IMAGE, "State": {"Running": True},
                "Mounts": [{"Source": str(self.root / "chain"), "Destination": "/data"}],
                "HostConfig": {"PortBindings": {"18232/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18320"}]}}}
        rpc = lambda _p, m, params=None, host="127.0.0.1": self.chain.rpc(m, params)  # noqa: E731
        with patch.object(chain, "_inspect", return_value=info), patch.object(chain, "_rpc", side_effect=rpc):
            started = chain.start(self.root, "zwa-local", 18320)
            status = chain.status(self.root, "zwa-local", 18320)
        self.assertEqual(started["state"], "CHAIN_ACTIVE")
        self.assertEqual(status["provenance"]["evidence"], "CONTAINER_INSPECTED")
        data = json.loads(self.identity.read_text())
        self.assertNotIn(chain.RUNTIME_KEY, data)  # startup never records provenance
        self.assertEqual(data["image"], ARM64)
        self.assertTrue(legacy_reader_accepts(data))
        with patch.object(chain, "_inspect", return_value=dict(info, Image=AMD64)), \
             patch.object(chain, "_rpc", side_effect=rpc):
            with self.assertRaisesRegex(chain.ChainError, "differs from pinned"):
                chain.status(self.root, "zwa-local", 18320)


class CommandLineTests(Fixture):
    def run_main(self, argv: list[str], stdin: bytes = b"") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        stream = io.TextIOWrapper(io.BytesIO(stdin))
        code = 0
        with patch.object(provenance.sys, "stdin", stream), redirect_stdout(out), redirect_stderr(err), \
             patch.object(provenance, "_rpc", side_effect=lambda _p, m, params=None, host=None:
                          self.chain.rpc(m, params)), \
             patch.object(provenance, "machine_platform", return_value="linux/amd64"), \
             patch.object(provenance.time, "time", return_value=NOW):
            try:
                provenance.main(argv)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 2
        return code, out.getvalue(), err.getvalue()

    def test_record_and_show(self):
        args = ["record", "--root", str(self.root), "--rpc-host", "zebra", "--expect-genesis", GENESIS,
                "--expect-anchor-height", "335"]
        code, out, _ = self.run_main(args + ["--dry-run"], json.dumps(attestation()).encode())
        self.assertEqual((code, json.loads(out)["reason"]), (0, "DRY_RUN"))
        self.assertNotIn(chain.RUNTIME_KEY, self.identity.read_text())
        code, out, _ = self.run_main(args, json.dumps(attestation()).encode())
        self.assertEqual((code, json.loads(out)["changed"]), (0, True))
        code, out, _ = self.run_main(["show", "--root", str(self.root)])
        shown = json.loads(out)
        self.assertEqual((code, shown["provenance"]["evidence"], shown["anchorHeight"]), (0, "HOST_ATTESTED", 335))
        with patch.object(provenance, "PROTECTED_ROOTS", frozenset()):
            code, out, _ = self.run_main(["revert", "--root", str(self.root)])
        self.assertEqual((code, json.loads(out)["changed"]), (0, True))
        self.assertNotIn(chain.RUNTIME_KEY, self.identity.read_text())

    def test_refusals_exit_nonzero_without_writing(self):
        before = self.tree()
        args = ["record", "--root", str(self.root), "--rpc-host", "zebra", "--expect-genesis", GENESIS,
                "--expect-anchor-height", "335"]
        for stdin in (b"", b"not json", b"[]", b"{" + b" " * 5000 + b"}",
                      json.dumps(attestation(imageId="sha256:" + "e" * 64)).encode()):
            code, _, err = self.run_main(args, stdin)
            self.assertEqual(code, 1)
            self.assertIn("PROVENANCE_REFUSED", err)
        code, _, _ = self.run_main(args[:-4] + ["--expect-genesis", "XYZ", "--expect-anchor-height", "335"],
                                   json.dumps(attestation()).encode())
        self.assertEqual(code, 2)
        self.assertEqual(self.tree(), before)


if __name__ == "__main__":
    unittest.main()
