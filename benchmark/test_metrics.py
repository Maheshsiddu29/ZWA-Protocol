"""Resource sampling and automatic stop conditions on fixture /proc and cgroup trees."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from benchmark import metrics
from benchmark.metrics import MetricsError
from benchmark.watchdog import GIB, MIB, StopController, Thresholds, Watchdog
from benchmark.workload import ResourceMonitor

MEMINFO = """MemTotal:        8040000 kB
MemFree:          500000 kB
MemAvailable:    {avail} kB
Buffers:           10000 kB
Cached:          1000000 kB
SwapTotal:       8388604 kB
SwapFree:        {swapfree} kB
Committed_AS:    9000000 kB
"""
STAT = "cpu  {busy} 0 0 {idle} 0 0 0 0 0 0\ncpu0 1 0 0 1 0 0 0 0 0 0\nbtime 1760000000\n"


class Tree:
    """A minimal /proc + /sys/fs/cgroup for one host and containers."""

    def __init__(self, root: Path):
        self.proc = root / "proc"
        self.cg = root / "cgroup"
        (self.proc / "pressure").mkdir(parents=True)
        (self.proc / "self").mkdir()
        self.cg.mkdir()
        (self.cg / "cgroup.controllers").write_text("cpu memory pids\n")
        self.host(avail=5_000_000, swapfree=8_388_604, busy=100, idle=900, oom=0)
        (self.proc / "loadavg").write_text("0.50 0.40 0.30 1/200 12345\n")
        (self.proc / "pressure" / "memory").write_text("some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
                                                       "full avg10=0.00 avg60=0.00 avg300=0.00 total=0\n")

    def host(self, *, avail, swapfree, busy, idle, oom):
        (self.proc / "meminfo").write_text(MEMINFO.format(avail=avail, swapfree=swapfree))
        (self.proc / "stat").write_text(STAT.format(busy=busy, idle=idle))
        (self.proc / "vmstat").write_text(f"pswpin 10\npswpout 20\noom_kill {oom}\n")

    def container(self, name: str, init_pid: int, *, current=100 * MIB, peak=200 * MIB, oom_kill=0) -> Path:
        directory = self.cg / "system.slice" / f"docker-{name}.scope"
        directory.mkdir(parents=True, exist_ok=True)
        files = {"memory.current": str(current), "memory.peak": str(peak), "memory.max": str(1 * GIB),
                 "memory.swap.current": "0", "memory.events": f"low 0\nhigh 0\nmax 0\noom 0\noom_kill {oom_kill}\n",
                 "memory.stat": "anon 50000000\nfile 40000000\n",
                 "cpu.stat": "usage_usec 2000000\nuser_usec 1500000\nsystem_usec 500000\nnr_throttled 3\n"
                             "throttled_usec 1000\n",
                 "pids.current": "2", "cgroup.procs": f"{init_pid}\n{init_pid + 1}\n",
                 "memory.pressure": "some avg10=1.50 avg60=0.00 avg300=0.00 total=0\n"}
        for key, value in files.items():
            (directory / key).write_text(value)
        self.process(init_pid, ["python3", "-m", "exchange.order_service", "serve", "--root", "/var/lib/zwa/x"],
                     f"0::/system.slice/docker-{name}.scope\n", rss=60_000, hwm=80_000)
        self.process(init_pid + 1, ["/opt/zwa/tx-tool/target/release/zcash_tx_tool", "wallet-signer", "--role",
                                    "buyer", "--home", "/var/lib/zwa/zwa4e-demo/demo-wallets/x", "authorize-live",
                                    "--terms", "t.json", "--approve", "ab" * 32],
                     f"0::/system.slice/docker-{name}.scope\n", rss=900_000, hwm=1_200_000)
        return directory

    def process(self, pid: int, argv: list[str], cgroup: str, *, rss: int, hwm: int, start: int = 5000):
        directory = self.proc / str(pid)
        directory.mkdir(exist_ok=True)
        (directory / "cmdline").write_text("\0".join(argv) + "\0")
        (directory / "cgroup").write_text(cgroup)
        fields = ["S"] + ["0"] * 10 + ["250", "50"] + ["0"] * 6 + [str(start)] + ["0"] * 20
        (directory / "stat").write_text(f"{pid} (proc name) " + " ".join(fields) + "\n")
        (directory / "status").write_text(f"Name:\tx\nVmHWM:\t{hwm} kB\nVmRSS:\t{rss} kB\n")


class Parsers(unittest.TestCase):
    def test_meminfo_in_bytes_with_swap_used(self):
        info = metrics.parse_meminfo(MEMINFO.format(avail=2_000_000, swapfree=8_000_000))
        self.assertEqual(info["MemAvailable"], 2_000_000 * 1024)
        self.assertEqual(info["SwapUsed"], (8_388_604 - 8_000_000) * 1024)
        with self.assertRaises(MetricsError):
            metrics.parse_meminfo("MemTotal: 1 kB\n")

    def test_cpu_and_load(self):
        busy, total, _ = metrics.parse_cpu("cpu  10 0 5 80 5 0 0 0 0 0\n")
        self.assertEqual((busy, total), (15, 100))
        self.assertEqual(metrics.parse_loadavg("1.5 1.0 0.5 2/100 1\n"), [1.5, 1.0, 0.5])

    def test_cgroup_path(self):
        self.assertEqual(metrics.parse_cgroup_path(
            "0::/system.slice/docker-ab.scope\n"), "/system.slice/docker-ab.scope")
        with self.assertRaisesRegex(MetricsError, "cgroup v1"):
            metrics.parse_cgroup_path("12:memory:/docker/ab\n")
        with self.assertRaises(MetricsError):
            metrics.parse_cgroup_path("0::/../../etc\n")

    def test_proc_stat_with_spaces_and_parentheses_in_comm(self):
        fields = ["R"] + ["0"] * 10 + ["7", "3"] + ["0"] * 6 + ["42"] + ["0"] * 5
        parsed = metrics.parse_proc_stat("9 (a (b) c) " + " ".join(fields))
        self.assertEqual((parsed.comm, parsed.utime, parsed.stime, parsed.starttime), ("a (b) c", 7, 3, 42))


class Classification(unittest.TestCase):
    def test_cryptographic_operations_get_fixed_labels(self):
        cases = {
            ("zcash_tx_tool", "wallet-signer", "--role", "seller", "--home", "/h", "authorize-live"):
                "tx-tool:signer:authorize-live:seller",
            ("/opt/zwa/tx-tool/target/release/zcash_tx_tool", "purchase-coordinator", "--home", "/c", "execute-live",
             "--terms", "x"): "tx-tool:coordinator:execute-live",
            ("zcash_tx_tool", "purchase-coordinator", "--home", "/c", "fund-address"):
                "tx-tool:coordinator:fund-address",
            ("/usr/local/bin/node", "/app/checkout/authorization/native_zec_v2.js", "generate", "a", "b"):
                "groth16:generate",
            ("node", "/x/native_zec_v2.js", "verify"): "groth16:verify",
            ("target/release/zebrad", "-c", "/config/regtest-config.toml"): "zebrad",
            ("python3", "-m", "exchange.demo_signer", "serve"): "engine:demo-signer",
            ("python3", "-c", "import json", "zwa-bench-relay"): "bench:portal-relay",
            ("/usr/bin/python3", "-I", "/app/exchange/landlock_exec.py", "--ro", "/usr", "--", "/t/zcash_tx_tool",
             "wallet-signer", "--role", "buyer", "--home", "/h", "balances"):
                "landlock-launch:tx-tool:signer:balances:buyer",
            ("bash",): "util:bash", (): "other", ("mystery",): "other",
        }
        for argv, label in cases.items():
            self.assertEqual(metrics.classify(list(argv)), label, argv)

    def test_label_never_contains_argument_values(self):
        label = metrics.classify(["zcash_tx_tool", "wallet-signer", "--role", "buyer", "--home", "/secret/home",
                                  "authorize-live", "--approve", "f" * 64])
        self.assertNotIn("secret", label)
        self.assertNotIn("f" * 16, label)


class Samplers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tree = Tree(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_host_sample(self):
        host = metrics.HostSampler(self.tree.proc)
        first = host.sample()
        self.assertIsNone(first["cpuPercent"])  # needs two readings
        self.tree.host(avail=4_000_000, swapfree=8_000_000, busy=150, idle=950, oom=0)
        second = host.sample()
        self.assertEqual(second["cpuPercent"], 50.0)
        self.assertEqual(second["memAvailable"], 4_000_000 * 1024)
        self.assertEqual(second["oomKillCount"], 0)
        self.assertEqual(second["memoryPressure"], {"someAvg10": 0.0, "fullAvg10": 0.0})

    def test_container_cgroup_and_process_memory_are_separate(self):
        directory = self.tree.container("abc", 100)
        found = metrics.cgroup_dir_for(100, self.tree.proc, self.tree.cg)
        self.assertEqual(found, directory)
        sampler = metrics.ContainerSampler("order-service", found, 100, proc=self.tree.proc, boot_time=1_760_000_000)
        sample = sampler.sample()
        self.assertEqual(sample["memoryCurrent"], 100 * MIB)
        self.assertEqual(sample["memoryPeak"], 200 * MIB)
        self.assertEqual(sample["memoryMax"], GIB)
        self.assertEqual(sample["cpuUsageUsec"], 2_000_000)
        labels = {p["label"]: p for p in sample["processes"]}
        signer = labels["tx-tool:signer:authorize-live:buyer"]
        self.assertEqual(signer["rssBytes"], 900_000 * 1024)
        self.assertEqual(signer["peakRssBytes"], 1_200_000 * 1024)
        self.assertEqual(signer["cpuSeconds"], 3.0)
        self.assertEqual(signer["startedAt"], 1_760_000_050.0)
        self.assertNotIn("cmdline", str(sample))

    def test_cgroup_v1_is_refused(self):
        (self.tree.cg / "cgroup.controllers").unlink()
        self.tree.container("abc", 100)
        with self.assertRaisesRegex(MetricsError, "cgroup v2"):
            metrics.cgroup_dir_for(100, self.tree.proc, self.tree.cg)

    def test_vanished_container_is_reported_not_raised(self):
        directory = self.tree.container("abc", 100)
        sampler = metrics.ContainerSampler("portal", directory, 100, proc=self.tree.proc)
        for name in list(directory.iterdir()):
            name.unlink()
        self.assertEqual(sampler.sample()["present"], False)


def sample(index, *, avail=5 * GIB, swap=0, oom=0, containers=None, disk=50 * GIB):
    return {"t": 1000.0 + index, "index": index,
            "host": {"memTotal": 8 * GIB, "memAvailable": avail, "swapTotal": 8 * GIB, "swapUsed": swap,
                     "cpuPercent": 10.0, "loadAvg": [0.1, 0.1, 0.1], "oomKillCount": oom,
                     "disk": {"/": {"freeBytes": disk, "totalBytes": 100 * GIB}}},
            "containers": containers if containers is not None else [
                {"service": "zebra", "present": True, "initPidAlive": True, "oomKillEvents": 0,
                 "state": {"status": "running", "restartCount": 0, "startedAt": "t0", "oomKilled": False}}]}


class StopConditions(unittest.TestCase):
    def setUp(self):
        self.controller = StopController(clock=lambda: 1.0)
        self.watchdog = Watchdog(Thresholds(), self.controller)

    def codes(self):
        return [r["code"] for r in self.controller.reasons]

    def test_quiet_run_triggers_nothing(self):
        for i in range(5):
            self.watchdog.evaluate(sample(i))
        self.assertTrue(self.controller.may_submit())

    def test_mem_available_below_1_5_gib(self):
        self.watchdog.evaluate(sample(0))
        self.watchdog.evaluate(sample(1, avail=int(1.5 * GIB)))
        self.assertTrue(self.controller.may_submit(), "exactly the threshold is allowed")
        self.watchdog.evaluate(sample(2, avail=int(1.5 * GIB) - 1))
        self.assertEqual(self.codes(), ["MEM_AVAILABLE_LOW"])
        self.assertFalse(self.controller.may_submit())
        self.assertEqual(self.controller.first["sample"], 2)

    def test_swap_growth_is_measured_from_the_run_start(self):
        self.watchdog.evaluate(sample(0, swap=3 * GIB))  # swap already in use before the run is fine
        self.watchdog.evaluate(sample(1, swap=3 * GIB + 256 * MIB))
        self.assertTrue(self.controller.may_submit())
        self.watchdog.evaluate(sample(2, swap=3 * GIB + 256 * MIB + 1))
        self.assertEqual(self.codes(), ["SWAP_GROWTH"])

    def test_host_oom_kill(self):
        self.watchdog.evaluate(sample(0, oom=7))
        self.watchdog.evaluate(sample(1, oom=7))
        self.assertTrue(self.controller.may_submit())
        self.watchdog.evaluate(sample(2, oom=8))
        self.assertEqual(self.codes(), ["HOST_OOM_KILL"])

    def test_container_oom_kill_from_cgroup_or_docker(self):
        base = {"service": "order-service", "present": True, "initPidAlive": True, "oomKillEvents": 0,
                "state": {"status": "running", "restartCount": 0, "startedAt": "t0"}}
        self.watchdog.evaluate(sample(0, containers=[base]))
        self.watchdog.evaluate(sample(1, containers=[{**base, "oomKillEvents": 1}]))
        self.assertEqual(self.codes(), ["CONTAINER_OOM_KILL"])
        other = Watchdog(Thresholds(), StopController())
        other.evaluate(sample(0, containers=[base]))
        other.evaluate(sample(1, containers=[{**base, "state": {**base["state"], "oomKilled": True}}]))
        self.assertEqual(other.controller.first["code"], "CONTAINER_OOM_KILL")

    def test_unexpected_restart(self):
        base = {"service": "demo-signer", "present": True, "initPidAlive": True, "oomKillEvents": 0,
                "state": {"status": "running", "restartCount": 0, "startedAt": "t0"}}
        for changed in ({**base, "state": {**base["state"], "restartCount": 1}},
                        {**base, "state": {**base["state"], "startedAt": "t1"}},
                        {**base, "initPidAlive": False}):
            controller = StopController()
            watchdog = Watchdog(Thresholds(), controller)
            watchdog.evaluate(sample(0, containers=[base]))
            watchdog.evaluate(sample(1, containers=[changed]))
            self.assertEqual(controller.first["code"], "CONTAINER_RESTARTED", changed)

    def test_exited_container(self):
        base = {"service": "zebra", "present": True, "initPidAlive": True, "oomKillEvents": 0,
                "state": {"status": "running", "restartCount": 0, "startedAt": "t0"}}
        self.watchdog.evaluate(sample(0, containers=[base]))
        self.watchdog.evaluate(sample(1, containers=[{**base, "present": False,
                                                      "state": {"status": "exited", "exitCode": 137}}]))
        self.assertIn("CONTAINER_NOT_RUNNING", self.codes())
        self.assertIn("exit 137", next(r["detail"] for r in self.controller.reasons
                                       if r["code"] == "CONTAINER_NOT_RUNNING"))

    def test_disk_free_below_10_gib(self):
        self.watchdog.evaluate(sample(0))
        self.watchdog.evaluate(sample(1, disk=10 * GIB - 1))
        self.assertEqual(self.codes(), ["DISK_FREE_LOW"])

    def test_first_reason_is_kept_and_conditions_report_once(self):
        self.watchdog.evaluate(sample(0))
        for i in range(1, 4):
            self.watchdog.evaluate(sample(i, avail=GIB, disk=GIB))
        self.assertEqual(self.codes(), ["MEM_AVAILABLE_LOW", "DISK_FREE_LOW"])
        self.assertEqual(self.controller.first["code"], "MEM_AVAILABLE_LOW")
        self.assertTrue(self.controller.first["first"])

    def test_repeated_sampling_failure_fails_closed(self):
        for _ in range(Thresholds().sampler_failure_limit - 1):
            self.watchdog.sampler_failed("boom")
        self.assertTrue(self.controller.may_submit())
        self.watchdog.sampler_failed("boom")
        self.assertEqual(self.codes(), ["SAMPLER_FAILED"])

    def test_thresholds_are_bounded(self):
        Thresholds(min_mem_available_bytes=2 * GIB, max_swap_growth_bytes=0)
        for bad in ({"min_mem_available_bytes": 0}, {"max_swap_growth_bytes": 3 * GIB},
                    {"min_disk_free_bytes": GIB}, {"settlement_timeout_s": 5}, {"level_timeout_s": 1.5}):
            with self.assertRaises(ValueError, msg=bad):
                Thresholds(**bad)

    def test_unknown_stop_code_is_a_bug(self):
        with self.assertRaises(ValueError):
            self.controller.trigger("WHATEVER", "x")


class Sink:
    def __init__(self):
        self.records = []

    def write(self, record):
        self.records.append(record)


class Monitor(unittest.TestCase):
    def test_monitor_samples_feeds_watchdog_and_survives_failures(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tree = Tree(Path(tmp.name))
        directory = tree.container("abc", 100)
        controller = StopController()
        sink = Sink()
        states = {"order-service": {"status": "running", "restartCount": 0, "startedAt": "t0", "pid": 100}}
        monitor = ResourceMonitor(host=metrics.HostSampler(tree.proc),
                                  containers={"order-service": metrics.ContainerSampler("order-service", directory, 100,
                                                                                        proc=tree.proc)},
                                  inspect=lambda: states, watchdog=Watchdog(Thresholds(), controller), sink=sink)
        self.assertIsNotNone(monitor.sample_once())
        tree.host(avail=1_000_000, swapfree=8_388_604, busy=200, idle=1000, oom=0)
        monitor.sample_once()
        self.assertEqual(controller.first["code"], "MEM_AVAILABLE_LOW")
        (tree.proc / "meminfo").unlink()
        self.assertIsNone(monitor.sample_once())
        self.assertIn("error", sink.records[-1])
        self.assertEqual(len(monitor.samples), 2)


if __name__ == "__main__":
    unittest.main()
