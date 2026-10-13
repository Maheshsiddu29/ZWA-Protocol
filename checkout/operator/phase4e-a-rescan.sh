#!/usr/bin/env bash
# Refresh private portfolio evidence from the actual independent wallets.
# This never creates a trade, proof, or wallet approval.
set -euo pipefail

PRODUCT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
ROOT="${ROOT:?set existing /private/tmp/zwa4e-* session root}"
PORT="${PORT:-18320}"
CONTAINER="${CONTAINER:-zwa-phase4e-regtest}"
PARAMS_DATA_HOME="${PARAMS_DATA_HOME:?set trusted ZcashParams data home}"
TOOL_DIR="${TX_TOOL_DIR:-/private/tmp/zwa4c1-tx-tool}"
BIN="${BIN:-$TOOL_DIR/target/release/zcash_tx_tool}"
[[ "$ROOT" =~ ^/private/tmp/zwa4e-[A-Za-z0-9-]+$ ]] || { echo 'invalid ROOT' >&2; exit 2; }
test "$(git -C "$TOOL_DIR" rev-parse HEAD)" = 16052323904be8efbb9e0f92d059fa57c9198f8a
test -x "$BIN" && test -r "$ROOT/exchange/instrument-registry.json"
test -r "$ROOT/profiles/buyer.sb" && test -r "$ROOT/profiles/seller.sb"
STATE=$(PYTHONPATH="$PRODUCT_DIR" python3 -m exchange.persistent_chain status --root "$ROOT" --container "$CONTAINER" --port "$PORT")
python3 -c 'import json,sys; assert json.loads(sys.argv[1])["state"] in ("CHAIN_ACTIVE", "CHAIN_RESTARTED")' "$STATE" || {
    echo "chain is not verified active: $STATE" >&2; exit 2;
}
umask 077
mkdir -p "$ROOT/exchange/rescans"
SCAN_DIR=$(mktemp -d "$ROOT/exchange/rescans/scan.XXXXXX")
for instrument in zwa-gold-reference zwa-treasury-style zwa-credit-participation; do
    asset=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["asset_base"])' "$ROOT/exchange/instruments/$instrument-issuance.json")
    for role in buyer seller; do
        home="$ROOT/$role"
        (cd "$home" && env -i PATH=/usr/bin:/bin HOME="$home" \
            XDG_DATA_HOME="$PARAMS_DATA_HOME" ZCASH_NODE_PORT="$PORT" \
            DATABASE_URL="$home/wallet.sqlite" \
            sandbox-exec -f "$ROOT/profiles/$role.sb" "$BIN" \
            wallet-signer --role "$role" --home "$home" balances --asset "$asset") \
            2>&1 | rg '^PHASE35F_BALANCES ' | tee "$SCAN_DIR/$instrument-$role.log"
    done
done
PYTHONPATH="$PRODUCT_DIR" python3 -m exchange.portfolio_scans \
    "$ROOT/exchange" "$ROOT/chain-identity.json" "$SCAN_DIR/portfolio.json" \
    --scan-dir "$SCAN_DIR" --restart
echo "PHASE4E_A_PRIVATE_RESCAN=$SCAN_DIR/portfolio.json"
