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

# Private roots on the macOS developer machine or a Linux deployment volume.
ROOT_PATTERN = re.compile(r"(?:/private/tmp|/var/lib/zwa)/zwa4e-[A-Za-z0-9-]+\Z")
TX_TOOL_REVISION = "16052323904be8efbb9e0f92d059fa57c9198f8a"
# Roots holding owner-reviewed evidence. Scripted signer approval is refused
# here regardless of what the configuration file says.
PROTECTED_ROOTS = frozenset({"/private/tmp/zwa4e-phase4e-b"})
# CUSTODIAL_DEMO_POLICY: disposable server-held demo wallets whose signer
# releases a group only inside a bounded standing policy after the browser
# confirmed the exact quote. It is never human wallet approval.
APPROVAL_MODES = ("HUMAN_APPROVED", "SCRIPTED_TEST_MODE", "CUSTODIAL_DEMO_POLICY")
CONFIG_NAME = "service-config.json"
ISOLATION_MODES = ("macos-sandbox", "linux-landlock")
DEMO_LIMITS = {
    # key: (minimum, maximum, default)
    "maxActiveWallets": (1, 500, 40),
    "walletLifetimeSeconds": (600, 86400, 7200),
    "maxWalletsPerHour": (1, 1000, 60),
    "maxWalletsPerClientPerHour": (1, 20, 3),
    "maxOrdersPerWallet": (1, 50, 5),
    "maxUnitsPerOrder": (1, 100, 5),
    "maxPaymentZatPerOrder": (1, 100_000_000, 1_000_000),
    "maxSponsoredOrdersPerHour": (1, 1000, 60),
    "sponsorReserve": (4, 200, 12),
}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class DemoPolicy:
    """Bounds for the hosted judge sandbox; enforced by the service and signer."""
    dealer_account_id: str = "dealer"
    max_active_wallets: int = 40
    wallet_lifetime_seconds: int = 7200
    max_wallets_per_hour: int = 60
    max_wallets_per_client_per_hour: int = 3
    max_orders_per_wallet: int = 5
    max_units_per_order: int = 5
    max_payment_zat_per_order: int = 1_000_000
    max_sponsored_orders_per_hour: int = 60
    sponsor_reserve: int = 12


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
    rpc_host: str = "127.0.0.1"
    # "docker-local": this machine runs the pinned container itself.
    # "external": a separately managed node (Compose); identity is still
    # checked through the genesis and anchor hashes on every status read.
    chain_management: str = "docker-local"
    isolation: str = "macos-sandbox"
    demo: DemoPolicy | None = None

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

    @property
    def demo_mode(self) -> bool:
        return self.approval_mode == "CUSTODIAL_DEMO_POLICY"

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


def _demo_policy(raw: object) -> DemoPolicy:
    if type(raw) is not dict or set(raw) != {"dealerAccountId", *DEMO_LIMITS}:
        raise ConfigError("demo policy schema")
    if not isinstance(raw["dealerAccountId"], str) or not re.fullmatch(r"[a-z][a-z0-9_-]{2,40}",
                                                                     raw["dealerAccountId"]):
        raise ConfigError("invalid dealer account")
    values = {}
    for key, (low, high, _) in DEMO_LIMITS.items():
        if type(raw[key]) is not int or not low <= raw[key] <= high:
            raise ConfigError(f"invalid demo {key}")
        values[re.sub(r"[A-Z]", lambda m: "_" + m.group(0).lower(), key)] = raw[key]
    return DemoPolicy(dealer_account_id=raw["dealerAccountId"], **values)


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
    if type(data) is not dict or data.get("version") not in (1, 2):
        raise ConfigError("service configuration schema")
    if data["version"] == 2:
        expected |= {"rpcHost", "chainManagement", "isolation", "demo"}
    if set(data) != expected:
        raise ConfigError("service configuration schema")
    if data["root"] != str(root_path):
        raise ConfigError("service configuration belongs to another root")
    if data["approvalMode"] not in APPROVAL_MODES:
        raise ConfigError("unsupported approval mode")
    if data["approvalMode"] != "HUMAN_APPROVED" and str(root_path) in PROTECTED_ROOTS:
        raise ConfigError("scripted or custodial approval is forbidden on a protected root")
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
    extra = {}
    if data["version"] == 2:
        if not isinstance(data["rpcHost"], str) or not re.fullmatch(
                r"127\.0\.0\.1|[a-z][a-z0-9-]{0,62}", data["rpcHost"]):
            raise ConfigError("RPC host must be loopback or a private service name")
        if data["chainManagement"] not in ("docker-local", "external"):
            raise ConfigError("unsupported chain management")
        if data["isolation"] not in ISOLATION_MODES:
            raise ConfigError("unsupported wallet isolation")
        demo = None
        if data["approvalMode"] == "CUSTODIAL_DEMO_POLICY":
            demo = _demo_policy(data["demo"])
        elif data["demo"] is not None:
            raise ConfigError("demo policy requires custodial demo mode")
        extra = {"rpc_host": data["rpcHost"], "chain_management": data["chainManagement"],
                 "isolation": data["isolation"], "demo": demo}
    elif data["approvalMode"] == "CUSTODIAL_DEMO_POLICY":
        raise ConfigError("custodial demo mode requires configuration version 2")
    return ExchangeConfig(root=root_path, container=data["container"], rpc_port=data["rpcPort"],
                          approval_mode=data["approvalMode"], tx_tool_dir=paths[0],
                          params_data_home=paths[1], node_bin=paths[2],
                          quote_validity_seconds=data["quoteValiditySeconds"],
                          approval_wait_seconds=data["approvalWaitSeconds"],
                          companion_stale_seconds=data["companionStaleSeconds"],
                          stabilization_blocks=data["stabilizationBlocks"], **extra)


def init(root: str, container: str, rpc_port: int, approval_mode: str,
         params_data_home: str, tx_tool_dir: str = "/private/tmp/zwa4c1-tx-tool",
         node_bin: str | None = None, *, rpc_host: str = "127.0.0.1",
         chain_management: str = "docker-local", isolation: str = "macos-sandbox",
         dealer_account_id: str = "dealer") -> dict:
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
    if approval_mode == "CUSTODIAL_DEMO_POLICY" or rpc_host != "127.0.0.1" or \
            chain_management != "docker-local" or isolation != "macos-sandbox":
        demo = None
        if approval_mode == "CUSTODIAL_DEMO_POLICY":
            demo = {"dealerAccountId": dealer_account_id,
                    **{key: default for key, (_, _, default) in DEMO_LIMITS.items()}}
            # A judge reviewing a quote holds the single dealer wallet; keep
            # that window short so one idle visitor cannot block the queue.
            data["quoteValiditySeconds"] = 90
            data["approvalWaitSeconds"] = 60
        data.update({"version": 2, "rpcHost": rpc_host, "chainManagement": chain_management,
                     "isolation": isolation, "demo": demo})
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
    create.add_argument("--rpc-host", default="127.0.0.1")
    create.add_argument("--chain-management", choices=("docker-local", "external"), default="docker-local")
    create.add_argument("--isolation", choices=ISOLATION_MODES, default="macos-sandbox")
    create.add_argument("--dealer-account", default="dealer")
    show = sub.add_parser("show")
    show.add_argument("--root", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "init":
        print(json.dumps(init(args.root, args.container, args.port, args.approval_mode,
                              args.params_data_home, args.tx_tool_dir, args.node_bin,
                              rpc_host=args.rpc_host, chain_management=args.chain_management,
                              isolation=args.isolation, dealer_account_id=args.dealer_account),
                         indent=2))
    else:
        config = load(args.root)
        print(json.dumps({"root": str(config.root), "container": config.container,
                          "rpcPort": config.rpc_port, "approvalMode": config.approval_mode,
                          "protected": config.protected, "isolation": config.isolation,
                          "demo": config.demo.__dict__ if config.demo else None}, indent=2))


if __name__ == "__main__":
    main()
