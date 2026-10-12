"""Local ZWA wallet companion for one buyer or seller wallet.

Run it yourself, in your own terminal, next to that wallet:

    python3 -m exchange.wallet_companion --root /private/tmp/zwa4e-... --role buyer

Pairing (operator-confirmed, local):
  1. The companion asks its own wallet for its receiving address and shows a
     one-time code in this terminal.
  2. You type that code into the exchange while signed in to your account.
  3. The companion shows which account claimed the code; you type PAIR here.
The pairing binds that portal session to this wallet's receiving address for
a few hours. It is not cryptographic wallet login: it proves that the person
using the browser can also see and type into this terminal.

Approval:
  When an order for this pairing reaches its approval turn, the companion
  starts the wallet's own `authorize-interactive` signer in this terminal.
  The signer reviews the exact terms against its own keys and asks you to
  type APPROVE <digest> on this terminal. Anything else rejects. The
  companion never types, pipes, or remembers that answer, and has no network
  listener: it only reads the private ledger and writes its own evidence.

SCRIPTED_TEST_MODE uses the signer's automated test flag instead. It is
refused for protected roots and is never human approval.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

from checkout.quote import from_terms
from .config import ExchangeConfig, load as load_config
from .order_ledger import OrderError, OrderLedger
from .order_paths import order_dir
from .tools import Tools, phase_lines, write_lines, write_new_json


def _say(text: str = "") -> None:
    print(text, flush=True)


def _ask(prompt: str) -> str:
    """Read one line from the controlling terminal, never from a pipe."""
    with open("/dev/tty", "r+") as tty:
        tty.write(prompt)
        tty.flush()
        return tty.readline().rstrip("\r\n")


def _new_code() -> str:
    return base64.b32encode(secrets.token_bytes(10)).decode()[:10]


class Companion:
    def __init__(self, config: ExchangeConfig, role: str):
        if role not in ("buyer", "seller"):
            raise SystemExit("role must be buyer or seller")
        self.config = config
        self.role = role
        self.mode = config.approval_mode
        self.ledger = OrderLedger(config.ledger_path)
        self.tools = Tools(config)
        self.pid = os.getpid()
        self.pairing: dict | None = None
        self._stop = threading.Event()
        if self.mode == "HUMAN_APPROVED" and not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise SystemExit("the human wallet companion must run in an interactive terminal")
        if self.mode == "SCRIPTED_TEST_MODE" and config.protected:
            raise SystemExit("scripted approval is forbidden on a protected root")

    def pair(self, ttl: int = 300) -> None:
        address = self.tools.wallet_address(self.role)
        code = _new_code()
        request = self.ledger.create_pairing_request(role=self.role, wallet_address=address, code=code,
                                                     companion_pid=self.pid, ttl_seconds=ttl)
        _say(f"ZWA {self.role} wallet companion · {self.mode.replace('_', ' ')}")
        _say(f"Wallet receiving address {address[:12]}…{address[-8:]}")
        _say("")
        _say(f"  Pairing code:  {code[:5]}-{code[5:]}")
        _say("")
        _say(f"Sign in to the exchange as the {self.role}, open Wallet, and enter this code "
             f"within {ttl // 60} minutes.")
        if self.mode == "SCRIPTED_TEST_MODE":
            # Disposable automation reads the code from a private file only.
            write_new_json(self.config.exchange / f"pairing-code-{self.role}-{self.pid}.json",
                           {"code": code, "mode": self.mode})
        while True:
            row = self.ledger.pairing_request(request["id"])
            if row["expires_at"] <= time.time():
                raise SystemExit("pairing code expired; start the companion again")
            if row["state"] == "CLAIMED":
                break
            time.sleep(1)
        _say(f"Portal account '{row['account_id']}' ({row['role']}) entered this code.")
        if self.mode == "HUMAN_APPROVED":
            answer = _ask(f"Type PAIR to let account '{row['account_id']}' request trades for this "
                          "wallet, or anything else to refuse: ")
            if answer != "PAIR":
                self.ledger.reject_pairing(request["id"], companion_pid=self.pid)
                raise SystemExit("pairing refused; nothing was bound")
        self.pairing = self.ledger.confirm_pairing(request["id"], companion_pid=self.pid)
        _say(f"Paired until {time.strftime('%H:%M', time.localtime(self.pairing['expires_at']))}. "
             "Keep this terminal open; approval requests will appear here.")

    def _heartbeat(self) -> None:
        while not self._stop.is_set():
            try:
                self.ledger.heartbeat(self.pairing["id"], pid=self.pid, mode=self.mode)
            except OrderError:
                pass
            self._stop.wait(2)

    def _pending(self) -> dict | None:
        turn = "TRADE_AUTHORIZED" if self.role == "buyer" else "BUYER_APPROVED"
        for order in self.ledger.orders_in((turn,)):
            quote = self.ledger.order_quote(order["id"])
            execution = self.ledger.execution(order["id"])
            if (not quote or quote[f"{self.role}_pairing_id"] != self.pairing["id"]
                    or not execution or execution["finished_at"] is not None
                    or execution["approval_mode"] != self.mode):
                continue
            work = order_dir(self.config.exchange, order["id"])
            if (work / f"{self.role}-approval.json").exists():
                continue
            if self.config.socket_path(order["id"]).is_socket():
                return order
        return None

    def approve(self, order: dict) -> None:
        order_id = order["id"]
        work = order_dir(self.config.exchange, order_id)
        terms_path = work / "quote" / "terms.json"
        terms = json.loads(terms_path.read_text())
        quote = from_terms(terms)
        own = terms["buyer_receive_address" if self.role == "buyer" else "seller_proceeds_address"]
        if quote["quoteDigest"] != order["quote_digest"] or own != self.pairing["wallet_address"]:
            raise SystemExit("approval request does not match this wallet's paired quote; refusing")
        quote_row = self.ledger.order_quote(order_id)
        _say("")
        _say("=" * 72)
        verb = "buy" if self.role == "buyer" else "sell"
        _say(f"Order {order_id}: {verb} {quote['quantity']} unit(s) of {order['instrument_id']} "
             f"for {quote['paymentAmount']:,} zatoshis.")
        _say(f"Price {quote_row['price_zat_per_unit']:,} zatoshis/unit · "
             f"{quote_row['classification']} (seller-set test price, not an oracle or market price).")
        _say("Groth16 issuance/eligibility authorization: verified by the exchange service and "
             "re-verified by the coordinator.")
        _say(f"Exact quote digest: {quote['quoteDigest']}")
        _say(f"The coordinator waits at most {self.config.approval_wait_seconds} seconds for both wallets.")
        _say("=" * 72)
        socket = str(self.config.socket_path(order_id))
        if self.mode == "HUMAN_APPROVED":
            args = ("authorize-interactive", "--terms", str(terms_path), "--socket", socket)
            command_label = "authorize-interactive"
        else:
            args = ("authorize-live", "--terms", str(terms_path), "--approve", quote["quoteDigest"],
                    "--socket", socket, "--scripted-test-mode")
            command_label = "authorize-live --scripted-test-mode"
        command, env, home = self.tools.signer_command(self.role, *args)
        # The signer opens /dev/tty itself for the human decision. Its stdout
        # is relayed here line by line and only structured lines are kept.
        process = subprocess.Popen(command, cwd=home, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        captured = []
        for line in process.stdout:
            # Show the signer's structured review and result, not its tracing.
            if line.startswith(("PHASE35F_REVIEW ", "PHASE35F_AUTHORIZED ", "PHASE35F_SIGNER_REJECTED ")):
                _say(line.rstrip("\n"))
            captured.append(line)
        status = process.wait()
        lines = phase_lines("".join(captured))
        write_lines(work / f"{self.role}-signer.log", lines)
        authorized = status == 0 and any(line.startswith("PHASE35F_AUTHORIZED ") for line in lines)
        write_new_json(work / f"{self.role}-approval.json", {
            "version": 1, "orderId": order_id, "role": self.role,
            "quoteDigest": quote["quoteDigest"], "pairingId": self.pairing["id"],
            "mode": self.mode, "signerCommand": command_label,
            "controllingTerminal": self.mode == "HUMAN_APPROVED",
            "result": "AUTHORIZED" if authorized else "REJECTED",
            "signerExit": status, "finishedAt": int(time.time())})
        _say("Signed group sent to the coordinator." if authorized
             else "Not approved. No signed group was released by this wallet.")

    def run(self) -> None:
        self.pair()
        threading.Thread(target=self._heartbeat, daemon=True).start()
        try:
            while self.ledger.pairing_usable(self.pairing["id"]):
                order = self._pending()
                if order:
                    self.approve(order)
                time.sleep(1)
            _say("Pairing expired or was revoked. Start the companion again to pair.")
        except KeyboardInterrupt:
            _say("Companion stopped. Pending approvals will time out without a signature.")
        finally:
            self._stop.set()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", required=True)
    parser.add_argument("--role", choices=("buyer", "seller"), required=True)
    args = parser.parse_args()
    os.umask(0o077)
    Companion(load_config(args.root), args.role).run()


if __name__ == "__main__":
    main()
