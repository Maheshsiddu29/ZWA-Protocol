#!/bin/bash
# Verify that the compiled Layer-B core is exactly the deterministic extraction
# of the pinned frozen research source. Research is read, never modified.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 scripts/verify-layer-b-local.py
if [ -d ../../ZWA-Lineage-Research ]; then
  python3 scripts/materialize-layer-b-core.py --check
fi

if rg -n 'reqwest|hyper|TcpStream|rpc_call|pub fn capture|Client::|\.post\(' vendor/layer-b-core/src vendor/layer-b-core/Cargo.toml; then
  echo "FAIL: network/capture code found in production Layer-B core" >&2
  exit 1
fi
echo "PASS: production Layer-B core has no network client"
