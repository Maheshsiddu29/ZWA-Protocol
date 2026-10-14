"""Host, container-cgroup and process measurements from Linux /proc and cgroup v2.

Three different memory quantities are kept apart on purpose:

* host memory: /proc/meminfo (MemAvailable, swap) for the whole machine;
* container memory: the container's cgroup v2 memory.current / memory.peak,
  which include page cache and kernel memory charged to that cgroup;
* process memory: VmRSS and VmHWM (peak RSS) of each process in a container,
  read from /proc/<pid>/status.

Cryptographic-operation latency is not measured here; see workload.py.

Every reader takes an injectable root so tests run on fixture trees. Nothing
here runs a subprocess: one sample is a handful of small file reads, cheap
enough for 1 Hz on a 2 vCPU host. A process's command line is read only to
classify it into a fixed label and is never stored or logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import time
from typing import Any, Callable, NamedTuple

PAGE = 4096


class MetricsError(RuntimeError):
    pass


def _read(path: Path) -> str:
    with open(path, encoding="ascii", errors="replace") as handle:
        return handle.read()


def _kv(text: str, scale: int = 1) -> dict[str, int]:
    out = {}
    for line in text.splitlines():
        parts = line.replace(":", " ").split()
        if len(parts) >= 2 and parts[1].lstrip("-").isdigit():
            out[parts[0]] = int(parts[1]) * scale
    return out


def parse_meminfo(text: str) -> dict[str, int]:
    """Bytes. /proc/meminfo reports kB (KiB)."""
    raw = _kv(text)
    needed = ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree")
    if not all(key in raw for key in needed):
        raise MetricsError("meminfo lacks MemTotal, MemAvailable or swap fields")
    out = {key: raw[key] * 1024 for key in (*needed, "Committed_AS", "Cached", "Buffers") if key in raw}
    out["SwapUsed"] = out["SwapTotal"] - out["SwapFree"]
    return out


def parse_cpu(text: str) -> tuple[int, int, dict[str, int]]:
    """(busy, total, detail) jiffies from the aggregate cpu line of /proc/stat."""
    for line in text.splitlines():
        if line.startswith("cpu "):
            values = [int(v) for v in line.split()[1:]]
            values += [0] * (10 - len(values))
            user, nice, system, idle, iowait, irq, softirq, steal = values[:8]
            # guest time is already included in user/nice.
            total = user + nice + system + idle + iowait + irq + softirq + steal
            busy = total - idle - iowait
            return busy, total, {"iowait": iowait, "steal": steal}
    raise MetricsError("/proc/stat has no aggregate cpu line")


def parse_loadavg(text: str) -> list[float]:
    parts = text.split()
    if len(parts) < 3:
        raise MetricsError("unexpected /proc/loadavg")
    return [float(v) for v in parts[:3]]


def parse_pressure(text: str) -> dict[str, float]:
    """PSI 'some'/'full' avg10 percentages (absent on kernels without PSI)."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] in ("some", "full"):
            for item in parts[1:]:
                if item.startswith("avg10="):
                    out[parts[0] + "Avg10"] = float(item[6:])
    return out


def parse_cgroup_path(text: str) -> str:
    """The unified (v2) path from /proc/<pid>/cgroup."""
    for line in text.splitlines():
        if line.startswith("0::"):
            path = line[3:].strip()
            if not path.startswith("/") or ".." in path.split("/"):
                raise MetricsError("unexpected cgroup path")
            return path
    raise MetricsError("process is not in a cgroup v2 hierarchy (cgroup v1 is not supported)")


class ProcStat(NamedTuple):
    comm: str
    state: str
    utime: int  # clock ticks
    stime: int
    starttime: int  # clock ticks since boot


def parse_proc_stat(text: str) -> ProcStat:
    """comm, state, utime, stime and starttime from /proc/<pid>/stat (comm may contain spaces and parentheses)."""
    left, right = text.find("("), text.rfind(")")
    if left < 0 or right < left:
        raise MetricsError("unexpected /proc/<pid>/stat")
    fields = text[right + 2:].split()
    return ProcStat(text[left + 1:right][:16], fields[0], int(fields[11]), int(fields[12]), int(fields[19]))


# ---------------------------------------------------------------- classification

def classify(argv: list[str]) -> str:
    """A fixed label for a process; the argv itself is never kept.

    The Landlock launcher execs into the tool, so a wrapped process shows the
    launcher's argv only for an instant; the part after `--` decides.
    """
    if not argv:
        return "other"
    if any(part.endswith("landlock_exec.py") for part in argv[:3]) and "--" in argv:
        inner = classify(argv[argv.index("--") + 1:])
        return "landlock-launch:" + inner
    name = os.path.basename(argv[0])
    words = argv[1:]
    if name == "zcash_tx_tool":
        role = ""
        if "--role" in words and words.index("--role") + 1 < len(words):
            role = ":" + {"buyer": "buyer", "seller": "seller"}.get(words[words.index("--role") + 1], "unknown")
        for tool in ("wallet-signer", "purchase-coordinator"):
            if tool in words:
                after = words[words.index(tool) + 1:]
                action = next((w for w in after if not w.startswith("-") and w in TX_TOOL_ACTIONS), "other")
                return f"tx-tool:{'signer' if tool == 'wallet-signer' else 'coordinator'}:{action}{role}"
        return "tx-tool:other"
    if name.startswith("node"):
        if any(part.endswith("native_zec_v2.js") for part in words):
            action = next((w for w in words if w in ("generate", "verify")), "other")
            return f"groth16:{action}"
        return "node:other"
    if name == "zebrad" or argv[0].endswith("/zebrad"):
        return "zebrad"
    if name.startswith("python"):
        if "zwa-bench-relay" in words:
            return "bench:portal-relay"
        if "zwa-bench-probe" in words:
            return "bench:probe"
        for module, label in (("exchange.order_service", "engine:order-service"),
                              ("exchange.demo_signer", "engine:demo-signer"),
                              ("exchange.portal_server", "engine:portal"),
                              ("exchange.demo_bootstrap", "engine:bootstrap")):
            if module in words:
                return label
        return "python:other"
    if name in ("sh", "bash", "cp", "sha256sum", "ps", "sleep", "tini", "docker-init"):
        return f"util:{name}"
    return "other"


TX_TOOL_ACTIONS = frozenset({"init", "address", "balances", "review", "authorize-live", "issue",
                             "prepare-sale", "execute-live", "fund-address", "mine-empty", "mine-issuance"})


# ---------------------------------------------------------------- samplers

@dataclass
class HostSampler:
    proc: Path = Path("/proc")
    disk_paths: tuple[Path, ...] = ()
    statvfs: Callable[[str], os.statvfs_result] = os.statvfs
    _cpu: tuple[int, int] | None = None

    def sample(self) -> dict:
        mem = parse_meminfo(_read(self.proc / "meminfo"))
        busy, total, detail = parse_cpu(_read(self.proc / "stat"))
        cpu = None
        if self._cpu is not None and total > self._cpu[1]:
            cpu = round(100.0 * (busy - self._cpu[0]) / (total - self._cpu[1]), 2)
        self._cpu = (busy, total)
        vmstat = _kv(_read(self.proc / "vmstat"))
        out: dict[str, Any] = {
            "memTotal": mem["MemTotal"], "memAvailable": mem["MemAvailable"], "swapTotal": mem["SwapTotal"],
            "swapUsed": mem["SwapUsed"], "cpuPercent": cpu, "loadAvg": parse_loadavg(_read(self.proc / "loadavg")),
            "oomKillCount": vmstat.get("oom_kill"), "swapInPages": vmstat.get("pswpin"),
            "swapOutPages": vmstat.get("pswpout"), "disk": {}}
        try:
            out["memoryPressure"] = parse_pressure(_read(self.proc / "pressure" / "memory"))
        except OSError:
            out["memoryPressure"] = None
        for path in self.disk_paths:
            stats = self.statvfs(str(path))
            out["disk"][str(path)] = {"freeBytes": stats.f_bavail * stats.f_frsize,
                                      "totalBytes": stats.f_blocks * stats.f_frsize}
        return out


def _cg_int(directory: Path, name: str) -> int | None:
    try:
        text = _read(directory / name).strip()
    except OSError:
        return None
    if text == "max":
        return None
    try:
        return int(text)
    except ValueError:
        return None


@dataclass
class ProcessRecord:
    pid: int
    label: str
    start_ticks: int


@dataclass
class ContainerSampler:
    """cgroup v2 counters and per-process memory for one container."""
    service: str
    cgroup_dir: Path
    init_pid: int
    proc: Path = Path("/proc")
    clock_ticks: int = 100
    boot_time: float = 0.0
    labels: dict[int, ProcessRecord] = field(default_factory=dict)

    def _label(self, pid: int, start_ticks: int) -> str:
        known = self.labels.get(pid)
        if known and known.start_ticks == start_ticks:
            return known.label
        try:
            argv = [part for part in _read(self.proc / str(pid) / "cmdline").split("\0") if part]
        except OSError:
            argv = []
        label = classify(argv)
        self.labels[pid] = ProcessRecord(pid, label, start_ticks)
        return label

    def sample(self) -> dict:
        if not (self.cgroup_dir / "memory.current").exists():
            return {"service": self.service, "present": False,
                    "initPidAlive": (self.proc / str(self.init_pid)).exists()}
        events = _kv(_read(self.cgroup_dir / "memory.events"))
        stat = _kv(_read(self.cgroup_dir / "memory.stat"))
        cpu = _kv(_read(self.cgroup_dir / "cpu.stat"))
        out: dict[str, Any] = {
            "service": self.service, "present": True,
            "initPidAlive": (self.proc / str(self.init_pid)).exists(),
            "memoryCurrent": _cg_int(self.cgroup_dir, "memory.current"),
            "memoryPeak": _cg_int(self.cgroup_dir, "memory.peak"),
            "memoryMax": _cg_int(self.cgroup_dir, "memory.max"),
            "swapCurrent": _cg_int(self.cgroup_dir, "memory.swap.current"),
            "memoryAnon": stat.get("anon"), "memoryFile": stat.get("file"),
            "oomEvents": events.get("oom", 0), "oomKillEvents": events.get("oom_kill", 0),
            "memoryMaxEvents": events.get("max", 0),
            "cpuUsageUsec": cpu.get("usage_usec"), "cpuThrottledUsec": cpu.get("throttled_usec"),
            "cpuNrThrottled": cpu.get("nr_throttled"),
            "pidsCurrent": _cg_int(self.cgroup_dir, "pids.current"), "processes": []}
        try:
            pressure = parse_pressure(_read(self.cgroup_dir / "memory.pressure"))
        except OSError:
            pressure = None
        out["memoryPressure"] = pressure
        try:
            pids = [int(line) for line in _read(self.cgroup_dir / "cgroup.procs").split() if line.isdigit()]
        except OSError:
            pids = []
        for pid in pids:
            try:
                stat_fields = parse_proc_stat(_read(self.proc / str(pid) / "stat"))
                status = _kv(_read(self.proc / str(pid) / "status"), 1024)
            except (OSError, MetricsError, IndexError, ValueError):
                continue  # the process exited between listing and reading
            out["processes"].append({
                "pid": pid, "label": self._label(pid, stat_fields.starttime),
                "rssBytes": status.get("VmRSS"), "peakRssBytes": status.get("VmHWM"),
                "cpuSeconds": round((stat_fields.utime + stat_fields.stime) / self.clock_ticks, 2),
                "startedAt": round(self.boot_time + stat_fields.starttime / self.clock_ticks, 2)})
        return out


def boot_time(proc: Path = Path("/proc")) -> float:
    for line in _read(proc / "stat").splitlines():
        if line.startswith("btime "):
            return float(line.split()[1])
    raise MetricsError("/proc/stat has no btime")


def cgroup_dir_for(pid: int, proc: Path = Path("/proc"), cgroup_root: Path = Path("/sys/fs/cgroup")) -> Path:
    if not (cgroup_root / "cgroup.controllers").exists():
        raise MetricsError("cgroup v2 is not mounted at /sys/fs/cgroup (cgroup v1 is not supported)")
    relative = parse_cgroup_path(_read(proc / str(pid) / "cgroup"))
    directory = cgroup_root / relative.lstrip("/")
    if not (directory / "memory.current").exists():
        raise MetricsError(f"container cgroup has no memory controller: {relative}")
    return directory


def now() -> float:
    return round(time.time(), 3)
