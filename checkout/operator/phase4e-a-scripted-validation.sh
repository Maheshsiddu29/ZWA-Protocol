#!/usr/bin/env bash
# Phase 4E-A: three scripted purchases on one persistent experimental chain.
# The signers use --scripted-test-mode; this is never human approval evidence.
set -euo pipefail

PRODUCT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
AUTH_SCRIPT="$PRODUCT_DIR/checkout/authorization/native_zec_v2.js"
NODE_BIN="${NODE_BIN:-$(command -v node)}"
TOOL_DIR="${TX_TOOL_DIR:-/private/tmp/zwa4c1-tx-tool}"
test "$(git -C "$TOOL_DIR" rev-parse HEAD)" = 16052323904be8efbb9e0f92d059fa57c9198f8a || { echo "tx-tool revision mismatch" >&2; exit 2; }
BIN="${BIN:-$TOOL_DIR/target/release/zcash_tx_tool}"
ROOT="${ROOT:?set a persistent private /private/tmp/zwa4e-* directory}"
PORT="${PORT:-18320}"
API_PORT="${API_PORT:-18820}"
CONTAINER="${CONTAINER:-zwa-phase4e-regtest}"
IMAGE=sha256:540a3429ef14ff3c575258255e4310951606d16c86ec5e1c37e285ea95178185
PARAMS_DATA_HOME="${PARAMS_DATA_HOME:?set to the XDG data home containing ZcashParams}"
test "${SCRIPTED_TEST_MODE:-}" = 1 || { echo 'set SCRIPTED_TEST_MODE=1 for scripted validation' >&2; exit 2; }
SCHEMA="$TOOL_DIR/migrations/wallet_init/up.sql"

[[ "$ROOT" =~ ^/private/tmp/zwa4e-[A-Za-z0-9-]+$ ]] || { echo 'ROOT must be a direct /private/tmp/zwa4e-* directory' >&2; exit 2; }
test ! -e "$ROOT/buyer" && test ! -e "$ROOT/seller" && test ! -e "$ROOT/coordinator" || {
    echo 'wallet profiles already exist; refusing to reinitialize or overwrite them' >&2; exit 2;
}
test -f "$SCHEMA"
test -x "$BIN"
test -x "$NODE_BIN" || { echo "Node.js is required for Groth16 verification" >&2; exit 2; }
test -r "$AUTH_SCRIPT"
for parameter in sapling-spend.params sapling-output.params; do
    test -r "$PARAMS_DATA_HOME/ZcashParams/$parameter" || {
        echo "missing trusted proving parameter: $parameter in $PARAMS_DATA_HOME/ZcashParams" >&2
        exit 2
    }
done
if ! (cd "$PARAMS_DATA_HOME/ZcashParams" && shasum -a 256 -c > /dev/null <<'CHECKSUMS'
8e48ffd23abb3a5fd9c5589204f32d9c31285a04b78096ba40a79b75677efc13  sapling-spend.params
2f0ebbcbb9bb0bcffe95a397e7eba89c29eb4dde6191c339db88570e3f3fb0e4  sapling-output.params
CHECKSUMS
); then
    echo "trusted proving parameter checksum mismatch in $PARAMS_DATA_HOME/ZcashParams" >&2
    exit 2
fi
test "$(docker image inspect --format '{{.Id}}' "$IMAGE")" = "$IMAGE"
CHAIN_STATE=$(PYTHONPATH="$PRODUCT_DIR" python3 -m exchange.persistent_chain status --root "$ROOT" --container "$CONTAINER" --port "$PORT")
python3 -c 'import json,sys; assert json.loads(sys.argv[1])["state"] in ("CHAIN_ACTIVE", "CHAIN_RESTARTED")' "$CHAIN_STATE" || {
    echo "persistent chain unavailable: $CHAIN_STATE" >&2; exit 2;
}
python3 - "$API_PORT" <<'PY'
import socket, sys
for raw in sys.argv[1:]:
    port = int(raw)
    if not 1 <= port <= 65535:
        raise SystemExit('invalid loopback port')
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', port))
        except OSError as exc:
            raise SystemExit(f'loopback port {port} is unavailable: {exc}') from exc
PY
umask 077
mkdir -p "$ROOT"/{buyer,seller,coordinator,exchange,logs,profiles}
BUYER="$ROOT/buyer"; SELLER="$ROOT/seller"; COORD="$ROOT/coordinator"
X="$ROOT/exchange"; LOGS="$ROOT/logs"; PROFILES="$ROOT/profiles"
API_PID=''
COORD_PID=''
cleanup() {
    if test -n "$API_PID"; then kill "$API_PID" 2>/dev/null || true; fi
    if test -n "$COORD_PID"; then kill "$COORD_PID" 2>/dev/null || true; fi
}
trap cleanup EXIT
for home in "$BUYER" "$SELLER" "$COORD"; do
    sqlite3 "$home/wallet.sqlite" < "$SCHEMA"
done

profile() {
    local name=$1; shift
    {
        echo '(version 1)'; echo '(allow default)'
        for denied in "$@"; do echo "(deny file-read* file-write* (subpath \"$denied\"))"; done
    } > "$PROFILES/$name.sb"
}
profile buyer "$SELLER" "$COORD"
profile seller "$BUYER" "$COORD"
profile coordinator "$BUYER" "$SELLER"
profile buyer-audit "$SELLER"
profile seller-audit "$BUYER"

run_role() {
    local home=$1 prof=$2; shift 2
    local logfile status
    logfile=$(mktemp "$LOGS/$prof.XXXXXX")
    set +e
    (cd "$home" && env -i PATH=/usr/bin:/bin HOME="$home" \
        XDG_DATA_HOME="$PARAMS_DATA_HOME" ZCASH_NODE_PORT="$PORT" \
        DATABASE_URL="$home/wallet.sqlite" \
        sandbox-exec -f "$PROFILES/$prof.sb" "$BIN" "$@") \
        2>&1 | tee "$logfile" | rg '^PHASE35(F|G)_'
    status=${PIPESTATUS[0]}
    set -e
    return "$status"
}
buyer() { run_role "$BUYER" buyer wallet-signer --role buyer --home "$BUYER" "$@"; }
seller() { run_role "$SELLER" seller wallet-signer --role seller --home "$SELLER" "$@"; }
coord() { run_role "$COORD" coordinator purchase-coordinator --home "$COORD" "$@"; }
field() { sed -n "s/.*${1}=\([^ ]*\).*/\1/p" | head -1; }
rpc() {
    curl -fsS --max-time 10 -H 'content-type: application/json' \
        --data "{\"jsonrpc\":\"1.0\",\"id\":\"zwa35g\",\"method\":\"$1\",\"params\":$2}" \
        "http://127.0.0.1:$PORT"
}

rpc getblockcount '[]' > "$X/start-blockcount.json"
python3 - "$X/start-blockcount.json" <<'PY'
import json, sys
assert json.load(open(sys.argv[1]))['result'] == 0, 'fresh chain required for issuance'
PY

buyer init; seller init; coord init
for attempt in "coordinator:$BUYER/seed.hex" "coordinator:$SELLER/seed.hex" \
               "buyer:$SELLER/seed.hex" "seller:$BUYER/seed.hex" "buyer:$COORD/sponsor-seed.hex"; do
    prof=${attempt%%:*}; path=${attempt#*:}
    if sandbox-exec -f "$PROFILES/$prof.sb" /bin/cat "$path" > /dev/null 2>&1; then
        echo "isolation failed: $prof can read $path" >&2; exit 1
    fi
done

BUYER_ADDR=$(buyer address | field address)
SELLER_ADDR=$(seller address | field address)
coord mine-empty --count 100
coord fund-address --address "$BUYER_ADDR" --coinbase-height 1
mkdir -p "$X/instruments"
for instrument in zwa-gold-reference zwa-treasury-style zwa-credit-participation; do
    seller issue --description "ZWA-EXPERIMENTAL-$instrument" --amount 100 --out "$X/instruments/$instrument-issuance.json"
    coord mine-issuance --issuance "$X/instruments/$instrument-issuance.json"
    asset=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["asset_base"])' "$X/instruments/$instrument-issuance.json")
    txid=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["txid"])' "$X/instruments/$instrument-issuance.json")
    rpc getrawtransaction "[\"$txid\",1]" > "$X/instruments/$instrument-chain.json"
    seller balances --asset "$asset" | tee "$X/instruments/$instrument-inventory.log"
done
PYTHONPATH="$PRODUCT_DIR" python3 -m exchange.operator_manifest "$X/instruments" "$X/instrument-registry.json"
BASE_X=$X
PYTHONPATH="$PRODUCT_DIR" python3 -m exchange.persistent_chain checkpoint --root "$ROOT" --container "$CONTAINER" --port "$PORT" > "$BASE_X/issuance-chain-anchor.json"
mkdir -p "$BASE_X/trades"
mkdir -p "$COORD/authorization"
FEE_HEIGHT=2
for INSTRUMENT_ID in zwa-gold-reference zwa-treasury-style zwa-credit-participation; do
case "$INSTRUMENT_ID" in
    zwa-gold-reference) PRICE_ZAT=10000; QUANTITY=7 ;;
    zwa-treasury-style) PRICE_ZAT=12500; QUANTITY=5 ;;
    zwa-credit-participation) PRICE_ZAT=8500; QUANTITY=3 ;;
esac
X="$BASE_X/trades/$INSTRUMENT_ID"
mkdir -p "$X"
AUTH_DIR="$COORD/authorization/$INSTRUMENT_ID"
cp "$BASE_X/instruments/$INSTRUMENT_ID-issuance.json" "$X/issuance.json"
ASSET=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["asset_base"])' "$X/issuance.json")
buyer balances --asset "$ASSET" | tee "$X/buyer-before.log"
seller balances --asset "$ASSET" | tee "$X/seller-before.log"
coord prepare-sale --asset "$ASSET" --quantity "$QUANTITY" --price-zat "$PRICE_ZAT" \
    --buyer-address "$BUYER_ADDR" --seller-address "$SELLER_ADDR" \
    --fee-coinbase-height "$FEE_HEIGHT" --out "$X/terms.json" --session-result "$X/session-result.json"
observe() { PYTHONPATH="$PRODUCT_DIR" python3 -m checkout.observe "$X/session-result.json" "$X/public-events.json" "$1" --mode SCRIPTED_TEST_MODE; }
observe SESSION_PREPARED
HASH=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sessionId"])' "$X/session-result.json")
"$NODE_BIN" "$AUTH_SCRIPT" generate "$X/terms.json" "$X/session-result.json" \
    "$X/issuance.json" "$AUTH_DIR"
"$NODE_BIN" "$AUTH_SCRIPT" verify "$X/terms.json" "$HASH" \
    "$AUTH_DIR/trust.json" "$AUTH_DIR/authorization.json" \
    > "$X/preflight-authorization.json"
buyer review --terms "$X/terms.json"
seller review --terms "$X/terms.json"

PYTHONPATH="$PRODUCT_DIR" python3 -m checkout.api --demo --port "$API_PORT" \
    --rpc-port "$PORT" --session-result "$X/session-result.json" --event-result "$X/public-events.json" \
    --wallet-scans "$X/public-scans.json" \
    --authorization-result "$X/authorization-result.json" \
    > "$LOGS/api.log" 2>&1 &
API_PID=$!
wait_for_api() {
    local attempt exit_code
    for attempt in $(seq 1 100); do
        if ! kill -0 "$API_PID" 2>/dev/null; then
            set +e; wait "$API_PID"; exit_code=$?; set -e
            echo "checkout API exited before readiness (status $exit_code); see $LOGS/api.log" >&2
            tail -20 "$LOGS/api.log" >&2
            return 1
        fi
        if curl -fsS --max-time 1 "http://127.0.0.1:$API_PORT/v1/checkout/info" \
            > "$X/api-info.json" 2> "$LOGS/api-readiness-error" \
            && python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["version"] == 1 and d["network"] == "regtest" and d["signingAvailableOverHttp"] is False and d["availability"]["state"] == "AVAILABLE"' "$X/api-info.json" 2> /dev/null \
            && kill -0 "$API_PID" 2>/dev/null; then
            return 0
        fi
        sleep 0.1
    done
    echo "checkout API did not become ready on 127.0.0.1:$API_PORT after 100 probes; see $LOGS/api.log" >&2
    cat "$LOGS/api-readiness-error" >&2
    test ! -s "$X/api-info.json" || cat "$X/api-info.json" >&2
    tail -20 "$LOGS/api.log" >&2
    return 1
}
wait_for_api
curl -fsS "http://127.0.0.1:$API_PORT/v1/checkout/sessions/$HASH" > "$X/api-quote-ready.json"

coord execute-live --terms "$X/terms.json" --socket "$X/approval.sock" \
    --wait-secs 300 --out "$X/receipt.json" --session-result "$X/session-result.json" \
    --trust "$AUTH_DIR/trust.json" \
    --authorization "$AUTH_DIR/authorization.json" \
    --authorization-verifier "$AUTH_SCRIPT" --authorization-node "$NODE_BIN" \
    --authorization-result "$X/authorization-result.json" &
COORD_PID=$!
for _ in $(seq 1 200); do
    test -S "$X/approval.sock" && break
    sleep 0.05
done
test -S "$X/approval.sock"
observe AWAITING_BUYER_APPROVAL
echo "SCRIPTED_TEST_MODE: buyer test signer authorizing exact quote $HASH"
if ! buyer authorize-live --terms "$X/terms.json" --approve "$HASH" --socket "$X/approval.sock" --scripted-test-mode; then
    observe BUYER_APPROVAL_NOT_GRANTED
    exit 1
fi
observe BUYER_AUTHORIZED
observe AWAITING_SELLER_APPROVAL
echo "SCRIPTED_TEST_MODE: seller test signer authorizing exact quote $HASH"
if ! seller authorize-live --terms "$X/terms.json" --approve "$HASH" --socket "$X/approval.sock" --scripted-test-mode; then
    observe SELLER_APPROVAL_NOT_GRANTED
    exit 1
fi
observe SELLER_AUTHORIZED
if ! wait "$COORD_PID"; then
    COORD_PID=''
    observe EXECUTION_FAILED
    exit 1
fi
COORD_PID=''
observe TRANSACTION_SUBMITTED
observe TRANSACTION_INCLUDED
test ! -e "$X/approval.sock"
test ! -e "$X/buyer-auth.json"
test ! -e "$X/seller-auth.json"
test ! -e "$X/purchase.json"

TXID=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["txid"])' "$X/receipt.json")
HEIGHT=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["height"])' "$X/receipt.json")
rpc getblock "[\"$HEIGHT\",1]" > "$X/independent-block.json"
rpc getrawtransaction "[\"$TXID\",1]" > "$X/independent-transaction.json"
curl -fsS "http://127.0.0.1:$API_PORT/v1/checkout/sessions/$HASH" > "$X/api-pending.json"
buyer balances --asset "$ASSET" | tee "$X/buyer-after.log"
seller balances --asset "$ASSET" | tee "$X/seller-after.log"
PYTHONPATH="$PRODUCT_DIR" python3 -m checkout.scans "$X/session-result.json" "$X/public-scans.json" \
    "$X/buyer-before.log" "$X/buyer-after.log" "$X/seller-before.log" "$X/seller-after.log"
coord mine-empty --count 2
curl -fsS "http://127.0.0.1:$API_PORT/v1/checkout/sessions/$HASH" > "$X/api-depth3.json"
curl -fsS "http://127.0.0.1:$API_PORT/v1/checkout/sessions/$HASH/receipt" > "$X/api-receipt.json"

run_role "$BUYER" buyer-audit wallet-signer --role buyer --home "$BUYER" \
    audit-leaks --scan "$X" "$LOGS" "$COORD" "$TOOL_DIR/src" "$TOOL_DIR/scripts"
run_role "$SELLER" seller-audit wallet-signer --role seller --home "$SELLER" \
    audit-leaks --scan "$X" "$LOGS" "$COORD" "$TOOL_DIR/src" "$TOOL_DIR/scripts"

python3 - "$X" "$PRICE_ZAT" "$QUANTITY" <<'PY'
import json, pathlib, sys
x = pathlib.Path(sys.argv[1])
ready = json.loads((x / 'api-quote-ready.json').read_text())
pending = json.loads((x / 'api-pending.json').read_text())
depth3 = json.loads((x / 'api-depth3.json').read_text())
receipt = json.loads((x / 'api-receipt.json').read_text())
block = json.loads((x / 'independent-block.json').read_text())['result']
tx = json.loads((x / 'independent-transaction.json').read_text())['result']
assert ready['status'] == 'QUOTE_READY'
assert pending['status'] == 'PENDING_FINALITY' and pending['receipt']['confirmations'] == 1
assert depth3['status'] == 'FINALIZED' and depth3['receipt']['confirmations'] == 3
assert receipt['purchaseSettlement'] == 'ACCEPTED' and receipt['rootedLineage'] == 'UNSUPPORTED'
auth = depth3['authorization']
assert all(auth[key] == 'VERIFIED' for key in ('issuerAuthentication', 'issuanceProvenance', 'credentialAuthorityAuthentication', 'investorEligibility', 'exactTradeAuthorization'))
assert auth['quoteDigest'] == depth3['sessionId']
assert auth['rootedNoteLineage'] == 'UNSUPPORTED'
assert receipt['investorEligibility'] == 'VERIFIED'
assert receipt['txid'] in block['tx'] and block['hash'] == receipt['blockHash']
assert tx['height'] == receipt['height'] and len(tx['hex']) // 2 == receipt['txBytes']
assert pending['quote']['paymentAmount'] == int(sys.argv[2]) and pending['quote']['maximumTotalDebit'] == int(sys.argv[2])
assert pending['quote']['quantity'] == int(sys.argv[3])
assert pending['quote']['networkFee'] == 5000 and pending['quote']['feePayer'] == 'DISPOSABLE_REGTEST_SPONSOR'
print('PHASE4D_SCRIPTED_INTEGRATION_PASS txid=' + receipt['txid'] + ' height=' + str(receipt['height']))
PY
observe CONFIRMATION_DEPTH_OBSERVED
observe RECEIPT_AVAILABLE
kill "$API_PID"
wait "$API_PID" 2>/dev/null || true
API_PID=''
PYTHONPATH="$PRODUCT_DIR" python3 -m exchange.persistent_chain checkpoint --root "$ROOT" --container "$CONTAINER" --port "$PORT" > "$X/chain-anchor.json"
FEE_HEIGHT=$((FEE_HEIGHT + 1))
done

# This pinned Zebra persists finalized state but drops its recent non-finalized
# window on restart. Mine enough empty blocks to make the last purchase's
# anchored block durable before stopping; no additional wallet approval occurs.
# The checkpoint anchors at least 100 blocks below the tip, so it must follow
# this mining for the anchor to cover every accepted purchase.
coord mine-empty --count 1100
PYTHONPATH="$PRODUCT_DIR" python3 -m exchange.persistent_chain checkpoint --root "$ROOT" --container "$CONTAINER" --port "$PORT" > "$BASE_X/post-trade-anchor.json"
mkdir -p "$BASE_X/portfolio-scans"
for instrument in zwa-gold-reference zwa-treasury-style zwa-credit-participation; do
    asset=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["asset_base"])' "$BASE_X/instruments/$instrument-issuance.json")
    buyer balances --asset "$asset" | tee "$BASE_X/portfolio-scans/$instrument-buyer.log"
    seller balances --asset "$asset" | tee "$BASE_X/portfolio-scans/$instrument-seller.log"
done
PYTHONPATH="$PRODUCT_DIR" python3 -m exchange.portfolio_scans "$BASE_X" "$ROOT/chain-identity.json" "$BASE_X/portfolio.json"
# An actual node restart must preserve the accepted transactions and scanned holdings.
PYTHONPATH="$PRODUCT_DIR" python3 -m exchange.persistent_chain stop --root "$ROOT" --container "$CONTAINER" --port "$PORT" > "$BASE_X/chain-stopped.json"
PYTHONPATH="$PRODUCT_DIR" python3 -m exchange.persistent_chain start --root "$ROOT" --container "$CONTAINER" --port "$PORT" > "$BASE_X/chain-restarted.json"
python3 - "$BASE_X/chain-restarted.json" <<'PY'
import json, sys
assert json.load(open(sys.argv[1]))['state'] == 'CHAIN_RESTARTED'
PY
for instrument in zwa-gold-reference zwa-treasury-style zwa-credit-participation; do
    asset=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["asset_base"])' "$BASE_X/instruments/$instrument-issuance.json")
    buyer balances --asset "$asset" | tee "$BASE_X/portfolio-scans/$instrument-buyer-restart.log"
    seller balances --asset "$asset" | tee "$BASE_X/portfolio-scans/$instrument-seller-restart.log"
done
PYTHONPATH="$PRODUCT_DIR" python3 -m exchange.portfolio_scans "$BASE_X" "$ROOT/chain-identity.json" "$BASE_X/portfolio-after-restart.json" --restart
echo "PHASE4E_A_SCRIPTED_INTEGRATION_PASS root=$ROOT purchases=3 chain=CHAIN_RESTARTED"
