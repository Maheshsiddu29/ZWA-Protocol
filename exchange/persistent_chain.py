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

IMAGE = "sha256:540a3429ef14ff3c575258255e4310951606d16c86ec5e1c37e285ea95178185"
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


def _rpc(port: int, method: str, params: list | None = None) -> object:
    body = json.dumps({"jsonrpc": "1.0", "id": "zwa4e", "method": method,
                       "params": params or []}).encode()
    request = Request(f"http://127.0.0.1:{port}", body,
                      {"content-type": "application/json"})
    with urlopen(request, timeout=5) as response:
        data = json.load(response)
    if data.get("error") is not None:
        raise ChainError(f"RPC {method} failed: {data['error']}")
    return data["result"]


def _meta(root: Path) -> dict | None:
    path = root / "chain-identity.json"
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 8192:
        raise ChainError("unsafe chain identity file")
    data = json.loads(path.read_text())
    if (data.get("version") != 1 or data.get("image") != IMAGE
            or data.get("configSha256") != CONFIG_SHA256
            or type(data.get("anchorHeight")) is not int or data["anchorHeight"] < 0
            or not isinstance(data.get("anchorHash"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", data["anchorHash"])
            or not isinstance(data.get("genesisHash"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", data["genesisHash"])
            or data.get("lastStartState") not in ("CHAIN_ACTIVE", "CHAIN_RESTARTED")):
        raise ChainError("chain identity or pinned configuration changed")
    return data


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
    if (root.parent != Path("/private/tmp")
            or not re.fullmatch(r"zwa4e-[A-Za-z0-9-]+", root.name)):
        raise ChainError("root must be an absolute /private/tmp/zwa4e-* directory")
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


def checkpoint(root: Path, port: int) -> dict:
    _paths(root)
    meta = _meta(root)
    if not meta:
        raise ChainError("chain has not been initialized")
    height = _rpc(port, "getblockcount")
    verify_anchor(meta, lambda h: _rpc(port, "getblockhash", [h]), height)
    meta["anchorHeight"] = height
    meta["anchorHash"] = _rpc(port, "getblockhash", [height])
    meta["lastObservedHeight"] = height
    _atomic_json(root / "chain-identity.json", meta)
    return {"state": "CHAIN_ACTIVE", "height": height,
            "anchorHash": meta["anchorHash"]}


def status(root: Path, container: str, port: int) -> dict:
    _paths(root)
    meta = _meta(root) if root.exists() else None
    if not meta:
        return {"state": "CHAIN_STOPPED", "reason": "UNINITIALIZED"}
    if meta.get("detectedReset"):
        return {"state": "CHAIN_RESET", "reason": meta["detectedReset"]}
    if not _state_exists(root / "chain"):
        return {"state": "CHAIN_RESET", "reason": "STATE_DATABASE_MISSING"}
    info = _inspect(container)
    if not info or not info["State"]["Running"]:
        return {"state": "CHAIN_STOPPED", "anchorHeight": meta["anchorHeight"]}
    _validate_container(info, root, port)
    try:
        height = _rpc(port, "getblockcount")
        genesis = _rpc(port, "getblockhash", [0])
        anchor = _rpc(port, "getblockhash", [meta["anchorHeight"]]) if height >= meta["anchorHeight"] else None
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
            "anchorHeight": meta["anchorHeight"], "anchorHash": meta["anchorHash"]}


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
