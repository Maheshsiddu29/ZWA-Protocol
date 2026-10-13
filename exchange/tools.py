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
import shutil
import sqlite3
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
NATIVE_LINE = re.compile(r"PHASE35F_BALANCES role=(Buyer|Seller) scanned_height=(\d+) native_zat=(\d+)\Z")
FUNDED_LINE = re.compile(r"PHASE35F_FUNDED recipient=([0-9a-f]{86}) native_zat=(\d+) "
                         r"txid=([0-9a-f]{64}) height=(\d+)\Z")
DEMO_WALLETS = "demo-wallets"
WALLET_ID = re.compile(r"[0-9a-f]{32}\Z")
# Every home that holds a seed. A wallet's isolation denies all of them and
# then re-allows only its own home (macOS sandbox rules: last match wins).
SECRET_HOMES = ("buyer", "seller", "coordinator", DEMO_WALLETS)


class ToolError(RuntimeError):
    pass


def phase_lines(text: str) -> list[str]:
    """Keep only the tool's structured public lines; drop everything else."""
    return [line for line in text.splitlines() if re.match(r"PHASE35[FG]_[A-Z_]+ ", line)]


class Tools:
    def __init__(self, config: ExchangeConfig):
        self.config = config

    def _wallet_env(self, home: Path) -> dict:
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home),
               "XDG_DATA_HOME": str(self.config.params_data_home),
               "ZCASH_NODE_PORT": str(self.config.rpc_port),
               "DATABASE_URL": str(home / "wallet.sqlite")}
        if self.config.rpc_host != "127.0.0.1":
            env["ZCASH_NODE_ADDRESS"] = self.config.rpc_host
        return env

    def _sandboxed(self, profile: str, home: Path, args: list[str]) -> list[str]:
        if self.config.isolation == "linux-landlock":
            from .isolation import linux_command
            return linux_command(self.config, profile, home, [str(self.config.tx_tool_bin), *args])
        sandbox = self.config.root / "profiles" / f"{profile}.sb"
        if sandbox.is_symlink() or not sandbox.is_file():
            raise ToolError(f"missing wallet isolation profile: {profile}")
        return ["sandbox-exec", "-f", str(sandbox), str(self.config.tx_tool_bin), *args]

    def wallet_home(self, role: str, wallet: str | None = None) -> tuple[Path, str]:
        """Home and isolation profile of a fixed role wallet or one demo wallet."""
        if role not in ("buyer", "seller"):
            raise ToolError("invalid wallet role")
        if wallet is None:
            return self.config.root / role, role
        if role != "buyer" or not WALLET_ID.fullmatch(wallet):
            raise ToolError("invalid demo wallet")
        return self.config.root / DEMO_WALLETS / wallet, f"demo-{wallet}"

    def create_demo_home(self, wallet: str) -> Path:
        """Create one demo wallet's private home and its isolation profile."""
        home, profile = self.wallet_home("buyer", wallet)
        parent = home.parent
        parent.mkdir(mode=0o700, exist_ok=True)
        if parent.is_symlink() or home.is_symlink():
            raise ToolError("demo wallet home cannot be a symlink")
        home.mkdir(mode=0o700, exist_ok=True)
        database = home / "wallet.sqlite"
        if not database.exists():
            # Same schema the operator scripts apply with sqlite3 from the
            # pinned tool's own migration.
            schema = (self.config.tx_tool_dir / "migrations/wallet_init/up.sql").read_text()
            os.close(os.open(database, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            with sqlite3.connect(database) as db:
                db.executescript(schema)
        if self.config.isolation == "linux-landlock":
            return home
        profiles = self.config.root / "profiles"
        path = profiles / f"{profile}.sb"
        rules = ["(version 1)", "(allow default)"]
        rules += [f'(deny file-read* file-write* (subpath "{self.config.root / name}"))'
                  for name in SECRET_HOMES]
        rules.append(f'(allow file-read* file-write* (subpath "{home}"))')
        text = "\n".join(rules) + "\n"
        if path.exists():
            if path.is_symlink() or path.read_text() != text:
                raise ToolError("existing demo isolation profile differs")
        else:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as out:
                out.write(text)
        return home

    def remove_demo_home(self, wallet: str) -> None:
        """Delete a retired disposable wallet's seed, database and profile."""
        home, profile = self.wallet_home("buyer", wallet)
        if home.is_symlink():
            raise ToolError("demo wallet home cannot be a symlink")
        if home.exists():
            shutil.rmtree(home)
        path = self.config.root / "profiles" / f"{profile}.sb"
        if path.is_file() and not path.is_symlink():
            path.unlink()

    def check_isolation(self) -> None:
        """Refuse to run if a fixed profile could read demo wallet seeds."""
        if self.config.isolation != "macos-sandbox":
            return
        demo = str(self.config.root / DEMO_WALLETS)
        for profile in ("seller", "coordinator"):
            path = self.config.root / "profiles" / f"{profile}.sb"
            if path.is_symlink() or not path.is_file() or (
                    f'(deny file-read* file-write* (subpath "{demo}"))' not in path.read_text()):
                raise ToolError(f"{profile} isolation profile does not exclude demo wallets")

    def signer_command(self, role: str, *args: str, wallet: str | None = None) -> tuple[list[str], dict, Path]:
        home, profile = self.wallet_home(role, wallet)
        command = self._sandboxed(profile, home, ["wallet-signer", "--role", role,
                                                  "--home", str(home), *args])
        return command, self._wallet_env(home), home

    def signer(self, role: str, *args: str, wallet: str | None = None, timeout: int = 900) -> list[str]:
        allowed = ("address", "balances", "review") + (("init",) if wallet else ())
        if not args or args[0] not in allowed:
            raise ToolError("operator service may not request wallet signing")
        command, env, home = self.signer_command(role, *args, wallet=wallet)
        result = subprocess.run(command, cwd=home, env=env, capture_output=True,
                                text=True, timeout=timeout)
        lines = phase_lines(result.stdout + "\n" + result.stderr)
        if result.returncode or any(line.startswith("PHASE35F_SIGNER_REJECTED") for line in lines):
            raise ToolError(f"{role} wallet {args[0] if args else ''} failed: "
                            + "; ".join(lines[-2:])[:400])
        return lines

    def wallet_address(self, role: str, wallet: str | None = None) -> str:
        for line in self.signer(role, "address", wallet=wallet):
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
        if not args or args[0] not in ("prepare-sale", "mine-empty", "fund-address"):
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
        return _rpc(self.config.rpc_port, method, params or [], host=self.config.rpc_host)

    def chain(self) -> dict:
        return chain_status(self.config.root, self.config.container, self.config.rpc_port,
                            host=self.config.rpc_host, managed=self.config.chain_management)

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
