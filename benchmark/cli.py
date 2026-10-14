"""zwa-bench: operator CLI for the isolated cryptographic workload benchmark.

    python3 -I deploy/scripts/zwa-bench.py <command> --project zwa-bench-<name> --workdir <dir> [options]

  plan       Read-only. Validates the project name, workdir, source checkout,
             Zebra configuration, proving parameters, images and rendered
             Compose configuration; refuses a project that already has any
             Docker resource. Prints renderedSha256.
  prepare    Repeats plan, requires --rendered-sha256, then creates the
             project on NEW volumes: parameters, Zebra (attested), bootstrap
             of fresh dealer/sponsor wallets and test assets, engine services.
  run        One benchmark level: --level 0|1|2|3. Each level is its own
             command; nothing escalates. Level 3 requires the reviewed
             Level 2 summary and its sha256.
  reconcile  Read-only re-check of a run's orders after a stop.
  report     Re-render a run's Markdown report from its summary.json.
  status     Read-only: containers and a probe snapshot.
  stop       `docker compose stop` for the project. Containers, volumes and
             evidence are kept.
  cleanup    Separate, explicit removal of exactly the resources the
             manifest names (containers, network, volumes), after label
             checks. Evidence is never deleted.

Exit status: 0 passed; 2 refused or failed before any submission; 3 the level
stopped or is partial (inspect, then reconcile); 4 the evidence scan found a
secret (restrict the evidence directory and report it).
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import signal
import subprocess
import sys
import time

from exchange.config import TX_TOOL_REVISION
from exchange.persistent_chain import PINNED_ZEBRA_IMAGES

from . import analysis, engine, evidence as ev, metrics, report, safety
from .docker import Compose, Docker
from .watchdog import GIB, MIB, StopController, Thresholds, Watchdog
from .workload import LevelPlan, LevelRun, ResourceMonitor

REPOSITORY = Path(__file__).resolve().parents[1]
DEPLOY = REPOSITORY / "deploy"
PARAMS_SHA256 = {
    "sapling-spend.params": "8e48ffd23abb3a5fd9c5589204f32d9c31285a04b78096ba40a79b75677efc13",
    "sapling-output.params": "2f0ebbcbb9bb0bcffe95a397e7eba89c29eb4dde6191c339db88570e3f3fb0e4",
}
TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
IMAGE_REF = re.compile(r"[a-z0-9][a-z0-9._/-]{0,200}(:[A-Za-z0-9_.-]{1,64})?\Z")
ENV_KEYS = ("ZWA_ENGINE_TAG", "ZWA_ZEBRA_IMAGE", "ZWA_PARAMS_DIR", "ZWA_PORTAL_TOKEN", "ZWA_PUBLIC_ORIGIN",
            "ZWA_SITE_ADDRESS", "ZWA_BIND_ADDRESS")
MIN_COMPOSE = (2, 24)  # `!reset` in deploy/benchmark/compose.bench.yaml
EXIT_REFUSED, EXIT_STOPPED, EXIT_SECRET = 2, 3, 4


class Refused(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"BENCH {message}", file=sys.stderr, flush=True)


def canonical_sha256(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class Bench:
    def __init__(self, args, docker: Docker | None = None, proc: Path = Path("/proc"),
                 cgroup_root: Path = Path("/sys/fs/cgroup"), deploy_dir: Path = DEPLOY,
                 repository: Path = REPOSITORY):
        self.args = args
        self.project = safety.check_project(args.project)
        self.repository = repository
        self.deploy_dir = deploy_dir
        self.workdir = ev.Workdir(safety.check_workdir(Path(args.workdir), repository))
        self.docker = docker or Docker(sudo=args.docker_sudo)
        override = getattr(args, "platform_override", None)
        self.override = Path(override).resolve() if override else None
        self.proc = proc
        self.cgroup_root = cgroup_root
        self.redactor = ev.Redactor()
        self._compose: Compose | None = None

    @property
    def compose(self) -> Compose:
        if self._compose is None:
            self._compose = Compose(self.docker, project=self.project, deploy_dir=self.deploy_dir,
                                    env_file=self.workdir.env_file, platform_override=self.override)
        return self._compose

    # ------------------------------------------------------------ inputs

    def source(self) -> dict:
        def git(*argv):
            done = subprocess.run(["git", "-C", str(self.repository), *argv], capture_output=True, text=True,
                                  timeout=30)
            if done.returncode:
                raise Refused(f"git {argv[0]} failed: {done.stderr.strip()[:200]}")
            return done.stdout
        allowed = set()
        if self.override is not None and self.repository in self.override.parents:
            allowed.add(f"?? {self.override.relative_to(self.repository)}")  # host-local, reviewed separately
        dirty = [line for line in git("status", "--porcelain").splitlines() if line not in allowed]
        if dirty and not self.args.allow_dirty:
            raise Refused(f"source checkout has uncommitted changes ({len(dirty)}); benchmark a committed "
                          "revision or pass --allow-dirty (recorded)")
        return {"sourceRevision": git("rev-parse", "HEAD").strip(), "sourceDirty": bool(dirty),
                "txToolRevision": TX_TOOL_REVISION,
                "harnessSha256": {path.name: ev.sha256_file(path)
                                  for path in sorted((self.repository / "benchmark").glob("*.py"))},
                "composeSha256": {str(path): ev.sha256_file(path) for path in self.compose.files}}

    def params(self) -> str:
        directory = self.args.params_dir
        if not directory or not os.path.isabs(directory) or os.path.normpath(directory) != directory \
                or re.search(r"[,:\s]", directory):
            raise Refused("--params-dir must be an absolute, normalized path without ',', ':' or spaces")
        for name, digest in PARAMS_SHA256.items():
            path = Path(directory) / name
            if path.is_symlink() or not path.is_file() or ev.sha256_file(path) != digest:
                raise Refused(f"proving parameter {name} is missing or fails its checksum")
        return directory

    def env_values(self) -> dict:
        tag, image = self.args.engine_tag, self.args.zebra_image
        if not TAG.fullmatch(tag) or not IMAGE_REF.fullmatch(image):
            raise Refused("invalid --engine-tag or --zebra-image")
        return {"ZWA_ENGINE_TAG": tag, "ZWA_ZEBRA_IMAGE": image, "ZWA_PARAMS_DIR": self.params(),
                "ZWA_PUBLIC_ORIGIN": "https://bench.invalid", "ZWA_SITE_ADDRESS": "bench.invalid",
                "ZWA_BIND_ADDRESS": "127.0.0.1"}

    def write_env(self) -> None:
        """The project's private env file: fixed keys and a fresh portal token, mode 0600."""
        values = self.env_values()
        path = self.workdir.env_file
        if path.exists():
            existing = self.read_env()
            if {k: v for k, v in existing.items() if k != "ZWA_PORTAL_TOKEN"} != values:
                raise Refused("existing private/bench.env differs from these options; use the same options "
                              "or a new workdir")
            return
        token = secrets.token_hex(32)
        ev.write_new(path, "".join(f"{key}={value}\n" for key, value in {**values, "ZWA_PORTAL_TOKEN": token}.items()))

    def read_env(self) -> dict:
        path = self.workdir.env_file
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
            raise Refused("private/bench.env must be an owner-only regular file")
        values = dict(line.split("=", 1) for line in path.read_text().splitlines() if line)
        if set(values) != set(ENV_KEYS) or len(values["ZWA_PORTAL_TOKEN"]) < 32:
            raise Refused("private/bench.env has unexpected keys")
        self.redactor.register(values["ZWA_PORTAL_TOKEN"])
        return values

    # ------------------------------------------------------------ docker facts

    def docker_facts(self) -> dict:
        version = json.loads(self.docker.ok("version", "--format", "{{json .}}", what="docker version"))
        server = version.get("Server") or {}
        compose_version = self.docker.ok("compose", "version", "--short", what="docker compose version").strip()
        numbers = tuple(int(n) for n in re.findall(r"\d+", compose_version)[:2])
        if numbers < MIN_COMPOSE:
            raise Refused(f"Docker Compose {compose_version} is too old; {MIN_COMPOSE[0]}.{MIN_COMPOSE[1]}+ needed")
        raw_arch = str(server.get("Arch"))
        arch = {"x86_64": "amd64", "aarch64": "arm64"}.get(raw_arch, raw_arch)
        return {"server": server.get("Version"), "os": server.get("Os"), "platform": f"linux/{arch}",
                "compose": compose_version}

    def resources(self) -> dict[str, list[str]]:
        label = f"label=com.docker.compose.project={self.project}"
        containers = self.docker.ok("ps", "-a", "-q", "--filter", label, what="docker ps").split()
        volumes = set(self.docker.ok("volume", "ls", "-q", "--filter", label, what="docker volume ls").split())
        volumes |= {name for name in self.docker.ok("volume", "ls", "-q", what="docker volume ls").split()
                    if name.startswith(self.project + "_")}
        networks = self.docker.ok("network", "ls", "-q", "--filter", label, what="docker network ls").split()
        return {"containers": containers, "volumes": sorted(volumes), "networks": networks}

    def image(self, ref: str) -> dict:
        data = json.loads(self.docker.ok("image", "inspect", ref, what=f"image inspect {ref}"))
        if not isinstance(data, list) or len(data) != 1:
            raise Refused(f"unexpected image inspect output for {ref}")
        item = data[0]
        return {"ref": ref, "id": item.get("Id"), "created": item.get("Created"),
                "architecture": item.get("Architecture"), "os": item.get("Os")}

    def attest(self, action: str, image: str) -> dict:
        argv = [sys.executable, "-I", str(self.deploy_dir / "scripts" / "zebra-runtime-attest.py"), action,
                "--image", image]
        if action != "check-image":
            argv += ["--project", self.project]
        if self.args.docker_sudo:
            argv.append("--docker-sudo")
        done = subprocess.run(argv, capture_output=True, text=True, timeout=300,
                              env={k: os.environ[k] for k in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONTEXT")
                                   if k in os.environ})
        if done.returncode:
            raise Refused(f"zebra {action} refused: {done.stderr.strip()[:300]}")
        try:
            return json.loads(done.stdout)
        except ValueError:
            return {"output": done.stdout.strip()[:500]}

    def host_checks(self, thresholds: Thresholds) -> list[str]:
        problems = []
        if platform.system() != "Linux":
            return [f"resource sampling needs a Linux Docker host (this is {platform.system()})"]
        if not (self.cgroup_root / "cgroup.controllers").exists():
            problems.append("cgroup v2 is not mounted at /sys/fs/cgroup")
        try:
            mem = metrics.parse_meminfo((self.proc / "meminfo").read_text())
            if mem["MemAvailable"] < thresholds.min_mem_available_bytes + GIB:
                problems.append(f"MemAvailable {mem['MemAvailable'] // MIB} MiB leaves less than 1 GiB above "
                                "the stop threshold before any work starts")
        except (OSError, metrics.MetricsError) as exc:
            problems.append(f"cannot read /proc/meminfo: {exc}")
        for path in self.disk_paths():
            stats = os.statvfs(path)
            if stats.f_bavail * stats.f_frsize < thresholds.min_disk_free_bytes:
                problems.append(f"{path}: free disk is below the stop threshold")
        return problems

    def disk_paths(self) -> list[str]:
        paths = [str(self.workdir.root)]
        try:
            root = self.docker.ok("info", "--format", "{{.DockerRootDir}}", what="docker info").strip()
            mounts = (self.proc / "self" / "mountinfo").read_text()
            paths.append(mount_point_for(root, mounts))
        except (Refused, safety.SafetyError, OSError):
            pass
        return sorted(set(paths))

    # ------------------------------------------------------------ commands

    def plan(self) -> dict:
        self.workdir.ensure()
        thresholds = thresholds_from(self.args)
        safety.check_zebra_config(self.deploy_dir / "zebra" / "regtest-config.toml")
        if self.override is not None and (self.override.is_symlink() or not self.override.is_file()):
            raise Refused("--platform-override must be an existing regular file")
        self.write_env()
        values = self.read_env()
        facts = self.docker_facts()
        pinned = PINNED_ZEBRA_IMAGES.get(facts["platform"])
        if pinned is None:
            raise Refused(f"no pinned Zebra image for {facts['platform']}")
        zebra = self.image(values["ZWA_ZEBRA_IMAGE"])
        if zebra["id"] != pinned:
            raise Refused(f"{values['ZWA_ZEBRA_IMAGE']} is {zebra['id']}, not the pinned {facts['platform']} image")
        self.attest("check-image", values["ZWA_ZEBRA_IMAGE"])
        engine_image = self.image(f"zwa-engine:{values['ZWA_ENGINE_TAG']}")
        expected = self.args.expect_engine_image_id
        if expected is not None:
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected):
                raise Refused("--expect-engine-image-id must be sha256:<64 hex>")
            if engine_image["id"] != expected:
                raise Refused(f"zwa-engine:{values['ZWA_ENGINE_TAG']} is {engine_image['id']}, not the verified "
                              f"engine image {expected}")
        rendered = json.loads(self.compose.ok("config", "--format", "json", setup_profile=True,
                                              what="docker compose config"))
        summary = safety.check_rendered(rendered, self.project, deploy_dir=self.deploy_dir,
                                        params_dir=values["ZWA_PARAMS_DIR"])
        if summary["engineImage"] != engine_image["ref"] or summary["zebraImage"] != values["ZWA_ZEBRA_IMAGE"]:
            raise Refused("rendered images differ from the requested engine tag or Zebra image")
        existing = self.resources()
        safety.project_resources_absent(existing, self.project)
        host = self.host_checks(thresholds)
        return {"plan": 1, "project": self.project, "createdAt": now_iso(), "docker": facts,
                "images": {"engine": engine_image, "zebra": zebra, "zebraPinned": pinned,
                           "engineExpected": expected},
                "rendered": summary, "renderedSha256": canonical_sha256(rendered),
                "source": self.source(), "thresholds": thresholds.as_dict(),
                "hostProblems": host, "readyForPrepare": not host,
                "zebraConfigSha256": safety.check_zebra_config(self.deploy_dir / "zebra" / "regtest-config.toml")}

    def prepare(self) -> dict:
        planned = self.plan()
        if planned["hostProblems"]:
            raise Refused("host is not ready: " + "; ".join(planned["hostProblems"]))
        if self.args.rendered_sha256 != planned["renderedSha256"]:
            raise Refused("--rendered-sha256 does not match this rendering; review the plan again")
        max_tx = self.args.max_transactions
        safety.check_budget(max_tx, 0, 0)
        if self.workdir.manifest_path.exists():
            raise Refused("this workdir is already prepared")
        manifest = {"manifest": 1, "project": self.project, "createdAt": now_iso(),
                    "maxTransactions": max_tx, "plan": planned,
                    "resources": {"volumes": planned["rendered"]["volumes"], "network": planned["rendered"]["network"],
                                  "services": ["zebra", *safety.RUN_SERVICES]}}
        ev.write_new(self.workdir.manifest_path, json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        steps = {}

        def step(name, fn):
            log(f"prepare: {name}")
            started = time.time()
            result = fn()
            steps[name] = {"seconds": round(time.time() - started, 1)}
            return result
        step("params", lambda: self.compose.ok("run", "--rm", "params", setup_profile=True, timeout=900))
        step("zebra", lambda: self.compose.ok("up", "-d", "zebra"))
        attestation = step("attest", lambda: self.attest("verify", self.args.zebra_image))
        boot = step("bootstrap", lambda: self.compose.ok("run", "--rm", "bootstrap", setup_profile=True,
                                                         timeout=5400))
        step("engine", lambda: self.compose.ok("up", "-d", *safety.RUN_SERVICES))
        controller = StopController()
        relay = engine.PortalRelay(self.compose)
        try:
            step("ready", lambda: engine.wait_ready(relay, controller, 600))
        finally:
            relay.close()
        snapshot = step("inventory", lambda: self.wait_inventory(600))
        states = self.inspect_services()
        for service, state in states.items():
            safety.check_container_mounts(service, state.pop("mounts"), self.project)
        result = {"prepared": 1, "status": "PREPARED", "project": self.project, "steps": steps,
                  "zebraAttestation": attestation, "containers": states, "bootstrapOutput":
                  self.redactor(boot.strip()[-4000:]), "probe": snapshot, "finishedAt": now_iso()}
        ev.write_new(self.workdir.evidence / "prepare-result.json", json.dumps(result, indent=2, sort_keys=True) + "\n")
        return {k: result[k] for k in ("status", "project", "steps", "finishedAt")}

    def wait_inventory(self, timeout: float) -> dict:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = engine.probe(self.compose)
            safety.check_genesis((last.get("chain") or {}).get("genesisHash"))
            problems = analysis.chain_problems(last)
            if not problems and len(last.get("dealerScans") or []) == 3 and len(last.get("offers") or []) == 3:
                return last
            time.sleep(5)
        raise Refused(f"dealer inventory was not scanned in time: {analysis.chain_problems(last or {})}")

    def inspect_services(self) -> dict[str, dict]:
        label = f"label=com.docker.compose.project={self.project}"
        rows = self.docker.ok("ps", "-a", "--filter", label, "--format",
                              '{{.ID}} {{.Label "com.docker.compose.service"}} {{.Label "com.docker.compose.oneoff"}}',
                              what="docker ps").split("\n")
        ids = {}
        for row in rows:
            parts = row.split()
            if len(parts) >= 2 and parts[1] in safety.LONG_RUNNING and (len(parts) < 3 or parts[2] != "True"):
                if parts[1] in ids:
                    raise Refused(f"more than one {parts[1]} container in {self.project}")
                ids[parts[1]] = parts[0]
        out = {}
        if ids:
            for item in json.loads(self.docker.ok("inspect", *ids.values(), what="docker inspect")):
                labels = (item.get("Config") or {}).get("Labels") or {}
                if labels.get("com.docker.compose.project") != self.project:
                    raise Refused("inspected container belongs to another project")
                state = item.get("State") or {}
                out[str(labels.get("com.docker.compose.service"))] = {
                    "id": item.get("Id"), "image": item.get("Image"), "status": state.get("Status"),
                    "pid": state.get("Pid"), "oomKilled": state.get("OOMKilled"), "exitCode": state.get("ExitCode"),
                    "startedAt": state.get("StartedAt"), "restartCount": item.get("RestartCount"),
                    "mounts": item.get("Mounts")}
        return out

    def run(self) -> tuple[dict, int]:
        level = self.args.level
        safety.check_level(level)
        manifest = self.workdir.manifest()
        if manifest.get("project") != self.project:
            raise Refused("this workdir belongs to another project")
        prepared = json.loads((self.workdir.evidence / "prepare-result.json").read_text()) \
            if (self.workdir.evidence / "prepare-result.json").is_file() else {}
        if prepared.get("status") != "PREPARED":
            raise Refused("the project was not prepared successfully")
        thresholds = thresholds_from(self.args)
        if level == 3:
            self.check_level2_review()
        wanted = safety.LEVELS[level]
        safety.check_budget(manifest["maxTransactions"], self.workdir.budget_used(), wanted)
        host = self.host_checks(thresholds)
        if host:
            raise Refused("host checks failed: " + "; ".join(host))
        self.read_env()
        lock = os.open(self.workdir.private / "run.lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused("another benchmark command holds this workdir")
        safety.check_budget(manifest["maxTransactions"], self.workdir.budget_used(), wanted)  # under the lock
        states = self.inspect_services()
        for service in safety.LONG_RUNNING:
            state = states.get(service)
            if not state or state["status"] != "running":
                raise Refused(f"{service} is not running")
            safety.check_container_mounts(service, state.pop("mounts"), self.project)
        images = manifest["plan"]["images"]
        if states["zebra"]["image"] != images["zebraPinned"] or any(
                states[s]["image"] != images["engine"]["id"] for s in safety.RUN_SERVICES):
            raise Refused("running images differ from the prepared manifest")
        attestation = self.attest("verify", images["zebra"]["ref"])
        run_id, run_dir = self.workdir.new_run(level)
        log(f"run {run_id}: level {level}, {wanted} purchase(s)")
        controller = StopController(on_stop=lambda reason: log(f"STOP {reason['code']}: {reason['detail']}"))
        events = ev.JsonLines(run_dir / "events.jsonl", self.redactor)
        samples = ev.JsonLines(run_dir / "samples.jsonl", self.redactor)
        watchdog = Watchdog(thresholds, controller)
        monitor = ResourceMonitor(host=metrics.HostSampler(self.proc, tuple(Path(p) for p in self.disk_paths())),
                                  containers={s: self.container_sampler(s, states[s]) for s in safety.LONG_RUNNING},
                                  inspect=self.monitor_states, watchdog=watchdog, sink=samples,
                                  interval=self.args.sample_interval, rebuild=self.container_sampler)
        plan = LevelPlan(level=level, wait_durability=not self.args.no_wait_durability,
                         idle_before_s=self.args.idle_before, idle_after_s=self.args.idle_after,
                         baseline_s=self.args.baseline_seconds)
        config = {"runId": run_id, "project": self.project, "level": level, "concurrency": wanted,
                  "plan": plan.__dict__, "thresholds": thresholds.as_dict(),
                  "sampleInterval": self.args.sample_interval, "startedAt": now_iso(),
                  "budget": {"max": manifest["maxTransactions"], "usedBefore": self.workdir.budget_used()},
                  "zebraAttestation": attestation, "containers": states}
        ev.write_new(run_dir / "run.json", json.dumps(self.redactor(config), indent=2, sort_keys=True) + "\n")
        relay = engine.PortalRelay(self.compose)
        previous = signal.signal(signal.SIGINT, lambda *_: controller.trigger(
            "OPERATOR_INTERRUPT", "operator interrupt: no new work; confirmed orders are being observed"))
        # The budget is reserved only once the level's preconditions pass, immediately before any submission.
        run = LevelRun(plan, thresholds=thresholds, relay=relay, probe=lambda ids: engine.probe(self.compose, ids),
                       monitor=monitor, controller=controller, events=events, redactor=self.redactor,
                       before_submit=lambda: self.workdir.reserve_budget(run_id, level, wanted))
        error = None
        try:
            engine.wait_ready(relay, controller, 120)
            summary = run.execute()
        except Exception as exc:  # noqa: BLE001 - recorded below; never hides a submitted purchase
            error = exc
        finally:
            signal.signal(signal.SIGINT, previous)
            relay.close()
            events.close()
            samples.close()
        probe_before = run.before
        if error is not None:
            submitted = any(p.timeline.get("walletRequested") for p in run.purchases)
            ev.write_new(run_dir / "run-error.json", json.dumps(self.redactor({
                "error": f"{type(error).__name__}: {error}"[:500], "submitted": submitted, "phases": run.phases,
                "stopReasons": controller.reasons,
                "transactions": [p.public() for p in run.purchases]}), indent=2, sort_keys=True) + "\n")
            if not submitted:
                raise Refused(f"level not started (nothing submitted): {error}")
            return {"runId": run_id, "result": "HARNESS_ERROR", "error": str(error)[:300],
                    "next": "inspect run-error.json, then run reconcile"}, EXIT_STOPPED
        prov = manifest["plan"]
        summary.update({
            "schema": "zwa-bench-summary/1", "fixture": False,
            "run": {"runId": run_id, "project": self.project, "startedAt": config["startedAt"], "endedAt": now_iso(),
                    "sampleInterval": self.args.sample_interval, "samplesFile": "samples.jsonl",
                    "eventsFile": "events.jsonl"},
            "provenance": {"sourceRevision": prov["source"]["sourceRevision"],
                           "sourceDirty": prov["source"]["sourceDirty"],
                           "txToolRevision": prov["source"]["txToolRevision"],
                           "engineImageId": images["engine"]["id"], "zebraImageId": images["zebra"]["id"],
                           "zebraPinned": images["zebraPinned"], "platform": prov["docker"]["platform"],
                           "renderedSha256": prov["renderedSha256"], "zebraAttestation": attestation},
            "thresholds": thresholds.as_dict(), "workload": {"level": level, "concurrency": wanted,
                                                             "purchases": [{"instrument": t["instrument"],
                                                                            "quantity": t["quantity"]}
                                                                           for t in summary["transactions"]],
                                                             "waitDurability": plan.wait_durability}})
        for name, data in (("probe-before.json", probe_before), ("probe-after.json", run.after)):
            if data is not None:
                ev.write_new(run_dir / name, json.dumps(self.redactor(data), indent=2, sort_keys=True) + "\n")
        text = json.dumps(self.redactor(summary), indent=2, sort_keys=True) + "\n"
        markdown = report.render(self.redactor(summary))
        if any(secret in text or secret in markdown for secret in self.redactor.values):
            raise Refused("refusing to write a summary that contains a registered secret")
        ev.write_new(run_dir / "summary.json", text)
        ev.write_new(run_dir / "report.md", markdown)
        findings = ev.scan(run_dir, self.redactor.values)
        if findings:
            ev.write_new(run_dir / "SECRET_SCAN_FAILED.json", json.dumps(findings, indent=2) + "\n")
            return {"runId": run_id, "result": "SECRET_SCAN_FAILED", "findings": findings}, EXIT_SECRET
        code = 0 if summary["result"] == "PASS" else EXIT_STOPPED
        return {"runId": run_id, "result": summary["result"], "stopReason": summary["stopReason"],
                "summarySha256": ev.sha256_file(run_dir / "summary.json"),
                "report": str(run_dir / "report.md")}, code

    def check_level2_review(self) -> None:
        path, digest = self.args.level2_summary, self.args.level2_summary_sha256
        if not path or not digest:
            raise Refused("level 3 requires --level2-summary and --level2-summary-sha256 from a reviewed Level 2 run")
        summary_path = Path(path)
        runs = self.workdir.evidence / "runs"
        if summary_path.is_symlink() or not summary_path.is_file() or summary_path.parent.parent != runs:
            raise Refused("--level2-summary must be a summary.json inside this workdir's runs")
        if ev.sha256_file(summary_path) != digest:
            raise Refused("--level2-summary-sha256 does not match the reviewed summary")
        summary = json.loads(summary_path.read_text())
        if summary.get("level") != 2 or summary.get("result") != "PASS" or \
                (summary.get("run") or {}).get("project") != self.project:
            raise Refused("level 3 is permitted only after a passing Level 2 run of this project")

    def container_sampler(self, service: str, state: dict) -> metrics.ContainerSampler:
        pid = state["pid"]
        return metrics.ContainerSampler(service, metrics.cgroup_dir_for(pid, self.proc, self.cgroup_root), pid,
                                        proc=self.proc, clock_ticks=os.sysconf("SC_CLK_TCK"),
                                        boot_time=metrics.boot_time(self.proc))

    def monitor_states(self) -> dict[str, dict]:
        states = self.inspect_services()
        for service, state in states.items():
            mounts = state.pop("mounts")
            safety.check_container_mounts(service, mounts, self.project)
        return states

    def reconcile(self) -> dict:
        run_dir = self.workdir.run_dir(self.args.run)
        self.read_env()
        ids = run_order_ids(run_dir)
        snapshot = engine.probe(self.compose, ids)
        index = {o["id"]: o["state"] for o in snapshot.get("orderIndex", [])}
        result = {"reconcile": 1, "runId": self.args.run, "at": now_iso(),
                  "chainProblems": analysis.chain_problems(snapshot),
                  "inventoryProblems": analysis.inventory_problems(snapshot),
                  "orders": {oid: {"state": index.get(oid),
                                   **analysis.reconcile((snapshot.get("orders") or {}).get(oid), index.get(oid))}
                             for oid in ids}}
        ev.write_new(run_dir / f"reconcile-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json",
                     json.dumps(self.redactor(result), indent=2, sort_keys=True) + "\n")
        return result

    def report(self) -> dict:
        run_dir = self.workdir.run_dir(self.args.run)
        summary = json.loads((run_dir / "summary.json").read_text())
        name = f"report-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.md"
        ev.write_new(run_dir / name, report.render(summary))
        return {"report": str(run_dir / name)}

    def status(self) -> dict:
        self.read_env()
        states = self.inspect_services()
        for state in states.values():
            state.pop("mounts", None)
        snapshot = engine.probe(self.compose) if states.get("portal", {}).get("status") == "running" else None
        return {"project": self.project, "containers": states, "budgetUsed": self.workdir.budget_used(),
                "probe": self.redactor(snapshot) if snapshot else None}

    def confirm_project(self) -> None:
        if self.args.confirm_project != self.project:
            raise Refused("--confirm-project must repeat the exact project name")

    def stop(self) -> dict:
        self.confirm_project()
        self.workdir.manifest()
        self.read_env()
        self.compose.ok("stop", what="docker compose stop")
        return {"stopped": self.project, "kept": "containers, volumes and evidence"}

    def cleanup(self) -> dict:
        """Remove exactly the manifest's resources. Never `down -v`, never prune, never evidence."""
        self.confirm_project()
        manifest = self.workdir.manifest()
        if ev.sha256_file(self.workdir.manifest_path) != self.args.confirm_manifest_sha256:
            raise Refused("--confirm-manifest-sha256 does not match evidence/manifest.json")
        if manifest.get("project") != self.project:
            raise Refused("the manifest belongs to another project")
        self.read_env()
        expected = set(manifest["resources"]["volumes"])
        if not expected or any(not name.startswith(self.project + "_") for name in expected):
            raise Refused("manifest volumes are not this project's")
        lock = os.open(self.workdir.private / "run.lock", os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused("a benchmark command is still running in this workdir")
        found = self.resources()
        unknown = set(found["volumes"]) - expected
        if unknown:
            raise Refused(f"refusing cleanup: unexpected volumes carry this project's name or label: {sorted(unknown)}")
        self.compose.ok("down", what="docker compose down (volumes kept by Compose)")
        removed = []
        for name in sorted(set(found["volumes"]) & expected):
            data = json.loads(self.docker.ok("volume", "inspect", name, what=f"volume inspect {name}"))
            labels = (data[0].get("Labels") or {}) if data else {}
            if labels.get("com.docker.compose.project") != self.project:
                raise Refused(f"volume {name} is not labelled for {self.project}; left in place")
            self.docker.ok("volume", "rm", name, what=f"volume rm {name}")
            removed.append(name)
        result = {"cleanup": 1, "project": self.project, "removedVolumes": removed, "at": now_iso(),
                  "evidenceKept": str(self.workdir.evidence)}
        ev.write_new(self.workdir.evidence / f"cleanup-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json",
                     json.dumps(result, indent=2, sort_keys=True) + "\n")
        return result


def run_order_ids(run_dir: Path) -> list[str]:
    """A run's order IDs from summary.json, else run-error.json, else events.jsonl (a harness that was killed)."""
    for name in ("summary.json", "run-error.json"):
        path = run_dir / name
        if path.is_file() and not path.is_symlink():
            return [t["orderId"] for t in json.loads(path.read_text()).get("transactions", []) if t.get("orderId")]
    ids: list[str] = []
    events = run_dir / "events.jsonl"
    if events.is_file() and not events.is_symlink():
        for line in events.read_text().splitlines():
            try:
                order_id = json.loads(line).get("orderId")
            except ValueError:
                continue  # a line cut short when the harness was killed
            if isinstance(order_id, str) and re.fullmatch(r"[0-9a-f]{32}", order_id) and order_id not in ids:
                ids.append(order_id)
    return ids


def mount_point_for(path: str, mountinfo: str) -> str:
    """Longest mount point containing `path` (so statvfs needs no access to the path itself)."""
    best = "/"
    for line in mountinfo.splitlines():
        parts = line.split()
        if len(parts) > 4:
            point = parts[4].replace("\\040", " ")
            if (path == point or path.startswith(point.rstrip("/") + "/")) and len(point) > len(best):
                best = point
    return best


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def thresholds_from(args) -> Thresholds:
    return Thresholds(
        min_mem_available_bytes=int(args.min_mem_available_gib * GIB),
        max_swap_growth_bytes=int(args.max_swap_growth_mib * MIB),
        min_disk_free_bytes=int(args.min_disk_free_gib * GIB),
        wallet_ready_timeout_s=args.wallet_ready_timeout, quote_timeout_s=args.quote_timeout,
        settlement_timeout_s=args.settlement_timeout, drain_timeout_s=args.drain_timeout,
        level_timeout_s=args.level_timeout)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="zwa-bench", description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = root.add_subparsers(dest="command", required=True)
    defaults = Thresholds()

    def common(p):
        p.add_argument("--project", required=True, help="zwa-bench-* Compose project (never a protected project)")
        p.add_argument("--workdir", required=True, help="absolute directory for private/ and evidence/")
        p.add_argument("--docker-sudo", action="store_true", help="run Docker as `sudo -n docker`")
        p.add_argument("--platform-override", help="the host's platform override, e.g. deploy/compose.amd64.yaml")

    def limits(p):
        p.add_argument("--min-mem-available-gib", type=float, default=defaults.min_mem_available_bytes / GIB)
        p.add_argument("--max-swap-growth-mib", type=float, default=defaults.max_swap_growth_bytes / MIB)
        p.add_argument("--min-disk-free-gib", type=float, default=defaults.min_disk_free_bytes / GIB)
        p.add_argument("--wallet-ready-timeout", type=int, default=defaults.wallet_ready_timeout_s)
        p.add_argument("--quote-timeout", type=int, default=defaults.quote_timeout_s)
        p.add_argument("--settlement-timeout", type=int, default=defaults.settlement_timeout_s)
        p.add_argument("--drain-timeout", type=int, default=defaults.drain_timeout_s)
        p.add_argument("--level-timeout", type=int, default=defaults.level_timeout_s)

    def images(p):
        p.add_argument("--engine-tag", required=True)
        p.add_argument("--zebra-image", default="qedit/zebra-zsa-swap-pinned:latest")
        p.add_argument("--params-dir", required=True, help="host directory holding the checksummed ZcashParams")
        p.add_argument("--allow-dirty", action="store_true", help="benchmark an uncommitted checkout (recorded)")
        p.add_argument("--expect-engine-image-id",
                       help="refuse unless the engine tag resolves to this verified image ID")

    for name in ("plan", "prepare"):
        p = sub.add_parser(name)
        common(p), images(p), limits(p)
        if name == "prepare":
            p.add_argument("--rendered-sha256", required=True, help="renderedSha256 printed by plan")
            p.add_argument("--max-transactions", type=int, default=safety.DEFAULT_MAX_TRANSACTIONS,
                           help=f"purchases this project may ever submit (<= {safety.HARD_MAX_TRANSACTIONS})")
    p = sub.add_parser("run")
    common(p), limits(p)
    p.add_argument("--level", type=int, required=True, choices=sorted(safety.LEVELS))
    p.add_argument("--baseline-seconds", type=int, default=300, help="level 0: idle sampling duration")
    p.add_argument("--idle-before", type=int, default=30)
    p.add_argument("--idle-after", type=int, default=30)
    p.add_argument("--sample-interval", type=float, default=1.0)
    p.add_argument("--no-wait-durability", action="store_true")
    p.add_argument("--level2-summary", help="level 3: the reviewed Level 2 summary.json")
    p.add_argument("--level2-summary-sha256", help="level 3: sha256 of that summary.json")
    for name in ("reconcile", "report"):
        p = sub.add_parser(name)
        common(p)
        p.add_argument("--run", required=True)
    p = sub.add_parser("status")
    common(p)
    p = sub.add_parser("stop")
    common(p)
    p.add_argument("--confirm-project", required=True)
    p = sub.add_parser("cleanup")
    common(p)
    p.add_argument("--confirm-project", required=True)
    p.add_argument("--confirm-manifest-sha256", required=True)
    return root


def validate_args(args) -> None:
    if args.command == "run":
        if not 0.25 <= args.sample_interval <= 1.0:
            raise Refused("--sample-interval must be between 0.25 and 1.0 seconds (at least one sample per second)")
        for name in ("baseline_seconds", "idle_before", "idle_after"):
            if not 0 <= getattr(args, name) <= 3600:
                raise Refused(f"--{name.replace('_', '-')} must be 0..3600")


def main(argv: list[str] | None = None, docker: Docker | None = None) -> int:
    os.umask(0o077)
    args = parser().parse_args(argv)
    try:
        validate_args(args)
        bench = Bench(args, docker)
        code = 0
        if args.command == "run":
            output, code = bench.run()
        else:
            output = getattr(bench, args.command)()
            if args.command == "plan":
                ev.write_new(bench.workdir.evidence / f"plan-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json",
                             json.dumps(output, indent=2, sort_keys=True) + "\n")
    except (Refused, safety.SafetyError, ev.EvidenceError, engine.EngineError, metrics.MetricsError,
            ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"BENCH_REFUSED {exc}", file=sys.stderr)
        return EXIT_REFUSED
    print(json.dumps(output, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    sys.exit(main())
