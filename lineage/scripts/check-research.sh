#!/bin/bash
# Optional frozen differential matrix; requires the exact sibling research HEAD.
set -euo pipefail
cd "$(dirname "$0")/.."
export CARGO_HOME="${CARGO_HOME:-$PWD/../../zsa-feasibility/.cargo-home}"
[ "$(git -C ../../ZWA-Lineage-Research rev-parse HEAD)" = 3e7115d0e4c2f1cf36355f028447fc1b58290583 ]
scripts/verify-r2b-l1-source.py
scripts/verify-layer-b-core-source.sh
cargo test --manifest-path research-tests/Cargo.toml --offline --locked
python3 scripts/build-phase-2-matrix.py --check
if [ -n "${LAYER_B_EVIDENCE_DIR:-}" ]; then python3 scripts/build-phase-2-matrix.py; fi
