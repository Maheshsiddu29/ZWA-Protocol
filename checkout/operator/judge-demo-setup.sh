#!/usr/bin/env bash
# Build a fresh, disposable judge-demo exchange root (CUSTODIAL_DEMO_POLICY)
# on the macOS developer machine: a new pinned Zebra regtest chain, a demo
# dealer wallet holding three issued experimental instruments, a fee sponsor,
# and the dealer's listings. No judge wallet exists until a judge starts a
# session. This never touches the protected Phase 4E root.
set -euo pipefail

PRODUCT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
NODE_BIN="${NODE_BIN:-$(command -v node)}"
TOOL_DIR="${TX_TOOL_DIR:-/private/tmp/zwa4c1-tx-tool}"
test "$(git -C "$TOOL_DIR" rev-parse HEAD)" = 16052323904be8efbb9e0f92d059fa57c9198f8a || { echo "tx-tool revision mismatch" >&2; exit 2; }
BIN="$TOOL_DIR/target/release/zcash_tx_tool"
ROOT="${ROOT:?set a new /private/tmp/zwa4e-judge-* directory}"
PORT="${PORT:?set an unused loopback RPC port}"
CONTAINER="${CONTAINER:?set a new container name}"
PARAMS_DATA_HOME="${PARAMS_DATA_HOME:?set to the XDG data home containing ZcashParams}"
UNITS="${UNITS:-1000}"
CAPACITY="${CAPACITY:-900}"
PRICES="${PRICES:-zwa-gold-reference=1000 zwa-treasury-style=1250 zwa-credit-participation=850}"
SCHEMA="$TOOL_DIR/migrations/wallet_init/up.sql"
IMAGE=sha256:540a3429ef14ff3c575258255e4310951606d16c86ec5e1c37e285ea95178185

[[ "$ROOT" =~ ^/private/tmp/zwa4e-judge-[A-Za-z0-9-]+$ ]] || { echo 'ROOT must be /private/tmp/zwa4e-judge-*' >&2; exit 2; }
test ! -e "$ROOT" || { echo 'ROOT already exists; refusing to reuse or overwrite it' >&2; exit 2; }
[[ "$UNITS" =~ ^[0-9]+$ && "$CAPACITY" =~ ^[0-9]+$ && "$CAPACITY" -le "$UNITS" ]] || { echo 'invalid UNITS/CAPACITY' >&2; exit 2; }
test -x "$BIN" && test -f "$SCHEMA" && test -x "$NODE_BIN"
if ! (cd "$PARAMS_DATA_HOME/ZcashParams" && shasum -a 256 -c > /dev/null <<'CHECKSUMS'
8e48ffd23abb3a5fd9c5589204f32d9c31285a04b78096ba40a79b75677efc13  sapling-spend.params
2f0ebbcbb9bb0bcffe95a397e7eba89c29eb4dde6191c339db88570e3f3fb0e4  sapling-output.params
CHECKSUMS
); then
    echo "trusted proving parameter checksum mismatch in $PARAMS_DATA_HOME/ZcashParams" >&2
    exit 2
fi
test "$(docker image inspect --format '{{.Id}}' "$IMAGE")" = "$IMAGE"
if docker inspect "$CONTAINER" > /dev/null 2>&1; then
    echo "container $CONTAINER already exists; choose a new name" >&2; exit 2
fi

umask 077
py() { PYTHONPATH="$PRODUCT_DIR" python3 "$@"; }
py -m exchange.persistent_chain start --root "$ROOT" --container "$CONTAINER" --port "$PORT"
mkdir -p "$ROOT"/{seller,coordinator,exchange,logs,profiles,demo-wallets}
SELLER="$ROOT/seller"; COORD="$ROOT/coordinator"; X="$ROOT/exchange"; PROFILES="$ROOT/profiles"
for home in "$SELLER" "$COORD"; do sqlite3 "$home/wallet.sqlite" < "$SCHEMA"; done

profile() {
    local name=$1; shift
    {
        echo '(version 1)'; echo '(allow default)'
        for denied in "$@"; do echo "(deny file-read* file-write* (subpath \"$denied\"))"; done
    } > "$PROFILES/$name.sb"
}
# Every profile excludes every other seed home, including all judge wallets.
profile seller "$ROOT/buyer" "$COORD" "$ROOT/demo-wallets"
profile coordinator "$ROOT/buyer" "$SELLER" "$ROOT/demo-wallets"

run_role() {
    local home=$1 prof=$2 status; shift 2
    set +e
    (cd "$home" && env -i PATH=/usr/bin:/bin HOME="$home" \
        XDG_DATA_HOME="$PARAMS_DATA_HOME" ZCASH_NODE_PORT="$PORT" \
        DATABASE_URL="$home/wallet.sqlite" \
        sandbox-exec -f "$PROFILES/$prof.sb" "$BIN" "$@") 2>&1 | tee -a "$ROOT/logs/setup.log" | grep -E '^PHASE35(F|G)_'
    status=${PIPESTATUS[0]}
    set -e
    return "$status"
}
seller() { run_role "$SELLER" seller wallet-signer --role seller --home "$SELLER" "$@"; }
coord() { run_role "$COORD" coordinator purchase-coordinator --home "$COORD" "$@"; }
rpc() {
    curl -fsS --max-time 10 -H 'content-type: application/json' \
        --data "{\"jsonrpc\":\"1.0\",\"id\":\"zwa-judge\",\"method\":\"$1\",\"params\":$2}" \
        "http://127.0.0.1:$PORT"
}

seller init; coord init
for attempt in "coordinator:$SELLER/seed.hex" "seller:$COORD/sponsor-seed.hex"; do
    prof=${attempt%%:*}; path=${attempt#*:}
    if sandbox-exec -f "$PROFILES/$prof.sb" /bin/cat "$path" > /dev/null 2>&1; then
        echo "isolation failed: $prof can read $path" >&2; exit 1
    fi
done

coord mine-empty --count 100
mkdir -p "$X/instruments"
for instrument in zwa-gold-reference zwa-treasury-style zwa-credit-participation; do
    seller issue --description "ZWA-EXPERIMENTAL-$instrument" --amount "$UNITS" --out "$X/instruments/$instrument-issuance.json"
    coord mine-issuance --issuance "$X/instruments/$instrument-issuance.json"
    asset=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["asset_base"])' "$X/instruments/$instrument-issuance.json")
    txid=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["txid"])' "$X/instruments/$instrument-issuance.json")
    rpc getrawtransaction "[\"$txid\",1]" > "$X/instruments/$instrument-chain.json"
    seller balances --asset "$asset" | grep '^PHASE35F_BALANCES ' > "$X/instruments/$instrument-inventory.log"
done
py -m exchange.operator_manifest "$X/instruments" "$X/instrument-registry.json" --expected-units "$UNITS"
# Pinned Zebra drops its newest ~99 blocks on restart; make issuance durable,
# then anchor the chain identity below the volatile window.
coord mine-empty --count 110
py -m exchange.persistent_chain checkpoint --root "$ROOT" --container "$CONTAINER" --port "$PORT" > "$X/issuance-chain-anchor.json"
py -m exchange.config init --root "$ROOT" --container "$CONTAINER" --port "$PORT" \
    --approval-mode CUSTODIAL_DEMO_POLICY --params-data-home "$PARAMS_DATA_HOME" \
    --tx-tool-dir "$TOOL_DIR" --node-bin "$NODE_BIN" > /dev/null
price_args=()
for entry in $PRICES; do price_args+=(--price "$entry"); done
py -m exchange.demo_setup --root "$ROOT" --capacity "$CAPACITY" "${price_args[@]}" > "$X/demo-listings.json"
echo "JUDGE_DEMO_ROOT_READY root=$ROOT port=$PORT container=$CONTAINER units=$UNITS capacity=$CAPACITY"
