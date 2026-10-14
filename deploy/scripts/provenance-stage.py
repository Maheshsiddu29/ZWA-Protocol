#!/usr/bin/env python3
"""Stage the Zebra provenance migration on NEW volumes restored from a paired backup.

Runs on the AMD64 EC2 Docker host from a separate checkout of the reviewed
commit, never from the live checkout. It never touches the live Compose
project or its volumes. The backup is mounted read-only and checksummed
before and after. Every Compose command uses compose.yaml together with the
host's AMD64 override, deploy/compose.amd64.yaml.

  plan   Read-only. Validates every input; verifies the backup checksums and
         the .tar.gz contents (paths, types, owners, modes, the chain
         identity, the proving parameters); renders and checks the Compose
         configuration; checks the pinned AMD64 image and the engine image's
         code; refuses existing staging volumes or containers. Prints the
         rendered-configuration sha256 for the operator to review.
  stage  Repeats every plan check and requires --rendered-sha256 from plan.
         Then: creates the new volumes, restores them preserving numeric
         owners and modes, and verifies them against the archives. It starts
         only zebra and checks the settled transaction. It runs asserted
         negative drills (each must refuse, and the identity hash must not
         change), then the migration and a revert rehearsal, and compares
         ledger/wallet/order files and state reports before and after. It
         stops at the first unexpected result.
  stop   `docker compose down` for the staging project. Volumes are kept.

All values come from validated options; nothing is interpolated into a shell.
Exit 0 = passed, 2 = refused or failed (stop and inspect), 3 = uncertain write
(do not revert; see docs/zebra-provenance-migration.md).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tarfile

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY))
from exchange.persistent_chain import (COMPOSE_PROJECT, PINNED_ZEBRA_IMAGES, RUNTIME_EVIDENCE,  # noqa: E402
                                       RUNTIME_KEY, ChainError, validate_identity)

VOLUMES = ("zwa-root", "zebra-state", "zwa-params")
IDENTITY_MEMBER = "zwa4e-demo/chain-identity.json"
ENGINE_ROOT = "/var/lib/zwa/zwa4e-demo"
# The base compose.yaml entrypoint: the layout of the ARM64 image. Never run it against the AMD64 image.
ARM64_ENTRYPOINT = ["target/release/zebrad", "-c", "/config/regtest-config.toml"]
PLATFORM = "linux/amd64"
PARAMS_SHA256 = {
    "sapling-spend.params": "8e48ffd23abb3a5fd9c5589204f32d9c31285a04b78096ba40a79b75677efc13",
    "sapling-output.params": "2f0ebbcbb9bb0bcffe95a397e7eba89c29eb4dde6191c339db88570e3f3fb0e4",
}
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
PAIRED = re.compile(r"paired-[A-Za-z0-9._-]{1,80}\Z")
CHECKSUM_LINE = re.compile(r"([0-9a-f]{64}) [ *]([A-Za-z0-9._-]{1,128})\Z")
SAFE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
            "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY")

# Fixed programs run inside one-shot engine containers; inputs only via argv.
SETTLED_CHECK = """
import json, sys, time
from exchange.persistent_chain import _rpc
txid, height = sys.argv[1], int(sys.argv[2])
rpc = lambda method, params=None: _rpc(18232, method, params, host="zebra")
for _ in range(90):
    try:
        tip = rpc("getblockcount")
        break
    except Exception:
        time.sleep(2)
else:
    raise SystemExit("zebra RPC did not become ready")
block = rpc("getblock", [str(height), 1])
print(json.dumps({"tip": tip, "height": height, "blockHash": rpc("getblockhash", [height]),
                  "blockHashInBlock": block.get("hash"), "txInBlock": txid in block.get("tx", [])},
                 sort_keys=True))
"""
FILES_SNAPSHOT = """
import hashlib, json, os, stat, sys
root, identity = sys.argv[1], sys.argv[2]
skip = {identity, os.path.join(os.path.dirname(identity), ".chain-identity.lock")}
out = {}
for base, dirs, files in os.walk(root):
    dirs.sort()
    for name in sorted(dirs + files):
        path = os.path.join(base, name)
        if path in skip:
            continue
        info = os.lstat(path)
        entry = [stat.filemode(info.st_mode), info.st_uid, info.st_gid]
        if stat.S_ISREG(info.st_mode):
            digest = hashlib.sha256()
            try:
                with open(path, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1 << 20), b""):
                        digest.update(chunk)
                entry.append(digest.hexdigest())
            except OSError:
                entry.append("UNREADABLE")
        elif stat.S_ISLNK(info.st_mode):
            entry.append(os.readlink(path))
        out[os.path.relpath(path, root)] = entry
print(json.dumps(out, sort_keys=True))
"""
STATUS_CHECK = """
import json, sys
from pathlib import Path
from exchange import persistent_chain
print(json.dumps(persistent_chain.status(Path(sys.argv[1]), "zebra", 18232, host="zebra",
                                         managed="external"), sort_keys=True))
"""


class StageError(RuntimeError):
    pass


class UncertainStage(StageError):
    pass


@dataclass
class Result:
    code: int
    out: str
    err: str


def load_attest_tool():
    spec = importlib.util.spec_from_file_location("zebra_runtime_attest_stage",
                                                  Path(__file__).resolve().parent / "zebra-runtime-attest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Docker:
    """Docker CLI with an explicit, validated prefix and a minimal environment."""

    def __init__(self, sudo: bool, runner=subprocess.run):
        self.prefix = ["sudo", "-n", "docker"] if sudo else ["docker"]
        self.runner = runner
        self.env = {key: os.environ[key] for key in SAFE_ENV if key in os.environ}

    def __call__(self, *args: str, input: bytes | None = None, timeout: int = 1800) -> Result:
        done = self.runner([*self.prefix, *args], input=input, capture_output=True, env=self.env,
                           timeout=timeout)
        out, err = done.stdout, done.stderr
        return Result(done.returncode, out.decode() if isinstance(out, bytes) else out,
                      err.decode() if isinstance(err, bytes) else err)

    def ok(self, *args: str, input: bytes | None = None, what: str = "docker command") -> str:
        result = self(*args, input=input)
        if result.code:
            hint = (" (Docker needs root on this host: re-run with --docker-sudo after `sudo -v`)"
                    if "permission denied" in result.err.lower() and self.prefix[0] != "sudo" else "")
            raise StageError(f"{what} failed (exit {result.code}): {result.err.strip()[:400]}{hint}")
        return result.out


@dataclass
class Config:
    backup_dir: Path
    recovery_root: Path
    checksum_file: str
    source_project: str
    project: str
    engine_tag: str
    env_file: Path
    evidence: Path
    expect_genesis: str
    expect_anchor_height: int
    settled_txid: str
    settled_height: int
    docker_sudo: bool = False
    deploy_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parents[1])
    repo_root: Path = REPOSITORY

    @property
    def base(self) -> Path:
        return self.deploy_dir / "compose.yaml"

    @property
    def override(self) -> Path:
        return self.deploy_dir / "compose.amd64.yaml"

    @property
    def stage_env(self) -> Path:
        return self.evidence / "stage.env"

    @property
    def engine_image(self) -> str:
        return f"zwa-engine:{self.engine_tag}"

    def archive(self, volume: str) -> str:
        return f"{self.source_project}_{volume}.tar.gz"


def validate_config(cfg: Config) -> None:
    if not COMPOSE_PROJECT.fullmatch(cfg.project) or not cfg.project.startswith("zwa-prov-"):
        raise StageError("--project must be a Compose project name starting with zwa-prov-")
    if not COMPOSE_PROJECT.fullmatch(cfg.source_project) or cfg.project == cfg.source_project:
        raise StageError("--source-project must be valid and differ from the staging project")
    if not TAG.fullmatch(cfg.engine_tag):
        raise StageError("invalid --engine-tag")
    if not HEX64.fullmatch(cfg.expect_genesis) or not HEX64.fullmatch(cfg.settled_txid):
        raise StageError("--expect-genesis and the settled txid must be 64 lowercase hex characters")
    if not 0 <= cfg.settled_height <= cfg.expect_anchor_height:
        raise StageError("the settled transaction must be at or below the expected durable anchor")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", cfg.checksum_file):
        raise StageError("--checksum-file must be a plain file name inside the backup directory")
    recovery = cfg.recovery_root.resolve()
    backup = cfg.backup_dir
    if (not backup.is_absolute() or backup.is_symlink() or not backup.is_dir()
            or backup.resolve().parent != recovery or not PAIRED.fullmatch(backup.name)):
        raise StageError(f"--backup-dir must be an existing {recovery}/paired-* directory (not a symlink)")
    if not cfg.evidence.is_absolute() or cfg.evidence.is_symlink():
        raise StageError("--evidence-dir must be an absolute path (not a symlink)")
    evidence = cfg.evidence.resolve() if cfg.evidence.exists() else cfg.evidence.parent.resolve() / cfg.evidence.name
    if evidence == backup.resolve() or backup.resolve() in evidence.parents:
        raise StageError("--evidence-dir must be outside the backup directory")
    for path, what in ((cfg.base, "compose.yaml"), (cfg.override, "deploy/compose.amd64.yaml"),
                       (cfg.deploy_dir / "zebra/regtest-config.toml", "zebra/regtest-config.toml"),
                       (cfg.env_file, "--env-file")):
        if path.is_symlink() or not path.is_file():
            raise StageError(f"{what} must be an existing regular file: {path}")
    web = cfg.deploy_dir / "env/web.env"
    if web.is_symlink() or not web.is_file() or stat.S_IMODE(web.stat().st_mode) != 0o600:
        raise StageError("deploy/env/web.env must exist with mode 600 in the staging checkout")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_checksums(cfg: Config) -> dict[str, str]:
    listing = cfg.backup_dir / cfg.checksum_file
    if listing.is_symlink() or not listing.is_file():
        raise StageError(f"checksum file {cfg.checksum_file} is missing from the backup directory")
    expected = {}
    for number, line in enumerate(listing.read_text().splitlines(), 1):
        if not line.strip():
            continue
        match = CHECKSUM_LINE.fullmatch(line.strip())
        if not match or match.group(2) in expected:
            raise StageError(f"unrecognized or duplicate checksum line {number}")
        expected[match.group(2)] = match.group(1)
    for volume in VOLUMES:
        if cfg.archive(volume) not in expected:
            raise StageError(f"{cfg.archive(volume)} is not listed in {cfg.checksum_file}")
    for name, digest in expected.items():
        path = cfg.backup_dir / name
        if path.is_symlink() or not path.is_file():
            raise StageError(f"{name} is missing or not a regular file")
        if _sha256(path) != digest:
            raise StageError(f"checksum mismatch: {name}")
    return expected


def _member_path(name: str) -> str:
    path = name[2:] if name.startswith("./") else name
    path = path.rstrip("/")
    if path == ".":
        return ""
    parts = path.split("/") if path else []
    if name.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise StageError(f"unsafe archive member path: {name!r}")
    return path


def _member_bytes(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    handle = archive.extractfile(member)
    if handle is None:
        raise StageError(f"archive member could not be read: {member.name!r}")
    return handle.read()


def inspect_archive(path: Path, volume: str, cfg: Config) -> tuple[dict, bytes | None]:
    """Manifest {path: [type, uid, gid, mode, size]} of a safe .tar.gz, read without extracting."""
    manifest: dict[str, list] = {}
    symlinks: set[str] = set()
    identity = None
    params: dict[str, str] = {}
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            for member in archive:
                name = _member_path(member.name)
                if name in manifest:
                    raise StageError(f"duplicate archive member: {name!r}")
                parents = name.split("/")[:-1]
                if any("/".join(parents[:i + 1]) in symlinks for i in range(len(parents))):
                    raise StageError(f"archive member below a symlink: {name!r}")
                if member.mode & 0o7000:
                    raise StageError(f"setuid/setgid/sticky archive member: {name!r}")
                if member.isreg():
                    kind = "f"
                elif member.isdir():
                    kind = "d"
                elif member.issym():
                    kind = "l"
                    symlinks.add(name)
                elif member.islnk():
                    if _member_path(member.linkname) not in manifest:
                        raise StageError(f"hard link to an unknown member: {name!r}")
                    kind = "h"
                else:
                    raise StageError(f"device, FIFO or other special archive member: {name!r}")
                manifest[name] = [kind, member.uid, member.gid, member.mode & 0o7777,
                                  member.size if kind == "f" else None]
                if volume == "zwa-root" and name == IDENTITY_MEMBER and kind == "f":
                    if member.size > 8192:
                        raise StageError("archived chain identity is too large")
                    identity = _member_bytes(archive, member)
                if volume == "zwa-params" and name in PARAMS_SHA256 and kind == "f":
                    params[name] = hashlib.sha256(_member_bytes(archive, member)).hexdigest()
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise StageError(f"{path.name} is not a readable .tar.gz archive: {exc}") from exc
    if volume == "zwa-root":
        if identity is None:
            raise StageError(f"{path.name} has no regular {IDENTITY_MEMBER}")
        check_archived_identity(identity, cfg)
    if volume == "zebra-state" and not any(name.startswith("state/") for name in manifest):
        raise StageError(f"{path.name} contains no Zebra state/ directory")
    if volume == "zwa-params" and params != PARAMS_SHA256:
        raise StageError(f"{path.name} does not contain the checksummed Sapling parameters")
    return manifest, identity


def check_archived_identity(raw: bytes, cfg: Config) -> dict:
    try:
        meta = validate_identity(json.loads(raw))
    except (ValueError, ChainError) as exc:
        raise StageError(f"archived chain identity is invalid: {exc}") from exc
    if meta.get("management") != "external" or meta.get("detectedReset"):
        raise StageError("archived chain identity is not an active external (Compose) identity")
    if meta["genesisHash"] != cfg.expect_genesis or meta["anchorHeight"] != cfg.expect_anchor_height:
        raise StageError("archived chain identity differs from --expect-genesis/--expect-anchor-height")
    if RUNTIME_KEY in meta:
        raise StageError("archived chain identity already records a runtime; this backup is not pre-migration")
    return meta


def stage_env_bytes(cfg: Config) -> bytes:
    """The operator's .env with only ZWA_ENGINE_TAG pointed at the reviewed staging engine image."""
    lines = [line for line in cfg.env_file.read_text().splitlines()
             if not re.match(r"\s*(export\s+)?ZWA_ENGINE_TAG\s*=", line)]
    return ("\n".join(lines + [f"ZWA_ENGINE_TAG={cfg.engine_tag}"]) + "\n").encode()


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def source_revision(repo_root: Path) -> str:
    """HEAD of this checkout; refuses tracked or untracked changes other than the AWS-local files."""
    head = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"], capture_output=True, text=True)
    status = subprocess.run(["git", "-C", str(repo_root), "status", "--porcelain"], capture_output=True, text=True)
    if head.returncode or status.returncode:
        raise StageError("the staging checkout must be a git checkout of the reviewed commit")
    extra = [line for line in status.stdout.splitlines() if line != "?? deploy/compose.amd64.yaml"]
    if extra:
        raise StageError("the staging checkout has local changes: " + "; ".join(extra[:5]))
    return head.stdout.strip()


class Stage:
    def __init__(self, cfg: Config, docker: Docker | None = None, attest_tool=None, log=print):
        self.cfg = cfg
        self.docker = docker or Docker(cfg.docker_sudo)
        self.tool = attest_tool or load_attest_tool()
        self.tool._docker = self._tool_docker  # host checks go through the same validated Docker access
        self.log = log

    def _tool_docker(self, *args: str) -> str:
        result = self.docker(*args)
        if result.code:
            raise self.tool.AttestError(result.err.strip() or f"docker {args[0]} failed")
        return result.out

    # ------------------------------------------------------------------ helpers

    def compose(self, *args: str, input: bytes | None = None) -> Result:
        cfg = self.cfg
        return self.docker("compose", "--project-directory", str(cfg.deploy_dir), "--env-file", str(cfg.stage_env),
                           "-p", cfg.project, "-f", str(cfg.base), "-f", str(cfg.override), *args, input=input)

    def engine(self, *args: str, input: bytes | None = None) -> Result:
        return self.compose("run", "--rm", "--no-deps", "-T", "--entrypoint", "python3", "portal", *args,
                            input=input)

    def engine_json(self, what: str, *args: str, input: bytes | None = None) -> dict:
        result = self.engine(*args, input=input)
        if result.code:
            raise StageError(f"{what} failed (exit {result.code}): {result.err.strip()[:400]}")
        try:
            return json.loads(result.out)
        except ValueError as exc:
            raise StageError(f"{what} printed no JSON") from exc

    def save(self, name: str, data) -> None:
        raw = data if isinstance(data, bytes) else (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
        _write_private(self.cfg.evidence / name, raw)

    # --------------------------------------------------------------------- plan

    def render(self) -> tuple[dict, bytes]:
        result = self.compose("config", "--format", "json")
        if result.code:
            raise StageError(f"docker compose config failed: {result.err.strip()[:400]}")
        raw = result.out.encode()
        try:
            rendered = json.loads(raw)
        except ValueError as exc:
            raise StageError("docker compose config printed no JSON") from exc
        return rendered, raw

    def check_rendered(self, rendered: dict, raw: bytes) -> dict:
        cfg = self.cfg
        if b"docker.sock" in raw or b"/var/run/docker" in raw:
            raise StageError("the rendered configuration mounts the Docker socket")
        if f"{cfg.source_project}_".encode() in raw:
            raise StageError(f"the rendered configuration references {cfg.source_project} volumes")
        if rendered.get("name") != cfg.project:
            raise StageError("the rendered project name differs from --project")
        services = rendered.get("services") or {}
        zebra, portal = services.get("zebra") or {}, services.get("portal") or {}
        entrypoint = zebra.get("entrypoint")
        if (not isinstance(entrypoint, list) or not entrypoint
                or not all(isinstance(part, str) for part in entrypoint)):
            raise StageError("the rendered zebra service has no explicit entrypoint")
        if entrypoint == ARM64_ENTRYPOINT:
            raise StageError("the rendered zebra entrypoint is the ARM64 entrypoint; "
                             "compose.amd64.yaml must override it for the AMD64 image")
        image = zebra.get("image")
        if not isinstance(image, str):
            raise StageError("the rendered zebra service has no image")
        try:
            image_id, platform = self.tool.check_image(image)
        except self.tool.AttestError as exc:
            raise StageError(f"zebra image check failed: {exc}") from exc
        if (platform, image_id) != (PLATFORM, PINNED_ZEBRA_IMAGES[PLATFORM]):
            raise StageError("the rendered zebra image is not the pinned AMD64 image")
        if portal.get("image") != cfg.engine_image:
            raise StageError(f"the rendered portal image is not {cfg.engine_image}")
        for name in ("zebra", "portal"):
            if (services.get(name) or {}).get("ports"):
                raise StageError(f"the rendered {name} service publishes ports")
        volumes = rendered.get("volumes") or {}
        for volume in VOLUMES:
            spec = volumes.get(volume) or {}
            if spec.get("name") != f"{cfg.project}_{volume}" or spec.get("external"):
                raise StageError(f"the rendered {volume} volume is not the new {cfg.project}_{volume}")
        mounts = {(m.get("source"), m.get("target")) for m in zebra.get("volumes") or []}
        if ("zebra-state", "/data") not in mounts:
            raise StageError("the rendered zebra service does not use the zebra-state volume at /data")
        portal_mounts = {(m.get("source"), m.get("target"), bool(m.get("read_only")))
                         for m in portal.get("volumes") or []}
        if ("zwa-root", "/var/lib/zwa", False) not in portal_mounts:
            raise StageError("the rendered portal service does not mount zwa-root at /var/lib/zwa")
        return {"zebraImage": image, "zebraImageId": image_id, "zebraEntrypoint": entrypoint}

    def check_engine_image(self) -> dict:
        info = self.docker.ok("image", "inspect", "--", self.cfg.engine_image, what="engine image inspect")
        data = json.loads(info)
        if not isinstance(data, list) or len(data) != 1 or f"{data[0].get('Os')}/{data[0].get('Architecture')}" \
                != PLATFORM:
            raise StageError(f"{self.cfg.engine_image} is not a linux/amd64 image")
        listing = self.docker.ok("run", "--rm", "--network", "none", "--entrypoint", "sh", self.cfg.engine_image,
                                 "-c", "cd /app/ZWA-Brand-Integration && sha256sum exchange/*.py",
                                 what="engine code listing")
        inside = {}
        for line in listing.splitlines():
            digest, _, name = line.partition("  ")
            inside[name] = digest
        expected = {f"exchange/{p.name}": _sha256(p) for p in sorted((self.cfg.repo_root / "exchange").glob("*.py"))
                    if not p.name.startswith("test_")}
        if inside != expected:
            differ = sorted(set(inside.items()) ^ set(expected.items()))[:5]
            raise StageError(f"{self.cfg.engine_image} does not contain the reviewed exchange code: {differ}")
        return {"engineImageId": data[0].get("Id"), "exchangeFiles": len(expected)}

    def check_isolation(self) -> None:
        cfg = self.cfg
        for volume in VOLUMES:
            result = self.docker("volume", "inspect", f"{cfg.project}_{volume}")
            if result.code == 0:
                raise StageError(f"volume {cfg.project}_{volume} already exists; staging needs new volumes")
            if "no such volume" not in result.err.lower():
                raise StageError(f"could not confirm that {cfg.project}_{volume} is absent: {result.err.strip()}")
        label = f"label=com.docker.compose.project={cfg.project}"
        if self.docker.ok("ps", "-a", "-q", "--filter", label).strip():
            raise StageError(f"containers already exist for Compose project {cfg.project}")
        if self.docker.ok("volume", "ls", "-q", "--filter", label).strip():
            raise StageError(f"volumes already exist for Compose project {cfg.project}")

    def plan(self, *, fresh_evidence: bool = True) -> dict:
        cfg = self.cfg
        validate_config(cfg)
        revision = source_revision(cfg.repo_root)
        if fresh_evidence:
            if cfg.evidence.exists():
                raise StageError("--evidence-dir must not exist yet")
            cfg.evidence.mkdir(mode=0o700, parents=False)
            _write_private(cfg.stage_env, stage_env_bytes(cfg))
        elif cfg.stage_env.read_bytes() != stage_env_bytes(cfg):
            raise StageError("--env-file changed since plan")
        checksums = verify_checksums(cfg)
        manifests, identity = {}, None
        for volume in VOLUMES:
            manifests[volume], found = inspect_archive(cfg.backup_dir / cfg.archive(volume), volume, cfg)
            identity = found if volume == "zwa-root" else identity
        if identity is None:
            raise StageError("no chain identity found in the zwa-root archive")
        host = self.tool.host_platform()
        if host != PLATFORM:
            raise StageError(f"this Docker host is {host}, not {PLATFORM}")
        rendered, raw = self.render()
        zebra = self.check_rendered(rendered, raw)
        engine = self.check_engine_image()
        self.check_isolation()
        summary = {"sourceRevision": revision, "project": cfg.project, "sourceProject": cfg.source_project,
                   "backupDir": str(cfg.backup_dir), "checksums": checksums,
                   "archivedIdentitySha256": hashlib.sha256(identity).hexdigest(),
                   "renderedSha256": hashlib.sha256(raw).hexdigest(),
                   "overrideSha256": _sha256(cfg.override), "stageEnvSha256": _sha256(cfg.stage_env),
                   "zebra": zebra, "engine": engine, "dockerPrefix": self.docker.prefix}
        self.manifests, self.archived_identity, self.rendered_raw = manifests, identity, raw
        return summary

    # -------------------------------------------------------------------- stage

    def restore(self) -> None:
        cfg = self.cfg
        for volume in VOLUMES:
            name = f"{cfg.project}_{volume}"
            self.docker.ok("volume", "create", "--label", f"com.docker.compose.project={cfg.project}",
                           "--label", f"com.docker.compose.volume={volume}", name, what=f"create {name}")
            self.docker.ok("run", "--rm", "--network", "none", "--user", "0:0", "--entrypoint", "tar",
                           "-v", f"{name}:/dst", "-v", f"{cfg.backup_dir}:/backup:ro", cfg.engine_image,
                           "--numeric-owner", "--same-owner", "--same-permissions", "-xzf",
                           f"/backup/{cfg.archive(volume)}", "-C", "/dst", what=f"restore {name}")
            listing = self.docker.ok("run", "--rm", "--network", "none", "--user", "0:0", "--entrypoint", "find",
                                     "-v", f"{name}:/dst:ro", cfg.engine_image, "/dst", "-printf",
                                     "%P\\t%y\\t%U\\t%G\\t%m\\t%s\\n", what=f"list {name}")
            self.compare_restored(volume, listing)

    def compare_restored(self, volume: str, listing: str) -> None:
        restored = {}
        for line in listing.splitlines():
            parts = line.split("\t")
            if len(parts) != 6:
                raise StageError(f"unexpected listing line from {volume}: {line[:120]!r}")
            path, kind, uid, gid, mode, size = parts
            restored[path] = [kind, int(uid), int(gid), int(mode, 8), int(size)]
        manifest = self.manifests[volume]
        for path, (kind, uid, gid, mode, size) in manifest.items():
            got = restored.get(path)
            want_kind = "f" if kind == "h" else kind
            if (got is None or got[0] != want_kind or got[1:3] != [uid, gid]
                    or (kind in ("f", "d") and got[3] != mode) or (kind == "f" and got[4] != size)):
                raise StageError(f"restored {volume} differs from the archive at {path or '/'}: "
                                 f"archive {[kind, uid, gid, oct(int(mode)), size]} restored {got}")
        extra = set(restored) - set(manifest) - {""}
        if extra:
            raise StageError(f"restored {volume} has entries not in the archive: {sorted(extra)[:5]}")

    def host_attest(self, name: str, zebra: dict) -> dict:
        try:
            attestation = self.tool.attest(zebra["zebraImage"], self.cfg.project)
            info = self.tool.zebra_container(self.cfg.project)
        except self.tool.AttestError as exc:
            raise StageError(f"host attestation failed: {exc}") from exc
        entrypoint = (info.get("Config") or {}).get("Entrypoint")
        if entrypoint != zebra["zebraEntrypoint"] or entrypoint == ARM64_ENTRYPOINT:
            raise StageError("the running zebra container does not use the reviewed AMD64 entrypoint")
        self.save(name, attestation)
        return attestation

    def show(self, name: str) -> dict:
        shown = self.engine_json("show", "-m", "exchange.zebra_provenance", "show", "--root", ENGINE_ROOT)
        self.save(name, shown)
        return shown

    def settled(self, name: str) -> dict:
        cfg = self.cfg
        result = self.engine_json("settled transaction check", "-c", SETTLED_CHECK, cfg.settled_txid,
                                  str(cfg.settled_height))
        self.save(name, result)
        if (not result.get("txInBlock") or result.get("blockHash") != result.get("blockHashInBlock")
                or type(result.get("tip")) is not int or result["tip"] < cfg.expect_anchor_height):
            raise StageError(f"settled transaction or durable anchor missing from the restored chain: {result}")
        return result

    def files(self, name: str) -> dict:
        snapshot = self.engine_json("file snapshot", "-c", FILES_SNAPSHOT, "/var/lib/zwa",
                                    f"{ENGINE_ROOT}/chain-identity.json")
        self.save(name, snapshot)
        return snapshot

    def state_report(self, name: str) -> dict:
        script = (self.cfg.deploy_dir / "scripts/m10-state-report.py").read_bytes()
        report = self.engine_json("m10 state report", "-", input=script)
        self.save(name, report)
        return report

    def record_args(self, *, anchor: int | None = None, project: str | None = None) -> list[str]:
        cfg = self.cfg
        return ["-m", "exchange.zebra_provenance", "record", "--root", ENGINE_ROOT, "--rpc-host", "zebra",
                "--expect-genesis", cfg.expect_genesis,
                "--expect-anchor-height", str(cfg.expect_anchor_height if anchor is None else anchor),
                "--expect-compose-project", cfg.project if project is None else project]

    def negative(self, name: str, args: list[str], stdin: bytes, refusal: str) -> dict:
        """A drill passes only if it exits 1 with the expected refusal and the identity is byte-identical."""
        before = self.show(f"drill-{name}-before.json")["sha256"]
        result = self.engine(*args, input=stdin)
        after = self.show(f"drill-{name}-after.json")["sha256"]
        outcome = {"drill": name, "exit": result.code, "stderr": result.err.strip()[-400:],
                   "identityUnchanged": before == after}
        self.save(f"drill-{name}.json", outcome)
        if result.code != 1 or "PROVENANCE_REFUSED" not in result.err or refusal not in result.err:
            raise StageError(f"negative drill {name!r} did not refuse as expected (exit {result.code}); "
                             "staging halted")
        if before != after:
            raise StageError(f"negative drill {name!r} changed the chain identity; staging halted")
        return outcome

    def positive(self, what: str, args: list[str], stdin: bytes | None = None) -> dict:
        result = self.engine(*args, input=stdin)
        if result.code == 3:
            raise UncertainStage(f"{what}: UNCERTAIN write outcome. Do not revert. {result.err.strip()[:400]}")
        if result.code:
            raise StageError(f"{what} failed (exit {result.code}): {result.err.strip()[:400]}")
        return json.loads(result.out)

    def stage(self, rendered_sha256: str) -> dict:
        cfg = self.cfg
        plan_path = cfg.evidence / "plan.json"
        if plan_path.is_symlink() or not plan_path.is_file() or (cfg.evidence / "stage.json").exists():
            raise StageError("run `plan` first into this --evidence-dir; stage runs once per evidence directory")
        planned = json.loads(plan_path.read_text())
        summary = self.plan(fresh_evidence=False)
        if not HEX64.fullmatch(rendered_sha256) or rendered_sha256 != summary["renderedSha256"] \
                or planned.get("renderedSha256") != rendered_sha256:
            raise StageError("--rendered-sha256 does not match the reviewed plan and the current rendering")
        for key in ("checksums", "archivedIdentitySha256", "overrideSha256", "stageEnvSha256", "zebra", "engine"):
            if planned.get(key) != summary[key]:
                raise StageError(f"{key} changed since plan")
        log = self.log

        log("restoring new volumes")
        self.restore()
        if verify_checksums(cfg) != summary["checksums"]:
            raise StageError("backup checksums changed during restore")

        log("starting zebra only")
        up = self.compose("up", "-d", "--no-deps", "zebra")
        if up.code:
            raise StageError(f"starting zebra failed: {up.err.strip()[:400]}")
        self.host_attest("attestation.json", summary["zebra"])
        self.settled("settled-before.json")
        shown = self.show("show-before.json")
        if (shown["sha256"] != summary["archivedIdentitySha256"] or shown["genesisHash"] != cfg.expect_genesis
                or shown["anchorHeight"] != cfg.expect_anchor_height
                or shown["provenance"]["evidence"] != "LABEL_ONLY"):
            raise StageError("restored identity differs from the archived pre-migration identity")
        self.state_report("state-before.json")
        files_before = self.files("files-before.json")

        log("negative drills")
        attestation = self.host_attest("attestation-drills.json", summary["zebra"])
        good = json.dumps(attestation).encode()

        def changed(**fields) -> bytes:
            return json.dumps(dict(attestation, **fields)).encode()
        unpinned = "sha256:" + "0" * 64
        arm64 = PINNED_ZEBRA_IMAGES["linux/arm64"]
        drills = [
            self.negative("wrong-anchor-height", self.record_args(anchor=cfg.expect_anchor_height - 1), good,
                          "expected genesis or anchor height"),
            self.negative("unpinned-image", self.record_args(), changed(imageId=unpinned, containerImageId=unpinned),
                          "not pinned"),
            self.negative("architecture-mismatch", self.record_args(),
                          changed(imageId=arm64, containerImageId=arm64), "architecture differs from its pin"),
            self.negative("arm64-on-amd64", self.record_args(),
                          changed(imageId=arm64, containerImageId=arm64, platform="linux/arm64"),
                          "differs from this machine's platform"),
            self.negative("other-compose-project", self.record_args(project=cfg.source_project), good,
                          "not the expected"),
            self.negative("stale-attestation", self.record_args(), changed(attestedAt=attestation["attestedAt"] - 3600),
                          "stale"),
            self.negative("malformed-attestation", self.record_args(), b"{", "not valid JSON"),
        ]

        log("migration")
        attestation = self.host_attest("attestation-record.json", summary["zebra"])
        good = json.dumps(attestation).encode()
        dry = self.positive("dry run", self.record_args() + ["--dry-run"], good)
        if dry.get("reason") != "DRY_RUN" or self.show("show-after-dry-run.json")["sha256"] != shown["sha256"]:
            raise StageError("the dry run changed the identity")
        written = self.positive("record", self.record_args(), good)
        again = self.positive("record again", self.record_args(), good)
        if written.get("outcome") != "WRITTEN" or again.get("reason") != "ALREADY_RECORDED":
            raise StageError(f"unexpected migration results: {written.get('outcome')}, {again.get('reason')}")
        after = self.show("show-after.json")
        runtime = after.get("zebraRuntime") or {}
        if (after["provenance"]["evidence"] != RUNTIME_EVIDENCE
                or runtime.get("imageId") != PINNED_ZEBRA_IMAGES[PLATFORM]
                or runtime.get("migratedFromSha256") != summary["archivedIdentitySha256"]
                or runtime.get("composeProject") != cfg.project
                or (after["genesisHash"], after["anchorHeight"]) != (cfg.expect_genesis, cfg.expect_anchor_height)):
            raise StageError("the migrated identity is not the expected record")
        if self.files("files-after.json") != files_before:
            raise StageError("files other than the chain identity changed during the migration")
        try:
            fresh = self.tool.verify(summary["zebra"]["zebraImage"], cfg.project, after)
        except self.tool.AttestError as exc:
            raise StageError(f"fresh host verification failed: {exc}") from exc
        status = self.engine_json("status", "-c", STATUS_CHECK, ENGINE_ROOT)
        self.save("status-after.json", status)
        if (status.get("state") not in ("CHAIN_ACTIVE", "CHAIN_RESTARTED")
                or status["provenance"].get("currentRuntimeVerified") is not False):
            raise StageError(f"unexpected status after migration: {status}")
        if self.state_report("state-after.json") != json.loads((cfg.evidence / "state-before.json").read_text()):
            raise StageError("the M10 state report changed across the migration")
        self.settled("settled-after.json")

        log("revert rehearsal")
        self.positive("revert dry run", ["-m", "exchange.zebra_provenance", "revert", "--root", ENGINE_ROOT,
                                         "--dry-run"])
        self.positive("revert", ["-m", "exchange.zebra_provenance", "revert", "--root", ENGINE_ROOT])
        if self.show("show-after-revert.json")["sha256"] != summary["archivedIdentitySha256"]:
            raise StageError("revert did not restore the exact pre-migration identity")
        attestation = self.host_attest("attestation-final.json", summary["zebra"])
        final = self.positive("record after revert", self.record_args(), json.dumps(attestation).encode())
        if final.get("outcome") != "WRITTEN" or self.files("files-final.json") != files_before:
            raise StageError("re-recording after the revert rehearsal failed")
        if verify_checksums(cfg) != summary["checksums"]:
            raise StageError("backup checksums changed during staging")
        result = {"result": "STAGING_PASSED", "project": cfg.project, "renderedSha256": rendered_sha256,
                  "archivedIdentitySha256": summary["archivedIdentitySha256"],
                  "migratedIdentitySha256": final["sha256"]["after"], "drills": [d["drill"] for d in drills],
                  "freshVerification": fresh, "zebraLeftRunning": True}
        self.save("stage.json", result)
        return result

    def stop(self) -> dict:
        result = self.compose("down")
        if result.code:
            raise StageError(f"docker compose down failed: {result.err.strip()[:400]}")
        return {"stopped": self.cfg.project, "volumesKept": [f"{self.cfg.project}_{v}" for v in VOLUMES]}


def parse(argv: list[str] | None) -> tuple[str, Config, str | None]:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("plan", "stage", "stop"))
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--recovery-root", type=Path, default=Path.home() / "zwa-recovery")
    parser.add_argument("--checksum-file", default="SHA256SUMS")
    parser.add_argument("--source-project", default="zwa-demo")
    parser.add_argument("--project", default="zwa-prov-stage")
    parser.add_argument("--engine-tag", required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--expect-genesis", required=True)
    parser.add_argument("--expect-anchor-height", type=int, required=True)
    parser.add_argument("--expect-settled-tx", required=True, metavar="TXID:HEIGHT")
    parser.add_argument("--docker-sudo", action="store_true", help="run Docker as `sudo -n docker`")
    parser.add_argument("--rendered-sha256", help="stage: the renderedSha256 printed by plan")
    args = parser.parse_args(argv)
    txid, _, height = args.expect_settled_tx.partition(":")
    if not height.isdigit():
        parser.error("--expect-settled-tx must be TXID:HEIGHT")
    if args.action == "stage" and not args.rendered_sha256:
        parser.error("stage requires --rendered-sha256 from plan")
    cfg = Config(backup_dir=args.backup_dir, recovery_root=args.recovery_root, checksum_file=args.checksum_file,
                 source_project=args.source_project, project=args.project, engine_tag=args.engine_tag,
                 env_file=args.env_file, evidence=args.evidence_dir, expect_genesis=args.expect_genesis,
                 expect_anchor_height=args.expect_anchor_height, settled_txid=txid, settled_height=int(height),
                 docker_sudo=args.docker_sudo)
    return args.action, cfg, args.rendered_sha256


def main(argv: list[str] | None = None, docker: Docker | None = None) -> None:
    os.umask(0o077)
    action, cfg, rendered = parse(argv)
    stage = Stage(cfg, docker, log=lambda message: print(f"STAGE {message}", file=sys.stderr, flush=True))
    try:
        if action == "plan":
            output = stage.plan()
            stage.save("plan.json", output)
            stage.save("rendered-config.json", stage.rendered_raw)
        elif action == "stage":
            output = stage.stage(rendered or "")
        else:
            validate_config(cfg)
            if not cfg.stage_env.is_file():
                raise StageError("no stage.env in --evidence-dir; nothing to stop")
            output = stage.stop()
    except UncertainStage as exc:
        print(f"STAGE_UNCERTAIN {exc}", file=sys.stderr)
        raise SystemExit(3) from exc
    except (StageError, ChainError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"STAGE_REFUSED {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
