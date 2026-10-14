"""Automatic stop conditions and the submission gate.

The watchdog evaluates every resource sample against configurable thresholds;
workers report settlement, receipt and inventory failures to the same
controller. The first condition closes the gate: no new wallet, purchase or
confirmation is submitted afterwards. Nothing here terminates a process. Work
already confirmed is left to the exchange's own state machine and observed
(see the drain and reconciliation procedure in docs/benchmark-runbook.md).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import threading
import time

GIB = 1 << 30
MIB = 1 << 20


@dataclass(frozen=True)
class Thresholds:
    """Conservative defaults for the 2 vCPU / 8 GiB / 8 GiB-swap AWS host."""
    min_mem_available_bytes: int = int(1.5 * GIB)
    max_swap_growth_bytes: int = 256 * MIB
    min_disk_free_bytes: int = 10 * GIB
    wallet_ready_timeout_s: int = 600
    quote_timeout_s: int = 240
    settlement_timeout_s: int = 900
    drain_timeout_s: int = 1200
    level_timeout_s: int = 3600
    sampler_failure_limit: int = 5

    # (lowest, highest) an operator may configure. Tightening is always
    # possible; these floors keep a typo from disabling a condition.
    BOUNDS = {
        "min_mem_available_bytes": (512 * MIB, 6 * GIB),
        "max_swap_growth_bytes": (0, 2 * GIB),
        "min_disk_free_bytes": (2 * GIB, 500 * GIB),
        "wallet_ready_timeout_s": (60, 1800),
        "quote_timeout_s": (30, 900),
        "settlement_timeout_s": (60, 1800),
        "drain_timeout_s": (60, 3600),
        "level_timeout_s": (300, 7200),
        "sampler_failure_limit": (1, 30),
    }

    def __post_init__(self) -> None:
        for item in fields(self):
            low, high = self.BOUNDS[item.name]
            value = getattr(self, item.name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"threshold {item.name} must be an integer in [{low}, {high}]")

    def as_dict(self) -> dict:
        return asdict(self)


# Stop reason codes. Resource codes come from the watchdog, the others from
# workers and checks. Every one closes the submission gate.
RESOURCE_CODES = ("MEM_AVAILABLE_LOW", "SWAP_GROWTH", "HOST_OOM_KILL", "CONTAINER_OOM_KILL",
                  "CONTAINER_RESTARTED", "CONTAINER_NOT_RUNNING", "DISK_FREE_LOW", "SAMPLER_FAILED")
WORK_CODES = ("WALLET_READY_TIMEOUT", "QUOTE_TIMEOUT", "SETTLEMENT_TIMEOUT", "LEVEL_TIMEOUT",
              "ORDER_FAILED", "RECEIPT_MISSING", "RECEIPT_INCONSISTENT", "INVENTORY_INCONSISTENT",
              "TX_VERIFICATION_FAILED", "DUPLICATE_SUBMISSION_MISMATCH", "PORTAL_REFUSED",
              "CHAIN_NOT_REGTEST", "OPERATOR_INTERRUPT", "HARNESS_ERROR", "DURABILITY_NOT_OBSERVED",
              "DURABILITY_LOST", "PURCHASE_AMBIGUOUS", "ACCEPTANCE_FAILED")
CODES = frozenset(RESOURCE_CODES + WORK_CODES)


class StopController:
    """Thread-safe record of stop reasons; the first one is the run's stop reason."""

    def __init__(self, clock=time.time, on_stop=None):
        self._lock = threading.Lock()
        self._reasons: list[dict] = []
        self._gate = threading.Event()
        self.clock = clock
        self.on_stop = on_stop

    def trigger(self, code: str, detail: str, **context) -> bool:
        if code not in CODES:
            raise ValueError(f"unknown stop code {code}")
        with self._lock:
            first = not self._reasons
            self._reasons.append({"code": code, "detail": detail[:500], "at": round(self.clock(), 3),
                                  "first": first, **context})
            self._gate.set()
        if self.on_stop:
            self.on_stop(self._reasons[-1])
        return first

    @property
    def stopped(self) -> bool:
        return self._gate.is_set()

    def may_submit(self) -> bool:
        return not self._gate.is_set()

    def wait(self, seconds: float) -> bool:
        """Sleep up to `seconds`; True if a stop was triggered meanwhile."""
        return self._gate.wait(seconds)

    @property
    def reasons(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._reasons]

    @property
    def first(self) -> dict | None:
        with self._lock:
            return dict(self._reasons[0]) if self._reasons else None


class Watchdog:
    """Evaluate one sample against the run's starting point and the thresholds."""

    def __init__(self, thresholds: Thresholds, controller: StopController):
        self.t = thresholds
        self.controller = controller
        self.baseline: dict | None = None
        self.failures = 0
        self._seen: set[tuple] = set()

    def _once(self, key: tuple, code: str, detail: str, **context) -> None:
        if key not in self._seen:
            self._seen.add(key)
            self.controller.trigger(code, detail, **context)

    def sampler_failed(self, error: str) -> None:
        self.failures += 1
        if self.failures >= self.t.sampler_failure_limit:
            self._once(("sampler",), "SAMPLER_FAILED",
                       f"{self.failures} consecutive samples failed; resources cannot be watched: {error[:200]}")

    def evaluate(self, sample: dict) -> None:
        self.failures = 0
        host, containers = sample["host"], sample["containers"]
        if self.baseline is None:
            self.baseline = {"swapUsed": host["swapUsed"], "oomKillCount": host.get("oomKillCount"),
                             "containers": {c["service"]: c for c in containers}}
        base = self.baseline
        index = sample.get("index")
        if host["memAvailable"] < self.t.min_mem_available_bytes:
            self._once(("mem",), "MEM_AVAILABLE_LOW",
                       f"MemAvailable {host['memAvailable']} B < {self.t.min_mem_available_bytes} B", sample=index)
        growth = host["swapUsed"] - base["swapUsed"]
        if growth > self.t.max_swap_growth_bytes:
            self._once(("swap",), "SWAP_GROWTH",
                       f"swap used grew {growth} B > {self.t.max_swap_growth_bytes} B during the run", sample=index)
        if host.get("oomKillCount") is not None and base.get("oomKillCount") is not None \
                and host["oomKillCount"] > base["oomKillCount"]:
            self._once(("host-oom",), "HOST_OOM_KILL",
                       f"kernel oom_kill counter rose {base['oomKillCount']} -> {host['oomKillCount']}", sample=index)
        for path, disk in host.get("disk", {}).items():
            if disk["freeBytes"] < self.t.min_disk_free_bytes:
                self._once(("disk", path), "DISK_FREE_LOW",
                           f"{path}: {disk['freeBytes']} B free < {self.t.min_disk_free_bytes} B", sample=index)
        for item in containers:
            service = item["service"]
            start = base["containers"].get(service, {})
            state = item.get("state") or {}
            if item.get("oomKillEvents", 0) > start.get("oomKillEvents", 0) or state.get("oomKilled"):
                self._once(("oom", service), "CONTAINER_OOM_KILL", f"{service}: cgroup oom_kill or OOMKilled",
                           sample=index, service=service)
            restarts = state.get("restartCount")
            start_restarts = (start.get("state") or {}).get("restartCount")
            started = state.get("startedAt")
            start_started = (start.get("state") or {}).get("startedAt")
            if (restarts is not None and start_restarts is not None and restarts > start_restarts) or \
                    (started and start_started and started != start_started) or \
                    (item.get("present") and item.get("initPidAlive") is False):
                self._once(("restart", service), "CONTAINER_RESTARTED",
                           f"{service}: restart count {start_restarts} -> {restarts} or its init process changed",
                           sample=index, service=service)
            if not item.get("present") or (state and state.get("status") not in (None, "running")):
                self._once(("down", service), "CONTAINER_NOT_RUNNING",
                           f"{service}: status {state.get('status')} exit {state.get('exitCode')}",
                           sample=index, service=service)
