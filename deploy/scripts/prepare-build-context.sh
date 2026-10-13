#!/usr/bin/env bash
# Archive the pinned transaction tool commit for the engine image build.
# The quarantined zcutil/fetch-params.sh is excluded and never executed.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
TOOL_DIR="${TX_TOOL_DIR:-/private/tmp/zwa4c1-tx-tool}"
REVISION=16052323904be8efbb9e0f92d059fa57c9198f8a
git -C "$TOOL_DIR" cat-file -e "$REVISION^{commit}"
mkdir -p "$REPO/deploy/build"
git -C "$TOOL_DIR" archive --format=tar "$REVISION" -- . ':(exclude)zcutil/fetch-params.sh' \
    > "$REPO/deploy/build/tx-tool.tar"
if tar -tf "$REPO/deploy/build/tx-tool.tar" | grep -q 'fetch-params.sh'; then
    echo "archive unexpectedly contains fetch-params.sh" >&2; exit 1
fi
SUM=$(shasum -a 256 "$REPO/deploy/build/tx-tool.tar" | cut -d' ' -f1)
echo "TX_TOOL_SHA256=$SUM"
