"""Record host-attested Zebra runtime provenance in an existing chain identity.

An explicit, operator-run metadata migration for externally managed (Compose)
chains. It never runs on startup or on a status check:

    python3 -I deploy/scripts/zebra-runtime-attest.py attest --image "$ZWA_ZEBRA_IMAGE" \\
        --project <compose project> > attestation.json            # on the host
    docker compose -p <project> -f compose.yaml [-f compose.amd64.yaml] run --rm --no-deps -T \\
        --entrypoint python3 portal -m exchange.zebra_provenance record \\
        --root /var/lib/zwa/zwa4e-demo --rpc-host zebra --rpc-port 18232 \\
        --expect-genesis <hash> --expect-anchor-height <height> \\
        --expect-compose-project <compose project> [--dry-run] < attestation.json

A recorded runtime is point-in-time evidence: it says which pinned
image one named container ran when the host attested it. It never proves the
image running later; re-run `zebra-runtime-attest.py verify` on the host
after every Zebra start, restart or container replacement. An existing record
is never changed by a later attestation.

Docker facts are verified only on the host (this process has no Docker
socket). Here the attestation is rechecked against the pinned image table and
this machine's platform, and the identity's genesis and durable anchor are
rechecked against the live chain. The only change is one added "zebraRuntime"
object: the version, recorded "image" label, genesis, anchor, heights and
every ledger, wallet and order file are left as they are.

Write guarantees, stated exactly:
- The whole read-check-write holds persistent_chain.identity_lock, so it is
  serialized with every writer that takes that lock (this module and
  persistent_chain's start/adopt/checkpoint/status). No such writer's update
  can be lost.
- Writers that do not take the lock (engines built before it existed, such
  as cf7f133) are not excluded. The bytes are re-compared just before the
  rename, but a non-cooperating write after that comparison can be
  overwritten. Stop every engine service except zebra before migrating.
- The new file is complete before the rename (temp file + fsync), so readers
  see the old or the new identity, never a partial one. A failure before the
  rename changes nothing (exit 1). A failure after the rename (directory
  fsync, read-back) is reported as UNCERTAIN (exit 3) with the before/after
  hashes: inspect with `show`, then re-run the same `record`, which confirms
  the content and re-fsyncs. Never revert blindly on an uncertain outcome.

    python3 -m exchange.zebra_provenance show --root /var/lib/zwa/zwa4e-demo

prints the recorded provenance without changing anything. `revert [--dry-run]`
restores the exact pre-migration bytes, but only while nothing else in the
identity has changed since the migration; otherwise restore the paired
pre-migration backup instead.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import sys
import tempfile
import time

from .config import PROTECTED_ROOTS
from .persistent_chain import (COMPOSE_PROJECT, PINNED_ZEBRA_IMAGES, RUNTIME_EVIDENCE, RUNTIME_KEY,
                               ZEBRA_SOURCE_REVISION, ChainError, _paths, _rpc, identity_lock, runtime_provenance,
                               validate_identity, verify_anchor)

IDENTITY = "chain-identity.json"
MAX_IDENTITY_BYTES = 8192
ATTESTATION_SCHEMA = "zwa-zebra-runtime-attestation/1"
ATTESTATION_FIELDS = frozenset({"schema", "imageId", "platform", "configuredImage", "containerId",
                                "containerImageId", "containerRunning", "composeProject", "attestedAt"})
MAX_ATTESTATION_BYTES = 4096
MAX_ATTESTATION_AGE = 900
MAX_CLOCK_SKEW = 120
MACHINE_PLATFORMS = {"x86_64": "linux/amd64", "amd64": "linux/amd64",
                     "aarch64": "linux/arm64", "arm64": "linux/arm64"}
PROJECT = COMPOSE_PROJECT
MIGRATION_LOCK_TIMEOUT = 10.0


class ProvenanceError(ChainError):
    pass


class UncertainWrite(ProvenanceError):
    """The rename happened (or may have), but the result is not confirmed."""

    def __init__(self, message: str, before: str, after: str):
        super().__init__(message)
        self.before, self.after = before, after


def machine_platform() -> str | None:
    return MACHINE_PLATFORMS.get(platform.machine().lower())


def validate_attestation(attestation: object, *, now: int, machine: str | None, project: str) -> dict:
    """The host's claims must be consistent, name a pin for this machine and the expected project."""
    if not isinstance(project, str) or not PROJECT.fullmatch(project):
        raise ProvenanceError("invalid expected Compose project")
    if not isinstance(attestation, dict) or set(attestation) != ATTESTATION_FIELDS:
        raise ProvenanceError("attestation schema")
    image, claimed = attestation["imageId"], attestation["platform"]
    if attestation["schema"] != ATTESTATION_SCHEMA:
        raise ProvenanceError("unsupported attestation schema")
    if not isinstance(image, str) or image not in PINNED_ZEBRA_IMAGES.values():
        raise ProvenanceError("Zebra image ID is not pinned")
    if not isinstance(claimed, str) or PINNED_ZEBRA_IMAGES.get(claimed) != image:
        raise ProvenanceError("Zebra image architecture differs from its pin")
    if machine != claimed:
        raise ProvenanceError("attested platform differs from this machine's platform")
    if attestation["containerImageId"] != image or attestation["containerRunning"] is not True:
        raise ProvenanceError("running Zebra container does not use the attested image")
    if (not isinstance(attestation["configuredImage"], str)
            or not 0 < len(attestation["configuredImage"]) <= 256
            or not isinstance(attestation["containerId"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", attestation["containerId"])
            or not isinstance(attestation["composeProject"], str)
            or not PROJECT.fullmatch(attestation["composeProject"])):
        raise ProvenanceError("attestation fields")
    if attestation["composeProject"] != project:
        raise ProvenanceError(f"attestation is for Compose project {attestation['composeProject']!r}, "
                              f"not the expected {project!r}")
    attested = attestation["attestedAt"]
    if type(attested) is not int or not now - MAX_ATTESTATION_AGE <= attested <= now + MAX_CLOCK_SKEW:
        raise ProvenanceError("attestation is stale or from the future")
    return attestation


def _read_regular(path: Path) -> tuple[bytes, os.stat_result]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError as exc:
        raise ProvenanceError("chain identity is missing") from exc
    except OSError as exc:
        raise ProvenanceError("unsafe chain identity file") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IDENTITY_BYTES:
            raise ProvenanceError("unsafe chain identity file")
        data = os.read(fd, MAX_IDENTITY_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_IDENTITY_BYTES:
        raise ProvenanceError("unsafe chain identity file")
    return data, info


def serialize(meta: dict) -> bytes:
    """Byte-for-byte the format persistent_chain._atomic_json writes."""
    return (json.dumps(meta, indent=2, sort_keys=True) + "\n").encode()


def _write_temp(directory: Path, payload: bytes, like: os.stat_result) -> Path:
    fd, name = tempfile.mkstemp(prefix=".chain-identity.", suffix=".provenance-tmp", dir=directory)
    temp = Path(name)
    try:
        try:
            view = memoryview(payload)
            while view:
                view = view[os.write(fd, view):]
            os.fchmod(fd, stat.S_IMODE(like.st_mode))
            if (os.fstat(fd).st_uid, os.fstat(fd).st_gid) != (like.st_uid, like.st_gid):
                # Keep the engine's ownership; fails closed when not permitted.
                os.fchown(fd, like.st_uid, like.st_gid)
            os.fsync(fd)
        finally:
            os.close(fd)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return temp


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace_locked(path: Path, original: bytes, payload: bytes, like: os.stat_result) -> None:
    """Replace the identity; the caller holds identity_lock (see the module docstring)."""
    before, after = hashlib.sha256(original).hexdigest(), hashlib.sha256(payload).hexdigest()
    temp = _write_temp(path.parent, payload, like)
    try:
        current, info = _read_regular(path)
        if current != original or (info.st_dev, info.st_ino) != (like.st_dev, like.st_ino):
            raise ProvenanceError("chain identity was changed by a writer that does not take the identity "
                                  "lock; nothing was written. Stop every engine service except zebra")
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    # From here on the new bytes are visible: report failures as uncertain, never as "nothing written".
    try:
        _fsync_directory(path.parent)
    except OSError as exc:
        raise UncertainWrite(f"identity renamed into place but the directory fsync failed ({exc}); "
                             "durability is unconfirmed", before, after) from exc
    try:
        written, _ = _read_regular(path)
    except ProvenanceError as exc:
        raise UncertainWrite(f"identity renamed into place but could not be read back ({exc})",
                             before, after) from exc
    if written != payload:
        raise UncertainWrite("identity differs from what was just written; a writer that does not take the "
                             "identity lock is active", before, hashlib.sha256(written).hexdigest())


def _confirm_durable(path: Path, data: bytes) -> None:
    """Re-fsync an identity whose content is already correct (reconciles an UNCERTAIN outcome)."""
    digest = hashlib.sha256(data).hexdigest()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        _fsync_directory(path.parent)
    except OSError as exc:
        raise UncertainWrite(f"recorded identity is correct but its durability is unconfirmed ({exc})",
                             digest, digest) from exc


def _chain_view(rpc, meta: dict) -> int:
    """Fail closed unless the live chain still has the identity's genesis and anchor."""
    try:
        tip = rpc("getblockcount")
        genesis = rpc("getblockhash", [0])
        if type(tip) is not int or tip < 0 or not isinstance(genesis, str):
            raise ProvenanceError("unexpected Zebra chain response")
        anchor = rpc("getblockhash", [meta["anchorHeight"]]) if tip >= meta["anchorHeight"] else None
    except ChainError:
        raise
    except Exception as exc:
        raise ProvenanceError(f"chain RPC unavailable: {type(exc).__name__}") from exc
    if genesis != meta["genesisHash"]:
        raise ProvenanceError("live genesis hash differs from the chain identity")
    try:
        verify_anchor(meta, lambda _height: anchor, tip)
    except ChainError as exc:
        raise ProvenanceError(f"durable anchor check failed: {exc}") from exc
    return tip


def record(root: Path, attestation: object, *, rpc, expect_genesis: str, expect_anchor_height: int,
           expect_project: str, dry_run: bool = False, now: int | None = None,
           machine: str | None = None) -> dict:
    """Add host-attested runtime provenance; see the module docstring."""
    root = Path(root)
    if str(root) in PROTECTED_ROOTS:
        raise ProvenanceError("refusing to migrate a protected research root")
    _paths(root)
    if root.is_symlink() or not root.is_dir():
        raise ProvenanceError("root must be an existing directory")
    with identity_lock(root, MIGRATION_LOCK_TIMEOUT):
        return _record_locked(root, attestation, rpc=rpc, expect_genesis=expect_genesis,
                              expect_anchor_height=expect_anchor_height, expect_project=expect_project,
                              dry_run=dry_run, now=now, machine=machine)


def _record_locked(root: Path, attestation: object, *, rpc, expect_genesis: str, expect_anchor_height: int,
                   expect_project: str, dry_run: bool, now: int | None, machine: str | None) -> dict:
    path = root / IDENTITY
    original, info = _read_regular(path)
    try:
        meta = validate_identity(json.loads(original))
    except ValueError as exc:
        raise ProvenanceError("chain identity is not valid JSON") from exc
    if meta.get("management") != "external":
        raise ProvenanceError("only externally managed (Compose) chain identities are migrated")
    if meta.get("detectedReset"):
        raise ProvenanceError("chain identity records a reset; refusing to migrate")
    if meta["genesisHash"] != expect_genesis or meta["anchorHeight"] != expect_anchor_height:
        raise ProvenanceError("chain identity differs from the operator's expected genesis or anchor height")
    claim = validate_attestation(attestation, now=int(time.time()) if now is None else now,
                                 machine=machine_platform() if machine is None else machine,
                                 project=expect_project)
    tip = _chain_view(rpc, meta)

    existing = meta.get(RUNTIME_KEY)
    if existing is not None:
        if existing["composeProject"] != expect_project:
            raise ProvenanceError(f"the recorded runtime belongs to Compose project {existing['composeProject']!r}, "
                                  f"not the expected {expect_project!r}; refusing")
        if (existing["imageId"], existing["platform"]) == (claim["imageId"], claim["platform"]):
            if not dry_run:
                _confirm_durable(path, original)
            # The existing record is left exactly as it is. A replaced container in the same project is
            # reported, never silently written: this attestation is only today's host check.
            return {"changed": False, "reason": "ALREADY_RECORDED", "durable": not dry_run,
                    "zebraRuntime": existing, "sha256": hashlib.sha256(original).hexdigest(),
                    "freshAttestation": {"containerId": claim["containerId"], "attestedAt": claim["attestedAt"],
                                         "composeProject": claim["composeProject"],
                                         "sameContainerAsRecord": claim["containerId"] == existing["containerId"]}}
        raise ProvenanceError("a different Zebra runtime is already recorded; refusing to overwrite it")

    runtime = {"schema": 2, "imageId": claim["imageId"], "platform": claim["platform"],
               "declaredSourceRevision": ZEBRA_SOURCE_REVISION, "evidence": RUNTIME_EVIDENCE,
               "attestedAt": claim["attestedAt"], "containerId": claim["containerId"],
               "composeProject": claim["composeProject"], "recordedImage": meta["image"],
               "verifiedChain": {"genesisHash": meta["genesisHash"], "anchorHeight": meta["anchorHeight"],
                                 "anchorHash": meta["anchorHash"], "observedHeight": tip},
               "migratedFromSha256": hashlib.sha256(original).hexdigest()}
    updated = dict(meta)
    updated[RUNTIME_KEY] = runtime
    validate_identity(updated)
    if {key: value for key, value in updated.items() if key != RUNTIME_KEY} != meta:
        raise ProvenanceError("internal error: migration would change chain identity")
    payload = serialize(updated)
    if len(payload) > MAX_IDENTITY_BYTES:
        raise ProvenanceError("migrated chain identity is too large")
    if dry_run:
        return {"changed": False, "reason": "DRY_RUN", "zebraRuntime": runtime}
    _replace_locked(path, original, payload, info)
    return {"changed": True, "outcome": "WRITTEN", "durable": True, "zebraRuntime": runtime,
            "sha256": {"before": runtime["migratedFromSha256"], "after": hashlib.sha256(payload).hexdigest()}}


def revert(root: Path, *, dry_run: bool = False) -> dict:
    """Undo record(): only when the identity is otherwise unchanged since then."""
    root = Path(root)
    if str(root) in PROTECTED_ROOTS:
        raise ProvenanceError("refusing to change a protected research root")
    _paths(root)
    with identity_lock(root, MIGRATION_LOCK_TIMEOUT):
        return _revert_locked(root, dry_run)


def _revert_locked(root: Path, dry_run: bool) -> dict:
    path = root / IDENTITY
    current, info = _read_regular(path)
    try:
        meta = validate_identity(json.loads(current))
    except ValueError as exc:
        raise ProvenanceError("chain identity is not valid JSON") from exc
    runtime = meta.get(RUNTIME_KEY)
    if runtime is None:
        return {"changed": False, "reason": "NOT_RECORDED", "sha256": hashlib.sha256(current).hexdigest()}
    previous = {key: value for key, value in meta.items() if key != RUNTIME_KEY}
    payload = serialize(previous)
    if hashlib.sha256(payload).hexdigest() != runtime["migratedFromSha256"]:
        raise ProvenanceError("chain identity changed since the migration; "
                              "restore the paired pre-migration backup instead")
    validate_identity(previous)
    if dry_run:
        return {"changed": False, "reason": "DRY_RUN", "sha256": runtime["migratedFromSha256"]}
    _replace_locked(path, current, payload, info)
    return {"changed": True, "outcome": "WRITTEN", "durable": True, "sha256": runtime["migratedFromSha256"]}


def show(root: Path) -> dict:
    """Read-only. It inspects no container, so it never claims more than the record."""
    root = Path(root)
    _paths(root)
    data, _ = _read_regular(root / IDENTITY)
    meta = validate_identity(json.loads(data))
    return {"genesisHash": meta["genesisHash"], "anchorHeight": meta["anchorHeight"],
            "anchorHash": meta["anchorHash"], "management": meta.get("management", "docker-local"),
            "provenance": runtime_provenance(meta, "external"), "zebraRuntime": meta.get(RUNTIME_KEY),
            "sha256": hashlib.sha256(data).hexdigest()}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    actions = parser.add_subparsers(dest="action", required=True)
    shown = actions.add_parser("show")
    shown.add_argument("--root", type=Path, required=True)
    reverted = actions.add_parser("revert")
    reverted.add_argument("--root", type=Path, required=True)
    reverted.add_argument("--dry-run", action="store_true")
    recorded = actions.add_parser("record")
    recorded.add_argument("--root", type=Path, required=True)
    recorded.add_argument("--rpc-host", required=True)
    recorded.add_argument("--rpc-port", type=int, default=18232)
    recorded.add_argument("--expect-genesis", required=True)
    recorded.add_argument("--expect-anchor-height", type=int, required=True)
    recorded.add_argument("--expect-compose-project", required=True)
    recorded.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.action == "show":
            output = show(args.root)
        elif args.action == "revert":
            output = revert(args.root, dry_run=args.dry_run)
        else:
            if not 1 <= args.rpc_port <= 65535 or args.expect_anchor_height < 0:
                parser.error("invalid port or anchor height")
            if not re.fullmatch(r"[0-9a-f]{64}", args.expect_genesis):
                parser.error("--expect-genesis must be 64 lowercase hex characters")
            if not PROJECT.fullmatch(args.expect_compose_project):
                parser.error("invalid --expect-compose-project")
            raw = sys.stdin.buffer.read(MAX_ATTESTATION_BYTES + 1)
            if len(raw) > MAX_ATTESTATION_BYTES:
                raise ProvenanceError("attestation is too large")
            try:
                attestation = json.loads(raw)
            except ValueError as exc:
                raise ProvenanceError("attestation is not valid JSON") from exc
            output = record(args.root, attestation,
                            rpc=lambda method, params=None: _rpc(args.rpc_port, method, params, host=args.rpc_host),
                            expect_genesis=args.expect_genesis, expect_anchor_height=args.expect_anchor_height,
                            expect_project=args.expect_compose_project, dry_run=args.dry_run)
    except UncertainWrite as exc:
        print(json.dumps({"outcome": "UNCERTAIN", "detail": str(exc),
                          "sha256": {"before": exc.before, "after": exc.after}}, indent=2, sort_keys=True))
        print(f"PROVENANCE_UNCERTAIN {exc}; before={exc.before} after={exc.after}. Do not revert: run `show`, "
              "compare its sha256, then re-run the same command to confirm and re-fsync", file=sys.stderr)
        raise SystemExit(3) from exc
    except (ChainError, OSError, ValueError) as exc:
        print(f"PROVENANCE_REFUSED {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(output, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
