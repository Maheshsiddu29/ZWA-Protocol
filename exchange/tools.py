"""Narrow wrappers around the pinned transaction tool, verifier, and Zebra RPC.

Every invocation has a fixed command shape. Wallet processes run under their
own sandbox profile with a scrubbed environment, exactly like the operator
scripts, so one role cannot read another role's seed. Nothing here accepts a
command, path, or RPC method from a browser.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

from .config import ExchangeConfig
from .persistent_chain import CONFIG as ZEBRA_CONFIG, _rpc, status as chain_status

MINER_ADDRESS = re.search(r"miner_address = '([0-9A-Za-z]+)'", ZEBRA_CONFIG).group(1)
COINBASE_ZAT = 625_000_000
COINBASE_MATURITY = 100
RPC_METHODS = frozenset({"getblockcount", "getblockhash", "getblock", "getrawtransaction",
                         "getaddressutxos"})
AUTH_SCRIPT = Path(__file__).resolve().parents[1] / "checkout/authorization/native_zec_v2.js"
ADDRESS_LINE = re.compile(r"PHASE35F_SIGNER_ADDRESS role=(Buyer|Seller) address=([0-9a-f]{86})\Z")


class ToolError(RuntimeError):
    pass


def phase_lines(text: str) -> list[str]:
    """Keep only the tool's structured public lines; drop everything else."""
    return [line for line in text.splitlines() if re.match(r"PHASE35[FG]_[A-Z_]+ ", line)]


class Tools:
    def __init__(self, config: ExchangeConfig):
        self.config = config

    def _wallet_env(self, home: Path) -> dict:
        return {"PATH": "/usr/bin:/bin", "HOME": str(home),
                "XDG_DATA_HOME": str(self.config.params_data_home),
                "ZCASH_NODE_PORT": str(self.config.rpc_port),
                "DATABASE_URL": str(home / "wallet.sqlite")}

    def _sandboxed(self, profile: str, home: Path, args: list[str]) -> list[str]:
        sandbox = self.config.root / "profiles" / f"{profile}.sb"
        if sandbox.is_symlink() or not sandbox.is_file():
            raise ToolError(f"missing wallet isolation profile: {profile}")
        return ["sandbox-exec", "-f", str(sandbox), str(self.config.tx_tool_bin), *args]

    def signer_command(self, role: str, *args: str) -> tuple[list[str], dict, Path]:
        if role not in ("buyer", "seller"):
            raise ToolError("invalid wallet role")
        home = self.config.root / role
        command = self._sandboxed(role, home, ["wallet-signer", "--role", role,
                                               "--home", str(home), *args])
        return command, self._wallet_env(home), home

    def signer(self, role: str, *args: str, timeout: int = 900) -> list[str]:
        if args and args[0] not in ("address", "balances", "review"):
            raise ToolError("operator service may not request wallet signing")
        command, env, home = self.signer_command(role, *args)
        result = subprocess.run(command, cwd=home, env=env, capture_output=True,
                                text=True, timeout=timeout)
        lines = phase_lines(result.stdout + "\n" + result.stderr)
        if result.returncode or any(line.startswith("PHASE35F_SIGNER_REJECTED") for line in lines):
            raise ToolError(f"{role} wallet {args[0] if args else ''} failed: "
                            + "; ".join(lines[-2:])[:400])
        return lines

    def wallet_address(self, role: str) -> str:
        for line in self.signer(role, "address"):
            match = ADDRESS_LINE.fullmatch(line)
            if match and match.group(1) == role.title():
                return match.group(2)
        raise ToolError("wallet did not report its receiving address")

    def coordinator_command(self, *args: str) -> tuple[list[str], dict, Path]:
        home = self.config.root / "coordinator"
        env = self._wallet_env(home)
        return self._sandboxed("coordinator", home, ["purchase-coordinator", "--home",
                                                     str(home), *args]), env, home

    def coordinator(self, *args: str, timeout: int = 1800) -> list[str]:
        if not args or args[0] not in ("prepare-sale", "mine-empty"):
            raise ToolError("unsupported coordinator action")
        command, env, home = self.coordinator_command(*args)
        result = subprocess.run(command, cwd=home, env=env, capture_output=True,
                                text=True, timeout=timeout)
        lines = phase_lines(result.stdout + "\n" + result.stderr)
        if result.returncode:
            raise ToolError("coordinator " + args[0] + " failed: " + "; ".join(lines[-2:])[:400])
        return lines

    def spawn_execute_live(self, args: list[str], log: Path) -> subprocess.Popen:
        command, env, home = self.coordinator_command("execute-live", *args)
        descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as out:
            # A separate session survives a service restart and can be
            # terminated as a group without touching the service itself.
            return subprocess.Popen(command, cwd=home, env=env, stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=True)

    def authorization(self, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
        if not args or args[0] not in ("generate", "verify"):
            raise ToolError("unsupported authorization action")
        return subprocess.run([str(self.config.node_bin), str(AUTH_SCRIPT), *args],
                              capture_output=True, text=True, timeout=timeout,
                              env={"PATH": "/usr/bin:/bin", "HOME": str(self.config.root)})

    def rpc(self, method: str, params: list | None = None) -> object:
        if method not in RPC_METHODS:
            raise ToolError("unsupported RPC method")
        return _rpc(self.config.rpc_port, method, params or [])

    def chain(self) -> dict:
        return chain_status(self.config.root, self.config.container, self.config.rpc_port)

    def mature_sponsor_coinbases(self, tip: int) -> list[tuple[int, str]]:
        """Unspent, mature, unmodified miner coinbase outputs, oldest first."""
        utxos = self.rpc("getaddressutxos", [{"addresses": [MINER_ADDRESS]}])
        if not isinstance(utxos, list):
            raise ToolError("unexpected UTXO response")
        found = []
        for item in utxos:
            if (type(item) is dict and item.get("satoshis") == COINBASE_ZAT
                    and item.get("outputIndex") == 0 and type(item.get("height")) is int
                    and 1 <= item["height"] and item["height"] + COINBASE_MATURITY <= tip + 1
                    and isinstance(item.get("txid"), str)):
                found.append((item["height"], item["txid"]))
        return sorted(found)

    def coinbase_txid(self, height: int) -> str:
        block = self.rpc("getblock", [str(height), 1])
        tx = block.get("tx") if isinstance(block, dict) else None
        if not isinstance(tx, list) or not tx:
            raise ToolError("block has no coinbase")
        return tx[0]


def write_lines(path: Path, lines: list[str]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as out:
        out.write("\n".join(lines) + ("\n" if lines else ""))


def write_new_json(path: Path, data: dict) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as out:
        json.dump(data, out, indent=2, sort_keys=True)
        out.write("\n")
