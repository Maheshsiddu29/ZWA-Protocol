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
import os
from pathlib import Path
import secrets
import re
import shutil
import stat
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
PARAMS_DIR = "/srv/zwa/params/ZcashParams"  # the AWS host's parameter directory
CONTAINER = "e" * 64


def load_stage():
    spec = importlib.util.spec_from_file_location("provenance_stage", REPOSITORY / "deploy/scripts/provenance-stage.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module through sys.modules
    spec.loader.exec_module(module)
    return module


stage_module = load_stage()
RealBackupReader = stage_module.BackupReader


def elf(machine: int = 0x3E) -> bytes:
    header = bytearray(b"\x7fELF\x02\x01\x01" + bytes(9))
    header += (2).to_bytes(2, "little") + machine.to_bytes(2, "little") + bytes(44)
    return bytes(header) + b"zebrad"


def rendering(deploy: Path, zebra_tag: str) -> dict:
    """The shape `docker compose config --format json` printed for compose.yaml plus an AMD64 override."""
    hardened = {"cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"], "restart": "unless-stopped",
                "logging": {"driver": "json-file", "options": {"max-file": "5", "max-size": "10m"}}}
    engine_mounts = [{"type": "volume", "source": "zwa-root", "target": "/var/lib/zwa", "volume": {}},
                     {"type": "volume", "source": "zwa-params", "target": "/opt/zwa/params/ZcashParams",
                      "read_only": True, "volume": {}}]
    engine = dict(hardened, image="zwa-engine:provenance-test", user="10001:10001", read_only=True,
                  tmpfs=["/tmp:size=64m,mode=1777"], networks={"internal": None}, entrypoint=None,
                  environment={"ZWA_PRIVATE_EXCHANGE_ROOT": "/var/lib/zwa/zwa4e-demo"},
                  depends_on={"zebra": {"condition": "service_started", "required": True}},
                  volumes=engine_mounts, mem_limit="3221225472", cpus=2)
    return {
        "name": "zwa-prov-stage",
        "services": {
            "zebra": dict(hardened, image=zebra_tag, entrypoint=AMD64_ENTRYPOINT, command=None, cpus=2,
                          mem_limit="4294967296", networks={"internal": None},
                          volumes=[{"type": "volume", "source": "zebra-state", "target": "/data", "volume": {}},
                                   {"type": "bind", "source": str(deploy / "zebra/regtest-config.toml"),
                                    "target": "/config/regtest-config.toml", "read_only": True, "bind": {}}]),
            "portal": dict(engine, command=["python3", "-m", "exchange.portal_server"], mem_limit="536870912",
                           environment={"ZWA_PORTAL_TOKEN": "t" * 40,
                                        "ZWA_PRIVATE_EXCHANGE_ROOT": "/var/lib/zwa/zwa4e-demo"},
                           healthcheck={"test": ["CMD", "python3", "-c", "pass"], "interval": "15s"}),
            "order-service": dict(engine, command=["python3", "-m", "exchange.order_service", "serve"]),
            "demo-signer": dict(engine, command=["python3", "-m", "exchange.demo_signer", "serve"]),
            "web": dict(hardened, image="zwa-web:local", user="10001:10001", read_only=True,
                        networks={"internal": None, "edge": None}, environment={"ZWA_DEMO_MODE": "1"}),
            # Setup-profile services: present because the driver always renders with --profile setup.
            "params": dict(engine, profiles=["setup"], restart="no", entrypoint=["sh", "-c"],
                           command=[stage_module.PARAMS_COMMAND],
                           volumes=[{"type": "bind", "source": PARAMS_DIR, "target": "/src", "read_only": True,
                                     "bind": {}},
                                    {"type": "volume", "source": "zwa-params",
                                     "target": "/opt/zwa/params/ZcashParams", "volume": {}}]),
            "bootstrap": dict(engine, profiles=["setup"], restart="no", command=stage_module.BOOTSTRAP_COMMAND),
            "caddy": dict(hardened, image="caddy:2.8.4-alpine", networks={"edge": None},
                          ports=[{"mode": "ingress", "host_ip": "127.0.0.1", "target": 80, "published": "80",
                                  "protocol": "tcp"},
                                 {"mode": "ingress", "host_ip": "127.0.0.1", "target": 443, "published": "443",
                                  "protocol": "tcp"}],
                          volumes=[{"type": "bind", "source": str(deploy / "Caddyfile"),
                                    "target": "/etc/caddy/Caddyfile", "read_only": True, "bind": {}},
                                   {"type": "volume", "source": "caddy-data", "target": "/data", "volume": {}},
                                   {"type": "volume", "source": "caddy-config", "target": "/config", "volume": {}}]),
        },
        "networks": {"edge": {"name": "zwa-prov-stage_edge", "ipam": {}},
                     "internal": {"name": "zwa-prov-stage_internal", "ipam": {}, "internal": True}},
        "volumes": {v: {"name": f"zwa-prov-stage_{v}"}
                    for v in ("zwa-root", "zwa-params", "zebra-state", "caddy-data", "caddy-config")},
        "x-engine": {"image": "zwa-engine:provenance-test"},
    }


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
    elif kind == "h":
        info.type, info.linkname = tarfile.LNKTYPE, link
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
        self.corrupt_hook = None
        self.volume_root = test.work / f"volumes-{secrets.token_hex(4)}"
        self.record_hook = None
        self.executable: bytes | None = elf()
        self.executable_mode = 0o755
        self.check_containers: list[str] = []
        self.removed: list[str] = []

    def __call__(self, argv, input=None, capture_output=True, env=None, timeout=None):
        self.calls.append(list(argv))
        prefix = 3 if argv[:3] == ["sudo", "-n", "docker"] else 1
        code, out, err = self.handle(argv[prefix:], input)
        return subprocess.CompletedProcess(argv, code, out if isinstance(out, bytes) else out.encode(),
                                           err.encode())

    def handle(self, args, stdin):
        t = self.t
        if args[0] == "compose":
            return self.compose(args, stdin)
        if args[:1] == ["info"]:
            return 0, json.dumps({"OSType": "linux", "Architecture": self.host_arch}), ""
        if args[:2] == ["image", "inspect"]:
            ref = args[-1]
            if ref == t.zebra_tag:
                return 0, json.dumps([dict(self.zebra_image, Config={"WorkingDir": "/opt/zebra"})]), ""
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
        if args[0] == "create":
            assert args[args.index("--network") + 1] == "none" and "--entrypoint" in args, args
            self.check_containers.append(f"{len(self.check_containers) + 1:064x}")
            return 0, self.check_containers[-1] + "\n", ""
        if args[0] == "cp":
            if self.executable is None:
                return 1, "", "Error: Could not find the file /usr/local/bin/zebrad in container"
            buffer = io.BytesIO()
            with tarfile.open(fileobj=buffer, mode="w") as tar:
                add(tar, "zebrad", data=self.executable, uid=0, mode=self.executable_mode)
            return 0, buffer.getvalue(), ""
        if args[:2] == ["rm", "-v"]:
            self.removed.append(args[2])
            return 0, args[2], ""
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
            manifest, _ = stage_module.inspect_archive(RealBackupReader(Path(backup), sudo=False), archive.name,
                                                       short, self.t.cfg)
            self.volumes[volume] = manifest
            self.unpack(archive, self.volume_root / volume)
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
        if entry == "python3":  # restored-content digests, run for real over the unpacked volume
            code = args[args.index("-c") + 1]
            directory = self.volume_root / volume
            if self.corrupt_hook:
                self.corrupt_hook(short, directory)
            out = io.StringIO()
            with patch.object(sys, "argv", ["-c", str(directory)]), redirect_stdout(out):
                exec(compile(code, "<digest snippet>", "exec"), {"__name__": "__main__"})
            return 0, out.getvalue(), ""
        if entry == "find":
            lines = []
            sizes = {digest: size for kind, _u, _g, _m, size, digest in self.volumes[volume].values() if kind == "f"}
            for path, (kind, uid, gid, mode, size, digest) in self.volumes[volume].items():
                size = sizes[digest] if kind == "h" else size  # find reports a hard link's file size
                kind = "f" if kind == "h" else kind
                lines.append([path, kind, uid, gid, 0o777 if kind == "l" else mode,
                              size if kind == "f" else 4096])
            if self.listing_hook:
                self.listing_hook(short, lines)
            return 0, "".join("\t".join([p, k, str(u), str(g), format(m, "o"), str(s)]) + "\n"
                              for p, k, u, g, m, s in lines), ""
        raise AssertionError(args)

    @staticmethod
    def unpack(archive: Path, directory: Path) -> None:
        """What `tar -xzf` leaves in the volume (contents and modes; ownership is simulated by `find`)."""
        directory.mkdir(parents=True)
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                name = stage_module._member_path(member.name)
                target = directory / name if name else directory
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif member.isreg():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(tar.extractfile(member).read())
                elif member.issym():
                    target.symlink_to(member.linkname)
                elif member.islnk():
                    os.link(directory / stage_module._member_path(member.linkname), target)

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
                                 f"ZWA_ZEBRA_IMAGE={self.zebra_tag}\nZWA_PARAMS_DIR={PARAMS_DIR}\n")
        self.recovery = self.work / "zwa-recovery"
        self.backup = self.recovery / "paired-20261012T1448Z"
        self.backup.mkdir(parents=True)
        self.params = {"sapling-spend.params": b"spend", "sapling-output.params": b"output"}
        self.build_archives()
        self.rendered = rendering(self.deploy, self.zebra_tag)
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
            add(tar, "./state/v26/000001.hardlink", kind="h", link="./state/v26/000001.sst", uid=0, mode=0o644)
        with tarfile.open(self.backup / "zwa-demo_zwa-params.tar.gz", "w:gz") as tar:
            add(tar, ".", kind="d", mode=0o755, uid=0)
            for name, data in self.params.items():
                add(tar, f"./{name}", data=data, uid=10001, mode=0o644)
        for volume in ("caddy-data", "caddy-config"):  # listed and verified, never restored
            with tarfile.open(self.backup / f"zwa-demo_{volume}.tar.gz", "w:gz") as tar:
                add(tar, ".", kind="d", mode=0o755, uid=0)
                add(tar, "./caddy", data=volume.encode(), uid=0, mode=0o600)
        self.write_sums()

    def write_sums(self, absolute: bool = True) -> None:
        """Like the AWS manifest: `sha256sum` output with absolute paths inside the backup directory."""
        names = sorted(p.name for p in self.backup.glob("*.tar.gz"))
        (self.backup / "SHA256SUMS").write_text("".join(
            f"{hashlib.sha256((self.backup / n).read_bytes()).hexdigest()}  {self.backup / n if absolute else n}\n"
            for n in names))

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
            self.assertEqual(call[call.index("--profile") + 1], "setup")

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
            ("publishes or exposes ports", lambda r: r["services"]["zebra"].update(ports=[{"target": 18232}])),
            ("portal image", lambda r: r["services"]["portal"].update(image="zwa-engine:live")),
            ("zebra mounts are not exactly", lambda r: r["services"]["zebra"].update(volumes=[])),
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
        self.assertIn("--backup-dir must be an absolute", err)
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


class ComposeAllowlistTests(StageFixture):
    """An otherwise valid rendering with exactly one unsafe addition must be refused by plan."""

    PRODUCTION = "/var/lib/docker/volumes/zwa-demo_zebra-state/_data"

    def refused(self, message: str, change) -> None:
        original = json.loads(json.dumps(self.rendered))
        change(self.rendered)
        try:
            code, _, err = self.main("plan")
            self.assertEqual(code, 2, f"{message}: plan was not refused")
            self.assertIn(message, err)
            self.assertEqual(self.mutating_calls(), [])
            self.assertEqual(self.host.check_containers, self.host.removed)
        finally:
            self.rendered = original
            shutil.rmtree(self.work / "evidence", ignore_errors=True)
            self.host.calls.clear()

    def test_the_real_shape_is_accepted(self):
        code, planned, err = self.main("plan")
        self.assertEqual(code, 0, err)
        self.assertEqual(planned["zebra"]["elfMachine"], "x86-64")
        self.assertEqual(planned["zebra"]["zebraExecutable"], "/usr/local/bin/zebrad")
        self.assertEqual(self.host.check_containers, self.host.removed)  # throwaway container removed

    def test_extra_mounts_are_refused(self):
        zebra = lambda r: r["services"]["zebra"]["volumes"]  # noqa: E731
        portal = lambda r: r["services"]["portal"]["volumes"]  # noqa: E731
        cases = [
            ("unapproved mount", lambda r: zebra(r).append(
                {"type": "bind", "source": self.PRODUCTION.replace("zwa-demo", "live"), "target": "/data2"})),
            ("unapproved mount", lambda r: zebra(r).append(
                {"type": "bind", "source": "/etc", "target": "/host-etc", "read_only": True, "bind": {}})),
            ("unapproved mount", lambda r: portal(r).append(
                {"type": "bind", "source": str(self.deploy / "env"), "target": "/x"})),  # writable bind
            ("zebra mounts are not exactly", lambda r: zebra(r).append(
                {"type": "volume", "source": "caddy-data", "target": "/extra", "volume": {}})),
            ("portal mounts are not exactly", lambda r: portal(r).append(
                {"type": "bind", "source": str(self.deploy / "Caddyfile"), "target": "/c", "read_only": True})),
            ("portal mounts are not exactly", lambda r: portal(r)[1].update(read_only=False)),
            ("unapproved mount", lambda r: r["services"]["web"].update(volumes=[
                {"type": "volume", "source": "live-wallets", "target": "/w", "volume": {}}])),
            ("unsupported mount", lambda r: portal(r)[0].update(volume={"subpath": "zwa4e-demo"})),
            ("unsupported mount", lambda r: zebra(r).append({"type": "tmpfs", "target": "/run"})),
            ("repeats a mount", lambda r: portal(r).append(dict(portal(r)[0]))),
        ]
        for message, change in cases:
            with self.subTest(message):
                self.refused(message, change)

    def test_unapproved_volumes_and_networks_are_refused(self):
        cases = [
            ("is not the new", lambda r: r["volumes"]["zwa-root"].update(  # a host path behind a "volume"
                driver="local", driver_opts={"type": "none", "o": "bind", "device": "/opt/zwa/live-root"})),
            ("references zwa-demo volumes", lambda r: r["volumes"]["zwa-root"].update(
                driver="local", driver_opts={"type": "none", "o": "bind", "device": self.PRODUCTION})),
            ("is not the new", lambda r: r["volumes"]["zwa-params"].update(external=True)),
            ("top-level volumes are not the reviewed set", lambda r: r["volumes"].update(
                {"shared": {"name": "shared", "external": True}})),
            ("internal/edge pair", lambda r: r["networks"].update({"shared": {"name": "shared", "external": True}})),
            ("not a new isolated", lambda r: r["networks"]["internal"].update(external=True)),
            ("not a new isolated", lambda r: r["networks"]["internal"].update(name="zwa-live_internal")),
            ("not a new isolated", lambda r: r["networks"]["edge"].update(driver="macvlan")),
            ("not internal", lambda r: r["networks"]["internal"].pop("internal")),
            ("must join only the internal network", lambda r: r["services"]["zebra"]["networks"].update(edge=None)),
            ("must join only the internal network", lambda r: r["services"]["portal"]["networks"].update(edge=None)),
            ("undeclared network", lambda r: r["services"]["web"]["networks"].update(host=None)),
        ]
        for message, change in cases:
            with self.subTest(message):
                self.refused(message, change)

    def test_host_namespaces_privileges_and_exposure_are_refused(self):
        cases = [
            ("network_mode", lambda r: r["services"]["zebra"].update(network_mode="host")),
            ("pid", lambda r: r["services"]["zebra"].update(pid="host")),
            ("ipc", lambda r: r["services"]["portal"].update(ipc="host")),
            ("privileged", lambda r: r["services"]["portal"].update(privileged=True)),
            ("privileged", lambda r: r["services"]["web"].update(privileged=True)),
            ("cap_add", lambda r: r["services"]["zebra"].update(cap_add=["NET_ADMIN"])),
            ("devices", lambda r: r["services"]["order-service"].update(devices=["/dev/sda:/dev/sda"])),
            ("userns_mode", lambda r: r["services"]["caddy"].update(userns_mode="host")),
            ("publishes or exposes ports", lambda r: r["services"]["zebra"].update(
                ports=[{"target": 18232, "published": "18232", "host_ip": "0.0.0.0"}])),
            ("publishes or exposes ports", lambda r: r["services"]["zebra"].update(expose=["18232"])),
            ("publishes or exposes ports", lambda r: r["services"]["web"].update(ports=[{"target": 3000}])),
            ("caddy may publish only 80 and 443", lambda r: r["services"]["caddy"]["ports"].append(
                {"target": 18232, "published": "18232"})),
            ("drop all capabilities", lambda r: r["services"]["zebra"].update(security_opt=[])),
            ("drop all capabilities", lambda r: r["services"]["portal"].update(cap_drop=[])),
            ("unprivileged read-only engine", lambda r: r["services"]["portal"].update(user="0:0")),
            ("unprivileged read-only engine", lambda r: r["services"]["portal"].update(read_only=False)),
            ("environment is not the reviewed one", lambda r: r["services"]["portal"]["environment"].update(
                ZWA_PRIVATE_EXCHANGE_ROOT="/var/lib/zwa/zwa4e-phase4e-b")),
            ("unreviewed settings", lambda r: r["services"]["zebra"].update(stdin_open=True)),
            ("unexpected Compose services", lambda r: r["services"].update(
                {"debug": {"image": "alpine", "network_mode": "host"}})),
            ("unexpected top-level Compose keys", lambda r: r.update(secrets={"s": {"file": "/etc/shadow"}})),
        ]
        for message, change in cases:
            with self.subTest(message):
                self.refused(message, change)

    def test_zebra_command_platform_and_config_are_exact(self):
        cases = [
            ("must be `<zebrad> -c /config/regtest-config.toml`", lambda r: r["services"]["zebra"].update(
                entrypoint=["/usr/local/bin/zebrad", "-c", "/tmp/other.toml"])),
            ("must be `<zebrad> -c /config/regtest-config.toml`", lambda r: r["services"]["zebra"].update(
                command=["start"])),
            ("platform is not linux/amd64", lambda r: r["services"]["zebra"].update(platform="linux/arm64")),
        ]
        for message, change in cases:
            with self.subTest(message):
                self.refused(message, change)
        (self.deploy / "zebra/regtest-config.toml").write_text("[network]\nnetwork = \"Mainnet\"\n")
        self.refused("not the pinned Zebra configuration", lambda r: None)

    def test_zebra_executable_must_be_an_x86_64_elf(self):
        for message, executable, mode in (
                ("not an x86-64 ELF", elf(0xB7), 0o755),               # aarch64 binary
                ("not an x86-64 ELF", b"#!/bin/sh\nexec zebrad\n", 0o755),
                ("not an executable regular file", elf(), 0o644),
                ("is not present", None, 0o755)):
            with self.subTest(message):
                self.host.executable, self.host.executable_mode = executable, mode
                self.refused(message, lambda r: None)


class SetupProfileTests(StageFixture):
    """params/bootstrap (setup profile): rendered, never started, held to exactly compose.yaml."""

    refused = ComposeAllowlistTests.refused

    def test_legitimate_params_service_is_accepted(self):
        code, planned, err = self.main("plan", "--expect-params-dir", PARAMS_DIR)
        self.assertEqual(code, 0, err)
        self.assertEqual(planned["paramsDir"], PARAMS_DIR)
        rendered = json.loads((self.work / "evidence/rendered-config.json").read_text())
        self.assertEqual(sorted(rendered["services"]), sorted(stage_module.KNOWN_SERVICES))

    def test_wrong_params_source_is_refused(self):
        params = lambda r: r["services"]["params"]["volumes"][0]  # noqa: E731
        for source in ("/srv/zwa/params/Other", "/srv/zwa", "/opt/zwa/repo/deploy", "/"):
            with self.subTest(source):
                self.refused("service params has an unapproved mount", lambda r: params(r).update(source=source))

    def test_writable_params_bind_is_refused(self):
        self.refused("service params has an unapproved mount",
                     lambda r: r["services"]["params"]["volumes"][0].pop("read_only"))

    def test_extra_binds_are_refused(self):
        cases = [
            ("service params has an unapproved mount", lambda r: r["services"]["params"]["volumes"].append(
                {"type": "bind", "source": "/etc", "target": "/etc-host", "read_only": True, "bind": {}})),
            ("params mounts are not exactly", lambda r: r["services"]["params"]["volumes"].append(
                {"type": "volume", "source": "zebra-state", "target": "/state", "volume": {}})),
            # The parameter bind is allowed on params only, never on another service.
            ("service bootstrap has an unapproved mount", lambda r: r["services"]["bootstrap"]["volumes"].append(
                {"type": "bind", "source": PARAMS_DIR, "target": "/src", "read_only": True, "bind": {}})),
            ("service portal has an unapproved mount", lambda r: r["services"]["portal"]["volumes"].append(
                {"type": "bind", "source": PARAMS_DIR, "target": "/src", "read_only": True, "bind": {}})),
            ("repeats a mount", lambda r: r["services"]["params"]["volumes"].append(
                dict(r["services"]["params"]["volumes"][0]))),
        ]
        for message, change in cases:
            with self.subTest(message):
                self.refused(message, change)

    def test_unexpected_setup_service_overrides_are_refused(self):
        params = lambda r: r["services"]["params"]  # noqa: E731
        bootstrap = lambda r: r["services"]["bootstrap"]  # noqa: E731
        cases = [
            ("must stay a one-shot in the setup profile", lambda r: params(r).pop("profiles")),
            ("must stay a one-shot in the setup profile", lambda r: bootstrap(r).update(restart="unless-stopped")),
            ("privileged", lambda r: params(r).update(privileged=True)),
            ("network_mode", lambda r: bootstrap(r).update(network_mode="host")),
            ("unprivileged read-only engine", lambda r: params(r).update(user="0:0")),
            ("must join only the internal network", lambda r: params(r)["networks"].update(edge=None)),
            ("command or environment is not the reviewed one", lambda r: params(r).update(
                command=["cp -r /src /opt/zwa/params/ZcashParams/"])),
            ("command or environment is not the reviewed one", lambda r: bootstrap(r).update(
                command=["python3", "-m", "exchange.demo_bootstrap", "--root", "/var/lib/zwa/zwa4e-phase4e-b"])),
            ("command or environment is not the reviewed one", lambda r: params(r)["environment"].update(X="1")),
            ("bootstrap mounts are not exactly", lambda r: bootstrap(r)["volumes"].append(
                {"type": "volume", "source": "zebra-state", "target": "/data", "volume": {}})),
            ("image is not zwa-engine:provenance-test", lambda r: bootstrap(r).update(image="zwa-engine:live")),
            ("unreviewed settings", lambda r: params(r).update(stdin_open=True)),
            ("drop all capabilities", lambda r: bootstrap(r).update(cap_drop=[])),
            ("missing ['bootstrap', 'params']", lambda r: [r["services"].pop(n) for n in ("params", "bootstrap")]),
        ]
        for message, change in cases:
            with self.subTest(message):
                self.refused(message, change)

    def test_params_dir_is_validated_from_the_env_file(self):
        base = self.env_file.read_text().replace(f"ZWA_PARAMS_DIR={PARAMS_DIR}\n", "")
        cases = [("", "exactly once"),
                 (f"ZWA_PARAMS_DIR={PARAMS_DIR}\nZWA_PARAMS_DIR=/srv/other/ZcashParams\n", "exactly once"),
                 ("ZWA_PARAMS_DIR=srv/zwa/params\n", "not a safe"),
                 ("ZWA_PARAMS_DIR=/srv/zwa/../../etc\n", "not a safe"),
                 ("ZWA_PARAMS_DIR=/\n", "not a safe"),
                 ("ZWA_PARAMS_DIR=/srv\n", "not a safe"),
                 ("ZWA_PARAMS_DIR=/var/lib/docker/volumes/x\n", "not a safe"),
                 ("ZWA_PARAMS_DIR=/etc/zwa/params\n", "not a safe"),
                 ("ZWA_PARAMS_DIR=/srv/zwa/params $(id)\n", "not a safe")]
        for line, message in cases:
            with self.subTest(line):
                self.env_file.write_text(base + line)
                self.refused(message, lambda r: None)
        self.env_file.write_text(base + f'export ZWA_PARAMS_DIR="{PARAMS_DIR}"\n')
        code, planned, err = self.main("plan")
        self.assertEqual((code, planned["paramsDir"]), (0, PARAMS_DIR), err)
        shutil.rmtree(self.work / "evidence")
        code, _, err = self.main("plan", "--expect-params-dir", "/srv/zwa/params/Other")
        self.assertEqual(code, 2)
        self.assertIn("differs from --expect-params-dir", err)

    def test_compose_settings_in_the_live_env_cannot_steer_staging(self):
        self.env_file.write_text(self.env_file.read_text() + "COMPOSE_PROFILES=other\nexport COMPOSE_FILE=/tmp/x.yml\n"
                                 "COMPOSE_PROJECT_NAME=zwa-demo\n")
        code, _, err = self.main("plan")
        self.assertEqual(code, 0, err)
        stage_env = (self.work / "evidence/stage.env").read_text()
        self.assertNotIn("COMPOSE_", stage_env)
        self.assertIn(f"ZWA_PARAMS_DIR={PARAMS_DIR}", stage_env)

    def test_setup_constants_are_exactly_compose_yaml(self):
        compose = (REPOSITORY / "deploy/compose.yaml").read_text()
        block = compose.split("      - >-\n", 1)[1].split("\n\n", 1)[0]
        folded = " ".join(line.strip() for line in block.splitlines())  # YAML folded scalar
        self.assertEqual(folded, stage_module.PARAMS_COMMAND)
        flow = compose.split("  bootstrap:", 1)[1].split("command: ", 1)[1].split("]", 1)[0] + "]"
        self.assertEqual(json.loads(" ".join(flow.split())), stage_module.BOOTSTRAP_COMMAND)
        self.assertIn('- ${ZWA_PARAMS_DIR:?set to the host directory holding the checksummed ZcashParams}:/src:ro',
                      compose)


FAKE_SUDO = """#!{python}
import json, os, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "sudo.log"), "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
try:
    with open(os.path.join(here, "sudo.ctl")) as handle:
        control = json.load(handle)
except FileNotFoundError:
    control = {{}}
args = sys.argv[1:]
if control.get("mode") == "deny":
    sys.stderr.write("sudo: a password is required\\n")
    sys.exit(1)
if control.get("mode") in ("truncate", "fail-after") and args[-1] == control.get("name"):
    out = subprocess.run(args, capture_output=True).stdout
    sys.stdout.buffer.write(out[: len(out) // 2] if control["mode"] == "truncate" else out)
    sys.stdout.flush()
    sys.exit(1)
os.execv(args[0], args)
"""


class BackupManifestTests(StageFixture):
    """sha256sum manifests with relative or absolute (AWS) entries; never rewritten."""

    def parse(self, text: str) -> dict:
        return stage_module.parse_manifest(text, self.backup)

    def test_absolute_five_entry_manifest_is_accepted_and_every_archive_verified(self):
        manifest = (self.backup / "SHA256SUMS").read_text()
        self.assertEqual(len(manifest.splitlines()), 5)
        entries = [line.split("  ", 1)[1] for line in manifest.splitlines()]
        self.assertTrue(all(entry.startswith(str(self.backup) + "/") for entry in entries))
        code, planned, err = self.main("plan")
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(planned["checksums"]), sorted(p.name for p in self.backup.glob("*.tar.gz")))
        self.assertEqual(len(planned["checksums"]), 5)
        self.assertEqual((self.backup / "SHA256SUMS").read_text(), manifest)  # never rewritten

    def test_relative_manifest_remains_compatible(self):
        self.write_sums(absolute=False)
        code, planned, err = self.main("plan")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(planned["checksums"]), 5)

    def test_every_listed_archive_is_checksummed_but_only_three_are_restored(self):
        caddy = self.backup / "zwa-demo_caddy-config.tar.gz"
        good = caddy.read_bytes()
        caddy.write_bytes(good[:-4] + b"evil")  # an archive that is never restored
        code, _, err = self.main("plan")
        self.assertEqual(code, 2)
        self.assertIn("checksum mismatch: zwa-demo_caddy-config.tar.gz", err)
        caddy.write_bytes(good)
        shutil.rmtree(self.work / "evidence")
        self.host.calls.clear()
        self.assertEqual(self.plan_and_stage()[0], 0)
        restores = [c for c in self.host.calls if "--entrypoint" in c and c[c.index("--entrypoint") + 1] == "tar"]
        self.assertEqual(sorted(c[c.index("-xzf") + 1] for c in restores),
                         [f"/backup/zwa-demo_{v}.tar.gz" for v in sorted(stage_module.VOLUMES)])

    def test_paths_outside_the_backup_traversal_nesting_and_duplicates_are_refused(self):
        b, digest = self.backup, "a" * 64
        other = self.recovery / "paired-other"
        cases = {
            f"{other}/zwa-demo_zwa-root.tar.gz": "outside",
            "/etc/passwd": "outside",
            f"{self.recovery}/zwa-demo_zwa-root.tar.gz": "outside",
            f"{b}/../{b.name}/zwa-demo_zwa-root.tar.gz": "outside",
            f"{b}/./zwa-demo_zwa-root.tar.gz": "outside",
            f"{b}//zwa-demo_zwa-root.tar.gz": "outside",
            f"{b}/sub/zwa-demo_zwa-root.tar.gz": "outside",
            f"{b}/": "outside",
            "../zwa-demo_zwa-root.tar.gz": "not a plain file name",
            "./zwa-demo_zwa-root.tar.gz": "not a plain file name",
            "sub/zwa-demo_zwa-root.tar.gz": "not a plain file name",
            "..": "not a plain file name",
            "zwa demo.tar.gz": "not a plain file name",
        }
        for entry, message in cases.items():
            with self.subTest(entry), self.assertRaisesRegex(stage_module.StageError, message):
                self.parse(f"{digest}  {entry}\n")
        for text in (f"{digest}  x.tar.gz\n{digest}  {b}/x.tar.gz\n",
                     f"{digest}  {b}/x.tar.gz\n{'b' * 64}  {b}/x.tar.gz\n",
                     f"{digest}  x.tar.gz\n{digest} *x.tar.gz\n"):
            with self.subTest(text), self.assertRaisesRegex(stage_module.StageError, "repeats x.tar.gz"):
                self.parse(text)
        for text in ("A" * 64 + "  x.tar.gz\n", "a" * 63 + "  x.tar.gz\n", f"{digest}x.tar.gz\n"):
            with self.subTest(text), self.assertRaisesRegex(stage_module.StageError, "unrecognized"):
                self.parse(text)
        self.assertEqual(self.parse(f"{digest}  x.tar.gz\n{digest} *{b}/y.tar.gz\n\n"),
                         {"x.tar.gz": digest, "y.tar.gz": digest})

    def test_symlinked_missing_or_non_regular_archives_are_refused(self):
        archive = self.backup / "zwa-demo_caddy-data.tar.gz"
        outside = self.work / "outside.tar.gz"
        archive.rename(outside)
        archive.symlink_to(outside)  # same bytes, so the checksum alone would pass
        code, _, err = self.main("plan")
        self.assertEqual(code, 2)
        self.assertIn("backup read of zwa-demo_caddy-data.tar.gz failed (exit 3)", err)
        archive.unlink()
        shutil.rmtree(self.work / "evidence")
        code, _, err = self.main("plan")
        self.assertIn("cannot open zwa-demo_caddy-data.tar.gz", err)
        archive.mkdir()
        shutil.rmtree(self.work / "evidence")
        code, _, err = self.main("plan")
        self.assertIn("zwa-demo_caddy-data.tar.gz is not a regular file", err)
        self.assertEqual(self.mutating_calls(), [])

    def test_symlinked_backup_directory_or_recovery_root_is_refused(self):
        alias = self.recovery / "paired-alias"
        alias.symlink_to(self.backup)
        code, _, err = self.main("plan", "--backup-dir", str(alias))
        self.assertEqual(code, 2)
        self.assertIn("not a symlink", err)
        root_alias = self.work / "recovery-alias"
        root_alias.symlink_to(self.recovery)
        shutil.rmtree(self.work / "evidence", ignore_errors=True)
        code, _, err = self.main("plan", "--recovery-root", str(root_alias),
                                 "--backup-dir", str(root_alias / self.backup.name))
        self.assertEqual(code, 2)
        self.assertRegex(err, "not a symlink|not a canonical absolute path")


class ProtectedBackupAccessTests(StageFixture):
    """A backup readable only by root: read through the narrow sudo reader, never chmod/chown/copied."""

    def snapshot(self) -> dict:
        return {p.name: (hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and os.access(p, os.R_OK)
                         else None, p.lstat().st_mode, p.lstat().st_uid, p.lstat().st_size, p.lstat().st_mtime_ns)
                for p in sorted(self.backup.iterdir())}

    def fake_sudo(self, control: dict | None = None) -> Path:
        tools = self.work / "fake-sudo"
        tools.mkdir(exist_ok=True)
        script = tools / "sudo"
        script.write_text(FAKE_SUDO.format(python=sys.executable))
        script.chmod(0o755)
        if control is not None:
            (tools / "sudo.ctl").write_text(json.dumps(control))
        return script

    def sudo_main(self, action: str, control: dict | None = None, *extra: str):
        script = self.fake_sudo(control)

        def reader(backup, *, sudo, python):
            self.assertTrue(sudo)
            self.assertEqual(python, stage_module.SYSTEM_PYTHON)  # what the real sudo would run
            return _SudoForTest(backup, script)
        with patch.object(stage_module, "BackupReader", side_effect=reader):
            return self.main(action, "--backup-sudo", *extra)

    def sudo_log(self) -> list[list[str]]:
        log = self.work / "fake-sudo/sudo.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_direct_read_of_a_root_only_manifest_fails_closed_with_a_hint(self):
        manifest = self.backup / "SHA256SUMS"
        manifest.chmod(0o000)
        self.addCleanup(manifest.chmod, 0o644)
        before = self.snapshot()
        code, _, err = self.main("plan")
        self.assertEqual(code, 2)
        self.assertIn("backup read of SHA256SUMS failed", err)
        self.assertIn("re-run with --backup-sudo", err)
        self.assertEqual(self.snapshot(), before)  # still mode 000: never chmodded
        self.assertEqual(stat.S_IMODE(manifest.lstat().st_mode), 0)
        self.assertEqual(self.mutating_calls(), [])

    def test_direct_read_of_a_root_only_directory_fails_closed(self):
        self.backup.chmod(0o000)
        self.addCleanup(self.backup.chmod, 0o755)
        code, _, err = self.main("plan")
        self.assertEqual(code, 2)
        self.assertIn("re-run with --backup-sudo", err)
        self.assertEqual(stat.S_IMODE(self.backup.lstat().st_mode), 0)

    def test_sudo_reads_use_only_the_fixed_reader_on_validated_names(self):
        before = self.snapshot()
        code, planned, err = self.sudo_main("plan")
        self.assertEqual(code, 0, err)
        self.assertEqual(planned["backupRead"], "sudo")
        calls = self.sudo_log()
        self.assertGreater(len(calls), 5)
        allowed = {"SHA256SUMS"} | set(planned["checksums"])
        for argv in calls:
            self.assertEqual(argv[:4], [sys.executable, "-I", "-c", stage_module.BACKUP_READER])
            self.assertEqual(argv[5], str(self.backup))
            self.assertIn(argv[4], ("list", "read"))
            if argv[4] == "read":
                self.assertEqual(len(argv), 7)
                self.assertIn(argv[6], allowed)
            else:
                self.assertEqual(len(argv), 6)
        self.assertEqual(self.snapshot(), before)

    def test_sudo_refusal_fails_closed(self):
        code, _, err = self.sudo_main("plan", {"mode": "deny"})
        self.assertEqual(code, 2)
        self.assertIn("failed (exit 1): sudo: a password is required", err)
        self.assertEqual(self.mutating_calls(), [])

    def test_failure_during_privileged_reading_fails_closed(self):
        for mode, name in (("truncate", "zwa-demo_zebra-state.tar.gz"),
                           ("fail-after", "zwa-demo_caddy-data.tar.gz"),   # full, correct bytes but exit 1
                           ("fail-after", "SHA256SUMS")):
            with self.subTest(mode=mode, name=name):
                shutil.rmtree(self.work / "evidence", ignore_errors=True)
                shutil.rmtree(self.work / "fake-sudo", ignore_errors=True)
                self.host.calls.clear()
                code, _, err = self.sudo_main("plan", {"mode": mode, "name": name})
                self.assertEqual(code, 2, f"{mode} {name} was accepted")
                self.assertRegex(err, f"backup read of {re.escape(name)} failed \\(exit 1\\)|checksum mismatch")
                self.assertEqual(self.mutating_calls(), [])

    def test_staging_with_sudo_reads_leaves_the_backup_byte_and_metadata_identical(self):
        before = self.snapshot()
        script = self.fake_sudo()
        with patch.object(stage_module, "BackupReader",
                          side_effect=lambda backup, *, sudo, python: _SudoForTest(backup, script)):
            code, result, err = self.plan_and_stage("--backup-sudo")
        self.assertEqual(code, 0, err)
        self.assertEqual(result["result"], "STAGING_PASSED")
        self.assertEqual(self.snapshot(), before)

    def test_backup_metadata_change_during_staging_halts(self):
        real = self.host.run

        def touch_backup(args):
            out = real(args)
            if args[args.index("--entrypoint") + 1] == "tar":
                os.utime(self.backup / "zwa-demo_caddy-data.tar.gz", ns=(1, 1))
            return out
        self.host.run = touch_backup
        code, _, err = self.plan_and_stage()
        self.assertEqual(code, 2)
        self.assertIn("original backup directory's files or metadata changed", err)

    def test_wallet_and_ledger_bytes_never_reach_output_or_evidence(self):
        code, _, err = self.plan_and_stage()
        self.assertEqual(code, 0, err)
        secrets_ = (b"dealer wallet", b"judge wallet", b"SQLite format 3")
        for path in (self.work / "evidence").iterdir():
            for marker in secrets_:
                self.assertNotIn(marker, path.read_bytes(), path.name)
        code, out, err = self.main("stop")
        for marker in secrets_:
            self.assertNotIn(marker.decode(), json.dumps(out) + err)

    def test_sudo_interpreter_must_be_root_owned(self):
        user_owned = self.work / "python3"
        user_owned.write_text("")
        with self.assertRaisesRegex(stage_module.StageError, "root-owned"):
            stage_module.BackupReader(self.backup, sudo=True, python=str(user_owned))
        with self.assertRaisesRegex(stage_module.StageError, "unavailable"):
            stage_module.BackupReader(self.backup, sudo=True, python=str(self.work / "missing"))
        if os.path.exists(stage_module.SYSTEM_PYTHON) and os.stat(stage_module.SYSTEM_PYTHON).st_uid == 0:
            reader = stage_module.BackupReader(self.backup, sudo=True)
            self.assertEqual(reader.command("read", str(self.backup), "SHA256SUMS"),
                             ["sudo", "-n", "/usr/bin/python3", "-I", "-c", stage_module.BACKUP_READER,
                              "read", str(self.backup), "SHA256SUMS"])


class _SudoForTest(RealBackupReader):
    """The real reader with --backup-sudo semantics, but `sudo` is the logging test double."""

    def __init__(self, backup, script):
        self.backup, self.sudo, self.popen = str(backup), True, subprocess.Popen
        self.prefix = [str(script), sys.executable]


class BackupReaderProgramTests(StageFixture):
    """The fixed reader program itself, run as its own process."""

    def run_reader(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-I", "-c", stage_module.BACKUP_READER, *args],
                              capture_output=True, timeout=20)

    def test_reader_refuses_non_canonical_paths_special_files_and_other_requests(self):
        alias = self.work / "alias"
        alias.symlink_to(self.backup)
        result = self.run_reader("list", str(alias))
        self.assertEqual(result.returncode, 3)
        self.assertIn(b"not a canonical absolute path", result.stderr)
        os.mkfifo(self.backup / "pipe.tar.gz")
        self.addCleanup((self.backup / "pipe.tar.gz").unlink)
        result = self.run_reader("read", str(self.backup), "pipe.tar.gz")  # must not hang on a FIFO
        self.assertEqual((result.returncode, result.stdout), (3, b""))
        self.assertIn(b"is not a regular file", result.stderr)
        for request in (("read", str(self.backup), "../x"), ("read", str(self.backup), ".."),
                        ("write", str(self.backup), "x"), ("read", "relative", "SHA256SUMS"), ("read",)):
            with self.subTest(request):
                self.assertEqual(self.run_reader(*request).returncode, 3)

    def test_reader_lists_metadata_only_and_streams_exact_bytes(self):
        listing = json.loads(self.run_reader("list", str(self.backup)).stdout)
        self.assertEqual(sorted(listing), sorted(p.name for p in self.backup.iterdir()))
        self.assertTrue(all(len(entry) == 5 for entry in listing.values()))
        archive = self.backup / "zwa-demo_zwa-root.tar.gz"
        self.assertEqual(self.run_reader("read", str(self.backup), archive.name).stdout, archive.read_bytes())


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

    def test_same_size_content_corruption_of_a_restored_file_halts_staging(self):
        def flip_one_byte(volume: str, directory: Path) -> None:
            if volume == "zwa-params":
                target = directory / "sapling-spend.params"
                data = bytearray(target.read_bytes())
                data[0] ^= 0xFF  # same size, owner and mode; different content
                target.write_bytes(bytes(data))
        self.host.corrupt_hook = flip_one_byte
        code, _, err = self.plan_and_stage()
        self.assertEqual(code, 2, "a corrupted restored file was accepted")
        self.assertIn("restored zwa-params content differs from the archive at sapling-spend.params", err)
        self.assertFalse(any("up" in c for c in self.host.calls))  # zebra never started

    def test_corruption_of_a_hard_linked_or_state_file_is_caught(self):
        def corrupt_state(volume: str, directory: Path) -> None:
            if volume == "zebra-state":
                linked = directory / "state/v26/000001.hardlink"
                self.assertEqual(linked.stat().st_ino, (directory / "state/v26/000001.sst").stat().st_ino)
                linked.write_bytes(b"rocksdX")  # same length as b"rocksdb", through the hard link
        self.host.corrupt_hook = corrupt_state
        code, _, err = self.plan_and_stage()
        self.assertEqual(code, 2)
        self.assertIn("restored zebra-state content differs from the archive at state/v26/000001.", err)

    def test_hard_link_to_a_missing_or_non_regular_member_is_refused(self):
        self.build_archives(extra=lambda tar: add(tar, "./zwa4e-demo/link", kind="h", link="./zwa4e-demo/missing"))
        code, _, err = self.main("plan")
        self.assertEqual(code, 2)
        self.assertIn("hard link to an unknown or non-regular member", err)

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
