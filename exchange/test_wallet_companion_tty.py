"""Controlling-terminal regressions for the wallet companion prompts.

Each test forks a child whose controlling terminal is a fresh pseudo-terminal,
so /dev/tty behaves as it does in Terminal.app. These automated keystrokes
verify parsing and fail-closed behaviour only; they are never human approval
evidence. Every ledger is a disposable /private/tmp/zwa4e-test-* root.
"""

from __future__ import annotations

import os
from pathlib import Path
import pty
import re
import secrets
import select
import shutil
import subprocess
import sys
import time
import unittest

from exchange.order_ledger import OrderLedger

PROJECT = Path(__file__).resolve().parents[1]
SESSION = "5" * 64


class PtyChild:
    """Run Python code in a child whose controlling terminal is a new pty."""

    def __init__(self, code: str, *args: str):
        self.pid, self.master = pty.fork()
        if self.pid == 0:
            os.environ["PYTHONPATH"] = str(PROJECT)
            os.execv(sys.executable, [sys.executable, "-c", code, *args])
        self.output = b""

    def read_until(self, pattern: bytes, timeout: float = 20) -> bytes:
        end = time.time() + timeout
        while pattern not in self.output and time.time() < end:
            ready, _, _ = select.select([self.master], [], [], 0.1)
            if ready:
                try:
                    chunk = os.read(self.master, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                self.output += chunk
        if pattern not in self.output:
            raise AssertionError(f"timed out waiting for {pattern!r}: {self.output[-400:]!r}")
        return self.output

    def send(self, data: bytes) -> None:
        os.write(self.master, data)

    def finish(self, timeout: float = 20) -> int:
        end = time.time() + timeout
        while time.time() < end:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                try:
                    while True:
                        ready, _, _ = select.select([self.master], [], [], 0.05)
                        if not ready:
                            break
                        chunk = os.read(self.master, 4096)
                        if not chunk:
                            break
                        self.output += chunk
                except OSError:
                    pass
                os.close(self.master)
                return os.waitstatus_to_exitcode(status)
            ready, _, _ = select.select([self.master], [], [], 0.1)
            if ready:
                try:
                    self.output += os.read(self.master, 4096)
                except OSError:
                    pass
        os.kill(self.pid, 9)
        raise AssertionError("child did not exit")


ASK = ("from exchange.wallet_companion import _ask\n"
       "answer = _ask('Type PAIR: ')\n"
       "print('ANSWER=' + repr(answer), flush=True)\n")


class AskTests(unittest.TestCase):
    def ask(self, keystrokes: bytes) -> str:
        child = PtyChild(ASK)
        child.read_until(b"Type PAIR: ")
        child.send(keystrokes)
        child.read_until(b"ANSWER=")
        self.assertEqual(child.finish(), 0)
        return re.search(rb"ANSWER='([^']*)'", child.output).group(1).decode()

    def test_buffered_read_write_open_is_the_reproduced_root_cause(self):
        child = PtyChild("import io\n"
                         "try:\n"
                         "    open('/dev/tty', 'r+')\n"
                         "    print('OPENED', flush=True)\n"
                         "except io.UnsupportedOperation as exc:\n"
                         "    print('UNSUPPORTED', exc, flush=True)\n")
        child.finish()
        self.assertIn(b"UNSUPPORTED", child.output)

    def test_returns_the_exact_line_typed_on_the_terminal(self):
        self.assertEqual(self.ask(b"PAIR\n"), "PAIR")
        self.assertEqual(self.ask(b"pair\n"), "pair")
        self.assertEqual(self.ask(b"PAIR now\n"), "PAIR now")
        digest = "ab" * 32
        self.assertEqual(self.ask(f"APPROVE {digest}\n".encode()), f"APPROVE {digest}")

    def test_end_of_input_is_an_empty_rejection(self):
        self.assertEqual(self.ask(b"\x04"), "")
        self.assertEqual(self.ask(b"\n"), "")

    def test_without_a_controlling_terminal_there_is_no_answer(self):
        # A new session has no controlling terminal; piped stdin is ignored.
        result = subprocess.run([sys.executable, "-c",
                                 "from exchange.wallet_companion import _ask\n"
                                 "try:\n    _ask('x')\nexcept OSError:\n    print('NO_TTY')\n"],
                                input="PAIR\n", capture_output=True, text=True, timeout=20,
                                env={**os.environ, "PYTHONPATH": str(PROJECT)}, start_new_session=True)
        self.assertEqual(result.stdout.strip(), "NO_TTY")


PAIR = """
import sys
from pathlib import Path
from exchange.config import ExchangeConfig
from exchange.wallet_companion import Companion
config = ExchangeConfig(root=Path(sys.argv[1]), container="zwa-test", rpc_port=18999,
                        approval_mode="HUMAN_APPROVED", tx_tool_dir=Path("/nonexistent"),
                        params_data_home=Path("/nonexistent"), node_bin=Path("/usr/bin/false"))
companion = Companion(config, "buyer")
companion.tools.wallet_address = lambda role: "b" * 86
try:
    companion.pair(ttl=120)
    print("RESULT PAIRED", flush=True)
except SystemExit as exc:
    print("RESULT REFUSED", exc, flush=True)
"""


class PairingPromptTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("/private/tmp/zwa4e-test-" + secrets.token_hex(6))
        (self.root / "exchange").mkdir(mode=0o700, parents=True)
        self.addCleanup(shutil.rmtree, self.root, True)
        self.ledger = OrderLedger(self.root / "exchange/exchange.sqlite")

    def claimed_child(self) -> tuple[PtyChild, str]:
        child = PtyChild(PAIR, str(self.root))
        output = child.read_until(b"within 2 minutes")
        code = re.search(rb"Pairing code:\s+([A-Z2-7]{5})-([A-Z2-7]{5})", output)
        self.ledger.claim_pairing(code=(code.group(1) + code.group(2)).decode(), account_id="buyer",
                                  role="buyer", session_hash=SESSION)
        child.read_until(b"Type PAIR to let account 'buyer'")
        with self.ledger._connection() as db:
            request = db.execute("SELECT id FROM pairing_requests").fetchone()[0]
        return child, request

    def state(self, request: str) -> tuple[str, int]:
        with self.ledger._connection() as db:
            state = db.execute("SELECT state FROM pairing_requests WHERE id=?", (request,)).fetchone()[0]
            pairings = db.execute("SELECT count(*) FROM wallet_pairings").fetchone()[0]
        return state, pairings

    def test_typed_pair_confirms_exactly_one_pairing(self):
        child, request = self.claimed_child()
        child.send(b"PAIR\n")
        child.read_until(b"RESULT PAIRED")
        child.finish()
        self.assertEqual(self.state(request), ("CONFIRMED", 1))
        self.assertIsNotNone(self.ledger.active_pairing(account_id="buyer", role="buyer", session_hash=SESSION))

    def test_any_other_answer_end_of_input_or_interrupt_rejects_the_claimed_code(self):
        for keystrokes in (b"pair\n", b"PAIR please\n", b"\n", b"\x04", b"\x03"):
            with self.subTest(keystrokes=keystrokes):
                child, request = self.claimed_child()
                child.send(keystrokes)
                child.finish()
                self.assertNotIn(b"RESULT PAIRED", child.output)
                self.assertEqual(self.state(request), ("REJECTED", 0))
                self.assertIsNone(self.ledger.active_pairing(account_id="buyer", role="buyer",
                                                             session_hash=SESSION))
                with self.ledger._transaction() as db:
                    db.execute("DELETE FROM pairing_requests")


if __name__ == "__main__":
    unittest.main()
