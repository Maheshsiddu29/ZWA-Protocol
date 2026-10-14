"""Operate one pinned, persistent experimental Zebra regtest chain.

This process never sees wallet seeds. The chain identity file is an operator
guard, not a consensus proof: callers still verify their own transactions and
wallet scans against the live RPC before advertising current holdings.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.request import Request, urlopen

# QED-it/zebra (zsa-swap-multiple-ag) revision every pinned image was built from.
ZEBRA_SOURCE_REVISION = "0aef55cea41b83f17610e6ea708e59995f9e739f"
# The only Zebra images accepted as runtime provenance, by build platform.
PINNED_ZEBRA_IMAGES = {
    "linux/arm64": "sha256:540a3429ef14ff3c575258255e4310951606d16c86ec5e1c37e285ea95178185",
    "linux/amd64": "sha256:2c3f3dde0230f572911d70b7f74cc8f4587c1a139b0756d904e3b09eba2e32b9",
}
# Every version-1 identity records this value under "image". For docker-local
# chains it is also the container image checked on each start and status. For
# external (Compose) chains this process cannot see the container, so the
# value is only a recorded label; host-attested runtime provenance is kept
# separately under RUNTIME_KEY and never inferred from the label.
RECORDED_IMAGE = PINNED_ZEBRA_IMAGES["linux/arm64"]
IMAGE = RECORDED_IMAGE  # docker-local runtime image (the local arm64 workflow)
RUNTIME_KEY = "zebraRuntime"
CONFIG = """[mining]
miner_address = 'tmLTZegcJN5zaufWQBARHkvqC62mTumm3jR'

[network]
network = "Regtest"
[network.testnet_parameters.activation_heights]
NU5 = 1
NU6 = 1
NU7 = 1
SWAP = 1

[state]
ephemeral = false
cache_dir = "/data/state"

[rpc]
listen_addr = "0.0.0.0:18232"
enable_cookie_auth = false
"""
CONFIG_SHA256 = hashlib.sha256(CONFIG.encode()).hexdigest()


class ChainError(RuntimeError):
    pass


def _atomic_json(path: Path, data: dict) -> None:
    temp = path.with_name(path.name + ".tmp")
    with temp.open("x") as out:
        json.dump(data, out, indent=2, sort_keys=True)
        out.write("\n")
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _docker(*args: str) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    if result.returncode:
        raise ChainError(result.stderr.strip() or "Docker command failed")
    return result.stdout.strip()


RPC_HOST = re.compile(r"127\.0\.0\.1|[a-z][a-z0-9-]{0,62}\Z")


def _rpc(port: int, method: str, params: list | None = None, host: str = "127.0.0.1") -> object:
    # Loopback on the developer machine, or a private Compose service name.
    if not RPC_HOST.fullmatch(host):
        raise ChainError("RPC host must be loopback or a private service name")
    body = json.dumps({"jsonrpc": "1.0", "id": "zwa4e", "method": method,
                       "params": params or []}).encode()
    request = Request(f"http://{host}:{port}", body,
                      {"content-type": "application/json"})
    with urlopen(request, timeout=5) as response:
        data = json.load(response)
    if data.get("error") is not None:
        raise ChainError(f"RPC {method} failed: {data['error']}")
    return data["result"]


def _hex64(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _height(value: object) -> bool:
    return type(value) is int and value >= 0


RUNTIME_FIELDS = frozenset({"schema", "imageId", "platform", "sourceRevision", "evidence", "attestedAt",
                            "recordedImage", "verifiedChain", "migratedFromSha256"})
VERIFIED_CHAIN_FIELDS = frozenset({"genesisHash", "anchorHeight", "anchorHash", "observedHeight"})


def validate_runtime(meta: dict) -> dict | None:
    """Strictly check the optional host-attested runtime provenance.

    It is written only by the explicit exchange.zebra_provenance migration,
    only for externally managed chains, and must name a pinned image built
    for its own platform. Anything else fails closed.
    """
    if RUNTIME_KEY not in meta:
        return None
    runtime = meta[RUNTIME_KEY]
    chain = runtime.get("verifiedChain") if isinstance(runtime, dict) else None
    if (not isinstance(runtime, dict) or set(runtime) != RUNTIME_FIELDS
            or meta.get("management") != "external"
            or type(runtime["schema"]) is not int or runtime["schema"] != 1
            or not isinstance(runtime["platform"], str)
            or PINNED_ZEBRA_IMAGES.get(runtime["platform"]) != runtime["imageId"]
            or runtime["sourceRevision"] != ZEBRA_SOURCE_REVISION
            or runtime["evidence"] != "HOST_ATTESTED"
            or type(runtime["attestedAt"]) is not int or runtime["attestedAt"] <= 0
            or runtime["recordedImage"] != meta["image"]
            or not _hex64(runtime["migratedFromSha256"])
            or not isinstance(chain, dict) or set(chain) != VERIFIED_CHAIN_FIELDS
            or chain["genesisHash"] != meta["genesisHash"]
            or not _height(chain["anchorHeight"]) or chain["anchorHeight"] > meta["anchorHeight"]
            or not _hex64(chain["anchorHash"])
            or (chain["anchorHeight"] == meta["anchorHeight"] and chain["anchorHash"] != meta["anchorHash"])
            or not _height(chain["observedHeight"]) or chain["observedHeight"] < chain["anchorHeight"]):
        raise ChainError("Zebra runtime provenance is malformed or not a pinned image")
    return runtime


def validate_identity(data: object) -> dict:
    """Version-1 chain identity, with or without recorded runtime provenance."""
    if (not isinstance(data, dict)
            or data.get("version") != 1 or data.get("image") != RECORDED_IMAGE
            or data.get("configSha256") != CONFIG_SHA256
            or not _height(data.get("anchorHeight"))
            or not _hex64(data.get("anchorHash"))
            or not _hex64(data.get("genesisHash"))
            or data.get("lastStartState") not in ("CHAIN_ACTIVE", "CHAIN_RESTARTED")
            or data.get("management", "external") != "external"):
        raise ChainError("chain identity or pinned configuration changed")
    validate_runtime(data)
    return data


def _meta(root: Path) -> dict | None:
    path = root / "chain-identity.json"
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 8192:
        raise ChainError("unsafe chain identity file")
    return validate_identity(json.loads(path.read_text()))


def runtime_provenance(meta: dict, managed: str) -> dict:
    """What this process can honestly say about the running Zebra image."""
    if managed == "docker-local":
        # _validate_container compared the live container's image ID.
        return {"recordedImage": meta["image"], "runtimeImage": IMAGE,
                "platform": "linux/arm64", "evidence": "CONTAINER_INSPECTED"}
    runtime = meta.get(RUNTIME_KEY)
    if runtime is None:
        # The recorded label alone never proves which image is running.
        return {"recordedImage": meta["image"], "evidence": "UNVERIFIED"}
    return {"recordedImage": meta["image"], "runtimeImage": runtime["imageId"],
            "platform": runtime["platform"], "evidence": "HOST_ATTESTED",
            "attestedAt": runtime["attestedAt"]}


def verify_anchor(meta: dict, block_hash_at_height, tip_height: int) -> None:
    """Fail closed if the recorded chain prefix no longer exists."""
    height = meta["anchorHeight"]
    if tip_height < height or block_hash_at_height(height) != meta["anchorHash"]:
        raise ChainError("CHAIN_RESET: recorded chain anchor is absent or changed")


def _inspect(container: str) -> dict | None:
    result = subprocess.run(["docker", "inspect", container], capture_output=True, text=True)
    if result.returncode:
        if "no such object" in result.stderr.lower() or "no such container" in result.stderr.lower():
            return None
        raise ChainError(result.stderr.strip() or "Docker inspect failed")
    return json.loads(result.stdout)[0]


def _validate_container(info: dict, root: Path, port: int) -> None:
    if info["Image"] != IMAGE:
        raise ChainError("container image differs from pinned Zebra image")
    mounts = info.get("Mounts", [])
    if not any(m.get("Source") == str(root / "chain") and
               m.get("Destination") == "/data" for m in mounts):
        raise ChainError("container is not using this persistent chain volume")
    bindings = info.get("HostConfig", {}).get("PortBindings", {}).get("18232/tcp", [])
    if not any(b.get("HostIp") == "127.0.0.1" and b.get("HostPort") == str(port)
               for b in bindings):
        raise ChainError("container RPC binding differs from configured loopback port")


def _paths(root: Path) -> tuple[Path, Path]:
    if (root.parent not in (Path("/private/tmp"), Path("/var/lib/zwa"))
            or not re.fullmatch(r"zwa4e-[A-Za-z0-9-]+", root.name)):
        raise ChainError("root must be an absolute /private/tmp/zwa4e-* or /var/lib/zwa/zwa4e-* directory")
    if root.is_symlink():
        raise ChainError("root cannot be a symlink")
    chain = root / "chain"
    return chain, chain / "regtest-config.toml"


def _state_exists(chain: Path) -> bool:
    state = chain / "state"
    return state.is_dir() and any(state.iterdir())


def start(root: Path, container: str, port: int) -> dict:
    chain, config = _paths(root)
    meta = _meta(root) if root.exists() else None
    if meta and meta.get("detectedReset"):
        raise ChainError("CHAIN_RESET: " + meta["detectedReset"])
    if meta and not _state_exists(chain):
        raise ChainError("CHAIN_RESET: recorded state database is missing")
    if not root.exists():
        root.mkdir(mode=0o700)
    chain.mkdir(mode=0o700, exist_ok=True)
    if config.exists():
        if config.is_symlink() or config.read_text() != CONFIG:
            raise ChainError("persistent Zebra configuration changed")
    else:
        if meta:
            raise ChainError("CHAIN_RESET: persistent Zebra configuration is missing")
        config.write_text(CONFIG)
        config.chmod(0o600)
    if not meta and _state_exists(chain):
        raise ChainError("unidentified chain state exists; refusing adoption")
    info = _inspect(container)
    was_running = bool(info and info["State"]["Running"])
    if info:
        _validate_container(info, root, port)
        if not info["State"]["Running"]:
            _docker("start", container)
    else:
        _docker("run", "-d", "--name", container,
                "-p", f"127.0.0.1:{port}:18232",
                "-v", f"{chain}:/data",
                "--entrypoint", "target/release/zebrad", IMAGE,
                "-c", "/data/regtest-config.toml")
    for _ in range(60):
        try:
            height = _rpc(port, "getblockcount")
            genesis = _rpc(port, "getblockhash", [0])
            break
        except Exception:
            time.sleep(1)
    else:
        raise ChainError("pinned Zebra did not become RPC-ready")
    if type(height) is not int or not isinstance(genesis, str):
        raise ChainError("unexpected Zebra chain response")
    if meta:
        try:
            if genesis != meta["genesisHash"]:
                raise ChainError("CHAIN_RESET: genesis hash changed")
            verify_anchor(meta, lambda h: _rpc(port, "getblockhash", [h]), height)
        except ChainError as exc:
            meta["detectedReset"] = str(exc)
            _atomic_json(root / "chain-identity.json", meta)
            raise
        state = meta["lastStartState"] if was_running else "CHAIN_RESTARTED"
    else:
        meta = {"version": 1, "image": IMAGE, "configSha256": CONFIG_SHA256,
                "genesisHash": genesis, "anchorHeight": 0, "anchorHash": genesis}
        state = "CHAIN_ACTIVE"
    meta["lastStartState"] = state
    meta["lastObservedHeight"] = height
    _atomic_json(root / "chain-identity.json", meta)
    return {"state": state, "height": height, "genesisHash": genesis,
            "anchorHeight": meta["anchorHeight"], "anchorHash": meta["anchorHash"]}


def adopt_external(root: Path, port: int, host: str) -> dict:
    """Record the identity of a fresh, separately managed chain (Compose).

    Only a chain at height 0 with no prior identity is adopted; from then on
    every status read checks its genesis and anchor hashes. "image" is the
    version-1 recorded label; runtime provenance is recorded separately by
    the explicit exchange.zebra_provenance step after a host attestation.
    """
    _paths(root)
    if _meta(root) is not None:
        raise ChainError("chain identity already exists; refusing to adopt again")
    height = _rpc(port, "getblockcount", host=host)
    if height != 0:
        raise ChainError("only a fresh chain at height 0 can be adopted")
    genesis = _rpc(port, "getblockhash", [0], host=host)
    meta = {"version": 1, "image": IMAGE, "configSha256": CONFIG_SHA256, "genesisHash": genesis,
            "anchorHeight": 0, "anchorHash": genesis, "lastStartState": "CHAIN_ACTIVE",
            "lastObservedHeight": 0, "management": "external"}
    _atomic_json(root / "chain-identity.json", meta)
    return {"state": "CHAIN_ACTIVE", "height": 0, "genesisHash": genesis}


def checkpoint(root: Path, port: int, host: str = "127.0.0.1") -> dict:
    _paths(root)
    meta = _meta(root)
    if not meta:
        raise ChainError("chain has not been initialized")
    height = _rpc(port, "getblockcount", host=host)
    verify_anchor(meta, lambda h: _rpc(port, "getblockhash", [h], host=host), height)
    # This pinned Zebra drops its last 99 non-finalized blocks on restart.
    # Never checkpoint the volatile tip as though it were durable.
    safe_height = max(meta["anchorHeight"], height - 100)
    meta["anchorHeight"] = safe_height
    meta["anchorHash"] = _rpc(port, "getblockhash", [safe_height], host=host)
    meta["lastObservedHeight"] = height
    _atomic_json(root / "chain-identity.json", meta)
    return {"state": "CHAIN_ACTIVE", "height": height, "anchorHeight": safe_height,
            "anchorHash": meta["anchorHash"]}


def status(root: Path, container: str, port: int, *, host: str = "127.0.0.1",
           managed: str = "docker-local") -> dict:
    """Live chain state. "external": a separately managed node (Compose);
    this process cannot inspect the container, so identity rests on the
    recorded genesis and anchor hashes alone."""
    _paths(root)
    meta = _meta(root) if root.exists() else None
    if not meta:
        return {"state": "CHAIN_STOPPED", "reason": "UNINITIALIZED"}
    if meta.get("detectedReset"):
        return {"state": "CHAIN_RESET", "reason": meta["detectedReset"]}
    if managed == "docker-local":
        if not _state_exists(root / "chain"):
            return {"state": "CHAIN_RESET", "reason": "STATE_DATABASE_MISSING"}
        info = _inspect(container)
        if not info or not info["State"]["Running"]:
            return {"state": "CHAIN_STOPPED", "anchorHeight": meta["anchorHeight"]}
        _validate_container(info, root, port)
    elif managed != "external":
        raise ChainError("unsupported chain management")
    try:
        height = _rpc(port, "getblockcount", host=host)
        genesis = _rpc(port, "getblockhash", [0], host=host)
        anchor = (_rpc(port, "getblockhash", [meta["anchorHeight"]], host=host)
                  if height >= meta["anchorHeight"] else None)
    except Exception as exc:
        return {"state": "CHAIN_STOPPED", "reason": "RPC_UNAVAILABLE", "detail": str(exc)}
    try:
        if genesis != meta["genesisHash"]:
            raise ChainError("genesis hash changed")
        verify_anchor(meta, lambda _h: anchor, height)
    except ChainError as exc:
        meta["detectedReset"] = str(exc)
        _atomic_json(root / "chain-identity.json", meta)
        return {"state": "CHAIN_RESET", "reason": str(exc)}
    return {"state": meta["lastStartState"], "height": height,
            "anchorHeight": meta["anchorHeight"], "anchorHash": meta["anchorHash"],
            "provenance": runtime_provenance(meta, managed)}


def stop(root: Path, container: str, port: int) -> dict:
    _paths(root)
    info = _inspect(container)
    if not info or not info["State"]["Running"]:
        return status(root, container, port)
    _validate_container(info, root, port)
    meta = _meta(root)
    if not meta:
        raise ChainError("chain has not been initialized")
    tip = _rpc(port, "getblockcount")
    verify_anchor(meta, lambda h: _rpc(port, "getblockhash", [h]), tip)
    # The recent non-finalized tip is not guaranteed to survive this pinned
    # Zebra restart. Keep the last explicitly checked anchor, never advance it
    # to the volatile tip during shutdown.
    _docker("stop", container)
    return {"state": "CHAIN_STOPPED", "tipBeforeStop": tip,
            "anchorHeight": meta["anchorHeight"], "anchorHash": meta["anchorHash"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "checkpoint", "status", "stop"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--container", default="zwa-phase4e-regtest")
    parser.add_argument("--port", type=int, default=18320)
    args = parser.parse_args()
    os.umask(0o077)
    if not 1 <= args.port <= 65535:
        parser.error("invalid port")
    try:
        result = {"start": start, "checkpoint": checkpoint,
                  "status": status, "stop": stop}[args.action]
        if args.action == "checkpoint":
            output = result(args.root, args.port)
        else:
            output = result(args.root, args.container, args.port)
    except (ChainError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(output, sort_keys=True))


if __name__ == "__main__":
    main()
