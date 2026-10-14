"""Isolation and safety rules every benchmark command checks before acting.

A benchmark project is a separate Compose project named zwa-bench-*, created
on new volumes, with the public web edge removed and no published port. These
checks refuse anything else: a protected project, a rendered configuration
that resolves to another project or mounts a foreign volume, a chain that is
not the pinned regtest chain, a level or concurrency outside the plan, or a
transaction count above the operator's budget.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import posixpath
import re

from exchange.persistent_chain import CONFIG as ZEBRA_CONFIG, CONFIG_SHA256 as ZEBRA_CONFIG_SHA256

# Projects holding real or reviewed state. Never a benchmark target, whatever
# the naming rule below would say.
PROTECTED_PROJECTS = frozenset({"zwa-demo", "zwa-restore-audit", "zwa-prov-stage", "zwa-prov-stage-r2",
                                "zwa-prov-preflight"})
BENCH_PROJECT = re.compile(r"zwa-bench-[a-z0-9][a-z0-9-]{0,39}\Z")
# Zcash genesis block hashes. Only regtest is ever accepted.
REGTEST_GENESIS = "029f11d80ef9765602235e1bc9727e3eb6ba20839319f761fee920d63401e327"
REFUSED_GENESIS = {
    "00040fe8ec8471911baa1db1266ea15dd06b4a8a5c453883c000b031973dce08": "mainnet",
    "05a60a92d99d85997cce3b87616c089f6124d7342af37106edc76126334a2c38": "testnet",
}
ENGINE_ROOT = "/var/lib/zwa/zwa4e-demo"
# Services of deploy/compose.yaml that remain after the benchmark override.
SETUP_SERVICES = ("params", "bootstrap")
RUN_SERVICES = ("order-service", "demo-signer", "portal")
SERVICES = frozenset({"zebra", *SETUP_SERVICES, *RUN_SERVICES})
LONG_RUNNING = ("zebra", *RUN_SERVICES)
VOLUME_KEYS = frozenset({"zwa-root", "zwa-params", "zebra-state"})
HOST_ACCESS_KEYS = ("privileged", "network_mode", "pid", "ipc", "uts", "userns_mode", "cgroup", "cgroup_parent",
                    "cap_add", "devices", "device_cgroup_rules", "volumes_from", "sysctls", "extra_hosts",
                    "runtime", "gpus", "storage_opt", "ports", "expose", "env_file")
ZEBRA_CONFIG_TARGET = "/config/regtest-config.toml"
PARAMS_TARGET = "/src"
ENGINE_USER = "10001:10001"

# Level -> purchases submitted concurrently. Level 0 submits nothing.
LEVELS = {0: 0, 1: 1, 2: 2, 3: 3}
MAX_CONCURRENCY = 3
# The operator chooses a budget at prepare time; it can never exceed this.
HARD_MAX_TRANSACTIONS = 12
DEFAULT_MAX_TRANSACTIONS = 6  # one Level 1, one Level 2 and one Level 3 run


class SafetyError(RuntimeError):
    """A rule refused the request. Nothing was changed."""


def check_project(project: object) -> str:
    """A zwa-bench-* name that is not, and does not normalize to, a protected project."""
    if not isinstance(project, str):
        raise SafetyError("project must be a string")
    folded = project.strip().lower()
    if folded in PROTECTED_PROJECTS or project in PROTECTED_PROJECTS:
        raise SafetyError(f"refusing protected Compose project {folded!r}")
    if not BENCH_PROJECT.fullmatch(project):
        raise SafetyError("benchmark project must match zwa-bench-[a-z0-9][a-z0-9-]* (at most 49 characters)")
    return project


def check_genesis(genesis: object) -> str:
    """Mainnet and testnet are refused by name; anything but regtest is refused."""
    if not isinstance(genesis, str):
        raise SafetyError("chain genesis hash is unavailable")
    if genesis in REFUSED_GENESIS:
        raise SafetyError(f"refusing {REFUSED_GENESIS[genesis]}: the benchmark runs only on disposable regtest")
    if genesis != REGTEST_GENESIS:
        raise SafetyError("chain genesis is not the Zcash regtest genesis")
    return genesis


def check_zebra_config(path: Path) -> str:
    """The node configuration must be byte-identical to the pinned regtest configuration."""
    if path.is_symlink() or not path.is_file():
        raise SafetyError(f"zebra configuration is not a regular file: {path}")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != ZEBRA_CONFIG_SHA256 or data.decode(errors="replace") != ZEBRA_CONFIG:
        raise SafetyError("zebra configuration differs from the pinned regtest configuration")
    if 'network = "Regtest"' not in ZEBRA_CONFIG:  # the pin itself must select regtest
        raise SafetyError("pinned zebra configuration does not select regtest")
    return digest


def check_level(level: object, concurrency: object | None = None) -> int:
    if type(level) is not int or level not in LEVELS:
        raise SafetyError("level must be 0, 1, 2 or 3")
    wanted = LEVELS[level] if concurrency is None else concurrency
    if type(wanted) is not int or wanted != LEVELS[level] or wanted > MAX_CONCURRENCY:
        raise SafetyError(f"level {level} runs exactly {LEVELS[level]} concurrent purchase(s)")
    return LEVELS[level]


def check_budget(max_transactions: object, used: int, wanted: int) -> int:
    """Refuse a run that would exceed the project's transaction budget."""
    if type(max_transactions) is not int or not 0 <= max_transactions <= HARD_MAX_TRANSACTIONS:
        raise SafetyError(f"transaction budget must be 0..{HARD_MAX_TRANSACTIONS}")
    if type(used) is not int or used < 0 or type(wanted) is not int or wanted < 0:
        raise SafetyError("invalid transaction count")
    if used + wanted > max_transactions:
        raise SafetyError(f"transaction budget exhausted: {used} used, {wanted} requested, "
                          f"{max_transactions} allowed for this project")
    return max_transactions - used - wanted


def check_workdir(workdir: Path, repository: Path) -> Path:
    """An absolute, non-symlinked directory outside the checkout and any exchange or Docker root."""
    if not workdir.is_absolute() or posixpath.normpath(str(workdir)) != str(workdir):
        raise SafetyError("--workdir must be an absolute, normalized path")
    for part in [workdir, *workdir.parents]:
        if part.is_symlink():
            raise SafetyError(f"--workdir must not pass through a symlink: {part}")
    resolved = workdir.resolve() if workdir.exists() else workdir.parent.resolve() / workdir.name
    repo = repository.resolve()
    if resolved == repo or repo in resolved.parents:
        raise SafetyError("--workdir must be outside the source checkout")
    text = str(resolved)
    if re.search(r"/zwa4e-[^/]*(/|\Z)", text) or "/zwa-recovery" in text:
        raise SafetyError("--workdir must not be inside an exchange root or the recovery root")
    for system in ("/var/lib/docker", "/proc", "/sys", "/dev", "/etc", "/boot", "/usr", "/bin", "/sbin"):
        if text == system or text.startswith(system + "/"):
            raise SafetyError(f"--workdir must not be under {system}")
    return resolved


def _mounts(service: dict) -> list[dict]:
    mounts = service.get("volumes") or []
    if not isinstance(mounts, list) or not all(isinstance(m, dict) for m in mounts):
        raise SafetyError("unexpected volume syntax in rendered configuration")
    return mounts


def check_rendered(rendered: object, project: str, *, deploy_dir: Path, params_dir: str) -> dict:
    """Validate `docker compose config --format json` for the benchmark project.

    Returns a summary of what the project would create and run.
    """
    check_project(project)
    if not isinstance(rendered, dict):
        raise SafetyError("rendered configuration is not an object")
    if rendered.get("name") != project:
        raise SafetyError(f"rendered project name {rendered.get('name')!r} is not {project!r}")
    services = rendered.get("services")
    if not isinstance(services, dict) or set(services) != SERVICES:
        found = sorted(services) if isinstance(services, dict) else services
        raise SafetyError(f"rendered services must be exactly {sorted(SERVICES)}, found {found}")
    volumes = rendered.get("volumes") or {}
    if not isinstance(volumes, dict) or set(volumes) != VOLUME_KEYS:
        raise SafetyError(f"rendered volumes must be exactly {sorted(VOLUME_KEYS)}")
    for key, spec in volumes.items():
        if not isinstance(spec, dict) or spec.get("external") or spec.get("name") != f"{project}_{key}":
            raise SafetyError(f"volume {key} must be a new {project}_{key} volume, not external or foreign")
        if spec.get("driver") not in (None, "local") or spec.get("driver_opts"):
            raise SafetyError(f"volume {key} must use the default local driver without options")
    networks = rendered.get("networks") or {}
    if set(networks) != {"internal"} or networks["internal"].get("internal") is not True \
            or networks["internal"].get("name") != f"{project}_internal" or networks["internal"].get("external"):
        raise SafetyError("the only network must be the project's own internal network")
    zebra_config = str((deploy_dir / "zebra" / "regtest-config.toml").resolve())
    limits = {}
    for name, service in services.items():
        if not isinstance(service, dict):
            raise SafetyError(f"service {name} is malformed")
        for key in HOST_ACCESS_KEYS:
            if service.get(key):
                raise SafetyError(f"service {name} sets {key}; a benchmark service may not")
        if set(service.get("networks") or {}) != {"internal"}:
            raise SafetyError(f"service {name} must join only the internal network")
        for mount in _mounts(service):
            kind, source, target = mount.get("type"), mount.get("source"), mount.get("target")
            if kind == "volume":
                if source not in VOLUME_KEYS:
                    raise SafetyError(f"service {name} mounts foreign volume {source!r}")
            elif kind == "bind":
                allowed = ((name == "zebra" and source == zebra_config and target == ZEBRA_CONFIG_TARGET)
                           or (name == "params" and source == params_dir and target == PARAMS_TARGET))
                if not allowed or mount.get("read_only") is not True:
                    raise SafetyError(f"service {name} has an unexpected or writable bind mount {source!r}")
            else:
                raise SafetyError(f"service {name} has unsupported mount type {kind!r}")
        if name != "zebra":
            if service.get("user") != ENGINE_USER or service.get("read_only") is not True:
                raise SafetyError(f"engine service {name} must run as {ENGINE_USER} with a read-only root")
            if (service.get("environment") or {}).get("ZWA_PRIVATE_EXCHANGE_ROOT") != ENGINE_ROOT:
                raise SafetyError(f"engine service {name} must use the exchange root {ENGINE_ROOT}")
        if "ALL" not in (service.get("cap_drop") or []):
            raise SafetyError(f"service {name} must drop all capabilities")
        if name in SETUP_SERVICES and service.get("profiles") != ["setup"]:
            raise SafetyError(f"one-shot service {name} must stay in the setup profile")
        limits[name] = {"memLimit": service.get("mem_limit"), "cpus": service.get("cpus")}
    for name in LONG_RUNNING:
        if not limits[name]["memLimit"]:
            raise SafetyError(f"service {name} has no memory limit")
    engine_images = {services[name].get("image") for name in SERVICES - {"zebra"}}
    if len(engine_images) != 1:
        raise SafetyError("engine services must share one engine image")
    return {"project": project, "services": sorted(services), "volumes": sorted(v["name"] for v in volumes.values()),
            "network": networks["internal"]["name"], "limits": limits,
            "engineImage": engine_images.pop(), "zebraImage": services["zebra"].get("image"),
            "zebraEntrypoint": services["zebra"].get("entrypoint")}


def project_resources_absent(listing: dict[str, list[str]], project: str) -> None:
    """Refuse to prepare unless no container, volume or network of the project exists yet."""
    found = {kind: names for kind, names in listing.items() if names}
    if found:
        raise SafetyError(f"project {project} already has Docker resources {found}; a benchmark always "
                          "starts on new volumes (use a new zwa-bench-* project name)")


def check_container_mounts(service: str, mounts: object, project: str) -> None:
    """A running benchmark container may mount only its own project's volumes."""
    if not isinstance(mounts, list):
        raise SafetyError(f"cannot read mounts of {service}")
    for mount in mounts:
        if not isinstance(mount, dict):
            raise SafetyError(f"cannot read mounts of {service}")
        if mount.get("Type") == "volume" and not str(mount.get("Name", "")).startswith(f"{project}_"):
            raise SafetyError(f"{service} mounts foreign volume {mount.get('Name')!r}")
