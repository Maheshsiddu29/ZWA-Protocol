"""Deployment-boundary regressions (no Docker, no chain, no wallets)."""

from __future__ import annotations

from dataclasses import replace
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import shutil
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from exchange import isolation, landlock_exec, persistent_chain, portal_server
from exchange.config import ConfigError, ExchangeConfig, TX_TOOL_REVISION

REPOSITORY = Path(__file__).resolve().parents[1]


def config(root: Path) -> ExchangeConfig:
    return ExchangeConfig(root=root, container="zebra", rpc_port=18232, approval_mode="CUSTODIAL_DEMO_POLICY",
                          tx_tool_dir=Path("/opt/zwa/tx-tool"), params_data_home=Path("/opt/zwa/params"),
                          node_bin=Path("/usr/local/bin/node"), rpc_host="zebra", chain_management="external",
                          isolation="linux-landlock")


class ZebraConfigTests(unittest.TestCase):
    def test_compose_zebra_config_is_the_pinned_configuration(self):
        self.assertEqual((REPOSITORY / "deploy/zebra/regtest-config.toml").read_text(), persistent_chain.CONFIG)


class LandlockProfileTests(unittest.TestCase):
    root = Path("/var/lib/zwa/zwa4e-demo")

    def granted(self, profile: str, home: Path) -> list[str]:
        ro, rw, sock = isolation.profile_paths(config(self.root), profile, home)
        return ro + rw + sock

    def test_a_demo_wallet_profile_reaches_no_other_home(self):
        home = self.root / "demo-wallets" / ("a" * 32)
        paths = self.granted(f"demo-{'a' * 32}", home)
        for other in ("seller", "coordinator", "buyer", "demo-wallets", "exchange"):
            self.assertNotIn(str(self.root / other), paths)
        self.assertIn(str(home), paths)
        self.assertFalse(any(p.startswith(str(self.root / "demo-wallets") + "/") and p != str(home) for p in paths))

    def test_dealer_and_coordinator_never_reach_wallet_homes(self):
        for profile in ("seller", "coordinator"):
            paths = self.granted(profile, self.root / profile)
            for other in ("demo-wallets", "seller" if profile == "coordinator" else "coordinator"):
                self.assertFalse(any(p == str(self.root / other) or p.startswith(str(self.root / other) + "/")
                                     for p in paths), (profile, other))

    def test_only_the_coordinator_may_create_the_approval_socket(self):
        self.assertEqual(isolation.profile_paths(config(self.root), "seller", self.root / "seller")[2], [])
        self.assertEqual(isolation.profile_paths(config(self.root), "coordinator", self.root / "coordinator")[2],
                         [str(self.root / "exchange/s")])
        with self.assertRaises(ValueError):
            isolation.profile_paths(config(self.root), "shell", self.root)

    def test_launcher_runs_isolated_by_path_and_refuses_bad_arguments(self):
        command = isolation.linux_command(config(self.root), "seller", self.root / "seller", ["/bin/true"])
        self.assertEqual(command[1:3], ["-I", str(REPOSITORY / "exchange/landlock_exec.py")])
        self.assertEqual(command[-2:], ["--", "/bin/true"])
        for argv in (["/bin/true"], ["--rw", "relative", "--", "/bin/true"], ["--shell", "/x", "--", "/bin/true"]):
            with self.assertRaises(SystemExit):
                landlock_exec.main(argv)

    def test_launcher_refuses_to_run_unconfined(self):
        with patch.object(landlock_exec, "abi_version", return_value=0), \
             patch.object(landlock_exec.os, "execv") as execv:
            with self.assertRaises(SystemExit):
                landlock_exec.main(["--rw", "/tmp", "--", "/bin/true"])
            execv.assert_not_called()


class RevisionStampTests(unittest.TestCase):
    def test_container_stamp_must_match_the_pinned_revision(self):
        folder = Path("/private/tmp/zwa4e-test-" + secrets.token_hex(6))
        folder.mkdir()
        self.addCleanup(shutil.rmtree, folder, True)
        cfg = replace(config(Path("/private/tmp/zwa4e-x")), tx_tool_dir=folder)
        (folder / "target/release").mkdir(parents=True)
        tool = folder / "target/release/zcash_tx_tool"
        tool.write_text("")
        tool.chmod(0o755)
        (folder / ".zwa-tx-tool-revision").write_text("0" * 40)
        with self.assertRaises(ConfigError):
            cfg.check_tool_revision()
        (folder / ".zwa-tx-tool-revision").write_text(TX_TOOL_REVISION + "\n")
        cfg.check_tool_revision()


class PortalServerTests(unittest.TestCase):
    def setUp(self):
        portal_server.Handler.token = b"t" * 40
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), portal_server.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def post(self, body: bytes, token: str | None):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(self.url + "/v1/portal", body, headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b"{}")

    def test_requires_the_shared_token(self):
        self.assertEqual(self.post(b"{}", None)[0], 401)
        self.assertEqual(self.post(b"{}", "t" * 39)[0], 401)

    def test_rejects_oversized_bodies_and_other_paths(self):
        self.assertEqual(self.post(b"x" * 9000, "t" * 40)[0], 413)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.url + "/admin", timeout=5)
        self.assertEqual(caught.exception.code, 404)

    def test_forwards_the_fixed_shape_and_hides_internal_errors(self):
        with patch.object(portal_server, "handle", return_value={"chainActive": True}):
            self.assertEqual(self.post(b'{"action":"market"}', "t" * 40), (200, {"ok": True, "data": {"chainActive": True}}))
        with patch.object(portal_server, "handle", side_effect=RuntimeError("/var/lib/zwa secret path")):
            status, body = self.post(b'{"action":"market"}', "t" * 40)
        self.assertEqual(body, {"ok": False, "error": "request could not be completed"})

    def test_health_endpoint(self):
        with urllib.request.urlopen(self.url + "/healthz", timeout=5) as response:
            self.assertEqual(json.load(response), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
