"""Isolation rules and the Docker command allowlist. No Docker is used.

benchmark/fixtures/rendered-config.json is the real `docker compose config
--format json` output for deploy/compose.yaml + deploy/benchmark/compose.bench.yaml
with `-p zwa-bench-render`, with the checkout path and token replaced.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from benchmark import safety
from benchmark.docker import Compose, Docker
from benchmark.safety import SafetyError

REPOSITORY = Path(__file__).resolve().parents[1]
DEPLOY = REPOSITORY / "deploy"
PARAMS = "/srv/zwa/params/ZcashParams"


def rendered(project: str = "zwa-bench-render") -> dict:
    text = (REPOSITORY / "benchmark/fixtures/rendered-config.json").read_text()
    text = text.replace("__DEPLOY__", str(DEPLOY.resolve())).replace("zwa-bench-render", project)
    return json.loads(text)


class Recorder:
    """A fake subprocess.run that records argv and never runs anything."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)

        class Done:
            returncode, stdout, stderr = 0, b"", b""
        return Done()


class ProjectNames(unittest.TestCase):
    def test_every_protected_project_is_refused(self):
        for name in ("zwa-demo", "zwa-restore-audit", "zwa-prov-stage", "zwa-prov-stage-r2", "zwa-prov-preflight"):
            with self.assertRaisesRegex(SafetyError, "protected"):
                safety.check_project(name)

    def test_protected_names_cannot_be_disguised(self):
        for name in ("ZWA-DEMO", " zwa-demo", "zwa-demo ", "Zwa-Prov-Stage"):
            with self.assertRaisesRegex(SafetyError, "protected"):
                safety.check_project(name)

    def test_only_zwa_bench_names(self):
        for name in ("zwa-bench", "zwa-bench-", "bench-a", "zwa-demo-bench", "zwa-bench-A", "zwa-bench-a_b",
                     "zwa-bench-" + "a" * 41, "zwa-bench-../x", "", None, 7):
            with self.assertRaises(SafetyError, msg=repr(name)):
                safety.check_project(name)
        self.assertEqual(safety.check_project("zwa-bench-aws-r1"), "zwa-bench-aws-r1")


class RenderedConfiguration(unittest.TestCase):
    def check(self, config, project="zwa-bench-render"):
        return safety.check_rendered(config, project, deploy_dir=DEPLOY, params_dir=PARAMS)

    def test_real_rendering_passes(self):
        summary = self.check(rendered())
        self.assertEqual(summary["volumes"], ["zwa-bench-render_zebra-state", "zwa-bench-render_zwa-params",
                                              "zwa-bench-render_zwa-root"])
        self.assertEqual(summary["network"], "zwa-bench-render_internal")
        self.assertNotIn("web", summary["services"])
        self.assertNotIn("caddy", summary["services"])

    def test_rendering_that_resolves_to_a_protected_project_is_refused(self):
        config = rendered()
        config["name"] = "zwa-demo"  # e.g. `-p` forgotten: compose.yaml's own `name: zwa-demo` wins
        with self.assertRaisesRegex(SafetyError, "rendered project name"):
            self.check(config)
        with self.assertRaisesRegex(SafetyError, "protected"):
            self.check(rendered("zwa-demo"), "zwa-demo")

    def test_foreign_volume_is_refused(self):
        config = rendered()
        config["volumes"]["zwa-root"]["name"] = "zwa-demo_zwa-root"
        with self.assertRaisesRegex(SafetyError, "not external or foreign"):
            self.check(config)
        config = rendered()
        config["volumes"]["zwa-root"]["external"] = True
        with self.assertRaisesRegex(SafetyError, "not external or foreign"):
            self.check(config)
        config = rendered()
        config["services"]["portal"]["volumes"].append({"type": "volume", "source": "zwa-demo_zwa-root",
                                                        "target": "/x"})
        with self.assertRaisesRegex(SafetyError, "foreign volume"):
            self.check(config)

    def test_extra_or_writable_bind_mounts_are_refused(self):
        config = rendered()
        config["services"]["order-service"]["volumes"].append(
            {"type": "bind", "source": "/var/run/docker.sock", "target": "/var/run/docker.sock", "read_only": True})
        with self.assertRaisesRegex(SafetyError, "bind mount"):
            self.check(config)
        config = rendered()
        config["services"]["params"]["volumes"][0]["read_only"] = False
        with self.assertRaisesRegex(SafetyError, "bind mount"):
            self.check(config)
        config = rendered()
        config["services"]["zebra"]["volumes"][1]["source"] = "/tmp/other.toml"
        with self.assertRaisesRegex(SafetyError, "bind mount"):
            self.check(config)

    def test_web_edge_or_published_ports_are_refused(self):
        config = rendered()
        config["services"]["caddy"] = {"image": "caddy", "networks": {"internal": None}}
        with self.assertRaisesRegex(SafetyError, "exactly"):
            self.check(config)
        config = rendered()
        config["services"]["zebra"]["ports"] = [{"target": 18232, "published": "18232"}]
        with self.assertRaisesRegex(SafetyError, "sets ports"):
            self.check(config)

    def test_host_access_is_refused(self):
        for key, value in (("privileged", True), ("network_mode", "host"), ("pid", "host"), ("cap_add", ["SYS_ADMIN"]),
                           ("env_file", ["./env/web.env"])):
            config = rendered()
            config["services"]["portal"][key] = value
            with self.assertRaisesRegex(SafetyError, f"sets {key}"):
                self.check(config)

    def test_engine_hardening_and_root_are_required(self):
        config = rendered()
        config["services"]["portal"]["user"] = "0:0"
        with self.assertRaisesRegex(SafetyError, "10001"):
            self.check(config)
        config = rendered()
        config["services"]["order-service"]["environment"]["ZWA_PRIVATE_EXCHANGE_ROOT"] = "/private/tmp/zwa4e-phase4e-b"
        with self.assertRaisesRegex(SafetyError, "exchange root"):
            self.check(config)
        config = rendered()
        del config["services"]["zebra"]["mem_limit"]
        with self.assertRaisesRegex(SafetyError, "no memory limit"):
            self.check(config)

    def test_non_internal_network_is_refused(self):
        config = rendered()
        config["networks"]["internal"]["internal"] = False
        with self.assertRaisesRegex(SafetyError, "internal network"):
            self.check(config)
        config = rendered()
        config["services"]["portal"]["networks"]["edge"] = None
        with self.assertRaisesRegex(SafetyError, "only the internal network"):
            self.check(config)

    def test_setup_services_stay_one_shot(self):
        config = rendered()
        config["services"]["bootstrap"]["profiles"] = []
        with self.assertRaisesRegex(SafetyError, "setup profile"):
            self.check(config)


class ResourcesAndMounts(unittest.TestCase):
    def test_existing_resources_mean_not_fresh(self):
        safety.project_resources_absent({"containers": [], "volumes": [], "networks": []}, "zwa-bench-a")
        with self.assertRaisesRegex(SafetyError, "new volumes"):
            safety.project_resources_absent({"containers": [], "volumes": ["zwa-bench-a_zwa-root"], "networks": []},
                                            "zwa-bench-a")

    def test_running_container_with_foreign_volume_is_refused(self):
        own = [{"Type": "volume", "Name": "zwa-bench-a_zwa-root"}, {"Type": "bind", "Source": "/x"}]
        safety.check_container_mounts("portal", own, "zwa-bench-a")
        for name in ("zwa-demo_zwa-root", "zwa-bench-ab_zwa-root", "0123abcd"):
            with self.assertRaisesRegex(SafetyError, "foreign volume"):
                safety.check_container_mounts("portal", own + [{"Type": "volume", "Name": name}], "zwa-bench-a")


class Mainnet(unittest.TestCase):
    def test_mainnet_and_testnet_are_refused_by_name(self):
        with self.assertRaisesRegex(SafetyError, "refusing mainnet"):
            safety.check_genesis("00040fe8ec8471911baa1db1266ea15dd06b4a8a5c453883c000b031973dce08")
        with self.assertRaisesRegex(SafetyError, "refusing testnet"):
            safety.check_genesis("05a60a92d99d85997cce3b87616c089f6124d7342af37106edc76126334a2c38")
        for other in ("0" * 64, "", None):
            with self.assertRaises(SafetyError):
                safety.check_genesis(other)
        self.assertEqual(safety.check_genesis(safety.REGTEST_GENESIS), safety.REGTEST_GENESIS)

    def test_zebra_configuration_must_be_the_pinned_regtest_file(self):
        safety.check_zebra_config(DEPLOY / "zebra" / "regtest-config.toml")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.toml"
            path.write_text((DEPLOY / "zebra" / "regtest-config.toml").read_text().replace('"Regtest"', '"Mainnet"'))
            with self.assertRaisesRegex(SafetyError, "differs"):
                safety.check_zebra_config(path)
            link = Path(tmp) / "link.toml"
            link.symlink_to(DEPLOY / "zebra" / "regtest-config.toml")
            with self.assertRaisesRegex(SafetyError, "regular file"):
                safety.check_zebra_config(link)


class LevelsAndBudget(unittest.TestCase):
    def test_levels_fix_concurrency(self):
        self.assertEqual([safety.check_level(level) for level in (0, 1, 2, 3)], [0, 1, 2, 3])
        for level, concurrency in ((4, None), (-1, None), (True, None), ("2", None), (2, 3), (3, 4), (1, 2)):
            with self.assertRaises(SafetyError):
                safety.check_level(level, concurrency)

    def test_transaction_budget(self):
        self.assertEqual(safety.check_budget(6, 3, 3), 0)
        with self.assertRaisesRegex(SafetyError, "budget exhausted"):
            safety.check_budget(6, 4, 3)
        with self.assertRaisesRegex(SafetyError, "budget must be"):
            safety.check_budget(safety.HARD_MAX_TRANSACTIONS + 1, 0, 1)
        for bad in ((6, -1, 1), (6, 0, -1), ("6", 0, 1)):
            with self.assertRaises(SafetyError):
                safety.check_budget(*bad)


class Workdir(unittest.TestCase):
    def test_workdir_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp).resolve() / "bench"
            self.assertEqual(safety.check_workdir(good, REPOSITORY), good)
            for bad in (Path("relative"), Path(tmp) / "a/../b", REPOSITORY / "evidence",
                        Path("/private/tmp/zwa4e-phase4e-b/bench"), Path.home() / "zwa-recovery" / "x",
                        Path("/var/lib/docker/volumes/x")):
                with self.assertRaises(SafetyError, msg=str(bad)):
                    safety.check_workdir(bad, REPOSITORY)
            link = Path(tmp) / "link"
            link.symlink_to(tmp)
            with self.assertRaisesRegex(SafetyError, "symlink"):
                safety.check_workdir(link / "bench", REPOSITORY)


class DockerAllowlist(unittest.TestCase):
    def setUp(self):
        self.runner = Recorder()
        self.docker = Docker(runner=self.runner)
        self.compose = Compose(self.docker, project="zwa-bench-a", deploy_dir=DEPLOY, env_file=Path("/w/private/bench.env"))

    def test_every_compose_command_names_the_project_and_the_override(self):
        self.compose("config", "--format", "json")
        argv = self.runner.calls[-1]
        self.assertEqual(argv[argv.index("-p") + 1], "zwa-bench-a")
        self.assertIn(str(DEPLOY / "benchmark" / "compose.bench.yaml"), argv)
        self.assertEqual(argv[-3:], ["config", "--format", "json"])

    def test_protected_project_cannot_build_a_compose(self):
        with self.assertRaisesRegex(SafetyError, "protected"):
            Compose(self.docker, project="zwa-demo", deploy_dir=DEPLOY, env_file=Path("/x"))

    def test_destructive_commands_have_no_path(self):
        refused = [("down", "-v"), ("down", "--volumes"), ("down", "--rmi", "all"), ("rm", "-f"), ("kill",),
                   ("up", "-d", "web"), ("up", "-d", "caddy"), ("up", "zebra"), ("run", "--rm", "portal"),
                   ("run", "params"), ("exec", "order-service", "sh"), ("exec", "-T", "zebra", "sh"),
                   ("exec", "-T", "portal", "rm", "-f", "/x"), ("stop", "-v"), ("restart",), ("pause",)]
        for args in refused:
            with self.assertRaises(SafetyError, msg=args):
                self.compose(*args)
        for args in (("system", "prune"), ("volume", "prune"), ("volume", "rm", "-f", "x"), ("rm", "x"),
                     ("container", "rm", "x"), ("network", "rm", "x"), ("kill", "x"), ("run", "alpine"),
                     ("image", "rm", "x"), ("ps", "-a", "-v")):
            with self.assertRaises(SafetyError, msg=args):
                self.docker(*args)
        self.assertEqual(self.runner.calls, [], "nothing may reach the Docker CLI")

    def test_allowed_shapes(self):
        self.compose("run", "--rm", "params", setup_profile=True)
        self.compose("up", "-d", "order-service", "demo-signer", "portal")
        self.compose("exec", "-T", "portal", "python3", "-c", "print(1)", "zwa-bench-probe")
        self.compose("stop")
        self.compose("down")
        self.docker("volume", "rm", "zwa-bench-a_zwa-root")
        self.assertEqual(len(self.runner.calls), 6)
        self.assertIn("--profile", self.runner.calls[0])

    def test_environment_is_minimal(self):
        import os
        os.environ["COMPOSE_PROJECT_NAME"] = "zwa-demo"
        os.environ["ZWA_ZEBRA_IMAGE"] = "evil"
        try:
            docker = Docker(runner=self.runner)
            self.assertNotIn("COMPOSE_PROJECT_NAME", docker.env)
            self.assertNotIn("ZWA_ZEBRA_IMAGE", docker.env)
        finally:
            del os.environ["COMPOSE_PROJECT_NAME"], os.environ["ZWA_ZEBRA_IMAGE"]

    def test_override_file_never_starts_web_or_publishes_ports(self):
        text = (DEPLOY / "benchmark" / "compose.bench.yaml").read_text()
        self.assertIn("ports: !reset []", text)
        self.assertIn("env_file: !reset []", text)
        config = copy.deepcopy(rendered())
        self.assertFalse(any(service.get("ports") for service in config["services"].values()))


if __name__ == "__main__":
    unittest.main()
