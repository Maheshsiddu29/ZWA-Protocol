#!/usr/bin/env bash
# Complete the approved existing gold order on the preserved Phase 4E chain.
# Both wallet signers are independent, disposable and SCRIPTED_TEST_MODE only.
set -euo pipefail

PRODUCT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT=/private/tmp/zwa4e-phase4e-b
BACKUP=/private/tmp/zwa4e-phase4e-b-owner-approved-backup-20261012
ORDER=ec27f52d2e7ad69287f7d4d4357025af
PORT=18321
CONTAINER=zwa-phase4e-b
TOOL_DIR=/private/tmp/zwa4c1-tx-tool
BIN="$TOOL_DIR/target/release/zcash_tx_tool"
NODE_BIN="${NODE_BIN:-$(command -v node)}"
PARAMS_DATA_HOME="${PARAMS_DATA_HOME:-/Users/mahesh/Desktop/Colosseum/ZWA/zsa-feasibility/.local/share}"
X="$ROOT/exchange/orders/$ORDER"
QUOTE="$ROOT/exchange/order-quotes/gold-1"
ASSET=da11b98079a035182d74c4f9825999ff604b301274f757de8c3da88c55708890
export PYTHONPATH="$PRODUCT_DIR"
umask 077

test "${SCRIPTED_TEST_MODE:-}" = 1 || { echo 'SCRIPTED_TEST_MODE=1 required' >&2; exit 2; }
test "$(git -C "$TOOL_DIR" rev-parse HEAD)" = 16052323904be8efbb9e0f92d059fa57c9198f8a
test -x "$BIN" && test -x "$NODE_BIN"
test -f "$BACKUP/backup-manifest.json" && test -f "$ROOT/chain-identity.json"
python3 - "$BACKUP" "$ROOT" <<'PY'
import hashlib,json,sys
from pathlib import Path
backup,root=map(Path,sys.argv[1:])
m=json.loads((backup/'backup-manifest.json').read_text())
assert m['source']==str(root) and m['snapshot']==str(backup)
assert hashlib.sha256((root/'chain-identity.json').read_bytes()).hexdigest()==m['sha256']['chain-identity.json']
assert json.loads((root/'chain-identity.json').read_text())['lastObservedHeight']==1114
PY
for parameter in sapling-spend.params sapling-output.params; do
    test -r "$PARAMS_DATA_HOME/ZcashParams/$parameter"
done
(cd "$PARAMS_DATA_HOME/ZcashParams" && shasum -a 256 -c > /dev/null <<'CHECKSUMS'
8e48ffd23abb3a5fd9c5589204f32d9c31285a04b78096ba40a79b75677efc13  sapling-spend.params
2f0ebbcbb9bb0bcffe95a397e7eba89c29eb4dde6191c339db88570e3f3fb0e4  sapling-output.params
CHECKSUMS
)
test ! -e "$X" || { echo 'order work directory already exists; inspect and recover manually' >&2; exit 2; }
mkdir -m 700 -p "$X"

run_role() {
    local role=$1 profile=$2 log=$3; shift 3
    local home="$ROOT/$role"
    local raw="$log.raw"
    (cd "$home" && env -i PATH=/usr/bin:/bin HOME="$home" \
        XDG_DATA_HOME="$PARAMS_DATA_HOME" ZCASH_NODE_PORT="$PORT" \
        DATABASE_URL="$home/wallet.sqlite" \
        sandbox-exec -f "$ROOT/profiles/$profile.sb" "$BIN" \
        wallet-signer --role "$role" --home "$home" "$@") > "$raw" 2>&1
    rg '^PHASE35F_' "$raw" > "$log"
}
run_coord() {
    (cd "$ROOT/coordinator" && env -i PATH=/usr/bin:/bin HOME="$ROOT/coordinator" \
        XDG_DATA_HOME="$PARAMS_DATA_HOME" ZCASH_NODE_PORT="$PORT" \
        DATABASE_URL="$ROOT/coordinator/wallet.sqlite" \
        sandbox-exec -f "$ROOT/profiles/coordinator.sb" "$BIN" \
        purchase-coordinator --home "$ROOT/coordinator" "$@")
}
COORD_PID=''
cleanup() { if test -n "$COORD_PID"; then kill "$COORD_PID" 2>/dev/null || true; fi; }
trap cleanup EXIT

# Independently rescan spendable wallet state before proof and signature release.
run_role buyer buyer "$X/buyer-before.log" balances --asset "$ASSET"
run_role seller seller "$X/seller-before.log" balances --asset "$ASSET"
python3 -m exchange.order_execution preflight > "$X/preflight.json"
echo 'PHASE4E_B_PREFLIGHT_PASS: chain, quote, expiry, reservation, wallet scans'

"$NODE_BIN" "$PRODUCT_DIR/checkout/authorization/native_zec_v2.js" generate \
    "$QUOTE/terms.json" "$QUOTE/session-result.json" \
    "$ROOT/exchange/instruments/zwa-gold-reference-issuance.json" \
    "$X/authorization" > "$X/proof-generation.log" 2>&1
python3 -m exchange.order_execution proof --node "$NODE_BIN" > "$X/proof-record.json"
run_role buyer buyer "$X/buyer-review.log" review --terms "$QUOTE/terms.json"
run_role seller seller "$X/seller-review.log" review --terms "$QUOTE/terms.json"
python3 -m exchange.order_execution authorize > "$X/trade-authorization-record.json"
echo 'PHASE4E_B_PROOF_VERIFIED: exact trade and authenticated disposable roots'

run_coord execute-live --terms "$QUOTE/terms.json" --socket "$X/approval.sock" \
    --wait-secs 300 --out "$X/receipt.json" --session-result "$QUOTE/session-result.json" \
    --trust "$X/authorization/trust.json" \
    --authorization "$X/authorization/authorization.json" \
    --authorization-verifier "$PRODUCT_DIR/checkout/authorization/native_zec_v2.js" \
    --authorization-node "$NODE_BIN" --authorization-result "$X/authorization-result.json" \
    > "$X/coordinator.log.raw" 2>&1 &
COORD_PID=$!
for _ in $(seq 1 200); do
    test -S "$X/approval.sock" && break
    kill -0 "$COORD_PID" 2>/dev/null || { echo 'coordinator exited before approval socket' >&2; exit 1; }
    sleep 0.05
done
test -S "$X/approval.sock"

echo 'SCRIPTED_TEST_MODE: independent buyer test signer approves exact quote'
run_role buyer buyer "$X/buyer-signer.log" authorize-live --terms "$QUOTE/terms.json" \
    --approve acc0dc68bc573e71e72e076b3b1ec83455cf0186587b373ed9e0b98bdafa07c0 \
    --socket "$X/approval.sock" --scripted-test-mode
python3 -m exchange.order_execution buyer > "$X/buyer-approval-record.json"
echo 'SCRIPTED_TEST_MODE: independent seller test signer approves exact quote'
run_role seller seller "$X/seller-signer.log" authorize-live --terms "$QUOTE/terms.json" \
    --approve acc0dc68bc573e71e72e076b3b1ec83455cf0186587b373ed9e0b98bdafa07c0 \
    --socket "$X/approval.sock" --scripted-test-mode
python3 -m exchange.order_execution seller > "$X/seller-approval-record.json"
wait "$COORD_PID"
COORD_PID=''
rg '^PHASE35G_EXECUTED ' "$X/coordinator.log.raw" > "$X/coordinator.log"
python3 -m exchange.order_execution submitted > "$X/submission-record.json"
python3 -m exchange.order_execution included > "$X/inclusion-record.json"
echo 'PHASE4E_B_INCLUDED: genuine proof-gated checkout on protected chain'

run_role buyer buyer "$X/buyer-after.log" balances --asset "$ASSET"
run_role seller seller "$X/seller-after.log" balances --asset "$ASSET"
python3 -m checkout.scans "$QUOTE/session-result.json" "$X/public-scans.json" \
    "$X/buyer-before.log" "$X/buyer-after.log" \
    "$X/seller-before.log" "$X/seller-after.log"
python3 -m exchange.order_execution reconciled > "$X/reconciliation-record.json"
python3 -m exchange.order_execution receipt > "$X/receipt-record.json"

# Finalize the new block before restart; the pinned Zebra drops volatile tips.
run_coord mine-empty --count 1100 > "$X/finality-mining.log" 2>&1
python3 -m exchange.persistent_chain checkpoint --root "$ROOT" --container "$CONTAINER" \
    --port "$PORT" > "$X/post-trade-anchor.json"
python3 -m exchange.persistent_chain stop --root "$ROOT" --container "$CONTAINER" \
    --port "$PORT" > "$X/restart-stop.json"
python3 -m exchange.persistent_chain start --root "$ROOT" --container "$CONTAINER" \
    --port "$PORT" > "$X/restart-start.json"
ROOT="$ROOT" PORT="$PORT" CONTAINER="$CONTAINER" \
    PARAMS_DATA_HOME="$PARAMS_DATA_HOME" \
    "$PRODUCT_DIR/checkout/operator/phase4e-a-rescan.sh" > "$X/post-restart-rescan.log"
python3 - "$ROOT" "$X" <<'PY'
import json, pathlib, sys
root,x=map(pathlib.Path,sys.argv[1:])
receipt=json.loads((x/'order-receipt.json').read_text())
assert receipt['approvalMode']=='SCRIPTED_TEST_MODE'
latest=max((root/'exchange/rescans').glob('scan.*/portfolio.json'),key=lambda p:p.stat().st_mtime_ns)
portfolio=json.loads(latest.read_text())
gold=next(h for h in portfolio['holdings'] if h['instrumentId']=='zwa-gold-reference')
assert (gold['buyerUnits'],gold['sellerUnits'])==(8,92)
assert portfolio['sellerNativeZat']==32000
assert any(r['txid']==receipt['txid'] for r in portfolio['receipts'])
print('PHASE4E_B_COMPLETE_SCRIPTED_TEST_MODE txid='+receipt['txid']+' height='+str(receipt['height']))
PY
