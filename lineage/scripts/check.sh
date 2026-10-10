#!/bin/bash
# Offline validation of the lineage workspace: fmt, clippy (warnings denied),
# tests (unit, golden, adversarial, differential, doc), and a fixture pin check.
#
# Requires the sibling checkouts this workspace builds against:
#   ../../ZWA-Lineage-Research   (frozen R2B-L1 and the research oracles)
#   ../../zsa-feasibility/.cargo-home   (pinned offline cargo home)
#
# Usage: lineage/scripts/check.sh [evidence-dir]
#   evidence-dir (optional): where to write differential.jsonl
set -euo pipefail
cd "$(dirname "$0")/.."
here="$PWD"
export CARGO_HOME="${CARGO_HOME:-$here/../../zsa-feasibility/.cargo-home}"
[ -d "$CARGO_HOME/registry" ] || { echo "FAIL: pinned cargo home not found at $CARGO_HOME"; exit 1; }
[ -d ../../ZWA-Lineage-Research/r2c-h1d/r2b-l1 ] || { echo "FAIL: research checkout not found"; exit 1; }
if [ $# -ge 1 ]; then
  mkdir -p "$1"
  export LINEAGE_EVIDENCE_DIR="$(cd "$1" && pwd)"
fi

echo "== fixture pins"
(cd tests/fixtures && shasum -a 256 -c SHA256SUMS)

echo "== frozen Layer-B core provenance"
scripts/verify-layer-b-core-source.sh

echo "== cargo fmt --check"
cargo fmt --check

echo "== cargo clippy (all targets, -D warnings)"
cargo clippy --offline --locked --all-targets -- -D warnings

echo "== cargo test"
cargo test --offline --locked

if [ -n "${LAYER_B_EVIDENCE_DIR:-}" ]; then
  echo "== full classified Layer-B matrix"
  python3 scripts/build-phase-2-matrix.py
fi

echo "== Phase 2 differential inventory consistency"
python3 scripts/build-phase-2-matrix.py --check

echo "== lock unchanged"
git diff --exit-code -- Cargo.lock

echo "PASS"
