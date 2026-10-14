"""The only path from the benchmark to Docker: an allowlist of command shapes.

Every Compose command names the benchmark project explicitly with `-p`, which
replaces the base file's `name: zwa-demo`, and uses the base file, the host's
platform override when given, and the benchmark override. Destructive or
global commands (prune, `down -v`, `rm -v`, kill, system-wide removal) have no
code path here: `_guard` refuses them before any process starts. Volume
removal exists only for the separate cleanup command, by exact name, after
the caller has checked the volume's project label.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess

from .safety import RUN_SERVICES, SETUP_SERVICES, SafetyError, check_project

SAFE_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
            "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY")
DOCKER_ALLOWED = {
    ("version",), ("info",), ("inspect",), ("image", "inspect"), ("ps",), ("volume", "ls"),
    ("volume", "inspect"), ("network", "ls"), ("volume", "rm"),
}
COMPOSE_ALLOWED = {"version", "config", "run", "up", "ps", "exec", "stop", "down", "images"}
FORBIDDEN_TOKENS = {"prune", "-v", "--volumes", "--rmi", "kill", "--remove-orphans", "-f", "--force",
                    "--privileged", "--user", "-u"}


@dataclass
class Result:
    code: int
    out: str
    err: str


class Docker:
    """Docker CLI with a fixed prefix and a minimal environment (no COMPOSE_* or ZWA_* leakage)."""

    def __init__(self, sudo: bool = False, runner=subprocess.run, popen=subprocess.Popen):
        self.prefix = ["sudo", "-n", "docker"] if sudo else ["docker"]
        self.runner = runner
        self.popen = popen
        self.env = {key: os.environ[key] for key in SAFE_ENV if key in os.environ}

    @staticmethod
    def _guard(args: tuple[str, ...]) -> None:
        if not args:
            raise SafetyError("empty docker command")
        if args[0] == "compose":
            return  # validated by Compose._guard, which builds every compose command
        head = tuple(args[:2]) if args[0] in ("image", "volume", "network", "container") else (args[0],)
        if head not in DOCKER_ALLOWED:
            raise SafetyError(f"docker {' '.join(head)} is not a benchmark command")
        if any(token in ("prune", "--force", "-f", "--volumes", "-v") for token in args[1:]):
            raise SafetyError("destructive docker flags are not available to the benchmark")

    def __call__(self, *args: str, input: bytes | None = None, timeout: int = 1800) -> Result:
        self._guard(args)
        done = self.runner([*self.prefix, *args], input=input, capture_output=True, env=self.env, timeout=timeout)
        out = done.stdout.decode(errors="replace") if isinstance(done.stdout, bytes) else (done.stdout or "")
        err = done.stderr.decode(errors="replace") if isinstance(done.stderr, bytes) else (done.stderr or "")
        return Result(done.returncode, out, err)

    def ok(self, *args: str, input: bytes | None = None, what: str = "docker command", timeout: int = 1800) -> str:
        result = self(*args, input=input, timeout=timeout)
        if result.code:
            raise SafetyError(f"{what} failed (exit {result.code}): {result.err.strip()[:400]}")
        return result.out

    def spawn(self, *args: str) -> subprocess.Popen:
        """A long-lived process with pipes (the portal relay)."""
        self._guard(args)
        return self.popen([*self.prefix, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=self.env, bufsize=0)


class Compose:
    """Compose commands for exactly one benchmark project."""

    def __init__(self, docker: Docker, *, project: str, deploy_dir: Path, env_file: Path,
                 platform_override: Path | None = None):
        self.docker = docker
        self.project = check_project(project)
        self.deploy_dir = deploy_dir
        self.env_file = env_file
        files = [deploy_dir / "compose.yaml"]
        if platform_override is not None:
            files.append(platform_override)
        files.append(deploy_dir / "benchmark" / "compose.bench.yaml")
        self.files = files

    def base(self) -> list[str]:
        argv = ["compose", "--project-directory", str(self.deploy_dir), "-p", self.project,
                "--env-file", str(self.env_file)]
        for path in self.files:
            argv += ["-f", str(path)]
        return argv

    def _guard(self, args: tuple[str, ...]) -> None:
        if not args or args[0] not in COMPOSE_ALLOWED:
            raise SafetyError(f"docker compose {args[:1]} is not a benchmark command")
        command, rest = args[0], args[1:]
        if any(token in FORBIDDEN_TOKENS for token in rest):
            raise SafetyError(f"refusing destructive or privileged compose arguments: {' '.join(rest)}")
        if command == "run" and (len(rest) != 2 or rest[0] != "--rm" or rest[1] not in SETUP_SERVICES):
            raise SafetyError("compose run is limited to the one-shot setup services with --rm")
        if command == "up":
            services = [token for token in rest if not token.startswith("-")]
            if rest[:1] != ("-d",) or not services or not set(services) <= {"zebra", *RUN_SERVICES}:
                raise SafetyError("compose up must name only zebra and the engine run services, detached")
        if command == "exec" and (len(rest) < 3 or rest[:2] != ("-T", "portal")):
            raise SafetyError("compose exec is limited to non-interactive commands in portal")
        if command == "down" and rest:
            raise SafetyError("compose down takes no arguments here (volumes are always kept)")

    def argv(self, *args: str, setup_profile: bool = False) -> tuple[str, ...]:
        self._guard(args)
        profile = ["--profile", "setup"] if setup_profile else []
        return (*self.base(), *profile, *args)

    def __call__(self, *args: str, setup_profile: bool = False, input: bytes | None = None,
                 timeout: int = 1800) -> Result:
        return self.docker(*self.argv(*args, setup_profile=setup_profile), input=input, timeout=timeout)

    def ok(self, *args: str, setup_profile: bool = False, input: bytes | None = None, what: str = "",
           timeout: int = 1800) -> str:
        result = self(*args, setup_profile=setup_profile, input=input, timeout=timeout)
        if result.code:
            raise SafetyError(f"{what or 'docker compose ' + args[0]} failed (exit {result.code}): "
                              f"{result.err.strip()[:400]}")
        return result.out

    def spawn_exec(self, *args: str) -> subprocess.Popen:
        return self.docker.spawn(*self.argv("exec", "-T", "portal", *args))
