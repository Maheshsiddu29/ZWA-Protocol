"""Deployment-boundary regressions (no Docker, no chain, no wallets)."""

from __future__ import annotations

from dataclasses import replace
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from exchange import isolation, landlock_exec, persistent_chain, portal_server, zebra_provenance
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


def load_attest_tool():
    spec = importlib.util.spec_from_file_location("zebra_runtime_attest",
                                                  REPOSITORY / "deploy/scripts/zebra-runtime-attest.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeDocker:
    """Answers the few read-only Docker CLI calls the host attestation makes."""

    def __init__(self, image_id: str, platform: str, host: str = "x86_64", container_image: str | None = None,
                 running: bool = True, containers: int = 1, project_label: str = "zwa-prov-stage"):
        self.image = {"Id": image_id, "Os": platform.split("/")[0], "Architecture": platform.split("/")[1]}
        self.host, self.running, self.containers = host, running, containers
        self.container_image = image_id if container_image is None else container_image
        self.project_label = project_label
        self.calls = []

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        if args[:2] == ("image", "inspect"):
            return json.dumps([self.image])
        if args[0] == "info":
            return json.dumps({"OSType": "linux", "Architecture": self.host})
        if args[0] == "ps":
            return "\n".join(f"{i:064x}" for i in range(1, self.containers + 1))
        if args[0] == "inspect":
            return json.dumps([{"Id": args[-1], "Image": self.container_image, "State": {"Running": self.running},
                                "Config": {"Labels": {"com.docker.compose.project": self.project_label,
                                                      "com.docker.compose.service": "zebra"}}}])
        raise AssertionError(args)


class ZebraHostAttestationTests(unittest.TestCase):
    AMD64 = persistent_chain.PINNED_ZEBRA_IMAGES["linux/amd64"]
    ARM64 = persistent_chain.PINNED_ZEBRA_IMAGES["linux/arm64"]

    def setUp(self):
        self.tool = load_attest_tool()

    def attest(self, docker: FakeDocker, project: str = "zwa-prov-stage") -> dict:
        with patch.object(self.tool, "_docker", side_effect=docker):
            return self.tool.attest("qedit/zebra-zsa-swap-pinned:latest", project, now=1_760_000_000)

    def test_amd64_and_arm64_hosts_attest_their_own_pin(self):
        for image, platform, host in ((self.AMD64, "linux/amd64", "x86_64"), (self.ARM64, "linux/arm64", "aarch64")):
            result = self.attest(FakeDocker(image, platform, host))
            self.assertEqual((result["imageId"], result["platform"], result["containerImageId"]),
                             (image, platform, image))
            # The engine-side migration accepts exactly what the host produced.
            zebra_provenance.validate_attestation(result, now=1_760_000_000, machine=platform,
                                                  project="zwa-prov-stage")

    def test_wrong_image_architecture_or_container_is_refused(self):
        cases = {
            "unpinned tag": FakeDocker("sha256:" + "e" * 64, "linux/amd64"),
            "amd64 pin reporting arm64": FakeDocker(self.AMD64, "linux/arm64", "aarch64"),
            "arm64 image on amd64 host": FakeDocker(self.ARM64, "linux/arm64", "x86_64"),
            "amd64 image on arm64 host": FakeDocker(self.AMD64, "linux/amd64", "aarch64"),
            "unknown host": FakeDocker(self.AMD64, "linux/amd64", "riscv64"),
            "container runs legacy image": FakeDocker(self.AMD64, "linux/amd64", container_image=self.ARM64),
            "container stopped": FakeDocker(self.AMD64, "linux/amd64", running=False),
            "no container": FakeDocker(self.AMD64, "linux/amd64", containers=0),
            "two containers": FakeDocker(self.AMD64, "linux/amd64", containers=2),
            "other project": FakeDocker(self.AMD64, "linux/amd64", project_label="zwa-demo"),
        }
        for name, docker in cases.items():
            with self.subTest(name), self.assertRaises(self.tool.AttestError):
                self.attest(docker)
        with self.assertRaises(self.tool.AttestError):
            self.attest(FakeDocker(self.AMD64, "linux/amd64"), project="../zwa")
        with self.assertRaises(self.tool.AttestError):
            self.tool.check_image("--help")

    def test_docker_calls_are_read_only(self):
        docker = FakeDocker(self.AMD64, "linux/amd64")
        self.attest(docker)
        self.assertEqual({call[0] for call in docker.calls}, {"image", "info", "ps", "inspect"})
        self.assertTrue(all(call[:2] == ("image", "inspect") for call in docker.calls if call[0] == "image"))

    def test_fresh_verification_compares_with_the_recorded_point_in_time_runtime(self):
        recorded = {"provenance": {"evidence": "POINT_IN_TIME_HOST_ATTESTATION", "attestedImage": self.AMD64,
                                   "attestedPlatform": "linux/amd64", "attestedContainerId": f"{1:064x}",
                                   "attestedComposeProject": "zwa-prov-stage"}}
        with patch.object(self.tool, "_docker", side_effect=FakeDocker(self.AMD64, "linux/amd64")):
            same = self.tool.verify("qedit/zebra-zsa-swap-pinned:latest", "zwa-prov-stage", recorded, now=1)
            recorded["provenance"]["attestedContainerId"] = "f" * 64  # container since replaced
            replaced = self.tool.verify("qedit/zebra-zsa-swap-pinned:latest", "zwa-prov-stage", recorded, now=1)
        self.assertEqual((same["verified"], same["sameContainerAsRecord"]), (True, True))
        self.assertIs(replaced["sameContainerAsRecord"], False)
        for provenance in ({"evidence": "LABEL_ONLY"},
                           dict(recorded["provenance"], attestedImage=self.ARM64, attestedPlatform="linux/arm64"),
                           dict(recorded["provenance"], attestedComposeProject="zwa-demo")):
            with patch.object(self.tool, "_docker", side_effect=FakeDocker(self.AMD64, "linux/amd64")), \
                 self.assertRaises(self.tool.AttestError):
                self.tool.verify("qedit/zebra-zsa-swap-pinned:latest", "zwa-prov-stage", {"provenance": provenance})
        with patch.object(self.tool, "_docker", side_effect=FakeDocker(self.AMD64, "linux/amd64", running=False)), \
             self.assertRaises(self.tool.AttestError):
            self.tool.verify("qedit/zebra-zsa-swap-pinned:latest", "zwa-prov-stage")

    def test_preflight_checks_both_pins_through_the_host_tool(self):
        script = (REPOSITORY / "deploy/scripts/preflight.sh").read_text()
        subprocess.run(["bash", "-n", str(REPOSITORY / "deploy/scripts/preflight.sh")], check=True)
        self.assertIn("scripts/zebra-runtime-attest.py check-image", script)
        for image in persistent_chain.PINNED_ZEBRA_IMAGES.values():
            self.assertIn(image, script)
        self.assertNotIn("test \"$(docker image inspect", script)

    def test_runbooks_make_no_overstated_or_unasserted_claims(self):
        runbook = (REPOSITORY / "docs/zebra-provenance-migration.md").read_text()
        for text in (runbook, (REPOSITORY / "deploy/README.md").read_text(),
                     (REPOSITORY / "exchange/zebra_provenance.py").read_text()):
            self.assertNotIn("|| true", text)                  # negative drills must be asserted
            self.assertNotIn("zebra_runtime_attest.py", text)  # the script is zebra-runtime-attest.py
            self.assertNotIn("compare-and-swap", text.lower())  # not guaranteed against non-cooperating writers
        blocks = re.findall(r"```sh\n(.*?)```", runbook, re.S)
        self.assertTrue(blocks)
        for line in "\n".join(blocks).splitlines():
            if "docker compose" in line or "compose -p" in line:
                self.assertIn("compose.amd64.yaml", line)
        self.assertIn('LIVE="/opt/zwa/repo"', runbook)
        self.assertIn('LIVE_DEPLOY="$LIVE/deploy"', runbook)
        self.assertNotIn("ZWA-Protocol", runbook)                     # the old, wrong live path
        # A bundle is verified and fetched only when one is supplied.
        self.assertRegex(runbook, r'if ! has_reviewed && \[\[ -n "\$BUNDLE" \]\]; then\n'
                                  r'  git -C "\$STAGE_CHECKOUT" bundle verify "\$BUNDLE"')
        self.assertEqual(runbook.count("bundle verify"), 1)
        # Live files are only read and copied out, never written into the live checkout.
        for line in "\n".join(blocks).splitlines():
            if re.search(r"\b(install|cp|mv|chmod|chown|rm|tee)\b", line) and "LIVE" in line:
                self.assertNotRegex(line, r"(install|cp|mv|chmod|chown|rm|tee)\b[^|]*\$LIVE", line)
        for required in ("zwa-recovery/paired-", ".tar.gz", "provenance-stage.py plan", "--rendered-sha256",
                         "--docker-sudo", "declaredSourceRevision", "POINT_IN_TIME_HOST_ATTESTATION",
                         "PROVENANCE_UNCERTAIN", "never revert blindly", "identity_lock", "x86-64 ELF",
                         "SHA-256 of every regular file", "Not yet exercised on real Docker",
                         "--backup-sudo", "BACKUP_READER", "never chmodded, chowned, copied, rewritten",
                         "paired-20261013T230743Z", "five absolute entries"):
            self.assertIn(required, runbook)

    def test_no_container_receives_the_docker_socket(self):
        compose = (REPOSITORY / "deploy/compose.yaml").read_text()
        self.assertNotIn("docker.sock", compose)
        self.assertNotIn("/var/run/docker", compose)


if __name__ == "__main__":
    unittest.main()
