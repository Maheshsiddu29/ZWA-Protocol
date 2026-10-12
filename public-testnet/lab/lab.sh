#!/usr/bin/env bash
# LOCAL LAB ONLY: run a private zebrad (community ZSA Testnet consensus,
# zcash-shielded-assets/zebra@6a91831) and the community lightwalletd on
# loopback. Separate state, separate ports; never touches the protected
# Phase 4E Docker chains or the public network.
set -euo pipefail

NODE_DIR=${ZWA_LAB_NODE_DIR:-/private/tmp/zwa-pub-lab-node}
ZEBRAD=${ZWA_LAB_ZEBRAD:-/private/tmp/zwa-pub-build/zebra-target/release/zebrad}
LWD=${ZWA_LAB_LWD:-/private/tmp/zwa-pub-build/lightwalletd}
HERE=$(cd "$(dirname "$0")" && pwd)
RPC=127.0.0.1:19232
GRPC=127.0.0.1:19067

case "$NODE_DIR" in
  /private/tmp/zwa-pub-lab-node|/private/tmp/zwa-pub-lab-node-*) ;;
  *) echo "refusing node dir $NODE_DIR" >&2; exit 2 ;;
esac

rpc() {
  curl -sS -m 30 -H 'content-type: application/json' \
    --data "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$1\",\"params\":${2:-[]}}" "http://$RPC"
}

owned_pid() { # pidfile expected-binary
  [ -f "$1" ] || return 1
  local pid; pid=$(cat "$1")
  ps -p "$pid" -o command= 2>/dev/null | grep -qF "$2" && echo "$pid"
}

start_node() { # miner_address internal_miner(true|false)
  mkdir -p "$NODE_DIR" && chmod 700 "$NODE_DIR"
  if owned_pid "$NODE_DIR/zebrad.pid" "$ZEBRAD" >/dev/null; then echo "zebrad already running" >&2; exit 1; fi
  sed -e "s#@MINER_ADDRESS@#$1#" -e "s#@INTERNAL_MINER@#$2#" -e "s#@NODE_DIR@#$NODE_DIR#g" \
    "$HERE/zebrad-lab.toml.in" > "$NODE_DIR/zebrad.toml"
  chmod 600 "$NODE_DIR/zebrad.toml"
  nohup "$ZEBRAD" -c "$NODE_DIR/zebrad.toml" start >> "$NODE_DIR/zebrad.log" 2>&1 &
  echo $! > "$NODE_DIR/zebrad.pid"
  for _ in $(seq 1 90); do
    if rpc getblockcount 2>/dev/null | grep -q '"result"'; then echo "zebrad up"; return 0; fi
    sleep 1
  done
  echo "zebrad did not answer RPC" >&2; exit 1
}

start_lwd() {
  if owned_pid "$NODE_DIR/lwd.pid" "$LWD" >/dev/null; then echo "lightwalletd already running" >&2; exit 1; fi
  mkdir -p "$NODE_DIR/lwd-data"
  nohup "$LWD" --no-tls-very-insecure --grpc-bind-addr "$GRPC" --http-bind-addr 127.0.0.1:19068 \
    --rpchost 127.0.0.1 --rpcport 19232 --rpcuser lab --rpcpassword lab \
    --data-dir "$NODE_DIR/lwd-data" --log-file "$NODE_DIR/lwd.log" >> "$NODE_DIR/lwd.out" 2>&1 &
  echo $! > "$NODE_DIR/lwd.pid"
  sleep 3
  owned_pid "$NODE_DIR/lwd.pid" "$LWD" >/dev/null && echo "lightwalletd up on $GRPC"
}

stop() { # pidfile binary
  local pid
  if pid=$(owned_pid "$1" "$2"); then kill "$pid"; for _ in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done; fi
  rm -f "$1"
}

case "${1:-}" in
  start-node) start_node "${2:?miner address}" "${3:-true}" ;;
  start-lwd) start_lwd ;;
  stop) stop "$NODE_DIR/lwd.pid" "$LWD"; stop "$NODE_DIR/zebrad.pid" "$ZEBRAD"; echo stopped ;;
  stop-node) stop "$NODE_DIR/zebrad.pid" "$ZEBRAD"; echo "zebrad stopped" ;;
  height) rpc getblockcount ;;
  rpc) rpc "${2:?method}" "${3:-[]}" ;;
  *) echo "usage: $0 start-node ADDR [true|false] | start-lwd | stop | stop-node | height | rpc METHOD [PARAMS]" >&2; exit 2 ;;
esac
