"""Operator configuration for one private persistent exchange root.

The configuration file lives inside the owner-only session root and is written
only by the operator CLI below. Browser requests never select a root, port,
binary, or approval mode; the portal bridge loads this file itself.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

ROOT_PATTERN = re.compile(r"/private/tmp/zwa4e-[A-Za-z0-9-]+\Z")
TX_TOOL_REVISION = "16052323904be8efbb9e0f92d059fa57c9198f8a"
# Roots holding owner-reviewed evidence. Scripted signer approval is refused
# here regardless of what the configuration file says.
PROTECTED_ROOTS = frozenset({"/private/tmp/zwa4e-phase4e-b"})
APPROVAL_MODES = ("HUMAN_APPROVED", "SCRIPTED_TEST_MODE")
CONFIG_NAME = "service-config.json"


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ExchangeConfig:
    root: Path
    container: str
    rpc_port: int
    approval_mode: str
    tx_tool_dir: Path
    params_data_home: Path
    node_bin: Path
    quote_validity_seconds: int = 600
    approval_wait_seconds: int = 300
    companion_stale_seconds: int = 15
    stabilization_blocks: int = 110

    @property
    def exchange(self) -> Path:
        return self.root / "exchange"

    @property
    def ledger_path(self) -> Path:
        return self.exchange / "exchange.sqlite"

    @property
    def chain_identity(self) -> Path:
        return self.root / "chain-identity.json"

    @property
    def tx_tool_bin(self) -> Path:
        return self.tx_tool_dir / "target/release/zcash_tx_tool"

    @property
    def protected(self) -> bool:
        return str(self.root) in PROTECTED_ROOTS

    def order_dir(self, order_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", order_id):
            raise ConfigError("invalid order ID")
        return self.exchange / "orders" / order_id

    def socket_path(self, order_id: str) -> Path:
        # macOS limits Unix socket paths to 104 bytes; keep this short.
        path = self.exchange / "s" / f"{order_id}.sock"
        if len(str(path)) >= 100:
            raise ConfigError("approval socket path is too long")
        return path

    def check_tool_revision(self) -> None:
        head = subprocess.run(["git", "-C", str(self.tx_tool_dir), "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10)
        if head.returncode or head.stdout.strip() != TX_TOOL_REVISION:
            raise ConfigError("pinned transaction tool revision mismatch")
        if not os.access(self.tx_tool_bin, os.X_OK):
            raise ConfigError("pinned transaction tool binary is missing")


def _root(value: str | Path | None) -> Path:
    raw = str(value if value is not None else os.environ.get("ZWA_PRIVATE_EXCHANGE_ROOT", ""))
    if not ROOT_PATTERN.fullmatch(raw):
        raise ConfigError("exchange root must be a direct /private/tmp/zwa4e-* directory")
    root = Path(raw)
    if root.is_symlink() or not root.is_dir():
        raise ConfigError("exchange root is unavailable")
    return root


def load(root: str | Path | None = None) -> ExchangeConfig:
    root_path = _root(root)
    path = root_path / "exchange" / CONFIG_NAME
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        raise ConfigError("exchange service is not configured")
    if path.stat().st_mode & 0o077:
        raise ConfigError("service configuration must be owner-only")
    data = json.loads(path.read_text())
    expected = {"version", "root", "container", "rpcPort", "approvalMode", "txToolDir",
                "paramsDataHome", "nodeBin", "quoteValiditySeconds", "approvalWaitSeconds",
                "companionStaleSeconds", "stabilizationBlocks"}
    if type(data) is not dict or set(data) != expected or data["version"] != 1:
        raise ConfigError("service configuration schema")
    if data["root"] != str(root_path):
        raise ConfigError("service configuration belongs to another root")
    if data["approvalMode"] not in APPROVAL_MODES:
        raise ConfigError("unsupported approval mode")
    if data["approvalMode"] == "SCRIPTED_TEST_MODE" and str(root_path) in PROTECTED_ROOTS:
        raise ConfigError("scripted approval is forbidden on a protected root")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}", str(data["container"])):
        raise ConfigError("invalid container name")
    if type(data["rpcPort"]) is not int or not 1024 <= data["rpcPort"] <= 65535:
        raise ConfigError("invalid RPC port")
    for key, low, high in (("quoteValiditySeconds", 60, 3600), ("approvalWaitSeconds", 30, 300),
                           ("companionStaleSeconds", 5, 120), ("stabilizationBlocks", 101, 2000)):
        if type(data[key]) is not int or not low <= data[key] <= high:
            raise ConfigError(f"invalid {key}")
    paths = [Path(data[key]) for key in ("txToolDir", "paramsDataHome", "nodeBin")]
    if any(not p.is_absolute() for p in paths):
        raise ConfigError("tool paths must be absolute")
    return ExchangeConfig(root=root_path, container=data["container"], rpc_port=data["rpcPort"],
                          approval_mode=data["approvalMode"], tx_tool_dir=paths[0],
                          params_data_home=paths[1], node_bin=paths[2],
                          quote_validity_seconds=data["quoteValiditySeconds"],
                          approval_wait_seconds=data["approvalWaitSeconds"],
                          companion_stale_seconds=data["companionStaleSeconds"],
                          stabilization_blocks=data["stabilizationBlocks"])


def init(root: str, container: str, rpc_port: int, approval_mode: str,
         params_data_home: str, tx_tool_dir: str = "/private/tmp/zwa4c1-tx-tool",
         node_bin: str | None = None) -> dict:
    root_path = _root(root)
    path = root_path / "exchange" / CONFIG_NAME
    if path.exists():
        raise ConfigError("refusing to overwrite an existing service configuration")
    node = node_bin or shutil.which("node")
    if not node:
        raise ConfigError("Node.js is required for Groth16 verification")
    data = {"version": 1, "root": str(root_path), "container": container, "rpcPort": rpc_port,
            "approvalMode": approval_mode, "txToolDir": tx_tool_dir,
            "paramsDataHome": params_data_home, "nodeBin": str(Path(node).resolve()),
            "quoteValiditySeconds": 600, "approvalWaitSeconds": 300,
            "companionStaleSeconds": 15, "stabilizationBlocks": 110}
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as out:
        json.dump(data, out, indent=2)
        out.write("\n")
    try:
        load(root_path).check_tool_revision()
    except ConfigError:
        path.unlink()
        raise
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("init")
    create.add_argument("--root", required=True)
    create.add_argument("--container", required=True)
    create.add_argument("--port", type=int, required=True)
    create.add_argument("--approval-mode", choices=APPROVAL_MODES, required=True)
    create.add_argument("--params-data-home", required=True)
    create.add_argument("--tx-tool-dir", default="/private/tmp/zwa4c1-tx-tool")
    create.add_argument("--node-bin")
    show = sub.add_parser("show")
    show.add_argument("--root", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "init":
        print(json.dumps(init(args.root, args.container, args.port, args.approval_mode,
                              args.params_data_home, args.tx_tool_dir, args.node_bin), indent=2))
    else:
        config = load(args.root)
        print(json.dumps({"root": str(config.root), "container": config.container,
                          "rpcPort": config.rpc_port, "approvalMode": config.approval_mode,
                          "protected": config.protected}, indent=2))


if __name__ == "__main__":
    main()
