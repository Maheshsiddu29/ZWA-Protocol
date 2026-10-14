"""Regressions for deploy/scripts/provenance-stage.py (the AWS staging driver).

No Docker and no network. A fake Docker host answers the driver's commands:
"restoring" really unpacks the .tar.gz fixtures into a /private/tmp/zwa4e-test-*
root, and engine one-shots run the real exchange.zebra_provenance CLI and the
driver's own embedded programs against it with a fake chain.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tarfile
import unittest
from unittest.mock import patch

from exchange import persistent_chain as chain
from exchange import zebra_provenance as provenance

REPOSITORY = Path(__file__).resolve().parents[1]
GENESIS = "029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327"
ANCHOR = "3" * 64
SETTLED = "c02b4e6da7e5deee80eeafa56d2334d5bc31a2f074f06f88ffa1966110da99c6"
AMD64 = chain.PINNED_ZEBRA_IMAGES["linux/amd64"]
ARM64 = chain.PINNED_ZEBRA_IMAGES["linux/arm64"]
AMD64_ENTRYPOINT = ["/usr/local/bin/zebrad", "-c", "/config/regtest-config.toml"]
CONTAINER = "e" * 64


def load_stage():
    spec = importlib.util.spec_from_file_location("provenance_stage", REPOSITORY / "deploy/scripts/provenance-stage.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


stage_module = load_stage()


def identity() -> dict:
    return {"version": 1, "image": ARM64, "configSha256": chain.CONFIG_SHA256, "genesisHash": GENESIS,
            "anchorHeight": 335, "anchorHash": ANCHOR, "lastStartState": "CHAIN_ACTIVE",
            "lastObservedHeight": 436, "management": "external"}


def add(archive: tarfile.TarFile, name: str, *, kind: str = "f", data: bytes = b"", uid: int = 10001,
        mode: int = 0o600, link: str = "") -> None:
    info = tarfile.TarInfo(name)
    info.uid = info.gid = uid
    info.mode = mode
    if kind == "d":
        info.type = tarfile.DIRTYPE
    elif kind == "l":
        info.type, info.linkname = tarfile.SYMTYPE, link
    elif kind == "c":
        info.type = tarfile.CHRTYPE
    else:
        info.size = len(data)
    archive.addfile(info, io.BytesIO(data) if kind == "f" else None)


class FakeHost:
    """The Docker CLI as the driver uses it, plus the pieces of a running stack."""

    def __init__(self, test: "StageFixture"):
        self.t = test
        self.calls: list[list[str]] = []
        self.volumes: dict[str, dict] = {}
        self.running = False
        self.host_arch = "x86_64"
        self.zebra_image = {"Id": AMD64, "Os": "linux", "Architecture": "amd64"}
        self.container_entrypoint = AMD64_ENTRYPOINT
        self.listing_hook = None
        self.record_hook = None

    def __call__(self, argv, input=None, capture_output=True, env=None, timeout=None):
        self.calls.append(list(argv))
        prefix = 3 if argv[:3] == ["sudo", "-n", "docker"] else 1
        code, out, err = self.handle(argv[prefix:], input)
        return subprocess.CompletedProcess(argv, code, out.encode(), err.encode())

    def handle(self, args, stdin):
        t = self.t
        if args[0] == "compose":
            return self.compose(args, stdin)
        if args[:1] == ["info"]:
            return 0, json.dumps({"OSType": "linux", "Architecture": self.host_arch}), ""
        if args[:2] == ["image", "inspect"]:
            ref = args[-1]
            if ref == t.zebra_tag:
                return 0, json.dumps([self.zebra_image]), ""
            if ref == "zwa-engine:provenance-test":
                return 0, json.dumps([{"Id": "sha256:" + "a" * 64, "Os": "linux", "Architecture": "amd64"}]), ""
            return 1, "", f"Error: No such image: {ref}"
        if args[:2] == ["volume", "inspect"]:
            return (0, "[]", "") if args[2] in self.volumes else (1, "", f"get {args[2]}: no such volume")
        if args[:2] == ["volume", "ls"]:
            return 0, "\n".join(self.volumes), ""
        if args[:2] == ["volume", "create"]:
            self.volumes[args[-1]] = {}
            return 0, args[-1], ""
        if args[:2] == ["ps", "-a"]:
            if "{{.ID}}" in args and self.running and "label=com.docker.compose.project=zwa-prov-stage" in args:
                return 0, CONTAINER + "\n", ""
            return 0, "", ""
        if args[:3] == ["inspect", "--type", "container"]:
            return 0, json.dumps([{"Id": CONTAINER, "Image": AMD64, "State": {"Running": self.running},
                                   "Config": {"Entrypoint": self.container_entrypoint,
                                              "Labels": {"com.docker.compose.project": "zwa-prov-stage",
                                                         "com.docker.compose.service": "zebra"}}}]), ""
        if args[0] == "run":
            return self.run(args)
        raise AssertionError(args)

    def run(self, args):
        entry = args[args.index("--entrypoint") + 1]
        if entry == "sh":  # engine code listing
            files = sorted(p for p in (REPOSITORY / "exchange").glob("*.py") if not p.name.startswith("test_"))
            return 0, "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  exchange/{p.name}\n"
                              for p in files), ""
        volume = next(a.split(":")[0] for a in args if a.endswith(":/dst") or a.endswith(":/dst:ro"))
        short = volume.split("_", 1)[1]
        if entry == "tar":
            backup = next(a[:-len(":/backup:ro")] for a in args if a.endswith(":/backup:ro"))
            archive = Path(backup) / args[args.index("-xzf") + 1].removeprefix("/backup/")
            manifest, _ = stage_module.inspect_archive(archive, short, self.t.cfg)
            self.volumes[volume] = manifest
            if short == "zwa-root":
                with tarfile.open(archive, "r:gz") as tar:
                    for member in tar:
                        name = stage_module._member_path(member.name)
                        if not name.startswith("zwa4e-demo/"):
                            continue
                        target = self.t.root / name.removeprefix("zwa4e-demo/")
                        if member.isdir():
                            target.mkdir(parents=True, exist_ok=True)
                        elif member.isreg():
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(tar.extractfile(member).read())
                            target.chmod(member.mode)
            return 0, "", ""
        if entry == "find":
            lines = []
            for path, (kind, uid, gid, mode, size) in self.volumes[volume].items():
                kind = "f" if kind == "h" else kind
                lines.append([path, kind, uid, gid, 0o777 if kind == "l" else mode,
                              size if kind == "f" else 4096])
            if self.listing_hook:
                self.listing_hook(short, lines)
            return 0, "".join("\t".join([p, k, str(u), str(g), format(m, "o"), str(s)]) + "\n"
                              for p, k, u, g, m, s in lines), ""
        raise AssertionError(args)

    def compose(self, args, stdin):
        t = self.t
        project = args[args.index("-p") + 1]
        files = [args[i + 1] for i, a in enumerate(args) if a == "-f"]
        assert files == [str(t.cfg.base), str(t.cfg.override)], files
        rest = args[args.index(str(t.cfg.override)) + 1:]
        if rest == ["config", "--format", "json"]:
            rendered = json.loads(json.dumps(t.rendered).replace("zwa-prov-stage", project))
            return 0, json.dumps(rendered, sort_keys=True), ""
        if rest == ["up", "-d", "--no-deps", "zebra"]:
            self.running = True
            return 0, "", ""
        if rest == ["down"]:
            self.running = False
            return 0, "", ""
        assert rest[:7] == ["run", "--rm", "--no-deps", "-T", "--entrypoint", "python3", "portal"], rest
        program = rest[7:]
        if program[:2] == ["-m", "exchange.zebra_provenance"]:
            if self.record_hook:
                hooked = self.record_hook(program[2:], stdin)
                if hooked is not None:
                    return hooked
            return t.run_provenance(program[2:], stdin)
        if program == ["-"]:
            return 0, json.dumps({"orders": {"o1": "RECEIPT_AVAILABLE"}, "ledgerIntegrity": "ok"}), ""
        if program[0] == "-c":
            return t.run_snippet(program[1], program[2:])
        raise AssertionError(program)


class StageFixture(unittest.TestCase):
    def setUp(self):
        token = secrets.token_hex(6)
        self.root = Path(f"/private/tmp/zwa4e-test-{token}")   # plays /var/lib/zwa/zwa4e-demo
        self.work = Path(f"/private/tmp/zwa-stage-test-{token}")
        for path in (self.root, self.work):
            path.mkdir(mode=0o700)
            self.addCleanup(shutil.rmtree, path, True)
        self.deploy = self.work / "deploy"
        (self.deploy / "zebra").mkdir(parents=True)
        (self.deploy / "scripts").mkdir()
        shutil.copy(REPOSITORY / "deploy/compose.yaml", self.deploy / "compose.yaml")
        shutil.copy(REPOSITORY / "deploy/zebra/regtest-config.toml", self.deploy / "zebra/regtest-config.toml")
        shutil.copy(REPOSITORY / "deploy/scripts/m10-state-report.py", self.deploy / "scripts/m10-state-report.py")
        (self.deploy / "compose.amd64.yaml").write_text("services:\n  zebra:\n    entrypoint: [...]\n")
        (self.deploy / "env").mkdir(mode=0o700)
        (self.deploy / "env/web.env").write_text("ZWA_SESSION_SECRET=x\n")
        (self.deploy / "env/web.env").chmod(0o600)
        self.zebra_tag = "qedit/zebra-zsa-swap-pinned:amd64"
        self.env_file = self.work / "live.env"
        self.env_file.write_text(f"ZWA_PORTAL_TOKEN={'t' * 40}\nZWA_ENGINE_TAG=live\n"
                                 f"ZWA_ZEBRA_IMAGE={self.zebra_tag}\n")
        self.recovery = self.work / "zwa-recovery"
        self.backup = self.recovery / "paired-20261012T1448Z"
        self.backup.mkdir(parents=True)
        self.params = {"sapling-spend.params": b"spend", "sapling-output.params": b"output"}
        self.build_archives()
        self.rendered = {
            "name": "zwa-prov-stage",
            "services": {
                "zebra": {"image": self.zebra_tag, "entrypoint": AMD64_ENTRYPOINT,
                          "volumes": [{"type": "volume", "source": "zebra-state", "target": "/data"}]},
                "portal": {"image": "zwa-engine:provenance-test",
                           "volumes": [{"type": "volume", "source": "zwa-root", "target": "/var/lib/zwa"}]}},
            "volumes": {v: {"name": f"zwa-prov-stage_{v}"} for v in stage_module.VOLUMES}}
        self.cfg = self.config()
        self.host = FakeHost(self)
        self.tip = 436
        patches = [patch.object(stage_module, "source_revision", return_value="f" * 40),
                   patch.object(stage_module, "PARAMS_SHA256",
                                {k: hashlib.sha256(v).hexdigest() for k, v in self.params.items()})]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def build_archives(self, *, identity_bytes: bytes | None = None, extra=None) -> None:
        raw = identity_bytes if identity_bytes is not None else provenance.serialize(identity())
        with tarfile.open(self.backup / "zwa-demo_zwa-root.tar.gz", "w:gz") as tar:
            add(tar, ".", kind="d", mode=0o755, uid=0)
            add(tar, "./zwa4e-demo", kind="d", mode=0o700)
            add(tar, "./zwa4e-demo/chain-identity.json", data=raw)
            add(tar, "./zwa4e-demo/exchange", kind="d", mode=0o700)
            add(tar, "./zwa4e-demo/exchange/exchange.sqlite", data=b"SQLite format 3\x00ledger")
            add(tar, "./zwa4e-demo/exchange/orders", kind="d", mode=0o700)
            add(tar, "./zwa4e-demo/exchange/orders/o1", kind="d", mode=0o700)
            add(tar, "./zwa4e-demo/exchange/orders/o1/order-receipt.json", data=f'{{"txid":"{SETTLED}"}}'.encode())
            add(tar, "./zwa4e-demo/seller", kind="d", mode=0o700)
            add(tar, "./zwa4e-demo/seller/ZcashParams", kind="l", link="/opt/zwa/params/ZcashParams", mode=0o777)
            if extra:
                extra(tar)
        with tarfile.open(self.backup / "zwa-demo_zebra-state.tar.gz", "w:gz") as tar:
            add(tar, ".", kind="d", mode=0o755, uid=0)
            add(tar, "./state", kind="d", mode=0o755, uid=0)
            add(tar, "./state/v26", kind="d", mode=0o755, uid=0)
            add(tar, "./state/v26/000001.sst", data=b"rocksdb", uid=0, mode=0o644)
        with tarfile.open(self.backup / "zwa-demo_zwa-params.tar.gz", "w:gz") as tar:
            add(tar, ".", kind="d", mode=0o755, uid=0)
            for name, data in self.params.items():
                add(tar, f"./{name}", data=data, uid=10001, mode=0o644)
        self.write_sums()

    def write_sums(self) -> None:
        names = sorted(p.name for p in self.backup.glob("*.tar.gz"))
        (self.backup / "SHA256SUMS").write_text("".join(
            f"{hashlib.sha256((self.backup / n).read_bytes()).hexdigest()}  {n}\n" for n in names))

    def args(self, action: str, *extra: str) -> list[str]:
        return [action, "--backup-dir", str(self.backup), "--recovery-root", str(self.recovery),
                "--engine-tag", "provenance-test", "--env-file", str(self.env_file),
                "--evidence-dir", str(self.work / "evidence"), "--expect-genesis", GENESIS,
                "--expect-anchor-height", "335", "--expect-settled-tx", f"{SETTLED}:325", *extra]

    def config(self, *extra: str):
        _, cfg, _ = stage_module.parse(self.args("plan", *extra))
        cfg.deploy_dir, cfg.repo_root = self.deploy, REPOSITORY
        return cfg

    def main(self, action: str, *extra: str) -> tuple[int, dict | None, str]:
        out, err = io.StringIO(), io.StringIO()
        real_parse = stage_module.parse

        def parse(argv):
            action_, cfg, rendered = real_parse(argv)
            cfg.deploy_dir, cfg.repo_root = self.deploy, REPOSITORY
            self.cfg = cfg
            return action_, cfg, rendered
        code = 0
        with patch.object(stage_module, "parse", side_effect=parse), redirect_stdout(out), redirect_stderr(err):
            try:
                stage_module.main(self.args(action, *extra),
                                  docker=stage_module.Docker("--docker-sudo" in extra, runner=self.host))
            except SystemExit as exc:
                code = exc.code
        try:
            data = json.loads(out.getvalue()) if out.getvalue() else None
        except ValueError:
            data = None
        return code, data, err.getvalue()

    # --- engine-side behaviour, run for real against the restored fixture root

    def rpc(self, method, params=None):
        if method == "getblockcount":
            return self.tip
        if method == "getblockhash":
            return {0: GENESIS, 335: ANCHOR}.get(params[0], f"{params[0]:064x}")
        if method == "getblock":
            height = int(params[0])
            return {"hash": f"{height:064x}", "tx": [SETTLED] if height == 325 else []}
        raise AssertionError(method)

    def engine_patches(self, stdin: bytes | None):
        rpc = lambda _port, method, params=None, host=None: self.rpc(method, params)  # noqa: E731
        return [patch.object(provenance, "_rpc", side_effect=rpc), patch.object(chain, "_rpc", side_effect=rpc),
                patch.object(provenance, "machine_platform", return_value="linux/amd64"),
                patch.object(provenance.sys, "stdin", io.TextIOWrapper(io.BytesIO(stdin or b"")))]

    def run_provenance(self, argv, stdin):
        argv = [str(self.root) if a == stage_module.ENGINE_ROOT else a for a in argv]
        out, err = io.StringIO(), io.StringIO()
        code = 0
        patches = self.engine_patches(stdin)
        for item in patches:
            item.start()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                provenance.main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
        finally:
            for item in reversed(patches):
                item.stop()
        return code, out.getvalue(), err.getvalue()

    def run_snippet(self, code, argv):
        mapping = {"/var/lib/zwa": str(self.root), stage_module.ENGINE_ROOT: str(self.root),
                   f"{stage_module.ENGINE_ROOT}/chain-identity.json": str(self.root / "chain-identity.json")}
        argv = [mapping.get(a, a) for a in argv]
        out = io.StringIO()
        patches = self.engine_patches(None)
        for item in patches:
            item.start()
        try:
            with patch.object(sys, "argv", ["-c", *argv]), redirect_stdout(out):
                exec(compile(code, "<engine snippet>", "exec"), {"__name__": "__main__"})
        finally:
            for item in reversed(patches):
                item.stop()
        return 0, out.getvalue(), ""

    def backup_hashes(self) -> dict:
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(self.backup.iterdir())}

    def mutating_calls(self) -> list[list[str]]:
        return [c for c in self.host.calls if ("create" in c and "volume" in c) or "tar" in c or "up" in c
                or ("run" in c and "portal" in c and "record" in c)]

    def plan_and_stage(self, *extra: str) -> tuple[int, dict | None, str]:
        code, planned, err = self.main("plan", *extra)
        self.assertEqual(code, 0, err)
        return self.main("stage", "--rendered-sha256", planned["renderedSha256"], *extra)


class StagingHappyPathTests(StageFixture):
    def test_plan_then_stage_passes_and_touches_only_new_volumes(self):
        backup = self.backup_hashes()
        receipt = None
        code, result, err = self.plan_and_stage()
        self.assertEqual(code, 0, err)
        self.assertEqual(result["result"], "STAGING_PASSED")
        self.assertEqual(len(result["drills"]), 7)
        self.assertEqual(self.backup_hashes(), backup)  # original paired backup untouched
        receipt = (self.root / "exchange/orders/o1/order-receipt.json").read_bytes()
        self.assertIn(SETTLED.encode(), receipt)   # original settlement evidence restored unchanged
        migrated = json.loads((self.root / "chain-identity.json").read_text())
        self.assertEqual(migrated[chain.RUNTIME_KEY]["composeProject"], "zwa-prov-stage")
        self.assertEqual(migrated[chain.RUNTIME_KEY]["imageId"], AMD64)
        for drill in result["drills"]:
            evidence = json.loads((self.work / "evidence" / f"drill-{drill}.json").read_text())
            self.assertEqual((evidence["exit"], evidence["identityUnchanged"]), (1, True), drill)
        for name in ("stage.env", "rendered-config.json", "plan.json", "state-before.json"):
            self.assertEqual((self.work / "evidence" / name).stat().st_mode & 0o777, 0o600)
        env = (self.work / "evidence/stage.env").read_text()
        self.assertIn("ZWA_ENGINE_TAG=provenance-test", env)
        self.assertNotIn("ZWA_ENGINE_TAG=live", env)

    def test_every_compose_call_uses_the_amd64_override_and_the_staging_project(self):
        self.assertEqual(self.plan_and_stage()[0], 0)
        compose = [c for c in self.host.calls if "compose" in c]
        self.assertGreater(len(compose), 20)
        for call in compose:
            files = [call[i + 1] for i, a in enumerate(call) if a == "-f"]
            self.assertEqual(files, [str(self.deploy / "compose.yaml"), str(self.deploy / "compose.amd64.yaml")])
            self.assertEqual(call[call.index("-p") + 1], "zwa-prov-stage")
            self.assertEqual(call[call.index("--env-file") + 1], str(self.work / "evidence/stage.env"))

    def test_isolation_read_only_backup_and_no_docker_socket(self):
        self.assertEqual(self.plan_and_stage()[0], 0)
        for call in self.host.calls:
            joined = " ".join(call)
            self.assertNotIn("docker.sock", joined)
            self.assertFalse(any(a.startswith("zwa-demo_") for a in call), call)  # never a live volume
            for i, arg in enumerate(call):
                if arg == "-v" and call[i + 1].startswith(str(self.backup)):
                    self.assertTrue(call[i + 1].endswith(":/backup:ro"), call)
        restores = [c for c in self.host.calls if "--entrypoint" in c and c[c.index("--entrypoint") + 1] == "tar"]
        self.assertEqual(len(restores), 3)
        for call in restores:
            self.assertIn("-xzf", call)
            self.assertTrue(call[call.index("-xzf") + 1].endswith(".tar.gz"))
            for flag in ("--numeric-owner", "--same-owner", "--same-permissions"):
                self.assertIn(flag, call)
            self.assertEqual(call[call.index("--network") + 1], "none")
        self.assertEqual(sorted(self.host.volumes), [f"zwa-prov-stage_{v}" for v in sorted(stage_module.VOLUMES)])

    def test_sudo_prefix_is_used_for_every_docker_call(self):
        code, _, err = self.plan_and_stage("--docker-sudo")
        self.assertEqual(code, 0, err)
        self.assertTrue(all(c[:3] == ["sudo", "-n", "docker"] for c in self.host.calls))

    def test_stop_keeps_volumes(self):
        self.assertEqual(self.plan_and_stage()[0], 0)
        code, result, _ = self.main("stop")
        self.assertEqual(code, 0)
        self.assertEqual(self.host.calls[-1][-1], "down")
        self.assertNotIn("-v", self.host.calls[-1][self.host.calls[-1].index("-f") + 4:])
        self.assertEqual(len(result["volumesKept"]), 3)


class NegativeDrillTests(StageFixture):
    def assert_halted(self, hook, message: str):
        self.host.record_hook = hook
        code, _, err = self.plan_and_stage()
        self.assertEqual(code, 2)
        self.assertIn(message, err)
        self.assertNotIn(chain.RUNTIME_KEY, (self.root / "chain-identity.json").read_text())
        self.assertFalse((self.work / "evidence/stage.json").exists())

    def test_unexpectedly_successful_drill_halts_staging(self):
        def succeed_on_wrong_anchor(argv, stdin):
            if "334" in argv:
                return 0, "{}", ""
            return None
        self.assert_halted(succeed_on_wrong_anchor, "did not refuse as expected (exit 0)")

    def test_drill_refusing_for_another_reason_halts_staging(self):
        def wrong_reason(argv, stdin):
            if b"0000000000000000" in (stdin or b""):
                return 1, "", "PROVENANCE_REFUSED chain RPC unavailable"
            return None
        self.assert_halted(wrong_reason, "'unpinned-image' did not refuse as expected (exit 1)")

    def test_drill_that_refuses_but_changes_the_identity_halts_staging(self):
        def refuse_after_writing(argv, stdin):
            if "zwa-demo" in argv:
                data = json.loads((self.root / "chain-identity.json").read_text())
                data["lastObservedHeight"] += 1
                chain._atomic_json(self.root / "chain-identity.json", data)
                return 1, "", "PROVENANCE_REFUSED attestation is for Compose project 'x', not the expected 'y'"
            return None
        self.host.record_hook = refuse_after_writing
        code, _, err = self.plan_and_stage()
        self.assertEqual(code, 2)
        self.assertIn("'other-compose-project' changed the chain identity", err)

    def test_uncertain_record_exits_3_and_never_reverts(self):
        seen = {"dry": False}

        def uncertain(argv, stdin):
            if argv[:1] == ["record"] and "--dry-run" in argv:
                seen["dry"] = True
            elif argv[:1] == ["record"] and seen["dry"]:  # the real record, after the drills and dry run
                return 3, json.dumps({"outcome": "UNCERTAIN"}), "PROVENANCE_UNCERTAIN directory fsync failed"
            return None
        self.host.record_hook = uncertain
        code, _, err = self.plan_and_stage()
        self.assertEqual(code, 3)
        self.assertIn("STAGE_UNCERTAIN", err)
        self.assertIn("Do not revert", err)
        self.assertFalse(any("revert" in c for c in self.host.calls))


class PlanRefusalTests(StageFixture):
    def assert_plan_refused(self, message: str, *extra: str):
        code, _, err = self.main("plan", *extra)
        self.assertEqual(code, 2, err)
        self.assertIn(message, err)
        self.assertEqual(self.mutating_calls(), [])
        shutil.rmtree(self.work / "evidence", ignore_errors=True)
        self.host.calls.clear()

    def test_backup_checksums_and_tar_gz_contents(self):
        archive = self.backup / "zwa-demo_zwa-root.tar.gz"
        good = archive.read_bytes()
        archive.write_bytes(good[:-8] + b"tampered")
        self.assert_plan_refused("checksum mismatch: zwa-demo_zwa-root.tar.gz")
        archive.write_bytes(good)
        sums = (self.backup / "SHA256SUMS").read_text()
        (self.backup / "SHA256SUMS").write_text("\n".join(line for line in sums.splitlines() if "params" not in line))
        self.assert_plan_refused("zwa-demo_zwa-params.tar.gz is not listed")
        self.write_sums()
        with tarfile.open(archive, "w") as tar:  # uncompressed, but named .tar.gz
            add(tar, "./zwa4e-demo/chain-identity.json", data=provenance.serialize(identity()))
        self.write_sums()
        self.assert_plan_refused("is not a readable .tar.gz archive")

    def test_unsafe_archive_members(self):
        cases = {
            "unsafe archive member path": lambda tar: add(tar, "./zwa4e-demo/../../etc/cron.d/x", data=b"x"),
            "device, FIFO": lambda tar: add(tar, "./zwa4e-demo/dev", kind="c"),
            "below a symlink": lambda tar: add(tar, "./zwa4e-demo/seller/ZcashParams/x", data=b"x"),
            "setuid": lambda tar: add(tar, "./zwa4e-demo/suid", data=b"x", mode=0o4755),
            "duplicate": lambda tar: add(tar, "zwa4e-demo/exchange/exchange.sqlite", data=b"other"),
        }
        for message, extra in cases.items():
            with self.subTest(message):
                self.build_archives(extra=extra)
                self.assert_plan_refused(message)
        self.build_archives(extra=lambda tar: add(tar, "/etc/passwd", data=b"x"))
        self.assert_plan_refused("unsafe archive member path")

    def test_archived_identity_must_be_the_expected_pre_migration_identity(self):
        migrated = identity()
        migrated[chain.RUNTIME_KEY] = {"schema": 2}
        for raw, message in ((provenance.serialize(dict(identity(), genesisHash="4" * 64)), "differs from --expect"),
                             (provenance.serialize(dict(identity(), anchorHeight=400)), "differs from --expect"),
                             (provenance.serialize(migrated), "invalid"),
                             (b"{not json", "invalid")):
            self.build_archives(identity_bytes=raw)
            self.assert_plan_refused(message)
        self.params["sapling-spend.params"] = b"other"
        with patch.object(stage_module, "PARAMS_SHA256", {"sapling-spend.params": "0" * 64,
                                                          "sapling-output.params": "0" * 64}):
            self.build_archives()
            self.assert_plan_refused("checksummed Sapling parameters")

    def test_rendered_configuration_is_inspected_before_anything_runs(self):
        def rendered(change):
            original = json.loads(json.dumps(self.rendered))
            change(self.rendered)
            self.addCleanup(setattr, self, "rendered", original)
            return original
        cases = [
            ("ARM64 entrypoint", lambda r: r["services"]["zebra"].update(entrypoint=stage_module.ARM64_ENTRYPOINT)),
            ("no explicit entrypoint", lambda r: r["services"]["zebra"].pop("entrypoint")),
            ("Docker socket", lambda r: r["services"]["portal"]["volumes"].append(
                {"type": "bind", "source": "/var/run/docker.sock", "target": "/var/run/docker.sock"})),
            ("references zwa-demo volumes", lambda r: r["volumes"]["zwa-root"].update(name="zwa-demo_zwa-root")),
            ("is not the new", lambda r: r["volumes"]["zebra-state"].update(external=True)),
            ("publishes ports", lambda r: r["services"]["zebra"].update(ports=[{"target": 18232}])),
            ("portal image", lambda r: r["services"]["portal"].update(image="zwa-engine:live")),
            ("zebra-state volume at /data", lambda r: r["services"]["zebra"].update(volumes=[])),
        ]
        for message, change in cases:
            with self.subTest(message):
                original = rendered(change)
                self.assert_plan_refused(message)
                self.rendered = original

    def test_zebra_image_and_host_architecture(self):
        self.host.zebra_image = {"Id": ARM64, "Os": "linux", "Architecture": "arm64"}
        self.assert_plan_refused("zebra image check failed")
        self.host.zebra_image = {"Id": AMD64, "Os": "linux", "Architecture": "arm64"}
        self.assert_plan_refused("zebra image check failed")
        self.host.zebra_image = {"Id": AMD64, "Os": "linux", "Architecture": "amd64"}
        self.host.host_arch = "aarch64"
        self.assert_plan_refused("not linux/amd64")

    def test_inputs_and_isolation(self):
        self.assert_plan_refused("must be a Compose project name starting with zwa-prov-", "--project", "zwa-demo")
        self.assert_plan_refused("must be valid and differ", "--project", "zwa-prov-x", "--source-project",
                                 "zwa-prov-x")
        self.assert_plan_refused("plain file name", "--checksum-file", "../SHA256SUMS")
        self.assert_plan_refused("invalid --engine-tag", "--engine-tag", "x;rm -rf /")
        outside = self.work / "paired-elsewhere"
        outside.mkdir()
        code, _, err = self.main("plan", "--recovery-root", str(self.work / "other"))
        self.assertEqual(code, 2)
        self.assertIn("--backup-dir must be an existing", err)
        (self.deploy / "env/web.env").chmod(0o644)
        self.assert_plan_refused("web.env must exist with mode 600")
        (self.deploy / "env/web.env").chmod(0o600)
        (self.deploy / "compose.amd64.yaml").unlink()
        self.assert_plan_refused("compose.amd64.yaml must be an existing regular file")
        (self.deploy / "compose.amd64.yaml").write_text("services: {}\n")
        self.host.volumes["zwa-prov-stage_zebra-state"] = {}
        self.assert_plan_refused("already exists; staging needs new volumes")

    def test_engine_image_must_contain_the_reviewed_code(self):
        real = self.host.run

        def stale_engine(args):
            code, out, err = real(args)
            if args[args.index("--entrypoint") + 1] == "sh":
                out = out.replace(out[:64], "0" * 64, 1)
            return code, out, err
        self.host.run = stale_engine
        self.assert_plan_refused("does not contain the reviewed exchange code")


class StageGateTests(StageFixture):
    def test_stage_requires_the_reviewed_rendering(self):
        code, planned, _ = self.main("plan")
        self.assertEqual(code, 0)
        code, _, err = self.main("stage", "--rendered-sha256", "0" * 64)
        self.assertEqual(code, 2)
        self.assertIn("does not match the reviewed plan", err)
        self.rendered["services"]["zebra"]["entrypoint"] = ["/opt/zebrad", "-c", "/config/regtest-config.toml"]
        code, _, err = self.main("stage", "--rendered-sha256", planned["renderedSha256"])
        self.assertEqual(code, 2)
        self.assertIn("does not match the reviewed plan", err)
        self.assertEqual(self.mutating_calls(), [])

    def test_stage_refuses_when_the_env_file_changed_since_plan(self):
        code, planned, _ = self.main("plan")
        self.env_file.write_text(self.env_file.read_text() + "ZWA_EXTRA=1\n")
        code, _, err = self.main("stage", "--rendered-sha256", planned["renderedSha256"])
        self.assertEqual(code, 2)
        self.assertIn("--env-file changed since plan", err)

    def test_restored_owner_mode_or_extra_entries_halt_staging(self):
        cases = {
            "owner": lambda volume, lines: volume == "zwa-root" and lines[2].__setitem__(2, 0),
            "mode": lambda volume, lines: volume == "zwa-root" and lines[2].__setitem__(4, 0o644),
            "extra": lambda volume, lines: volume == "zebra-state" and lines.append(
                ["state/injected", "f", 0, 0, 0o644, 1]),
        }
        for name, hook in cases.items():
            with self.subTest(name):
                self.host = FakeHost(self)
                self.host.listing_hook = hook
                shutil.rmtree(self.work / "evidence", ignore_errors=True)
                code, _, err = self.plan_and_stage()
                self.assertEqual(code, 2)
                self.assertRegex(err, "differs from the archive|entries not in the archive")
                self.assertFalse(any("up" in c for c in self.host.calls))

    def test_running_zebra_must_use_the_reviewed_amd64_entrypoint(self):
        self.host.container_entrypoint = stage_module.ARM64_ENTRYPOINT
        code, _, err = self.plan_and_stage()
        self.assertEqual(code, 2)
        self.assertIn("does not use the reviewed AMD64 entrypoint", err)

    def test_arm64_entrypoint_constant_matches_compose_yaml(self):
        compose = (REPOSITORY / "deploy/compose.yaml").read_text()
        self.assertIn('entrypoint: ["target/release/zebrad", "-c", "/config/regtest-config.toml"]', compose)
        self.assertEqual(stage_module.ARM64_ENTRYPOINT, ["target/release/zebrad", "-c", "/config/regtest-config.toml"])


if __name__ == "__main__":
    unittest.main()
