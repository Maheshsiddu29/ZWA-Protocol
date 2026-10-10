#!/bin/bash
# Offline validation of the lineage workspace: fmt, clippy (warnings denied),
# tests (unit, golden, adversarial, rooted, doc), and a fixture pin check.
#
# Requires a prepared Cargo cache; research differential tests are separate.
# Usage: lineage/scripts/check.sh
set -euo pipefail
cd "$(dirname "$0")/.."
here="$PWD"
export CARGO_HOME="${CARGO_HOME:-$here/../../zsa-feasibility/.cargo-home}"
[ -d "$CARGO_HOME/registry" ] || { echo "FAIL: pinned cargo home not found at $CARGO_HOME"; exit 1; }
echo "== fixture pins"
(cd tests/fixtures && shasum -a 256 -c SHA256SUMS)

echo "== pinned R2B-L1 source"
python3 scripts/verify-r2b-l1-source.py

echo "== frozen Layer-B core provenance"
scripts/verify-layer-b-core-source.sh

echo "== cargo fmt --check"
cargo fmt --all --check

echo "== cargo clippy (all targets, -D warnings)"
cargo clippy --offline --locked --workspace --all-targets -- -D warnings

echo "== cargo test"
cargo test --offline --locked --workspace

echo "== lock unchanged"
git diff --exit-code -- Cargo.lock

echo "PASS"
