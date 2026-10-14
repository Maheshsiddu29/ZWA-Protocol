"""CLI gates, cleanup, evidence handling, schema and report. A fake Docker host; nothing runs."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from benchmark import analysis, cli, evidence as ev, report, safety, workload
from benchmark.correlation import CorrelationStore
from benchmark.docker import Docker
from benchmark.test_safety import rendered
from benchmark.test_workload import Harness
from benchmark.watchdog import CODES
from exchange.persistent_chain import PINNED_ZEBRA_IMAGES

REPOSITORY = Path(__file__).resolve().parents[1]
SCHEMAS = REPOSITORY / "deploy" / "benchmark" / "schema"
ENGINE_ID = "sha256:" + "e" * 64
PROJECT = "zwa-bench-test"


class Done:
    def __init__(self, code, out, err=""):
        self.returncode, self.stdout, self.stderr = code, out.encode(), err.encode()


def compose_subcommand(argv: list[str]) -> list[str]:
    """The compose subcommand and its arguments, from a full ["docker", "compose", ...] argv."""
    rest, i = argv[2:], 0
    while i < len(rest) and rest[i] in ("--project-directory", "-p", "--env-file", "-f", "--profile"):
        i += 2
    return rest[i:]


class FakeDockerHost:
    def __init__(self, params_dir: str, *, platform="linux/amd64", name=PROJECT, volumes=(), labelled=None,
                 zebra_id=None, probes=None):
        self.calls: list[list[str]] = []
        # Answers to the read-only probe, in order (the last repeats); None = the portal cannot be reached.
        self.probes = list(probes or [])
        self.platform = platform
        self.params_dir = params_dir
        self.name = name
        self.volumes = list(volumes)
        self.labelled = labelled if labelled is not None else {v: PROJECT for v in volumes}
        self.zebra_id = zebra_id or PINNED_ZEBRA_IMAGES[platform]

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        args = argv[1:]
        if args[0] == "version":
            arch = {"linux/amd64": "x86_64", "linux/arm64": "aarch64"}[self.platform]
            return Done(0, json.dumps({"Server": {"Version": "27.3.1", "Os": "linux", "Arch": arch}}))
        if args[:2] == ["compose", "version"]:
            return Done(0, "2.29.7\n")
        if args[0] == "compose":
            sub = compose_subcommand(argv)
            if sub[0] == "config":
                config = rendered(self.name)
                config["services"]["params"]["volumes"][0]["source"] = self.params_dir
                text = json.dumps(config).replace('"zwa-engine:local"', '"zwa-engine:bench"')  # ZWA_ENGINE_TAG
                return Done(0, text)
            if sub[0] == "exec" and "zwa-bench-probe" in sub:
                answer = self.probes.pop(0) if len(self.probes) > 1 else (self.probes[0] if self.probes else None)
                if answer is None:
                    return Done(1, "", "service \"portal\" is not running")
                return Done(0, json.dumps(answer))
            return Done(0, "")
        if args[:2] == ["image", "inspect"]:
            ident = self.zebra_id if "zebra" in args[2] else ENGINE_ID
            return Done(0, json.dumps([{"Id": ident, "Created": "2026-10-01", "Architecture": "amd64", "Os": "linux"}]))
        if args[:2] == ["volume", "ls"]:
            if "--filter" in args:
                return Done(0, "\n".join(v for v in self.volumes if self.labelled.get(v) == PROJECT))
            return Done(0, "\n".join(self.volumes))
        if args[0] in ("ps",) or args[:2] == ["network", "ls"]:
            return Done(0, "")
        if args[:2] == ["volume", "inspect"]:
            labels = {"com.docker.compose.project": self.labelled.get(args[2])}
            return Done(0, json.dumps([{"Name": args[2], "Labels": labels}]))
        if args[:2] == ["volume", "rm"]:
            self.volumes.remove(args[2])
            return Done(0, args[2])
        if args[0] == "info":
            return Done(0, "/var/lib/docker\n")
        raise AssertionError(f"unexpected docker call {argv}")

    def compose_calls(self) -> list[list[str]]:
        return [compose_subcommand(c) for c in self.calls if c[1] == "compose" and c[2] != "version"]


OFFER = "0f" * 16


def snapshot(orders=(), *, tip=900, live=True, wallets=(), details=None, genesis=None):
    """A read-only probe answer for a benchmark exchange (exchange.portal / engine.PROBE shape)."""
    from benchmark.safety import REGTEST_GENESIS
    genesis = genesis or REGTEST_GENESIS
    released = ("CANCELLED", "EXPIRED", "REJECTED", "EXECUTION_FAILED")
    index = [{"id": oid, "offer_id": OFFER, "quantity": 1, "state": state} for oid, state in orders]
    sold = sum(1 for o in index if o["state"] == "RECEIPT_AVAILABLE")
    reserved = sum(1 for o in index if o["state"] not in released and o["state"] != "RECEIPT_AVAILABLE")
    detail = {}
    for o in index:
        settled = o["state"] == "RECEIPT_AVAILABLE"
        detail[o["id"]] = {"id": o["id"], "state": o["state"], "evidence": {},
                           "artifactMtimeNs": {"stabilization.json": 1} if settled else {},
                           "onChain": {"txInBlock": True, "blockHashMatches": True} if settled else None}
    detail.update(details or {})
    return {"probe": 1, "approvalMode": "CUSTODIAL_DEMO_POLICY", "integrity": "ok",
            "identity": {"genesisHash": genesis},
            "chain": {"live": True, "tip": tip, "tipHash": format(tip, "064x"), "genesisHash": genesis}
            if live else {"live": False, "error": "URLError"},
            "offers": [{"id": OFFER, "instrument_id": "zwa-gold-reference", "asset_base": "a" * 64, "capacity": 900,
                        "reserved": reserved, "sold": sold, "state": "OFFER_CREATED"}],
            "dealerPolicy": "ACTIVE", "dealerScans": [{"asset_base": "a" * 64, "asset_units": 1000 - sold}],
            "settlements": [{"order_id": o["id"], "txid": "e" * 64} for o in index
                            if o["state"] == "RECEIPT_AVAILABLE"],
            "wallets": [{"account_id": f"judge-{i:020d}", "state": state} for i, state in enumerate(wallets)],
            "orderIndex": index, "orders": detail}


def run_cli(argv, docker) -> tuple[int, dict | None, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv, docker)
    text = out.getvalue()
    return code, (json.loads(text) if text.strip() else None), err.getvalue()


class CliCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name).resolve()
        self.workdir = base / "bench"
        self.params = base / "params"
        self.params.mkdir()
        digests = {}
        for name in ("sapling-spend.params", "sapling-output.params"):
            (self.params / name).write_bytes(name.encode())
            digests[name] = hashlib.sha256(name.encode()).hexdigest()
        for target, value in ((cli, "PARAMS_SHA256"),):
            patcher = patch.object(target, value, digests)
            patcher.start()
            self.addCleanup(patcher.stop)
        attest = patch.object(cli.Bench, "attest", lambda self, action, image: {"attested": action})
        attest.start()
        self.addCleanup(attest.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def common(self, project=PROJECT):
        return ["--project", project, "--workdir", str(self.workdir)]

    def plan_args(self, project=PROJECT):
        return ["plan", *self.common(project), "--engine-tag", "bench", "--params-dir", str(self.params),
                "--allow-dirty"]

    def docker(self, host: FakeDockerHost) -> Docker:
        return Docker(runner=host)


class Plan(CliCase):
    def test_protected_project_is_refused_before_any_docker_call(self):
        for project in sorted(safety.PROTECTED_PROJECTS):
            host = FakeDockerHost(str(self.params))
            code, _, err = run_cli(self.plan_args(project), self.docker(host))
            self.assertEqual(code, cli.EXIT_REFUSED)
            self.assertIn("protected", err)
            self.assertEqual(host.calls, [])

    def test_plan_on_a_fresh_project(self):
        host = FakeDockerHost(str(self.params))
        code, out, err = run_cli(self.plan_args(), self.docker(host))
        self.assertEqual(code, 0, err)
        self.assertRegex(out["renderedSha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(out["images"]["zebra"]["id"], PINNED_ZEBRA_IMAGES["linux/amd64"])
        env = (self.workdir / "private" / "bench.env").read_text()
        self.assertEqual(oct((self.workdir / "private" / "bench.env").stat().st_mode & 0o777), "0o600")
        token = re.search(r"ZWA_PORTAL_TOKEN=(\w+)", env).group(1)
        self.assertNotIn(token, json.dumps(out))
        for path in (self.workdir / "evidence").glob("plan-*.json"):
            self.assertNotIn(token, path.read_text())
        # read-only toward Docker: no compose command other than config
        self.assertEqual([c[0] for c in host.compose_calls()], ["config"])

    def test_plan_refuses_existing_volumes(self):
        host = FakeDockerHost(str(self.params), volumes=[f"{PROJECT}_zwa-root"])
        code, _, err = run_cli(self.plan_args(), self.docker(host))
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("new volumes", err)

    def test_plan_refuses_a_rendering_that_resolves_elsewhere(self):
        host = FakeDockerHost(str(self.params), name="zwa-demo")
        code, _, err = run_cli(self.plan_args(), self.docker(host))
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("rendered project name 'zwa-demo' is not 'zwa-bench-test'", err)
        self.assertFalse(any(c[1:3] == ["volume", "ls"] for c in host.calls), "refused before inspecting resources")

    def test_plan_refuses_an_unpinned_zebra_image(self):
        host = FakeDockerHost(str(self.params), zebra_id="sha256:" + "0" * 64)
        code, _, err = run_cli(self.plan_args(), self.docker(host))
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("not the pinned", err)

    def test_plan_pins_the_verified_engine_image(self):
        host = FakeDockerHost(str(self.params))
        code, out, err = run_cli(self.plan_args() + ["--expect-engine-image-id", ENGINE_ID], self.docker(host))
        self.assertEqual(code, 0, err)
        self.assertEqual(out["images"]["engineExpected"], ENGINE_ID)
        for wrong in ("sha256:" + "0" * 64, "latest"):
            code, _, err = run_cli(self.plan_args() + ["--expect-engine-image-id", wrong],
                                   self.docker(FakeDockerHost(str(self.params))))
            self.assertEqual(code, cli.EXIT_REFUSED)
            self.assertRegex(err, "engine image|expect-engine-image-id")

    def test_plan_refuses_bad_parameters(self):
        (self.params / "sapling-spend.params").write_bytes(b"tampered")
        code, _, err = run_cli(self.plan_args(), self.docker(FakeDockerHost(str(self.params))))
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("checksum", err)

    def test_workdir_inside_checkout_is_refused(self):
        argv = ["plan", "--project", PROJECT, "--workdir", str(REPOSITORY / "bench-evidence"), "--engine-tag", "x",
                "--params-dir", str(self.params)]
        host = FakeDockerHost(str(self.params))
        code, _, err = run_cli(argv, self.docker(host))
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertEqual(host.calls, [])

    def test_prepare_refuses_without_the_reviewed_rendering(self):
        host = FakeDockerHost(str(self.params))
        with patch.object(cli.Bench, "host_checks", lambda self, t: []):
            code, _, err = run_cli(["prepare", *self.plan_args()[1:], "--rendered-sha256", "0" * 64], self.docker(host))
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("rendered-sha256", err)
        self.assertEqual([c[0] for c in host.compose_calls()], ["config"], "nothing was created")
        self.assertFalse((self.workdir / "evidence" / "manifest.json").exists())

    def test_prepare_refuses_on_a_host_that_cannot_be_measured(self):
        host = FakeDockerHost(str(self.params))
        with patch.object(cli.Bench, "host_checks", lambda self, t: ["resource sampling needs a Linux Docker host"]):
            code, _, err = run_cli(["prepare", *self.plan_args()[1:], "--rendered-sha256", "0" * 64], self.docker(host))
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("Linux", err)

    def test_prepare_refuses_a_budget_above_the_hard_maximum(self):
        host = FakeDockerHost(str(self.params))
        with patch.object(cli.Bench, "host_checks", lambda self, t: []):
            _, planned, _ = run_cli(self.plan_args(), self.docker(host))
            code, _, err = run_cli(["prepare", *self.plan_args()[1:], "--rendered-sha256", planned["renderedSha256"],
                                    "--max-transactions", str(safety.HARD_MAX_TRANSACTIONS + 1)], self.docker(host))
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("budget", err)


class Prepared(CliCase):
    """A workdir as prepare leaves it, without running prepare."""

    def setUp(self):
        super().setUp()
        work = ev.Workdir(self.workdir)
        work.ensure()
        self.work = work
        token = "t" * 64
        ev.write_new(work.env_file, "".join(f"{k}={v}\n" for k, v in {
            "ZWA_ENGINE_TAG": "bench", "ZWA_ZEBRA_IMAGE": "qedit/zebra-zsa-swap-pinned:latest",
            "ZWA_PARAMS_DIR": str(self.params), "ZWA_PORTAL_TOKEN": token, "ZWA_PUBLIC_ORIGIN": "https://bench.invalid",
            "ZWA_SITE_ADDRESS": "bench.invalid", "ZWA_BIND_ADDRESS": "127.0.0.1"}.items()))
        self.volumes = [f"{PROJECT}_{v}" for v in ("zebra-state", "zwa-params", "zwa-root")]
        manifest = {"manifest": 1, "project": PROJECT, "maxTransactions": 6,
                    "resources": {"volumes": self.volumes, "network": f"{PROJECT}_internal"},
                    "plan": {"images": {"engine": {"id": ENGINE_ID}, "zebra": {"ref": "z", "id": "x"},
                                        "zebraPinned": "x"}}}
        ev.write_new(work.manifest_path, json.dumps(manifest))
        ev.write_new(work.evidence / "prepare-result.json", json.dumps({"status": "PREPARED"}))
        self.manifest_sha = ev.sha256_file(work.manifest_path)

    def run_args(self, level, *extra):
        return ["run", *self.common(), "--level", str(level), *extra]

    def test_run_refuses_an_unprepared_or_foreign_workdir(self):
        host = FakeDockerHost(str(self.params))
        code, _, err = run_cli(["run", "--project", "zwa-bench-other", "--workdir", str(self.workdir), "--level", "1"],
                               self.docker(host))
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("another project", err)
        self.assertEqual(host.calls, [])

    def test_level3_needs_a_reviewed_passing_level2(self):
        host = FakeDockerHost(str(self.params))
        code, _, err = run_cli(self.run_args(3), self.docker(host))
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("reviewed Level 2", err)
        run_dir = self.work.evidence / "runs" / "20261014T000000Z-L2-abcdef"
        run_dir.mkdir()
        summary = run_dir / "summary.json"
        for content, message in (({"level": 2, "result": "STOPPED", "run": {"project": PROJECT}}, "passing Level 2"),
                                 ({"level": 1, "result": "PASS", "run": {"project": PROJECT}}, "passing Level 2"),
                                 ({"level": 2, "result": "PASS", "run": {"project": "zwa-bench-x"}},
                                  "passing Level 2")):
            if summary.exists():
                summary.unlink()
            summary.write_text(json.dumps(content))
            code, _, err = run_cli(self.run_args(3, "--level2-summary", str(summary), "--level2-summary-sha256",
                                                 ev.sha256_file(summary)), self.docker(host))
            self.assertEqual(code, cli.EXIT_REFUSED)
            self.assertIn(message, err)
        code, _, err = run_cli(self.run_args(3, "--level2-summary", str(summary), "--level2-summary-sha256", "0" * 64),
                               self.docker(host))
        self.assertIn("does not match", err)
        outside = Path(self.tmp.name) / "summary.json"
        outside.write_text(json.dumps({"level": 2, "result": "PASS", "run": {"project": PROJECT}}))
        code, _, err = run_cli(self.run_args(3, "--level2-summary", str(outside), "--level2-summary-sha256",
                                             ev.sha256_file(outside)), self.docker(host))
        self.assertIn("inside this workdir", err)
        self.assertEqual(host.calls, [])

    def level2_summary(self, mutate=None, **faults) -> Path:
        with tempfile.TemporaryDirectory() as tmp:
            summary = complete(Harness(2, Path(tmp), **faults).execute(), fixture=False)
        summary["run"]["project"] = PROJECT
        if mutate:
            mutate(summary)
        index = len(list((self.work.evidence / "runs").iterdir()))
        run_dir = self.work.evidence / "runs" / f"20261014T00000{index}Z-L2-abcdef"
        run_dir.mkdir()
        path = run_dir / "summary.json"
        path.write_text(json.dumps(summary))
        return path

    def level3(self, path: Path):
        with patch.object(cli.Bench, "host_checks", lambda self, t: ["sentinel: past the Level 2 gate"]):
            return run_cli(self.run_args(3, "--level2-summary", str(path), "--level2-summary-sha256",
                                         ev.sha256_file(path)), self.docker(FakeDockerHost(str(self.params))))

    def test_a_failed_level2_never_unlocks_level3(self):
        failed = self.level2_summary(fail_at="BUYER_APPROVED")
        code, _, err = self.level3(failed)
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("passing Level 2", err)
        self.assertIn("outcome", err)

    def test_a_summary_that_claims_pass_is_rechecked(self):
        def forge(summary):  # e.g. a summary written by the earlier, lenient harness
            summary["transactions"][1]["outcome"] = "PORTAL_REFUSED"
            summary["result"], summary["acceptance"]["passed"] = "PASS", True
        code, _, err = self.level3(self.level2_summary(forge))
        self.assertIn("P2: outcome PORTAL_REFUSED", err)
        legacy = self.level2_summary(lambda s: [s.pop(k) for k in ("monitoring", "finalProbePresent", "acceptance")])
        code, _, err = self.level3(legacy)
        self.assertIn("passing Level 2", err)
        fixture = self.level2_summary(lambda s: s.update(fixture=True))
        self.assertIn("passing Level 2", self.level3(fixture)[2])

    def test_a_verified_level2_reaches_the_next_gate(self):
        code, _, err = self.level3(self.level2_summary())
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("sentinel: past the Level 2 gate", err)

    def test_budget_counts_every_reserved_run(self):
        for i, (level, count) in enumerate(((1, 1), (2, 2), (2, 2))):
            self.work.reserve_budget(f"20261014T00000{i}Z-L{level}-abcdef", level, count)
        host = FakeDockerHost(str(self.params))
        code, _, err = run_cli(self.run_args(2), self.docker(host))
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("budget exhausted", err)
        self.assertEqual(host.calls, [])

    def test_run_refuses_a_host_that_cannot_be_measured(self):
        host = FakeDockerHost(str(self.params))
        with patch.object(cli.Bench, "host_checks", lambda self, t: ["cgroup v2 is not mounted"]):
            code, _, err = run_cli(self.run_args(1), self.docker(host))
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("cgroup v2", err)
        self.assertEqual(self.work.budget_used(), 0, "a refused run reserves nothing")

    def test_sample_interval_must_be_at_least_one_per_second(self):
        code, _, err = run_cli(self.run_args(0, "--sample-interval", "2"),
                               self.docker(FakeDockerHost(str(self.params))))
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("one sample per second", err)

    def test_stop_keeps_volumes(self):
        host = FakeDockerHost(str(self.params), volumes=self.volumes)
        code, _, err = run_cli(["stop", *self.common(), "--confirm-project", "zwa-bench-tset"], self.docker(host))
        self.assertEqual(code, cli.EXIT_REFUSED)
        code, out, err = run_cli(["stop", *self.common(), "--confirm-project", PROJECT], self.docker(host))
        self.assertEqual(code, 0, err)
        self.assertEqual(host.compose_calls(), [["stop"]])
        self.assertEqual(host.volumes, self.volumes)

    def cleanup_args(self, project=PROJECT, sha=None):
        return ["cleanup", *self.common(), "--confirm-project", project, "--confirm-manifest-sha256",
                sha or self.manifest_sha]

    def test_cleanup_needs_both_confirmations(self):
        host = FakeDockerHost(str(self.params), volumes=self.volumes)
        for argv in (self.cleanup_args(project="zwa-bench-tes"), self.cleanup_args(sha="0" * 64)):
            code, _, _ = run_cli(argv, self.docker(host))
            self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertEqual(host.compose_calls(), [])
        self.assertEqual(host.volumes, self.volumes)

    def test_cleanup_refuses_unknown_volumes_of_the_project(self):
        host = FakeDockerHost(str(self.params), volumes=self.volumes + [f"{PROJECT}_extra"])
        code, _, err = run_cli(self.cleanup_args(), self.docker(host))
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("unexpected volumes", err)
        self.assertEqual(host.compose_calls(), [])

    def test_cleanup_refuses_a_volume_labelled_for_another_project(self):
        labelled = {v: PROJECT for v in self.volumes}
        labelled[self.volumes[0]] = "zwa-demo"
        host = FakeDockerHost(str(self.params), volumes=self.volumes, labelled=labelled)
        code, _, err = run_cli(self.cleanup_args(), self.docker(host))
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("not labelled", err)
        self.assertIn(self.volumes[0], host.volumes)

    def test_cleanup_removes_exactly_the_manifest_resources_and_keeps_evidence(self):
        other = ["zwa-demo_zwa-root", "zwa-prov-stage_zwa-root", "zwa-bench-other_zwa-root"]
        host = FakeDockerHost(str(self.params), volumes=self.volumes + other,
                              labelled={**{v: PROJECT for v in self.volumes}, **{v: v.split("_")[0] for v in other}},
                              probes=[snapshot([("a" * 32, "RECEIPT_AVAILABLE")], wallets=["READY"])])
        before = sorted(p.name for p in self.work.evidence.iterdir())
        with patch.object(cli, "SLEEP", lambda seconds: None):
            code, out, err = run_cli(self.cleanup_args(), self.docker(host))
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(out["removedVolumes"]), sorted(self.volumes))
        self.assertEqual(sorted(host.volumes), sorted(other))
        self.assertEqual([c[0] for c in host.compose_calls()], ["exec", "exec", "down"])
        flat = " ".join(" ".join(c) for c in host.calls)
        for token in (" -v", "--volumes", "prune", "--force"):
            self.assertNotIn(token, flat)
        after = sorted(p.name for p in self.work.evidence.iterdir())
        self.assertTrue(set(before) <= set(after), "evidence is never deleted")


class CleanupQuiescence(Prepared):
    """Review finding 3: cleanup must prove the exchange is at rest and every purchase is resolved."""

    def cleanup(self, probes, *extra):
        host = FakeDockerHost(str(self.params), volumes=self.volumes, probes=probes)
        with patch.object(cli, "SLEEP", lambda seconds: None):
            code, out, err = run_cli(self.cleanup_args() + list(extra), self.docker(host))
        return host, code, out, err

    def assertRefusedUntouched(self, host, code, err, text):
        self.assertEqual(code, cli.EXIT_REFUSED, err)
        self.assertIn(text, err)
        self.assertNotIn("down", [c[0] for c in host.compose_calls()])
        self.assertFalse(any(c[1:3] == ["volume", "rm"] for c in host.calls))
        self.assertEqual(host.volumes, self.volumes)

    def partial_run(self, order_id):
        run_dir = self.work.evidence / "runs" / "20261014T010000Z-L1-abcdef"
        run_dir.mkdir()
        (run_dir / "summary.json").write_text(json.dumps({"result": "PARTIAL", "transactions": [
            {"orderId": order_id, "outcome": "IN_PROGRESS_AT_DRAIN_END"}]}))

    def test_partial_run_then_cleanup_is_refused(self):
        order = "b" * 32
        self.partial_run(order)
        host, code, _, err = self.cleanup([snapshot([(order, "SELLER_APPROVED")])])
        self.assertRefusedUntouched(host, code, err, "not quiescent")
        self.assertIn("SELLER_APPROVED", err)

    def test_every_unresolved_order_state_is_refused(self):
        for state in ("BUYER_REQUESTED", "QUOTE_READY", "BUYER_CONFIRMED", "PROOF_VERIFIED", "TRADE_AUTHORIZED",
                      "BUYER_APPROVED", "SUBMITTED", "INCLUDED", "RECONCILED", "UNCERTAIN"):
            host, code, _, err = self.cleanup([snapshot([("c" * 32, state)])])
            self.assertRefusedUntouched(host, code, err, state)

    def test_unavailable_rpc_is_refused(self):
        host, code, _, err = self.cleanup([snapshot(live=False)])
        self.assertRefusedUntouched(host, code, err, "chain RPC")

    def test_unavailable_exchange_state_is_refused(self):
        host, code, _, err = self.cleanup([None])
        self.assertRefusedUntouched(host, code, err, "state is unavailable")

    def test_an_exchange_still_changing_is_refused(self):
        host, code, _, err = self.cleanup([snapshot(tip=900), snapshot(tip=901)])
        self.assertRefusedUntouched(host, code, err, "changed between")

    def test_wallet_provisioning_in_progress_is_refused(self):
        for state in ("REQUESTED", "CREATED", "FUNDING"):
            host, code, _, err = self.cleanup([snapshot(wallets=[state])])
            self.assertRefusedUntouched(host, code, err, state)

    def test_settlement_without_durability_or_inclusion_is_refused(self):
        order = "d" * 32
        for detail, text in (({"artifactMtimeNs": {}}, "stabilization"),
                             ({"artifactMtimeNs": {"stabilization.json": 1, "dropped.json": 2}}, "dropped"),
                             ({"onChain": {"txInBlock": False, "blockHashMatches": True}}, "not verified")):
            base = snapshot([(order, "RECEIPT_AVAILABLE")])
            base["orders"][order].update(detail)
            host, code, _, err = self.cleanup([base])
            self.assertRefusedUntouched(host, code, err, text)

    def test_an_order_named_by_evidence_but_missing_from_the_ledger_is_refused(self):
        self.partial_run("e" * 32)
        host, code, _, err = self.cleanup([snapshot()])
        self.assertRefusedUntouched(host, code, err, "missing from the ledger")

    def test_an_unresolved_purchase_correlation_is_refused(self):
        store = CorrelationStore(self.work.correlations, "20261014T010000Z-L1-abcdef")
        store.begin("P1", account="judge-00000000000000000000", session="s" * 64, key="bench-" + "0" * 24,
                    offer_id=OFFER, instrument="zwa-gold-reference", quantity=1)
        host, code, _, err = self.cleanup([snapshot()])
        self.assertRefusedUntouched(host, code, err, "unresolved purchase")
        self.assertNotIn("s" * 64, err)
        self.assertEqual(host.compose_calls(), [], "refused before even reading the exchange")

    def test_inconsistent_inventory_is_refused(self):
        bad = snapshot([("f" * 32, "RECEIPT_AVAILABLE")])
        bad["offers"][0]["sold"] = 0
        host, code, _, err = self.cleanup([bad])
        self.assertRefusedUntouched(host, code, err, "sold")

    def test_quiescent_exchange_is_cleaned_with_a_recorded_check(self):
        settled = "a" * 32
        self.partial_run(settled)
        host, code, out, err = self.cleanup([snapshot([(settled, "RECEIPT_AVAILABLE"), ("9" * 32, "EXPIRED")],
                                                      wallets=["READY", "RETIRED"])])
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(out["removedVolumes"]), sorted(self.volumes))
        (check,) = self.work.evidence.glob("cleanup-check-*.json")
        recorded = json.loads(check.read_text())
        self.assertEqual(recorded["result"], "QUIESCENT")
        self.assertEqual(recorded["orders"], {"EXPIRED": 1, "RECEIPT_AVAILABLE": 1})

    def test_stopped_stack_cannot_be_cleaned_until_reconciled(self):
        host, code, _, err = self.cleanup([None])
        self.assertIn("restart", err)

    def test_quiescence_window_is_bounded(self):
        host = FakeDockerHost(str(self.params), volumes=self.volumes, probes=[snapshot()])
        code, _, err = run_cli(self.cleanup_args() + ["--quiescence-seconds", "1"], self.docker(host))
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("quiescence", err)


class FailedPrepareCleanup(CliCase):
    """A project whose prepare never finished: nothing was ever submitted, so no exchange state needs preserving."""

    def setUp(self):
        super().setUp()
        self.work = ev.Workdir(self.workdir)
        self.work.ensure()
        ev.write_new(self.work.env_file, "".join(f"{k}={v}\n" for k, v in {
            "ZWA_ENGINE_TAG": "bench", "ZWA_ZEBRA_IMAGE": "z", "ZWA_PARAMS_DIR": str(self.params),
            "ZWA_PORTAL_TOKEN": "t" * 64, "ZWA_PUBLIC_ORIGIN": "https://bench.invalid",
            "ZWA_SITE_ADDRESS": "bench.invalid", "ZWA_BIND_ADDRESS": "127.0.0.1"}.items()))
        self.volumes = [f"{PROJECT}_{v}" for v in ("zebra-state", "zwa-params", "zwa-root")]
        ev.write_new(self.work.manifest_path, json.dumps({"manifest": 1, "project": PROJECT, "maxTransactions": 6,
                                                          "resources": {"volumes": self.volumes}}))
        self.args = ["cleanup", *self.common(), "--confirm-project", PROJECT, "--confirm-manifest-sha256",
                     ev.sha256_file(self.work.manifest_path), "--prepare-failed"]

    def run_cleanup(self):
        host = FakeDockerHost(str(self.params), volumes=self.volumes)
        return host, *run_cli(self.args, self.docker(host))

    def test_failed_prepare_with_no_submissions_can_be_cleaned_without_a_probe(self):
        host, code, out, err = self.run_cleanup()
        self.assertEqual(code, 0, err)
        self.assertNotIn("exec", [c[0] for c in host.compose_calls()])
        (check,) = self.work.evidence.glob("cleanup-check-*.json")
        self.assertEqual(json.loads(check.read_text())["result"], "PREPARE_FAILED_NO_SUBMISSIONS")

    def test_prepare_failed_is_refused_once_anything_ran(self):
        ev.write_new(self.work.evidence / "prepare-result.json", json.dumps({"status": "PREPARED"}))
        host, code, _, err = self.run_cleanup()
        self.assertEqual(code, cli.EXIT_REFUSED)
        self.assertIn("was prepared", err)
        (self.work.evidence / "prepare-result.json").unlink()
        self.work.reserve_budget("20261014T000000Z-L0-abcdef", 0, 0)
        host, code, _, err = self.run_cleanup()
        self.assertIn("level run", err)
        self.assertEqual(host.volumes, self.volumes)


class RunOrderIds(unittest.TestCase):
    def test_order_ids_survive_a_killed_harness(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            a, b = "a" * 32, "b" * 32
            (run / "events.jsonl").write_text(
                json.dumps({"event": "STATE", "orderId": a}) + "\n" + json.dumps({"event": "STATE", "orderId": a})
                + "\n" + json.dumps({"event": "STATE", "orderId": b}) + "\n" + '{"event": "STATE", "ord')
            self.assertEqual(cli.run_order_ids(run), [a, b])
            (run / "run-error.json").write_text(json.dumps({"transactions": [{"orderId": b}, {"orderId": None}]}))
            self.assertEqual(cli.run_order_ids(run), [b])
            (run / "summary.json").write_text(json.dumps({"transactions": [{"orderId": a}]}))
            self.assertEqual(cli.run_order_ids(run), [a])


class Evidence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_files_are_created_once(self):
        ev.write_new(self.dir / "a.json", "{}")
        with self.assertRaises(FileExistsError):
            ev.write_new(self.dir / "a.json", "{}")
        stream = ev.JsonLines(self.dir / "s.jsonl", ev.Redactor())
        stream.close()
        with self.assertRaises(FileExistsError):
            ev.JsonLines(self.dir / "s.jsonl", ev.Redactor())
        os.symlink(self.dir / "a.json", self.dir / "link.json")
        with self.assertRaises(OSError):
            ev.write_new(self.dir / "link.json", "{}")

    def test_redaction_by_key_and_value(self):
        redactor = ev.Redactor()
        session = redactor.register("s" * 64)
        with self.assertRaises(ev.EvidenceError):
            redactor.register("short")
        data = {"sessionHash": "anything", "portalToken": "x", "clientHash": "y", "seed": "z", "password": "p",
                "nested": [{"note": f"session {session} leaked"}], "txid": "ab" * 32, "quoteDigest": "cd" * 32,
                "sessionOutcome": "ACCEPTED", "exactTradeAuthorization": "VERIFIED",
                "text": "secret-extended-key-regtest1" + "q" * 60}
        out = redactor(data)
        for key in ("sessionHash", "portalToken", "clientHash", "seed", "password"):
            self.assertEqual(out[key], ev.REDACTED, key)
        self.assertEqual(out["nested"][0]["note"], f"session {ev.REDACTED} leaked")
        self.assertEqual(out["txid"], "ab" * 32, "public identifiers are kept")
        self.assertEqual(out["quoteDigest"], "cd" * 32)
        self.assertEqual(out["sessionOutcome"], "ACCEPTED")
        self.assertEqual(out["exactTradeAuthorization"], "VERIFIED")
        self.assertEqual(out["text"], ev.REDACTED)

    def test_jsonl_writes_are_redacted(self):
        redactor = ev.Redactor()
        secret = redactor.register("k" * 40)
        stream = ev.JsonLines(self.dir / "e.jsonl", redactor)
        stream.write({"detail": f"error containing {secret}", "token": secret})
        stream.close()
        self.assertNotIn(secret, (self.dir / "e.jsonl").read_text())
        self.assertEqual(ev.scan(self.dir, redactor.values), [])

    def test_scan_finds_leaks(self):
        (self.dir / "a.json").write_text('{"x": "' + "z" * 64 + '"}')
        (self.dir / "b.md").write_text("-----BEGIN EC PRIVATE KEY-----")
        (self.dir / "c.json").write_text('{"mnemonic": "abandon"}')
        findings = ev.scan(self.dir, {"z" * 64})
        self.assertEqual(len(findings), 3, findings)


class Validator:
    """The subset of JSON Schema 2020-12 that the benchmark schemas use."""

    TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}

    def __init__(self, schema):
        self.root = schema

    def type_ok(self, value, name):
        if name == "null":
            return value is None
        if name == "integer":
            return type(value) is int
        if name == "number":
            return type(value) in (int, float)
        return type(value) is self.TYPES[name]

    def errors(self, value, schema, path="$"):
        if "$ref" in schema:
            return self.errors(value, self.root["$defs"][schema["$ref"].split("/")[-1]], path)
        out = []
        if "oneOf" in schema:
            matching = [s for s in schema["oneOf"] if not self.errors(value, s, path)]
            if len(matching) != 1:
                out.append(f"{path}: matches {len(matching)} oneOf branches")
            return out
        if "const" in schema and value != schema["const"]:
            out.append(f"{path}: {value!r} != const {schema['const']!r}")
        if "enum" in schema and value not in schema["enum"]:
            out.append(f"{path}: {value!r} not in enum")
        if "type" in schema:
            names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
            if not any(self.type_ok(value, n) for n in names):
                return out + [f"{path}: {type(value).__name__} is not {names}"]
        if isinstance(value, dict):
            for key in schema.get("required", []):
                if key not in value:
                    out.append(f"{path}: missing {key}")
            props = schema.get("properties", {})
            for key, item in value.items():
                if key in props:
                    out += self.errors(item, props[key], f"{path}.{key}")
                elif schema.get("additionalProperties") is False:
                    out.append(f"{path}: unexpected {key}")
                elif isinstance(schema.get("additionalProperties"), dict):
                    out += self.errors(item, schema["additionalProperties"], f"{path}.{key}")
        if isinstance(value, list):
            if "maxItems" in schema and len(value) > schema["maxItems"]:
                out.append(f"{path}: too many items")
            for i, item in enumerate(value):
                if "items" in schema:
                    out += self.errors(item, schema["items"], f"{path}[{i}]")
        if type(value) in (int, float):
            if "minimum" in schema and value < schema["minimum"]:
                out.append(f"{path}: below minimum")
            if "maximum" in schema and value > schema["maximum"]:
                out.append(f"{path}: above maximum")
        if isinstance(value, str):
            if "pattern" in schema and not re.search(schema["pattern"], value):
                out.append(f"{path}: does not match {schema['pattern']}")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                out.append(f"{path}: too long")
        return out


def complete(summary: dict, *, fixture: bool) -> dict:
    """Add the fields cli.run adds around LevelRun's summary."""
    return {**summary, "schema": "zwa-bench-summary/1", "fixture": fixture,
            "run": {"runId": "20261014T120000Z-L2-a1b2c3", "project": PROJECT, "startedAt": "2026-10-14T12:00:00Z",
                    "endedAt": "2026-10-14T12:10:00Z", "sampleInterval": 1.0, "samplesFile": "samples.jsonl",
                    "eventsFile": "events.jsonl"},
            "provenance": {"sourceRevision": "f" * 40, "sourceDirty": False, "txToolRevision": "1" * 40,
                           "engineImageId": ENGINE_ID, "zebraImageId": PINNED_ZEBRA_IMAGES["linux/amd64"],
                           "zebraPinned": PINNED_ZEBRA_IMAGES["linux/amd64"], "platform": "linux/amd64",
                           "renderedSha256": "a" * 64, "zebraAttestation": {}},
            "thresholds": __import__("benchmark.watchdog", fromlist=["Thresholds"]).Thresholds().as_dict(),
            "workload": {"level": summary["level"], "concurrency": summary["concurrency"],
                         "purchases": [{"instrument": t["instrument"], "quantity": t["quantity"]}
                                       for t in summary["transactions"]], "waitDurability": True}}


class Schema(unittest.TestCase):
    def setUp(self):
        self.summary_schema = json.loads((SCHEMAS / "summary.schema.json").read_text())
        self.sample_schema = json.loads((SCHEMAS / "sample.schema.json").read_text())

    def test_schema_enums_match_the_code(self):
        defs = self.summary_schema["$defs"]
        self.assertEqual(set(defs["reason"]["properties"]["code"]["enum"]), set(CODES))
        self.assertEqual(defs["transaction"]["properties"]["outcome"]["enum"], list(workload.OUTCOMES))
        self.assertEqual(defs["transaction"]["properties"]["reconciliation"]["properties"]["class"]["enum"],
                         list(analysis.RECONCILE_CLASSES))
        self.assertEqual(self.summary_schema["properties"]["result"]["enum"], list(workload.RESULTS))
        self.assertEqual(set(defs["transaction"]["properties"]["instrument"]["enum"]), set(workload.INSTRUMENTS))

    def test_summaries_from_every_kind_of_run_validate(self):
        for level, faults in ((0, {}), (1, {}), (2, {}), (2, {"fail_at": "BUYER_APPROVED"}),
                              (1, {"stall_at": "SELLER_APPROVED"}), (1, {"duplicate_bug": True})):
            with tempfile.TemporaryDirectory() as tmp:
                summary = complete(Harness(level, Path(tmp), **faults).execute(), fixture=True)
            errors = Validator(self.summary_schema).errors(json.loads(json.dumps(summary)), self.summary_schema)
            self.assertEqual(errors, [], (level, faults))

    def test_an_invalid_summary_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = complete(Harness(1, Path(tmp)).execute(), fixture=True)
        summary["transactions"][0]["outcome"] = "LOOKS_FINE"
        summary["level"] = 4
        errors = Validator(self.summary_schema).errors(summary, self.summary_schema)
        self.assertTrue(any("outcome" in e for e in errors) and any("level" in e for e in errors), errors)

    def test_real_samples_validate(self):
        from benchmark import metrics
        from benchmark.test_metrics import Tree
        with tempfile.TemporaryDirectory() as tmp:
            tree = Tree(Path(tmp))
            directory = tree.container("abc", 100)
            sampler = metrics.ContainerSampler("order-service", directory, 100, proc=tree.proc)
            item = sampler.sample()
            item["state"] = {"status": "running"}
            record = {"t": 1.0, "index": 0, "host": metrics.HostSampler(tree.proc).sample(), "containers": [item]}
        validator = Validator(self.sample_schema)
        self.assertEqual(validator.errors(json.loads(json.dumps(record)), self.sample_schema), [])
        self.assertEqual(validator.errors({"t": 1.0, "index": 3, "error": "x"}, self.sample_schema), [])
        record["containers"][0]["processes"][0]["cmdline"] = "leak"
        self.assertNotEqual(validator.errors(record, self.sample_schema), [])


class Report(unittest.TestCase):
    def test_fixture_banner_and_limitations(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = complete(Harness(2, Path(tmp)).execute(), fixture=True)
        text = report.render(summary)
        self.assertIn("Test fixture, not a measurement", text)
        self.assertIn("Not measured or approximate", text)
        self.assertIn("| groth16ProofGeneration |", text)
        measured = report.render({**summary, "fixture": False})
        self.assertNotIn("Test fixture", measured)

    def test_committed_sample_report_is_the_rendered_fixture(self):
        fixture = json.loads((REPOSITORY / "benchmark/fixtures/sample-summary.json").read_text())
        self.assertIs(fixture["fixture"], True)
        self.assertEqual(Validator(json.loads((SCHEMAS / "summary.schema.json").read_text())).errors(
            fixture, json.loads((SCHEMAS / "summary.schema.json").read_text())), [])
        self.assertEqual((REPOSITORY / "docs/benchmark-sample-report.md").read_text(), report.render(fixture))


if __name__ == "__main__":
    unittest.main()
