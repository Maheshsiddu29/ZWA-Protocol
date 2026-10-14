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
         configuration; checks the pinned AMD64 image, that zebra's effective
         user can read its configuration through the exact read-only mount,
         and that the engine image's files are the reviewed code and usable
         by the engine user 10001; refuses existing staging volumes or
         containers. Never changes a file mode. Prints the
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
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import subprocess
import sys
import tarfile

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY))
from exchange.persistent_chain import CONFIG as ZEBRA_CONFIG, CONFIG_SHA256  # noqa: E402
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
CHECKSUM_ENTRY = re.compile(r"([0-9a-f]{64}) [ *](\S(?:.*\S)?)\Z")
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
RESTORED_DIGESTS = """
import hashlib, json, os, stat, sys
root = sys.argv[1]
out = {}
for base, dirs, files in os.walk(root):
    for name in dirs + files:
        path = os.path.join(base, name)
        if stat.S_ISREG(os.lstat(path).st_mode):
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
            out[os.path.relpath(path, root)] = digest.hexdigest()
print(json.dumps(out, sort_keys=True))
"""
STATUS_CHECK = """
import json, sys
from pathlib import Path
from exchange import persistent_chain
print(json.dumps(persistent_chain.status(Path(sys.argv[1]), "zebra", 18232, host="zebra",
                                         managed="external"), sort_keys=True))
"""


KNOWN_SERVICES = frozenset({"zebra", "portal", "order-service", "demo-signer", "web", "caddy", "params", "bootstrap"})
DECLARED_VOLUMES = frozenset({"zwa-root", "zwa-params", "zebra-state", "caddy-data", "caddy-config"})
ZEBRA_KEYS = frozenset({"image", "entrypoint", "command", "volumes", "networks", "cap_drop", "security_opt",
                        "restart", "mem_limit", "cpus", "logging", "platform"})
PORTAL_KEYS = frozenset({"image", "entrypoint", "command", "volumes", "networks", "cap_drop", "security_opt",
                         "restart", "mem_limit", "cpus", "logging", "environment", "healthcheck", "depends_on",
                         "read_only", "tmpfs", "user", "platform"})
SETUP_KEYS = frozenset({"image", "entrypoint", "command", "volumes", "networks", "cap_drop", "security_opt",
                        "restart", "mem_limit", "cpus", "logging", "environment", "depends_on", "read_only", "tmpfs",
                        "user", "profiles", "platform"})
# The setup-profile services exactly as deploy/compose.yaml defines them (pinned by a test against that file).
PARAMS_COMMAND = ("cp /src/sapling-spend.params /src/sapling-output.params /opt/zwa/params/ZcashParams/ && "
                  "cd /opt/zwa/params/ZcashParams && printf '%s  %s\\n%s  %s\\n' "
                  "8e48ffd23abb3a5fd9c5589204f32d9c31285a04b78096ba40a79b75677efc13 sapling-spend.params "
                  "2f0ebbcbb9bb0bcffe95a397e7eba89c29eb4dde6191c339db88570e3f3fb0e4 sapling-output.params "
                  "| sha256sum -c -")
BOOTSTRAP_COMMAND = ["python3", "-m", "exchange.demo_bootstrap", "--root", "/var/lib/zwa/zwa4e-demo",
                     "--rpc-host", "zebra", "--rpc-port", "18232", "--params-data-home", "/opt/zwa/params",
                     "--tx-tool-dir", "/opt/zwa/tx-tool", "--node-bin", "/usr/local/bin/node"]
PARAMS_DIR = re.compile(r"/[A-Za-z0-9._/-]{1,250}\Z")
SYSTEM_PATHS = ("/var/lib/docker", "/var/run", "/run", "/proc", "/sys", "/dev", "/etc", "/boot", "/root")
HOST_ACCESS_KEYS = ("privileged", "network_mode", "pid", "ipc", "uts", "userns_mode", "cgroup", "cgroup_parent",
                    "cap_add", "devices", "device_cgroup_rules", "volumes_from", "sysctls", "extra_hosts", "runtime",
                    "isolation", "gpus", "storage_opt")
ELF_MACHINE_X86_64 = 0x3E
ZEBRA_CONFIG_TARGET = "/config/regtest-config.toml"
SAFE_BIND_SOURCE = re.compile(r"/[A-Za-z0-9._/-]{1,4000}\Z")  # no ',' or ':' to break --mount/-v parsing
IMAGE_USER = re.compile(r"[A-Za-z0-9_.-]{0,64}(?::[A-Za-z0-9_.-]{1,64})?\Z")
MAX_ACCOUNT_FILE = 65536
# The engine containers' identity exactly as compose.yaml sets it (`user: "10001:10001"`, checked on the
# rendering), and what the engine reads or runs inside its image.
ENGINE_UID = ENGINE_GID = 10001
ENGINE_CODE = "/app/ZWA-Brand-Integration"
ENGINE_PATHS = ("/app", "/opt/zwa/tx-tool", "/usr/local/bin/node")
ENGINE_EXECUTABLES = ("/opt/zwa/tx-tool/target/release/zcash_tx_tool", "/usr/local/bin/node")
READ, EXECUTE = 4, 1  # permission bits; EXECUTE on a directory is search

# Run inside a one-shot container with the security settings of the container it stands in for (user,
# groups, no capabilities, no-new-privileges, no network, read-only root, the same read-only bind). It
# lists the given paths without following symlinks, really opens and hashes every regular file, and
# prints the identity it ran as. Paths only via argv; it writes nothing.
ACCESS_PROBE = r"""
import hashlib, json, os, stat, sys
report = {"identity": {"uid": os.getuid(), "euid": os.geteuid(), "gid": os.getgid(), "egid": os.getegid(),
                       "groups": sorted(set(os.getgroups()))},
          "files": {}, "dirs": {}, "links": {}, "other": {}, "errors": {}}
def sha256(path):
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None
pending = list(reversed(sys.argv[1:]))
while pending:
    path = pending.pop()
    try:
        info = os.lstat(path)
    except OSError as exc:
        report["errors"][path] = exc.strerror or "error"
        continue
    meta = [stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid]
    if stat.S_ISDIR(info.st_mode):
        try:
            names = sorted(os.listdir(path))
        except OSError:
            names = None
        report["dirs"][path] = meta + [names is not None]
        pending.extend(os.path.join(path, name) for name in reversed(names or []))
    elif stat.S_ISREG(info.st_mode):
        report["files"][path] = meta + [sha256(path)]
    elif stat.S_ISLNK(info.st_mode):
        report["links"][path] = os.readlink(path)
    else:
        report["other"][path] = meta
print(json.dumps(report, sort_keys=True))
"""


# The only code that touches the original backup. A fixed program run as its own process: unprivileged
# by default, or with --backup-sudo as `sudo -n /usr/bin/python3 -I -c BACKUP_READER ...`. It reads and
# lists, never writes; it opens only plain names directly inside one canonical directory, without
# following symlinks, and streams bytes to the driver (no copy is written anywhere).
BACKUP_READER = r"""
import json, os, re, stat, sys
def fail(message):
    sys.stderr.write("BACKUP_READ_REFUSED " + message + "\n")
    sys.exit(3)
if len(sys.argv) < 3:
    fail("usage")
op, backup = sys.argv[1], sys.argv[2]
if not os.path.isabs(backup) or os.path.realpath(backup) != backup:
    fail("backup directory is not a canonical absolute path")
try:
    directory = os.open(backup, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
except OSError as exc:
    fail("cannot open backup directory: " + (exc.strerror or "error"))
if op == "list" and len(sys.argv) == 3:
    out = {}
    for name in sorted(os.listdir(directory)):
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        out[name] = [stat.filemode(info.st_mode), info.st_uid, info.st_gid, info.st_size, info.st_mtime_ns]
    sys.stdout.write(json.dumps(out, sort_keys=True))
elif (op == "read" and len(sys.argv) == 4 and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", sys.argv[3])
        and sys.argv[3] not in (".", "..")):
    try:
        fd = os.open(sys.argv[3], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    except OSError as exc:
        fail("cannot open " + sys.argv[3] + ": " + (exc.strerror or "error"))
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        fail(sys.argv[3] + " is not a regular file")
    out = sys.stdout.buffer
    while True:
        chunk = os.read(fd, 1 << 20)
        if not chunk:
            break
        out.write(chunk)
    out.flush()
else:
    fail("unsupported request")
"""
BACKUP_NAME = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")
MAX_MANIFEST_BYTES = 65536
SYSTEM_PYTHON = "/usr/bin/python3"


class StageError(RuntimeError):
    pass


def trusted_interpreter(path: str) -> None:
    """The interpreter sudo runs must be root-owned and writable only by root."""
    try:
        info = os.stat(path)
    except OSError as exc:
        raise StageError(f"--backup-sudo interpreter {path} is unavailable") from exc
    if (not os.path.isabs(path) or not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or info.st_mode & 0o022):
        raise StageError(f"--backup-sudo interpreter {path} is not a root-owned, root-only-writable file")


class BackupReader:
    """Reads the original paired backup through BACKUP_READER; never writes, copies or chmods it."""

    def __init__(self, backup: Path, *, sudo: bool, python: str = SYSTEM_PYTHON,
                 sudo_command: tuple[str, ...] = ("sudo", "-n"), popen=subprocess.Popen):
        if sudo:
            trusted_interpreter(python)
        self.backup, self.sudo, self.popen = str(backup), sudo, popen
        self.prefix = [*sudo_command, python] if sudo else [sys.executable]

    def command(self, *args: str) -> list[str]:
        return [*self.prefix, "-I", "-c", BACKUP_READER, *args, ]

    def _failure(self, what: str, code: int, err: bytes) -> StageError:
        detail = err.decode(errors="replace").strip()[-300:]
        hint = (" The backup is not readable by this user: re-run with --backup-sudo."
                if not self.sudo and "Permission denied" in detail else "")
        return StageError(f"backup read of {what} failed (exit {code}): {detail}{hint}")

    @contextmanager
    def open(self, name: str):
        """Yield the file's bytes as a stream; fail closed unless the reader read all of it and exited 0."""
        if not BACKUP_NAME.fullmatch(name) or name in (".", ".."):
            raise StageError(f"invalid backup file name {name!r}")
        process = self.popen(self.command("read", self.backup, name), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
        try:
            yield process.stdout
            while process.stdout.read(1 << 20):  # drain whatever the consumer did not need
                pass
            err = process.stderr.read()
            code = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            process.stdout.close()
            process.stderr.close()
        if code:
            raise self._failure(name, code, err)

    def read_small(self, name: str, limit: int) -> bytes:
        with self.open(name) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise StageError(f"{name} is larger than {limit} bytes")
        return data

    def sha256(self, name: str) -> str:
        digest = hashlib.sha256()
        with self.open(name) as stream:
            for chunk in iter(lambda: stream.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def listing(self) -> dict:
        process = self.popen(self.command("list", self.backup), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             stdin=subprocess.DEVNULL)
        out, err = process.communicate()
        if process.returncode:
            raise self._failure("the backup directory", process.returncode, err)
        try:
            return json.loads(out)
        except ValueError as exc:
            raise StageError("unreadable backup directory listing") from exc


def parse_manifest(text: str, backup: Path) -> dict[str, str]:
    """{file name: sha256} from a sha256sum manifest whose entries are plain names inside the backup
    directory, or absolute paths directly inside exactly that directory. The file itself is never rewritten."""
    root = str(backup)
    expected: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        match = CHECKSUM_ENTRY.fullmatch(line.rstrip("\r"))
        if not match:
            raise StageError(f"unrecognized checksum line {number}")
        digest, entry = match.groups()
        if entry.startswith("/"):
            if (posixpath.normpath(entry) != entry or posixpath.dirname(entry) != root
                    or not BACKUP_NAME.fullmatch(posixpath.basename(entry))):
                raise StageError(f"checksum line {number} names a path outside {root}/ or a nested path")
            name = posixpath.basename(entry)
        elif BACKUP_NAME.fullmatch(entry) and entry not in (".", ".."):
            name = entry
        else:
            raise StageError(f"checksum line {number} is not a plain file name or an absolute path in {root}/")
        if name in expected:
            raise StageError(f"checksum line {number} repeats {name}")
        expected[name] = digest
    return expected


def _mounts(name: str, service: dict) -> set[tuple]:
    """(type, source, target, read_only) per mount; refuses mount options and duplicates."""
    found = []
    for mount in service.get("volumes") or []:
        if (not isinstance(mount, dict) or set(mount) - {"type", "source", "target", "read_only", "volume", "bind"}
                or mount.get("type") not in ("volume", "bind") or mount.get("volume") not in (None, {})
                or set(mount.get("bind") or {}) - {"create_host_path"}):
            raise StageError(f"service {name} has an unsupported mount {mount}")
        found.append((mount["type"], mount.get("source"), mount.get("target"), bool(mount.get("read_only"))))
    if len(set(found)) != len(found):
        raise StageError(f"service {name} repeats a mount")
    return set(found)


def _hardened(name: str, service: dict) -> None:
    if service.get("cap_drop") != ["ALL"] or service.get("security_opt") != ["no-new-privileges:true"]:
        raise StageError(f"the rendered {name} service must drop all capabilities with no-new-privileges")


def dac_allows(meta: list, uid: int, groups: frozenset[int], want: int) -> bool:
    """Linux permission bits for a process with no capabilities (cap_drop: ALL), so not even uid 0 may
    override them: the owner class alone applies to the owner, then the group class, then other."""
    mode, owner, group = meta[:3]
    bits = mode >> 6 if uid == owner else mode >> 3 if group in groups else mode
    return bits & want == want


def _account_lines(raw: bytes | None, fields: int, what: str) -> list[list[str]]:
    if raw is None:
        return []
    if len(raw) > MAX_ACCOUNT_FILE:
        raise StageError(f"the zebra image's {what} is too large")
    rows = []
    for line in raw.decode(errors="strict").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        row = line.split(":")
        # runc reads a malformed numeric field as 0 (root): refuse instead of guessing.
        if len(row) < fields or not row[2].isdigit() or (what == "/etc/passwd" and not row[3].isdigit()):
            raise StageError(f"the zebra image's {what} has a malformed line")
        rows.append(row)
    return rows


def resolve_user(spec: object, passwd: bytes | None, group: bytes | None) -> tuple[int, int, frozenset[int]]:
    """(uid, primary gid, supplementary gids) Docker gives a container whose image sets USER `spec`
    (runc's GetExecUser with Docker's root defaults). Unresolvable or unusual specs are refused."""
    if not isinstance(spec, str) or not IMAGE_USER.fullmatch(spec):
        raise StageError(f"unsupported zebra image user {spec!r}")
    user_arg, _, group_arg = spec.partition(":")
    try:
        users = _account_lines(passwd, 7, "/etc/passwd")
        groups = _account_lines(group, 4, "/etc/group")
    except UnicodeDecodeError as exc:
        raise StageError("the zebra image's account files are not text") from exc
    if user_arg == "":
        matches = [u for u in users if int(u[2]) == 0]
    elif user_arg.isdigit():
        matches = [u for u in users if int(u[2]) == int(user_arg)]
    else:
        matches = [u for u in users if u[0] == user_arg]
    if matches:
        name, uid, gid = matches[0][0], int(matches[0][2]), int(matches[0][3])
    elif user_arg and not user_arg.isdigit():
        raise StageError(f"the zebra image's user {user_arg!r} is not in its /etc/passwd")
    else:
        name, uid, gid = None, int(user_arg or 0), 0
    extra: frozenset[int] = frozenset()
    if group_arg:
        found = [g for g in groups if (int(g[2]) == int(group_arg) if group_arg.isdigit() else g[0] == group_arg)]
        if found:
            gid = int(found[0][2])
        elif group_arg.isdigit():
            gid = int(group_arg)
        else:
            raise StageError(f"the zebra image's group {group_arg!r} is not in its /etc/group")
    elif name is not None:
        extra = frozenset(int(g[2]) for g in groups if name in g[3].split(","))
    return uid, gid, extra


def probe_report(output: str, what: str) -> dict:
    """ACCESS_PROBE output, type-checked; anything malformed fails closed."""
    try:
        report = json.loads(output)
    except ValueError as exc:
        raise StageError(f"{what} printed no JSON") from exc

    def ints(values, count):
        return isinstance(values, list) and len(values) >= count and all(type(v) is int for v in values[:count])
    identity = report.get("identity") if isinstance(report, dict) else None
    if (not isinstance(identity, dict) or not all(type(identity.get(k)) is int for k in ("uid", "euid", "gid", "egid"))
            or not ints(identity.get("groups"), len(identity.get("groups") or []))
            or not all(isinstance(report.get(k), dict) for k in ("files", "dirs", "links", "other", "errors"))
            or not all(ints(m, 3) and len(m) == 4 and (m[3] is None or isinstance(m[3], str) and HEX64.fullmatch(m[3]))
                       for m in report["files"].values())
            or not all(ints(m, 3) and len(m) == 4 and isinstance(m[3], bool) for m in report["dirs"].values())):
        raise StageError(f"{what} printed a malformed report")
    return report


def access_problems(report: dict, uid: int, groups: frozenset[int], executables: tuple[str, ...] = ()) -> list[str]:
    """Every path the identity could not list, search, read or run, judged twice: by the probe's real
    open/listdir as that identity, and by the permission bits it saw."""
    problems = [f"{path}: {error}" for path, error in report["errors"].items()]
    for path, meta in report["dirs"].items():
        if not meta[3] or not dac_allows(meta, uid, groups, READ | EXECUTE):
            problems.append(f"{path}/ is not listable and searchable (mode {meta[0]:04o}, owner {meta[1]}:{meta[2]})")
    for path, meta in report["files"].items():
        if meta[3] is None or not dac_allows(meta, uid, groups, READ):
            problems.append(f"{path} is not readable (mode {meta[0]:04o}, owner {meta[1]}:{meta[2]})")
    problems += [f"{path} is a device, FIFO or socket" for path in report["other"]]
    for path in executables:
        meta = report["files"].get(path)
        if meta is None or not dac_allows(meta, uid, groups, READ | EXECUTE):
            problems.append(f"{path} is not an executable regular file for uid {uid}")
    return sorted(problems)


class UncertainStage(StageError):
    pass


@dataclass
class Result:
    code: int
    out: str
    err: str
    raw: bytes = b""


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

    def __call__(self, *args, input: bytes | None = None, timeout: int = 1800, binary: bool = False) -> Result:
        done = self.runner([*self.prefix, *args], input=input, capture_output=True, env=self.env,
                           timeout=timeout)
        out, err = done.stdout, done.stderr
        raw = out if isinstance(out, bytes) else out.encode()
        text = "" if binary else raw.decode(errors="replace")
        return Result(done.returncode, text, err.decode(errors="replace") if isinstance(err, bytes) else err, raw)

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
    expect_params_dir: str | None = None
    backup_sudo: bool = False
    backup_python: str = SYSTEM_PYTHON
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
    # Existence, type and canonical path are checked by BACKUP_READER, which may need --backup-sudo
    # when the backup is readable only by root.
    if (not backup.is_absolute() or backup.is_symlink() or posixpath.normpath(str(backup)) != str(backup)
            or backup.parent != recovery or not PAIRED.fullmatch(backup.name)):
        raise StageError(f"--backup-dir must be an absolute {recovery}/paired-* path (not a symlink)")
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


def verify_checksums(cfg: Config, reader: BackupReader) -> dict[str, str]:
    """Verify every archive the manifest lists (all of them, not only the three that are restored)."""
    raw = reader.read_small(cfg.checksum_file, MAX_MANIFEST_BYTES)
    try:
        expected = parse_manifest(raw.decode(), cfg.backup_dir)
    except UnicodeDecodeError as exc:
        raise StageError(f"{cfg.checksum_file} is not text") from exc
    for volume in VOLUMES:
        if cfg.archive(volume) not in expected:
            raise StageError(f"{cfg.archive(volume)} is not listed in {cfg.checksum_file}")
    for name, digest in expected.items():
        if reader.sha256(name) != digest:
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


def _member_sha256(archive: tarfile.TarFile, member: tarfile.TarInfo) -> str:
    """Streamed in 1 MiB chunks: bounded memory for the ~49 MB proving parameters and the chain state."""
    handle = archive.extractfile(member)
    if handle is None:
        raise StageError(f"archive member could not be read: {member.name!r}")
    digest = hashlib.sha256()
    for chunk in iter(lambda: handle.read(1 << 20), b""):
        digest.update(chunk)
    return digest.hexdigest()


def inspect_archive(reader: BackupReader, name: str, volume: str, cfg: Config) -> tuple[dict, bytes | None]:
    """Manifest {path: [type, uid, gid, mode, size, sha256]} of a safe .tar.gz, streamed, never extracted."""
    with reader.open(name) as stream:
        return _inspect_stream(stream, name, volume, cfg)


def _inspect_stream(stream, archive_name: str, volume: str, cfg: Config) -> tuple[dict, bytes | None]:
    manifest: dict[str, list] = {}
    symlinks: set[str] = set()
    identity = None
    params: dict[str, str] = {}
    try:
        with tarfile.open(fileobj=stream, mode="r|gz") as archive:
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
                    linked = manifest.get(_member_path(member.linkname))
                    if linked is None or linked[0] != "f":
                        raise StageError(f"hard link to an unknown or non-regular member: {name!r}")
                    kind = "h"
                else:
                    raise StageError(f"device, FIFO or other special archive member: {name!r}")
                # A streamed archive can read each member once: the identity's bytes are read once
                # (it is small) and hashed; every other regular member is hashed in chunks.
                if volume == "zwa-root" and name == IDENTITY_MEMBER and kind == "f":
                    if member.size > 8192:
                        raise StageError("archived chain identity is too large")
                    identity = _member_bytes(archive, member)
                    digest = hashlib.sha256(identity).hexdigest()
                else:
                    digest = (_member_sha256(archive, member) if kind == "f"
                              else manifest[_member_path(member.linkname)][5] if kind == "h" else None)
                manifest[name] = [kind, member.uid, member.gid, member.mode & 0o7777,
                                  member.size if kind == "f" else None, digest]
                if volume == "zwa-params" and name in PARAMS_SHA256 and kind == "f":
                    params[name] = digest
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise StageError(f"{archive_name} is not a readable .tar.gz archive: {exc}") from exc
    if volume == "zwa-root":
        if identity is None:
            raise StageError(f"{archive_name} has no regular {IDENTITY_MEMBER}")
        check_archived_identity(identity, cfg)
    if volume == "zebra-state" and not any(name.startswith("state/") for name in manifest):
        raise StageError(f"{archive_name} contains no Zebra state/ directory")
    if volume == "zwa-params" and params != PARAMS_SHA256:
        raise StageError(f"{archive_name} does not contain the checksummed Sapling parameters")
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
    """The operator's .env with ZWA_ENGINE_TAG pointed at the reviewed staging engine image.

    COMPOSE_* lines (COMPOSE_PROFILES, COMPOSE_FILE, COMPOSE_PROJECT_NAME, ...) are dropped: the driver
    alone chooses the profiles, files and project, so the rendering does not depend on the live .env.
    """
    lines = [line for line in cfg.env_file.read_text().splitlines()
             if not re.match(r"\s*(export\s+)?(ZWA_ENGINE_TAG|COMPOSE_[A-Z0-9_]*)\s*=", line)]
    return ("\n".join(lines + [f"ZWA_ENGINE_TAG={cfg.engine_tag}"]) + "\n").encode()


def params_dir(cfg: Config) -> str:
    """The host parameter directory params binds read-only at /src, validated from the env file."""
    values = []
    for line in cfg.env_file.read_text().splitlines():
        match = re.match(r"\s*(?:export\s+)?ZWA_PARAMS_DIR\s*=\s*(.*?)\s*\Z", line)
        if match:
            value = match.group(1)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values.append(value)
    if len(values) != 1:
        raise StageError("--env-file must define ZWA_PARAMS_DIR exactly once")
    value = values[0]
    if (not PARAMS_DIR.fullmatch(value) or posixpath.normpath(value) != value or value == "/"
            or len(PurePosixPath(value).parts) < 3
            or any(value == p or value.startswith(p + "/") for p in SYSTEM_PATHS)):
        raise StageError(f"ZWA_PARAMS_DIR is not a safe absolute parameter directory: {value!r}")
    if cfg.expect_params_dir is not None and value != cfg.expect_params_dir:
        raise StageError(f"ZWA_PARAMS_DIR {value!r} differs from --expect-params-dir {cfg.expect_params_dir!r}")
    return value


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
    def __init__(self, cfg: Config, docker: Docker | None = None, attest_tool=None, log=print,
                 reader: BackupReader | None = None):
        self.reader = reader
        self.cfg = cfg
        self.docker = docker or Docker(cfg.docker_sudo)
        self.tool = attest_tool or load_attest_tool()
        self.tool._docker = self._tool_docker  # host checks go through the same validated Docker access
        self.log = log

    @property
    def backup(self) -> BackupReader:
        if self.reader is None:
            raise StageError("internal error: the backup reader is set up by plan")
        return self.reader

    def _tool_docker(self, *args: str) -> str:
        result = self.docker(*args)
        if result.code:
            raise self.tool.AttestError(result.err.strip() or f"docker {args[0]} failed")
        return result.out

    # ------------------------------------------------------------------ helpers

    def compose(self, *args: str, input: bytes | None = None) -> Result:
        cfg = self.cfg
        # --profile setup: the rendering always includes the setup services, so all eight are checked.
        # It starts nothing by itself: `up` and `run` name their single service.
        return self.docker("compose", "--project-directory", str(cfg.deploy_dir), "--env-file", str(cfg.stage_env),
                           "--profile", "setup", "-p", cfg.project, "-f", str(cfg.base), "-f", str(cfg.override),
                           *args, input=input)

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
        """Exact allowlist for what staging starts (zebra, portal); safety rules for every service."""
        cfg = self.cfg
        if b"docker.sock" in raw or b"/var/run/docker" in raw:
            raise StageError("the rendered configuration mounts the Docker socket")
        if f"{cfg.source_project}_".encode() in raw:
            raise StageError(f"the rendered configuration references {cfg.source_project} volumes")
        if rendered.get("name") != cfg.project:
            raise StageError("the rendered project name differs from --project")
        unknown = {k for k in rendered if not k.startswith("x-")} - {"name", "services", "volumes", "networks"}
        if unknown:
            raise StageError(f"unexpected top-level Compose keys: {sorted(unknown)}")
        self._check_volumes_and_networks(rendered)
        services = rendered.get("services") or {}
        if set(services) != KNOWN_SERVICES:
            raise StageError(f"unexpected Compose services: extra {sorted(set(services) - KNOWN_SERVICES)}, "
                             f"missing {sorted(KNOWN_SERVICES - set(services))}")
        for name, service in services.items():
            self._check_any_service(name, service, rendered)
        zebra = self._check_zebra(services["zebra"])
        self._check_portal(services["portal"])
        self._check_setup("params", services["params"], entrypoint=["sh", "-c"], command=[PARAMS_COMMAND],
                          mounts={("bind", self.params_dir, "/src", True),
                                  ("volume", "zwa-params", "/opt/zwa/params/ZcashParams", False)})
        self._check_setup("bootstrap", services["bootstrap"], entrypoint=None, command=BOOTSTRAP_COMMAND,
                          mounts={("volume", "zwa-root", "/var/lib/zwa", False),
                                  ("volume", "zwa-params", "/opt/zwa/params/ZcashParams", True)})
        return zebra

    def _check_volumes_and_networks(self, rendered: dict) -> None:
        cfg = self.cfg
        volumes = rendered.get("volumes") or {}
        if set(volumes) != DECLARED_VOLUMES:
            raise StageError(f"the rendered top-level volumes are not the reviewed set: {sorted(volumes)}")
        for name, spec in volumes.items():
            # Only a plain project-scoped name: no external, driver or driver_opts (which can bind host paths).
            if spec != {"name": f"{cfg.project}_{name}"}:
                raise StageError(f"the rendered {name} volume is not the new {cfg.project}_{name}: {spec}")
        networks = rendered.get("networks") or {}
        if set(networks) != {"internal", "edge"}:
            raise StageError(f"the rendered networks are not the reviewed internal/edge pair: {sorted(networks)}")
        for name, spec in networks.items():
            allowed = {"name", "ipam", "driver"} | ({"internal"} if name == "internal" else set())
            if (not isinstance(spec, dict) or set(spec) - allowed or spec.get("name") != f"{cfg.project}_{name}"
                    or spec.get("ipam", {}) != {} or spec.get("driver", "bridge") != "bridge"):
                raise StageError(f"the rendered {name} network is not a new isolated {cfg.project}_{name} network")
        if networks["internal"].get("internal") is not True:
            raise StageError("the rendered internal network is not internal (it would have a route out)")

    def _check_any_service(self, name: str, service: dict, rendered: dict) -> None:
        cfg = self.cfg
        if not isinstance(service, dict):
            raise StageError(f"service {name} is malformed")
        for key in HOST_ACCESS_KEYS:
            if service.get(key) not in (None, False, [], {}):
                raise StageError(f"service {name} sets {key}; host namespaces, privileges and devices are refused")
        if (service.get("ports") or service.get("expose")) and name != "caddy":
            raise StageError(f"service {name} publishes or exposes ports; only caddy may")
        for port in service.get("ports") or []:
            if not isinstance(port, dict) or port.get("target") not in (80, 443):
                raise StageError("caddy may publish only 80 and 443")
        networks = service.get("networks") or {}
        if not isinstance(networks, dict) or set(networks) - set(rendered.get("networks") or {}):
            raise StageError(f"service {name} joins an undeclared network")
        deploy = cfg.deploy_dir.resolve()
        for mount in _mounts(name, service):
            kind, source, _target, read_only = mount
            if kind == "volume" and source in DECLARED_VOLUMES:
                continue
            if kind == "bind" and read_only and isinstance(source, str) and (
                    Path(source).resolve() == deploy or deploy in Path(source).resolve().parents):
                continue
            if name == "params" and mount == ("bind", self.params_dir, "/src", True):
                continue  # the one external bind: the validated host parameter directory, read-only
            raise StageError(f"service {name} has an unapproved mount {mount}")

    def _check_zebra(self, zebra: dict) -> dict:
        cfg = self.cfg
        extra = set(zebra) - ZEBRA_KEYS
        if extra:
            raise StageError(f"the rendered zebra service has unreviewed settings: {sorted(extra)}")
        entrypoint = zebra.get("entrypoint")
        if (not isinstance(entrypoint, list) or not entrypoint
                or not all(isinstance(part, str) for part in entrypoint)):
            raise StageError("the rendered zebra service has no explicit entrypoint")
        if entrypoint == ARM64_ENTRYPOINT:
            raise StageError("the rendered zebra entrypoint is the ARM64 entrypoint; "
                             "compose.amd64.yaml must override it for the AMD64 image")
        if entrypoint[1:] != ["-c", "/config/regtest-config.toml"] or zebra.get("command") is not None:
            raise StageError("the rendered zebra command must be `<zebrad> -c /config/regtest-config.toml`")
        if zebra.get("platform", PLATFORM) != PLATFORM:
            raise StageError("the rendered zebra platform is not linux/amd64")
        _hardened("zebra", zebra)
        if zebra.get("networks") not in ({"internal": None}, {"internal": {}}):
            raise StageError("the rendered zebra service must join only the internal network")
        config = cfg.deploy_dir / "zebra/regtest-config.toml"
        expected = {("volume", "zebra-state", "/data", False),
                    ("bind", str(config), "/config/regtest-config.toml", True)}
        if _mounts("zebra", zebra) != expected:
            raise StageError(f"the rendered zebra mounts are not exactly {sorted(expected)}")
        try:
            pinned = config.read_text() == ZEBRA_CONFIG
        except OSError as exc:
            info = config.lstat()
            raise StageError(f"deploy/zebra/regtest-config.toml (mode {stat.S_IMODE(info.st_mode):04o}, owner "
                             f"{info.st_uid}:{info.st_gid}) is not readable by this operator: {exc.strerror}. "
                             "This tool never changes file modes") from exc
        if not pinned:
            raise StageError("deploy/zebra/regtest-config.toml is not the pinned Zebra configuration")
        image = zebra.get("image")
        if not isinstance(image, str):
            raise StageError("the rendered zebra service has no image")
        try:
            image_id, platform = self.tool.check_image(image)
        except self.tool.AttestError as exc:
            raise StageError(f"zebra image check failed: {exc}") from exc
        if (platform, image_id) != (PLATFORM, PINNED_ZEBRA_IMAGES[PLATFORM]):
            raise StageError("the rendered zebra image is not the pinned AMD64 image")
        return {"zebraImage": image, "zebraImageId": image_id, "zebraEntrypoint": entrypoint}

    def _check_setup(self, name: str, service: dict, *, entrypoint, command, mounts: set) -> None:
        """Setup-profile services: never started by staging, but held to exactly what compose.yaml defines."""
        extra = set(service) - SETUP_KEYS
        if extra:
            raise StageError(f"the rendered {name} service has unreviewed settings: {sorted(extra)}")
        if service.get("profiles") != ["setup"] or service.get("restart") != "no":
            raise StageError(f"the rendered {name} service must stay a one-shot in the setup profile")
        if service.get("image") != self.cfg.engine_image or service.get("platform", PLATFORM) != PLATFORM:
            raise StageError(f"the rendered {name} image is not {self.cfg.engine_image}")
        _hardened(name, service)
        if (service.get("user") != "10001:10001" or service.get("read_only") is not True
                or service.get("tmpfs") != ["/tmp:size=64m,mode=1777"]):
            raise StageError(f"the rendered {name} is not the unprivileged read-only engine container")
        if service.get("networks") not in ({"internal": None}, {"internal": {}}):
            raise StageError(f"the rendered {name} service must join only the internal network")
        if (service.get("entrypoint") != entrypoint or service.get("command") != command
                or (service.get("environment") or {}) != {"ZWA_PRIVATE_EXCHANGE_ROOT": ENGINE_ROOT}
                or set(service.get("depends_on") or {}) - {"zebra"}):
            raise StageError(f"the rendered {name} command or environment is not the reviewed one")
        if _mounts(name, service) != mounts:
            raise StageError(f"the rendered {name} mounts are not exactly {sorted(mounts)}")

    def _check_portal(self, portal: dict) -> None:
        cfg = self.cfg
        extra = set(portal) - PORTAL_KEYS
        if extra:
            raise StageError(f"the rendered portal service has unreviewed settings: {sorted(extra)}")
        if portal.get("image") != cfg.engine_image:
            raise StageError(f"the rendered portal image is not {cfg.engine_image}")
        if portal.get("platform", PLATFORM) != PLATFORM:
            raise StageError("the rendered portal platform is not linux/amd64")
        _hardened("portal", portal)
        if (portal.get("user") != "10001:10001" or portal.get("read_only") is not True
                or portal.get("tmpfs") != ["/tmp:size=64m,mode=1777"]):
            raise StageError("the rendered portal is not the unprivileged read-only engine container")
        if portal.get("networks") not in ({"internal": None}, {"internal": {}}):
            raise StageError("the rendered portal service must join only the internal network")
        environment = portal.get("environment") or {}
        if (set(environment) != {"ZWA_PORTAL_TOKEN", "ZWA_PRIVATE_EXCHANGE_ROOT"}
                or environment["ZWA_PRIVATE_EXCHANGE_ROOT"] != ENGINE_ROOT):
            raise StageError("the rendered portal environment is not the reviewed one")
        if set(portal.get("depends_on") or {}) - {"zebra"}:
            raise StageError("the rendered portal depends on unreviewed services")
        expected = {("volume", "zwa-root", "/var/lib/zwa", False),
                    ("volume", "zwa-params", "/opt/zwa/params/ZcashParams", True)}
        if _mounts("portal", portal) != expected:
            raise StageError(f"the rendered portal mounts are not exactly {sorted(expected)}")

    def check_zebra_executable(self, zebra: dict) -> dict:
        """The entrypoint binary inside the pinned image is an x86-64 ELF executable; also the image's
        USER and account files, from which the zebra container's identity is resolved.

        Copies them out of a never-started, network-less throwaway container, removed afterwards
        (with any anonymous volume the image declares). No chain volume is involved.
        """
        image = zebra["zebraImage"]
        info = json.loads(self.docker.ok("image", "inspect", "--", image, what="zebra image inspect"))[0]
        config = info.get("Config") or {}
        workdir = config.get("WorkingDir") or "/"
        path = posixpath.normpath(posixpath.join(workdir, zebra["zebraEntrypoint"][0]))
        created = self.docker.ok("create", "--network", "none", "--label", "zwa.provenance-stage=executable-check",
                                 "--entrypoint", path, image, what="executable check container").strip()
        if not HEX64.fullmatch(created):
            raise StageError("unexpected container id from docker create")
        try:
            copied = self.docker("cp", "-L", f"{created}:{path}", "-", binary=True)
            accounts = {name: self._copy_account_file(created, name) for name in ("/etc/passwd", "/etc/group")}
        finally:
            self.docker.ok("rm", "-v", created, what="remove executable check container")
        self.zebra_identity = (config.get("User") or "", *resolve_user(config.get("User") or "",
                                                                       accounts["/etc/passwd"], accounts["/etc/group"]))
        if copied.code:
            raise StageError(f"zebra entrypoint {path} is not present in {image}: {copied.err.strip()[:200]}")
        try:
            with tarfile.open(fileobj=io.BytesIO(copied.raw), mode="r:") as archive:
                member = archive.next()
                if member is None or not member.isreg() or not member.mode & 0o111:
                    raise StageError(f"zebra entrypoint {path} is not an executable regular file")
                data = _member_bytes(archive, member)
        except tarfile.TarError as exc:
            raise StageError(f"could not read zebra entrypoint {path}: {exc}") from exc
        if (data[:4] != b"\x7fELF" or data[4:6] != b"\x02\x01"
                or int.from_bytes(data[18:20], "little") != ELF_MACHINE_X86_64):
            raise StageError(f"zebra entrypoint {path} is not an x86-64 ELF executable")
        return {"zebraExecutable": path, "zebraExecutableSha256": hashlib.sha256(data).hexdigest(),
                "elfMachine": "x86-64"}

    def _copy_account_file(self, container: str, path: str) -> bytes | None:
        """A small regular file from the throwaway container, or None when the image has no such file."""
        copied = self.docker("cp", "-L", f"{container}:{path}", "-", binary=True)
        if copied.code:
            if re.search(r"could not find the file|no such file", copied.err, re.IGNORECASE):
                return None
            raise StageError(f"could not copy {path} out of the zebra image: {copied.err.strip()[:200]}")
        try:
            with tarfile.open(fileobj=io.BytesIO(copied.raw), mode="r:") as archive:
                member = archive.next()
                if member is None or not member.isreg() or member.size > MAX_ACCOUNT_FILE:
                    raise StageError(f"the zebra image's {path} is not a small regular file")
                return _member_bytes(archive, member)
        except tarfile.TarError as exc:
            raise StageError(f"could not read the zebra image's {path}: {exc}") from exc

    def probe(self, what: str, image: str, *, user: tuple[int, int], groups: frozenset[int] = frozenset(),
              bind: tuple[str, str] | None = None, paths: tuple[str, ...]) -> dict:
        """ACCESS_PROBE in a one-shot container of `image` as uid:gid plus `groups`, with every capability
        dropped, no-new-privileges, no network and a read-only root; `bind` is mounted read-only."""
        args = ["run", "--rm", "--network", "none", "--user", f"{user[0]}:{user[1]}", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges:true", "--read-only"]
        for gid in sorted(groups):
            args += ["--group-add", str(gid)]
        if bind is not None:
            source, target = bind
            if not SAFE_BIND_SOURCE.fullmatch(source) or not SAFE_BIND_SOURCE.fullmatch(target):
                raise StageError(f"{what}: unsupported characters in the bind path {source!r}")
            args += ["--mount", f"type=bind,source={source},target={target},readonly"]
        result = self.docker(*args, "--entrypoint", "python3", image, "-I", "-c", ACCESS_PROBE, *paths)
        if result.code:
            raise StageError(f"{what} failed (exit {result.code}): {result.err.strip()[:400]}")
        report = probe_report(result.out, what)
        identity = report["identity"]
        ran = (identity["uid"], identity["euid"], identity["gid"], identity["egid"])
        if (ran != (user[0], user[0], user[1], user[1])
                or set(identity["groups"]) | {user[1]} != set(groups) | {user[1]}):
            raise StageError(f"{what} ran as {identity}, not as uid {user[0]}, gid {user[1]}, groups {sorted(groups)}")
        return report

    def check_zebra_config(self) -> dict:
        """The zebra container can read the pinned configuration through its exact mount.

        compose.yaml sets no `user` for zebra (and the allowlist refuses one), so zebra runs as its image's
        USER with all capabilities dropped: even uid 0 cannot override permission bits. The bind source is
        checked here on the host, then read in a probe container with zebra's resolved identity and the
        same read-only bind. Nothing is ever chmodded: an unreadable file stops staging.
        """
        config = self.cfg.deploy_dir / "zebra/regtest-config.toml"
        spec, uid, gid, extra = self.zebra_identity
        groups = extra | {gid}
        who = f"the zebra container user {spec or '(image default: root)'} = uid {uid}, gid {gid}, " \
              f"groups {sorted(groups)}, with all capabilities dropped"
        try:
            info = os.lstat(config)
        except OSError as exc:
            raise StageError(f"deploy/zebra/regtest-config.toml is missing: {exc.strerror}") from exc
        if not stat.S_ISREG(info.st_mode) or Path(os.path.realpath(config)) != config:
            raise StageError("deploy/zebra/regtest-config.toml must be a regular file reached without symlinks")
        host = [stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid]
        if not dac_allows(host, uid, groups, READ):
            raise StageError(f"deploy/zebra/regtest-config.toml (mode {host[0]:04o}, owner {host[1]}:{host[2]}) "
                             f"is not readable by {who}. Prepare the staging checkout under umask 022 "
                             "(see the runbook); this tool never changes file modes")
        if _sha256(config) != CONFIG_SHA256:
            raise StageError("deploy/zebra/regtest-config.toml is not the pinned Zebra configuration")
        report = self.probe("zebra configuration probe", self.cfg.engine_image, user=(uid, gid), groups=extra,
                            bind=(str(config), ZEBRA_CONFIG_TARGET), paths=(ZEBRA_CONFIG_TARGET,))
        seen = report["files"].get(ZEBRA_CONFIG_TARGET)
        if seen is None or set(report["files"]) != {ZEBRA_CONFIG_TARGET} or report["links"] or report["dirs"]:
            raise StageError(f"the zebra configuration mount at {ZEBRA_CONFIG_TARGET} is not a regular file")
        problems = access_problems(report, uid, groups)
        if problems:
            raise StageError(f"the zebra configuration is not readable inside the container by {who}: "
                             f"{problems[0]}. This tool never changes file modes")
        if seen[3] != CONFIG_SHA256 or seen[0] != host[0]:
            raise StageError(f"the file mounted at {ZEBRA_CONFIG_TARGET} is not the checkout's pinned configuration")
        return {"user": spec, "uid": uid, "gid": gid, "groups": sorted(groups), "mode": f"{host[0]:04o}",
                "owner": host[1:], "sha256": CONFIG_SHA256}

    def check_engine_image(self) -> dict:
        """The engine image holds the reviewed exchange code, and the engine user can use all of its files.

        Every engine service runs as 10001:10001 with no capabilities (checked on the rendering). An image
        built from restrictive source modes (e.g. an overlay COPY of mode-600 files) installs root-owned code
        that user cannot read. A probe container with exactly that identity and hardening lists, opens and
        hashes everything under ENGINE_PATHS; the hashes it read are compared with the checkout.
        """
        image = self.cfg.engine_image
        info = self.docker.ok("image", "inspect", "--", image, what="engine image inspect")
        data = json.loads(info)
        if not isinstance(data, list) or len(data) != 1 or f"{data[0].get('Os')}/{data[0].get('Architecture')}" \
                != PLATFORM:
            raise StageError(f"{image} is not a linux/amd64 image")
        report = self.probe("engine file check", image, user=(ENGINE_UID, ENGINE_GID), paths=ENGINE_PATHS)
        problems = access_problems(report, ENGINE_UID, frozenset({ENGINE_GID}), ENGINE_EXECUTABLES)
        if problems:
            raise StageError(f"{image} has {len(problems)} path(s) the engine user {ENGINE_UID}:{ENGINE_GID} cannot "
                             f"use, e.g. {problems[:5]}. Rebuild it from world-readable sources (see the runbook); "
                             "this tool never changes file modes")
        prefix = f"{ENGINE_CODE}/exchange/"
        inside = {path[len(ENGINE_CODE) + 1:]: meta[3] for path, meta in report["files"].items()
                  if path.startswith(prefix) and path.endswith(".py") and "/" not in path[len(prefix):]}
        expected = {f"exchange/{p.name}": _sha256(p) for p in sorted((self.cfg.repo_root / "exchange").glob("*.py"))
                    if not p.name.startswith("test_")}
        if inside != expected:
            differ = sorted(set(inside.items()) ^ set(expected.items()))[:5]
            raise StageError(f"{image} does not contain the reviewed exchange code: {differ}")
        return {"engineImageId": data[0].get("Id"), "exchangeFiles": len(expected),
                "engineUser": f"{ENGINE_UID}:{ENGINE_GID}", "engineFilesReadable": len(report["files"])}

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
        if self.reader is None:
            self.reader = BackupReader(cfg.backup_dir, sudo=cfg.backup_sudo, python=cfg.backup_python)
        backup_listing = self.reader.listing()  # names and metadata only; recorded to prove nothing changed
        checksums = verify_checksums(cfg, self.reader)
        manifests, identity = {}, None
        for volume in VOLUMES:
            manifests[volume], found = inspect_archive(self.reader, cfg.archive(volume), volume, cfg)
            identity = found if volume == "zwa-root" else identity
        if identity is None:
            raise StageError("no chain identity found in the zwa-root archive")
        self.params_dir = params_dir(cfg)
        host = self.tool.host_platform()
        if host != PLATFORM:
            raise StageError(f"this Docker host is {host}, not {PLATFORM}")
        rendered, raw = self.render()
        zebra = self.check_rendered(rendered, raw)
        zebra.update(self.check_zebra_executable(zebra))
        engine = self.check_engine_image()
        zebra_config = self.check_zebra_config()  # before any volume is restored or service started
        self.check_isolation()
        summary = {"sourceRevision": revision, "project": cfg.project, "sourceProject": cfg.source_project,
                   "backupDir": str(cfg.backup_dir), "backupListing": backup_listing,
                   "backupRead": "sudo" if cfg.backup_sudo else "direct", "checksums": checksums,
                   "archivedIdentitySha256": hashlib.sha256(identity).hexdigest(),
                   "renderedSha256": hashlib.sha256(raw).hexdigest(),
                   "overrideSha256": _sha256(cfg.override), "stageEnvSha256": _sha256(cfg.stage_env),
                   "zebra": zebra, "zebraConfig": zebra_config, "engine": engine, "paramsDir": self.params_dir,
                   "dockerPrefix": self.docker.prefix}
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
            digests = self.docker.ok("run", "--rm", "--network", "none", "--user", "0:0", "--entrypoint", "python3",
                                     "-v", f"{name}:/dst:ro", cfg.engine_image, "-c", RESTORED_DIGESTS, "/dst",
                                     what=f"hash {name}")
            self.compare_contents(volume, digests)

    def compare_restored(self, volume: str, listing: str) -> None:
        restored = {}
        for line in listing.splitlines():
            parts = line.split("\t")
            if len(parts) != 6:
                raise StageError(f"unexpected listing line from {volume}: {line[:120]!r}")
            path, kind, uid, gid, mode, size = parts
            restored[path] = [kind, int(uid), int(gid), int(mode, 8), int(size)]
        manifest = self.manifests[volume]
        for path, (kind, uid, gid, mode, size, _digest) in manifest.items():
            got = restored.get(path)
            want_kind = "f" if kind == "h" else kind
            if (got is None or got[0] != want_kind or got[1:3] != [uid, gid]
                    or (kind in ("f", "d") and got[3] != mode) or (kind == "f" and got[4] != size)):
                raise StageError(f"restored {volume} differs from the archive at {path or '/'}: "
                                 f"archive {[kind, uid, gid, oct(int(mode)), size]} restored {got}")
        extra = set(restored) - set(manifest) - {""}
        if extra:
            raise StageError(f"restored {volume} has entries not in the archive: {sorted(extra)[:5]}")

    def compare_contents(self, volume: str, output: str) -> None:
        try:
            restored = json.loads(output)
        except ValueError as exc:
            raise StageError(f"no content digests from restored {volume}") from exc
        expected = {path: entry[5] for path, entry in self.manifests[volume].items() if entry[0] in ("f", "h")}
        if not isinstance(restored, dict) or set(restored) != set(expected):
            differ = sorted(set(restored if isinstance(restored, dict) else {}) ^ set(expected))[:5]
            raise StageError(f"restored {volume} regular files differ from the archive: {differ}")
        for path, digest in expected.items():
            if restored[path] != digest:
                raise StageError(f"restored {volume} content differs from the archive at {path}")

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
        for key in ("checksums", "archivedIdentitySha256", "overrideSha256", "stageEnvSha256", "zebra", "zebraConfig",
                    "engine", "paramsDir", "backupListing"):
            if planned.get(key) != summary[key]:
                raise StageError(f"{key} changed since plan")
        log = self.log

        log("restoring new volumes")
        self.restore()
        if verify_checksums(cfg, self.backup) != summary["checksums"]:
            raise StageError("backup checksums changed during restore")

        log("starting zebra only")
        if self.check_zebra_config() != summary["zebraConfig"]:  # again, immediately before the start
            raise StageError("the zebra configuration's mode, owner or identity changed since plan")
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
        if verify_checksums(cfg, self.backup) != summary["checksums"]:
            raise StageError("backup checksums changed during staging")
        if self.backup.listing() != summary["backupListing"]:
            raise StageError("the original backup directory's files or metadata changed during staging")
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
    parser.add_argument("--expect-params-dir", help="pin ZWA_PARAMS_DIR, e.g. /srv/zwa/params/ZcashParams")
    parser.add_argument("--docker-sudo", action="store_true", help="run Docker as `sudo -n docker`")
    parser.add_argument("--backup-sudo", action="store_true",
                        help="read a root-only backup through `sudo -n /usr/bin/python3 -I -c <fixed reader>`")
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
                 docker_sudo=args.docker_sudo, expect_params_dir=args.expect_params_dir,
                 backup_sudo=args.backup_sudo)
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
