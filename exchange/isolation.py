"""Linux Landlock profiles for wallet, dealer and coordinator tool invocations.

Each invocation may read the system, the pinned tool and the proving
parameters, and may write only its own home. Wallets may also read the order
evidence directory (exact terms) and connect to the coordinator's approval
socket; the coordinator may also write order evidence and create that socket.
No profile can open another home, so no process reads a seed it does not own.
"""

from __future__ import annotations

from pathlib import Path
import sys

from .config import ExchangeConfig

REPOSITORY = Path(__file__).resolve().parents[1]
SYSTEM = ["/usr", "/lib", "/lib64", "/bin", "/etc"]


def profile_paths(config: ExchangeConfig, profile: str, home: Path) -> tuple[list[str], list[str], list[str]]:
    """(read-only, read-write, socket) grants for one profile."""
    exchange = config.exchange
    read_only = SYSTEM + [str(config.tx_tool_dir), str(config.params_data_home)]
    read_write = [str(home), "/tmp", "/dev/null", "/dev/urandom"]
    sockets: list[str] = []
    if profile == "coordinator":
        # Runs the SHA-pinned Groth16 verifier with Node before assembling.
        read_only += [str(REPOSITORY / "checkout"), str(REPOSITORY.parent / "ZWA-Protocol"),
                      str(Path(config.node_bin).parent)]
        read_only.append(str(exchange / "instruments"))  # seller issuance to mine at setup
        read_write.append(str(exchange / "orders"))
        sockets.append(str(exchange / "s"))
    elif profile in ("buyer", "seller") or profile.startswith("demo-"):
        read_only.append(str(exchange / "orders"))
    else:
        raise ValueError("unknown isolation profile")
    return read_only, read_write, sockets


def linux_command(config: ExchangeConfig, profile: str, home: Path, argv: list[str]) -> list[str]:
    read_only, read_write, sockets = profile_paths(config, profile, home)
    # Isolated mode, by path: the launcher imports only the standard library.
    command = [sys.executable, "-I", str(REPOSITORY / "exchange" / "landlock_exec.py")]
    for flag, paths in (("--ro", read_only), ("--rw", read_write), ("--sock", sockets)):
        for path in paths:
            command += [flag, path]
    return command + ["--", *argv]
