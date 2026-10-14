#!/usr/bin/env python3
"""Entry point for the isolated benchmark harness; see benchmark/cli.py and docs/benchmark-runbook.md.

    python3 -I deploy/scripts/zwa-bench.py plan --project zwa-bench-<name> --workdir <dir> ...
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from benchmark.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
