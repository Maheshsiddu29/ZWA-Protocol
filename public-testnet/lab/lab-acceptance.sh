#!/usr/bin/env bash
# LOCAL LAB acceptance for the community ZSA Testnet client.
#
# Preconditions: lab.sh has a node and lightwalletd running, ROOT is a lab
# root with labfaucet/issuer/recipient wallets, and every coinbase output paid
# to the lab faucet has matured (the miner was moved to another address at
# least 101 blocks earlier). Everything here talks only to the loopback lab node.
set -euo pipefail

ROOT=${ROOT:?set ROOT=/private/tmp/zwa-pub-lab-*}
TOOL=${TOOL:-/private/tmp/zwa-pub-build/tool-target/release/zwa-zsa-pub}
NODECHECK=${NODECHECK:-/private/tmp/zwa-pub-build/nodecheck-target/release/zwa-node-check}
SCRATCH=${SCRATCH:?set SCRATCH to a private scratch directory}
STAMP=$(date -u +%Y%m%d%H%M%S)
ASSET="ZWA-TESTNET-LAB-$STAMP"
ISSUE_AMOUNT=1000000
TRANSFER_AMOUNT=250000
FUND_ZAT=1000000   # mirrors one 0.01 tZEC public faucet claim

case "$ROOT" in /private/tmp/zwa-pub-lab-*) ;; *) echo "lab roots only" >&2; exit 2 ;; esac
mkdir -p "$SCRATCH"
z() { "$TOOL" --root "$ROOT" "$@"; }
field() { python3 -I -c 'import json,sys; v=json.load(sys.stdin)
for k in sys.argv[1].split("."): v=v[k] if not isinstance(v,list) else v[int(k)]
print(v)' "$1"; }
pass() { echo "PASS  $*"; }
expect_fail() { # description, command...
  local d=$1; shift
  if "$@" >"$SCRATCH/neg.out" 2>&1; then echo "FAIL  expected refusal: $d"; cat "$SCRATCH/neg.out"; exit 1; fi
  pass "refused: $d ($(tail -1 "$SCRATCH/neg.out" | cut -c1-110))"
}
wait_mined() { # txid min_confirmations
  for _ in $(seq 1 120); do
    if z verify --txid "$1" >"$SCRATCH/verify-$1.json" 2>"$SCRATCH/verify.err"; then
      if [ "$(field inclusion.mined <"$SCRATCH/verify-$1.json")" = "True" ] &&
         [ "$(field inclusion.confirmations <"$SCRATCH/verify-$1.json")" -ge "$2" ]; then return 0; fi
    fi
    sleep 10
  done
  echo "FAIL  $1 not mined with $2 confirmations"; cat "$SCRATCH/verify.err"; exit 1
}

z identity >/dev/null && pass "live identity matches pins and root binding"

# --- negative policy checks (no transaction built) ---
expect_fail "public profile on a lab root" "$TOOL" --root "$ROOT" init --profile public
expect_fail "lab profile pointed at the public endpoint" "$TOOL" --root /private/tmp/zwa-pub-lab-neg-$STAMP init --profile lab --lwd https://zsa.methyl.cc
expect_fail "protected Phase 4E root" "$TOOL" --root /private/tmp/zwa4e-phase4e-b identity
expect_fail "asset name without experimental prefix" z prepare-issue --name GOLD --amount 1

# --- lab funding: shield matured coinbase, then lab faucet Orchard -> issuer Orchard
#     (stands in for one public faucet claim) ---
z sync --role labfaucet >/dev/null
z balance --role labfaucet >"$SCRATCH/labfaucet.json"
if [ "$(field zecZat.transparent <"$SCRATCH/labfaucet.json")" != "0" ]; then
  SHIELD=$(z lab-shield | field labShieldTxid)
  wait_mined "$SHIELD" 1 && pass "lab faucet coinbase shielded to Orchard without transparent outputs ($SHIELD)"
else
  pass "lab faucet already shielded (Orchard $(field zecZat.orchard <"$SCRATCH/labfaucet.json") zat)"
fi
z balance --role issuer >"$SCRATCH/issuer-before.json"
ISSUER_START=$(field zecZat.orchard <"$SCRATCH/issuer-before.json")
if [ "$ISSUER_START" -lt "$FUND_ZAT" ]; then
  FUND=$(z lab-fund --amount $((FUND_ZAT - ISSUER_START)) | field labFundingTxid)
  wait_mined "$FUND" 1 && pass "lab funding $FUND mined"
else
  pass "issuer already has its laboratory fee budget"
fi
z balance --role issuer >"$SCRATCH/issuer-funded.json"
[ "$(field zecZat.orchard <"$SCRATCH/issuer-funded.json")" = "$FUND_ZAT" ] && pass "issuer holds a faucet-sized Orchard fee budget"

# --- issuance ---
z prepare-issue --name "$ASSET" --amount $ISSUE_AMOUNT >"$SCRATCH/issue-plan.json"
ISSUE=$(field facts.txid <"$SCRATCH/issue-plan.json")
BASE=$(field asset_base <"$SCRATCH/issue-plan.json")
pass "issuance prepared $ISSUE fee $(field facts.fee_zat <"$SCRATCH/issue-plan.json") zat (node min $(field facts.conventional_fee_zat <"$SCRATCH/issue-plan.json"))"
"$NODECHECK" "$ROOT/pending/$ISSUE.tx" >"$SCRATCH/issue-nodecheck.json"
[ "$(field facts.issuance.actions.0.assetBase <"$SCRATCH/issue-nodecheck.json")" = "$BASE" ] &&
  pass "node zebra-chain@6a91831 parses issuance and derives the same AssetBase"
expect_fail "duplicate asset name" z prepare-issue --name "$ASSET" --amount 1
flip() { python3 -I -c 'import sys;p,o=sys.argv[1],int(sys.argv[2]);b=bytearray(open(p,"rb").read());o=o if o>=0 else len(b)//2;b[o]^=1;open(p,"wb").write(b)' "$1" "$2"; }
cp "$ROOT/pending/$ISSUE.tx" "$SCRATCH/tampered-header.tx"; flip "$SCRATCH/tampered-header.tx" 4
expect_fail "node parser on a corrupted version group id" "$NODECHECK" "$SCRATCH/tampered-header.tx"
cp "$ROOT/pending/$ISSUE.tx" "$SCRATCH/tampered-body.tx"; flip "$SCRATCH/tampered-body.tx" -1
"$NODECHECK" "$SCRATCH/tampered-body.tx" >/dev/null && pass "a mid-body bit flip still parses (node-check is format-only)"
LAB_RPC="$(dirname "$0")/lab.sh"
"$LAB_RPC" rpc sendrawtransaction "[\"$(xxd -p "$SCRATCH/tampered-body.tx" | tr -d '\n')\"]" >"$SCRATCH/tampered-submit.json"
python3 -I -c 'import json,sys; r=json.load(open(sys.argv[1])); assert r.get("result") is None and r.get("error",{}).get("code") == -25 and "consensus validation" in r["error"]["message"]' "$SCRATCH/tampered-submit.json" && pass "lab node full validation rejects the tampered body ($(python3 -I -c 'import json,sys;print(json.load(open(sys.argv[1]))["error"]["message"][:90])' "$SCRATCH/tampered-submit.json"))" ||
  { echo "FAIL  lab node accepted tampered bytes"; cat "$SCRATCH/tampered-submit.json"; exit 1; }
z broadcast --txid "$ISSUE" >/dev/null && pass "issuance submitted through lab lightwalletd"
expect_fail "second submission of the same txid" z broadcast --txid "$ISSUE"
wait_mined "$ISSUE" 2 && pass "issuance mined: height $(field inclusion.blockHeight <"$SCRATCH/verify-$ISSUE.json") conf $(field inclusion.confirmations <"$SCRATCH/verify-$ISSUE.json")"
[ "$(field walletObservation.status <"$SCRATCH/verify-$ISSUE.json")" = "OBSERVED_AS_EXPECTED" ] && pass "issuer wallet observes the issued supply"
node_inclusion() { # txid verify-json; independent full-node RPC reads
  python3 -I - "$LAB_RPC" "$1" "$2" "$ROOT" <<'EOF'
import json, subprocess, sys
lab, txid, receipt_path, root = sys.argv[1:]
receipt = json.load(open(receipt_path))
def rpc(method, params):
    reply = json.loads(subprocess.check_output([lab, "rpc", method, json.dumps(params)], text=True))
    assert reply.get("error") is None, (method, reply.get("error"))
    return reply["result"]
raw = rpc("getrawtransaction", [txid, 1])
height = receipt["inclusion"]["blockHeight"]
block_hash = rpc("getblockhash", [height])
block = rpc("getblock", [block_hash, 1])
assert raw["txid"] == txid and raw["blockhash"] == block_hash
assert raw["height"] == height and raw["in_active_chain"] is True
assert raw["vin"] == raw["vout"] == []
assert raw["orchard"]["valueBalanceZat"] == receipt["transaction"]["feeZat"]
assert block["hash"] == block_hash and block["height"] == height and txid in block["tx"]
assert receipt["inclusion"]["blockHash"] == block_hash
assert bytes.fromhex(raw["hex"]) == open(f"{root}/pending/{txid}.tx", "rb").read()
print(f"PASS  independent node RPC: {txid} in block {block_hash} at height {height}")
EOF
}
node_inclusion "$ISSUE" "$SCRATCH/verify-$ISSUE.json"

# --- shielded transfer to a separate wallet ---
expect_fail "transfer above issuer holdings" z prepare-transfer --asset-base "$BASE" --amount $((ISSUE_AMOUNT + 1))
z prepare-transfer --asset-base "$BASE" --amount $TRANSFER_AMOUNT >"$SCRATCH/transfer-plan.json"
XFER=$(field facts.txid <"$SCRATCH/transfer-plan.json")
[ "$(field recipient_decryption_checked <"$SCRATCH/transfer-plan.json")" = "True" ] &&
  pass "transfer prepared $XFER; recipient viewing key decrypts exactly $TRANSFER_AMOUNT units before broadcast"
"$NODECHECK" "$ROOT/pending/$XFER.tx" >/dev/null && pass "node zebra-chain@6a91831 parses the transfer"
z broadcast --txid "$XFER" >/dev/null && pass "transfer submitted"
expect_fail "second submission of the transfer txid" z broadcast --txid "$XFER"
wait_mined "$XFER" 2 && pass "transfer mined: height $(field inclusion.blockHeight <"$SCRATCH/verify-$XFER.json") conf $(field inclusion.confirmations <"$SCRATCH/verify-$XFER.json")"
[ "$(field walletObservation.status <"$SCRATCH/verify-$XFER.json")" = "OBSERVED_AS_EXPECTED" ] && pass "recipient wallet observes the transfer"
node_inclusion "$XFER" "$SCRATCH/verify-$XFER.json"
z balance --role recipient >"$SCRATCH/recipient-final.json"
z balance --role issuer >"$SCRATCH/issuer-final.json"
python3 -I - "$SCRATCH" "$BASE" $ISSUE_AMOUNT $TRANSFER_AMOUNT <<'EOF'
import json,sys
d,base,issued,sent=sys.argv[1],sys.argv[2],int(sys.argv[3]),int(sys.argv[4])
bal=lambda f:sum(a["balance"] for a in json.load(open(f"{d}/{f}"))["assets"] if a["assetBase"]==base)
r,i=bal("recipient-final.json"),bal("issuer-final.json")
assert r==sent and i==issued-sent, (r,i)
print(f"PASS  wallet reconciliation: recipient {r}, issuer {i}, total {r+i} = issued {issued}")
zec=lambda f,role:json.load(open(f"{d}/{role}-{f}.json"))["zecZat"]
funded=zec("funded","issuer"); final=zec("final","issuer"); recipient=zec("final","recipient")
fees=sum(json.load(open(f"{d}/{kind}-plan.json"))["facts"]["fee_zat"] for kind in ("issue","transfer"))
assert funded["orchard"]-final["orchard"]==fees, (funded,final,fees)
assert all(final[p]==0 for p in ("transparent","sapling"))
assert recipient["transparent"]==recipient["sapling"]==recipient["orchard"]==0
print(f"PASS  ZEC accounting: issuer {funded['orchard']} - fees {fees} = {final['orchard']} zat; no transparent change")
EOF
echo "RESULT lab acceptance passed: asset $BASE issuance $ISSUE transfer $XFER"
