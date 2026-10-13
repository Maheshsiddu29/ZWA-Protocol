"""Bootstrap a fresh judge-demo root inside the container deployment.

    python3 -m exchange.demo_bootstrap --root /var/lib/zwa/zwa4e-demo \\
        --rpc-host zebra --rpc-port 18232 --params-data-home /opt/zwa/params \\
        --tx-tool-dir /opt/zwa/tx-tool [--units 1000 --capacity 900]

The Linux counterpart of checkout/operator/judge-demo-setup.sh. It adopts a
fresh, separately managed Zebra regtest chain at height 0, creates the demo
dealer and fee-sponsor wallets (each confined by Landlock to its own home),
issues three experimental instruments to the dealer, makes the issuance
restart-durable, writes the CUSTODIAL_DEMO_POLICY configuration and lists the
scanned inventory. It refuses to touch an existing root, a protected root, or
a chain that already has blocks.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

from . import config as config_module, persistent_chain
from .config import PROTECTED_ROOTS, load as load_config
from .demo_setup import bootstrap as list_inventory
from .operator_manifest import build as build_manifest
from .registry import load_catalog
from .tools import ToolError, Tools, phase_lines

DEFAULT_PRICES = {"zwa-gold-reference": 1000, "zwa-treasury-style": 1250, "zwa-credit-participation": 850}


def _run(tools: Tools, command: tuple[list[str], dict, Path]) -> list[str]:
    argv, env, home = command
    result = subprocess.run(argv, cwd=home, env=env, capture_output=True, text=True, timeout=1800)
    lines = phase_lines(result.stdout + "\n" + result.stderr)
    if result.returncode or any(line.startswith(("PHASE35F_SIGNER_REJECTED", "PHASE35F_COORDINATOR_REJECTED"))
                                for line in lines):
        raise ToolError(" ; ".join(lines[-2:])[:400] or f"command failed with {result.returncode}")
    return lines


def bootstrap(root: str, *, rpc_host: str, rpc_port: int, params_data_home: str, tx_tool_dir: str,
              node_bin: str, units: int, capacity: int, prices: dict[str, int]) -> dict:
    root_path = Path(root)
    if str(root_path) in PROTECTED_ROOTS:
        raise SystemExit("refusing to bootstrap a protected root")
    if not config_module.ROOT_PATTERN.fullmatch(str(root_path)):
        raise SystemExit("root must be /var/lib/zwa/zwa4e-* or /private/tmp/zwa4e-*")
    if (root_path / "exchange" / config_module.CONFIG_NAME).exists() or (root_path / "seller").exists():
        raise SystemExit("root is already initialized; refusing to reuse it")
    os.umask(0o077)
    root_path.mkdir(mode=0o700, exist_ok=True)
    for name in ("exchange", "seller", "coordinator", "demo-wallets", "profiles", "logs"):
        (root_path / name).mkdir(mode=0o700, exist_ok=True)
    (root_path / "exchange" / "instruments").mkdir(mode=0o700, exist_ok=True)
    schema = (Path(tx_tool_dir) / "migrations/wallet_init/up.sql").read_text()
    for role in ("seller", "coordinator"):
        with sqlite3.connect(root_path / role / "wallet.sqlite") as db:
            db.executescript(schema)
        os.chmod(root_path / role / "wallet.sqlite", 0o600)

    persistent_chain.adopt_external(root_path, rpc_port, rpc_host)
    config_module.init(str(root_path), "zebra", rpc_port, "CUSTODIAL_DEMO_POLICY", params_data_home,
                       tx_tool_dir, node_bin, rpc_host=rpc_host, chain_management="external",
                       isolation="linux-landlock")
    config = load_config(root_path)
    tools = Tools(config)
    tools.check_isolation()
    for role in ("seller", "coordinator"):
        tools.link_params(root_path / role)
    seller = lambda *args: _run(tools, tools.signer_command("seller", *args))  # noqa: E731
    coord = lambda *args: _run(tools, tools.coordinator_command(*args))  # noqa: E731

    seller("init")
    coord("init")
    coord("mine-empty", "--count", "100")
    instruments = root_path / "exchange" / "instruments"
    for instrument in load_catalog():
        staged = root_path / "seller" / f"{instrument}-issuance.json"
        seller("issue", "--description", f"ZWA-EXPERIMENTAL-{instrument}", "--amount", str(units),
               "--out", str(staged))
        target = instruments / f"{instrument}-issuance.json"
        shutil.copyfile(staged, target)
        staged.unlink()
        coord("mine-issuance", "--issuance", str(target))
        issuance = json.loads(target.read_text())
        transaction = tools.rpc("getrawtransaction", [issuance["txid"], 1])
        (instruments / f"{instrument}-chain.json").write_text(json.dumps({"result": transaction}))
        lines = [line for line in tools.signer("seller", "balances", "--asset", issuance["asset_base"])
                 if line.startswith("PHASE35F_BALANCES ")]
        (instruments / f"{instrument}-inventory.log").write_text("\n".join(lines) + "\n")
    registry = build_manifest(instruments, units)
    (root_path / "exchange" / "instrument-registry.json").write_text(json.dumps(registry, indent=2) + "\n")
    # Pinned Zebra drops its newest ~99 blocks on restart; make issuance durable.
    coord("mine-empty", "--count", "110")
    persistent_chain.checkpoint(root_path, rpc_port, rpc_host)
    return list_inventory(str(root_path), capacity, prices)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", required=True)
    parser.add_argument("--rpc-host", required=True)
    parser.add_argument("--rpc-port", type=int, default=18232)
    parser.add_argument("--params-data-home", required=True)
    parser.add_argument("--tx-tool-dir", required=True)
    parser.add_argument("--node-bin", default=shutil.which("node") or "/usr/local/bin/node")
    parser.add_argument("--units", type=int, default=1000)
    parser.add_argument("--capacity", type=int, default=900)
    args = parser.parse_args()
    if not 0 < args.capacity <= args.units <= 1_000_000:
        parser.error("invalid units or capacity")
    print(json.dumps(bootstrap(args.root, rpc_host=args.rpc_host, rpc_port=args.rpc_port,
                               params_data_home=args.params_data_home, tx_tool_dir=args.tx_tool_dir,
                               node_bin=args.node_bin, units=args.units, capacity=args.capacity,
                               prices=DEFAULT_PRICES), indent=2))


if __name__ == "__main__":
    main()
