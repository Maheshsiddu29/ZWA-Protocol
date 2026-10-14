"""Evidence layout, secret redaction, and the per-project transaction budget.

    <workdir>/private/          0700  bench.env (portal token); never evidence
    <workdir>/evidence/         0700  everything an operator reviews
        plan-<time>.json        a read-only plan
        manifest.json           the prepared project: images, revision, resources
        budget/<run>.json       one reservation per level run (counts toward the budget)
        runs/<run>/             run.json, samples.jsonl, events.jsonl,
                                probe-before.json, probe-after.json,
                                summary.json, report.md

Files are created exclusively and never overwritten or deleted by the
benchmark. Every record passes through `Redactor` before it is written, and
`scan` re-reads the evidence to prove no registered secret reached it.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time

SECRET_KEY = re.compile(r"(?i)(session|token|secret|password|passphrase|seed|mnemonic|private|spend(ing)?_?key|"
                        r"viewing_?key|cookie|csrf|authorization|bearer|client_?hash|credential)")
# Public identifiers whose names would otherwise match the pattern above.
PUBLIC_KEYS = frozenset({"sessionOutcome", "authorizationSha256", "exactTradeAuthorization",
                         "credentialAuthorityAuthentication", "portalTokenConfigured"})
# Encodings of Zcash key material and generic private keys. None is ever expected in evidence.
SECRET_PATTERNS = (re.compile(r"secret-extended-key-[a-z]+1[0-9a-z]{20,}"),
                   re.compile(r"\buview[a-z]*1[0-9a-z]{40,}"), re.compile(r"\burview[a-z]*1[0-9a-z]{40,}"),
                   re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
                   re.compile(r"(?i)\"(seed|mnemonic|spendingKey|privateKey)\"\s*:"))
REDACTED = "[REDACTED]"


class EvidenceError(RuntimeError):
    pass


class Redactor:
    """Key-based and value-based redaction. Values registered here never reach evidence."""

    def __init__(self):
        self._values: set[str] = set()
        self._lock = threading.Lock()

    def register(self, value: str) -> str:
        if not isinstance(value, str) or len(value) < 8:
            raise EvidenceError("refusing to register a short secret (it would redact ordinary text)")
        with self._lock:
            self._values.add(value)
        return value

    @property
    def values(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._values)

    def text(self, value: str) -> str:
        for secret in self.values:
            if secret in value:
                value = value.replace(secret, REDACTED)
        for pattern in SECRET_PATTERNS:
            value = pattern.sub(REDACTED, value)
        return value

    def __call__(self, value):
        if isinstance(value, dict):
            return {key: (REDACTED if isinstance(key, str) and key not in PUBLIC_KEYS and SECRET_KEY.search(key)
                          else self(item)) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self(item) for item in value]
        if isinstance(value, str):
            return self.text(value)
        return value


def dumps(data) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def write_new(path: Path, text: str) -> str:
    """Create a file exclusively (0600) and return its sha256. Never overwrites."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "w") as out:
        out.write(text)
        out.flush()
        os.fsync(out.fileno())
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class JsonLines:
    """Append-only JSONL stream created exclusively; every record redacted."""

    def __init__(self, path: Path, redact: Redactor):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND |
                             getattr(os, "O_NOFOLLOW", 0), 0o600)
        self.path = path
        self._out = os.fdopen(descriptor, "w")
        self._redact = redact
        self._lock = threading.Lock()
        self.count = 0

    def write(self, record: dict) -> None:
        line = dumps(self._redact(record)) + "\n"
        with self._lock:
            self._out.write(line)
            self._out.flush()
            self.count += 1
            if self.count % 30 == 0:
                os.fsync(self._out.fileno())

    def close(self) -> None:
        with self._lock:
            if not self._out.closed:
                self._out.flush()
                os.fsync(self._out.fileno())
                self._out.close()


class Workdir:
    def __init__(self, root: Path):
        self.root = root
        self.private = root / "private"
        self.evidence = root / "evidence"

    def ensure(self) -> None:
        for path in (self.root, self.private, self.evidence, self.evidence / "budget", self.evidence / "runs"):
            path.mkdir(mode=0o700, exist_ok=True)
            if path.is_symlink() or not path.is_dir():
                raise EvidenceError(f"{path} must be a directory, not a symlink")
            os.chmod(path, 0o700)

    @property
    def env_file(self) -> Path:
        return self.private / "bench.env"

    @property
    def correlations(self) -> Path:
        """Protected purchase correlation records (session hash and idempotency key); never evidence."""
        return self.private / "correlations"

    @property
    def manifest_path(self) -> Path:
        return self.evidence / "manifest.json"

    def manifest(self) -> dict:
        path = self.manifest_path
        if path.is_symlink() or not path.is_file():
            raise EvidenceError("this workdir has no prepared benchmark project (run prepare first)")
        return json.loads(path.read_text())

    def new_run(self, level: int) -> tuple[str, Path]:
        run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-L{level}-" + os.urandom(3).hex()
        path = self.evidence / "runs" / run_id
        path.mkdir(mode=0o700)
        return run_id, path

    def run_dir(self, run_id: str) -> Path:
        if not re.fullmatch(r"\d{8}T\d{6}Z-L[0-3]-[0-9a-f]{6}", run_id):
            raise EvidenceError("invalid run id")
        path = self.evidence / "runs" / run_id
        if path.is_symlink() or not path.is_dir():
            raise EvidenceError(f"no such run {run_id}")
        return path

    # ------------------------------------------------------------ budget

    def budget_used(self) -> int:
        """Purchases reserved by every run so far, whatever their outcome."""
        used = 0
        for path in sorted((self.evidence / "budget").glob("*.json")):
            if path.is_symlink():
                raise EvidenceError("budget entry is a symlink")
            entry = json.loads(path.read_text())
            if type(entry.get("transactions")) is not int or entry["transactions"] < 0:
                raise EvidenceError(f"malformed budget entry {path.name}")
            used += entry["transactions"]
        return used

    def reserve_budget(self, run_id: str, level: int, transactions: int) -> None:
        write_new(self.evidence / "budget" / f"{run_id}.json",
                  dumps({"runId": run_id, "level": level, "transactions": transactions,
                         "reservedAt": round(time.time(), 3)}) + "\n")


def scan(directory: Path, secrets: frozenset[str] | set[str]) -> list[str]:
    """Paths of evidence files containing a registered secret or a key-material pattern."""
    findings = []
    for base, dirs, files in os.walk(directory):
        dirs.sort()
        for name in sorted(files):
            path = Path(base) / name
            if path.is_symlink():
                findings.append(f"{path}: symlink in evidence")
                continue
            text = path.read_text(errors="replace")
            if any(secret and secret in text for secret in secrets):
                findings.append(f"{path}: contains a registered secret")
            for pattern in SECRET_PATTERNS:
                if pattern.search(text):
                    findings.append(f"{path}: matches {pattern.pattern[:40]}")
    return findings
