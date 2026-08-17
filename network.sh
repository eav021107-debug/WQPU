#!/usr/bin/env bash
set -euo pipefail

REPO="eav021107-debug/WQPU"
REF="${WQPU_REF:-next-foundation}"
BASE="${WQPU_NETWORK_HOME:-$HOME/.local/share/wqpu-network}"
SOURCE_DIR="${WQPU_SOURCE_DIR:-}"
GO_VERSION="1.25.9"
CHAIN_PID=""
NODE_PIDS=()

say() { printf '%s\n' "$*"; }
fail() { printf 'WQPU NETWORK: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "requires '$1'"; }

cleanup() {
  for pid in "${NODE_PIDS[@]:-}"; do
    if [ -n "$pid" ]; then kill "$pid" 2>/dev/null || true; fi
  done
  for pid in "${NODE_PIDS[@]:-}"; do
    if [ -n "$pid" ]; then wait "$pid" 2>/dev/null || true; fi
  done
  if [ -n "$CHAIN_PID" ]; then
    kill "$CHAIN_PID" 2>/dev/null || true
    wait "$CHAIN_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

usage() {
  cat <<'EOF'
WQPU physical-machine devnet

  network.sh host HOST_ADVERTISE_IP [PROVIDER_SLOT]
  network.sh join CHAIN_HOST_IP THIS_MACHINE_IP [PROVIDER_SLOT]
  network.sh infer CHAIN_HOST_IP THIS_MACHINE_IP [REMOTE_SLOT...]

Typical two-machine test:
  machine A: network.sh host 192.168.1.10
  machine B: network.sh join 192.168.1.10 192.168.1.20
  machine A: network.sh infer 192.168.1.10 192.168.1.10 1 2

Slots 0..7 are deterministic devnet-only identities. Slot 0 is reserved as the
transient inference coordinator by this script. This mode is for an isolated LAN
only; never expose its devnet ports directly to the public Internet.
EOF
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    python3 - "$1" <<'PY'
import hashlib, sys
h = hashlib.sha256()
with open(sys.argv[1], 'rb') as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b''):
        h.update(chunk)
print(h.hexdigest())
PY
  fi
}

install_private_go() {
  local os arch asset expected archive tmp actual root
  case "$(uname -s)" in
    Linux) os=linux ;;
    Darwin) os=darwin ;;
    *) fail "physical-machine devnet currently supports Linux and macOS" ;;
  esac
  case "$(uname -m)" in
    x86_64|amd64) arch=amd64 ;;
    arm64|aarch64) arch=arm64 ;;
    *) fail "unsupported CPU architecture: $(uname -m)" ;;
  esac
  asset="go${GO_VERSION}.${os}-${arch}.tar.gz"
  case "${os}/${arch}" in
    linux/amd64) expected="00859d7bd6defe8bf84d9db9e57b9a4467b2887c18cd93ae7460e713db774bc1" ;;
    linux/arm64) expected="ec342e7389b7f489564ed5463c63b16cf8040023dabc7861256677165a8c0e2b" ;;
    darwin/amd64) expected="92cb78fba4796e218c1accb0ea0a214ef2094c382049a244ad6505505d015fbe" ;;
    darwin/arm64) expected="9528be7329b9770631a6bd09ca2f3a73ed7332bec01d87435e75e92d8f130363" ;;
  esac
  root="$BASE/toolchains/go${GO_VERSION}-${os}-${arch}"
  if [ ! -x "$root/bin/go" ]; then
    say "WQPU NETWORK: installing private Go ${GO_VERSION}..."
    mkdir -p "$BASE"
    archive="$BASE/$asset"
    curl -fL --retry 3 "https://go.dev/dl/$asset" -o "$archive"
    actual="$(sha256_file "$archive")"
    [ "$actual" = "$expected" ] || fail "Go archive checksum mismatch"
    tmp="$root.tmp.$$"
    rm -rf "$tmp"
    mkdir -p "$tmp"
    tar -xzf "$archive" -C "$tmp"
    rm -f "$archive"
    [ -x "$tmp/go/bin/go" ] || fail "private Go extraction failed"
    rm -rf "$root"
    mv "$tmp/go" "$root"
    rm -rf "$tmp"
  fi
  export GOROOT="$root"
  export PATH="$GOROOT/bin:$PATH"
}

validate_host_token() {
  case "$1" in
    ""|*[!A-Za-z0-9.-]*) fail "use an IPv4 address or DNS hostname, got '$1'" ;;
  esac
}

validate_slot() {
  case "$1" in
    ''|*[!0-9]*) fail "slot must be an integer within 0..7" ;;
  esac
  [ "$1" -ge 0 ] && [ "$1" -le 7 ] || fail "slot must be within 0..7"
}

provider_port() { printf '%d' "$((17443 + $1))"; }

prepare() {
  need curl
  need tar
  need git
  need python3
  mkdir -p "$BASE"
  if [ "${WQPU_USE_SYSTEM_GO:-0}" = "1" ]; then need go; else install_private_go; fi
  case "$(go version)" in *"go${GO_VERSION}"*) ;; *) fail "Go ${GO_VERSION} is required; got: $(go version)" ;; esac
  export GOTOOLCHAIN=local
  export GOFLAGS="-mod=mod"
  export GOMODCACHE="${GOMODCACHE:-$BASE/go/pkg/mod}"
  export GOCACHE="${GOCACHE:-$BASE/go/cache}"
  mkdir -p "$GOMODCACHE" "$GOCACHE"

  if [ -z "$SOURCE_DIR" ]; then
    SOURCE_DIR="$BASE/source"
    local archive="$BASE/wqpu-${REF//\//-}.tar.gz"
    local stage="$BASE/source.new.$$"
    say "WQPU NETWORK: downloading ${REPO}@${REF}..."
    rm -rf "$stage" "$archive"
    mkdir -p "$stage"
    curl -fL --retry 3 "https://codeload.github.com/${REPO}/tar.gz/${REF}" -o "$archive"
    tar -xzf "$archive" -C "$stage" --strip-components=1
    rm -f "$archive"
    [ -f "$stage/chain/devnet.sh" ] || fail "downloaded source is incomplete"
    rm -rf "$SOURCE_DIR"
    mv "$stage" "$SOURCE_DIR"
  fi
  [ -f "$SOURCE_DIR/chain/devnet.sh" ] || fail "invalid WQPU source directory: $SOURCE_DIR"

  say "WQPU NETWORK: resolving Go dependencies..."
  (cd "$SOURCE_DIR/chain" && GOWORK=off go mod tidy && GOWORK=off go mod download all)
  (cd "$SOURCE_DIR/client" && GOWORK=off go mod tidy && GOWORK=off go mod download all)

  export WQPU_CHAIN_SRC="${WQPU_CHAIN_SRC:-$BASE/chain-src/cosmos-evm}"
  export WQPU_CHAIN_BIN_DIR="${WQPU_CHAIN_BIN_DIR:-$BASE/chain-bin}"
  export WQPU_CHAIN_HOME="${WQPU_CHAIN_HOME:-$BASE/devnet-home}"
  export WQPU_RUNTIME_BASE="${WQPU_RUNTIME_BASE:-$BASE/llama-runtime}"
  mkdir -p "$WQPU_CHAIN_BIN_DIR" "$WQPU_RUNTIME_BASE"
}

wait_rpc() {
  local rpc="$1" i=0 response
  while [ "$i" -lt 300 ]; do
    response="$(curl -fsS -H 'content-type: application/json' --data '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}' "$rpc" 2>/dev/null || true)"
    if python3 - "$response" <<'PY'
import json, sys
try:
    value = int(json.loads(sys.argv[1]).get('result', '0x0'), 16)
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if value >= 1 else 1)
PY
    then return 0; fi
    if [ -n "$CHAIN_PID" ] && ! kill -0 "$CHAIN_PID" 2>/dev/null; then
      cat "$BASE/devnet.log" >&2 2>/dev/null || true
      fail "devnet exited before becoming ready"
    fi
    i=$((i + 1))
    sleep 2
  done
  fail "WQPU chain RPC did not become ready: $rpc"
}

publish_slot() {
  local rpc="$1" slot="$2" endpoint="$3"
  say "WQPU NETWORK: publishing slot $slot -> $endpoint on-chain..."
  (cd "$SOURCE_DIR/chain" && GOWORK=off go run ./cmd/wqpu-compute-bootstrap publish-one "$rpc" "$slot" "$endpoint")
}

start_devnet() {
  local rpc="http://127.0.0.1:8545"
  WQPU_DEVNET_TEST_ADDRESS="$(cd "$SOURCE_DIR/chain" && GOWORK=off go run ./cmd/wqpu-compute-bootstrap address)"
  export WQPU_DEVNET_TEST_ADDRESS
  export WQPU_DEVNET_PUBLIC_RPC=1
  say "WQPU NETWORK: building and starting the shared sovereign devnet..."
  bash "$SOURCE_DIR/chain/devnet.sh" --reset >"$BASE/devnet.log" 2>&1 &
  CHAIN_PID=$!
  wait_rpc "$rpc"
}

run_node() {
  local rpc="$1" slot="$2" port
  port="$(provider_port "$slot")"
  say "WQPU NETWORK: starting physical compute slot $slot on 0.0.0.0:$port..."
  (cd "$SOURCE_DIR/client" && GOWORK=off go run ./cmd/wqpu-dev-node "$rpc" "$slot" "wqpu://0.0.0.0:$port" "$WQPU_RUNTIME_BASE")
}

wait_node_ready() {
  local pid="$1" log="$2" i=0
  while [ "$i" -lt 180 ]; do
    if grep -Fq 'WQPU PHYSICAL NODE READY' "$log" 2>/dev/null; then return 0; fi
    if ! kill -0 "$pid" 2>/dev/null; then cat "$log" >&2 || true; fail "physical compute node exited before ready"; fi
    i=$((i + 1)); sleep 1
  done
  cat "$log" >&2 || true
  fail "physical compute node did not become ready"
}

host_mode() {
  [ "$#" -ge 1 ] && [ "$#" -le 2 ] || { usage >&2; exit 2; }
  local advertise="$1" slot="${2:-1}" port rpc
  validate_host_token "$advertise"
  validate_slot "$slot"
  [ "$slot" -ne 0 ] || fail "slot 0 is reserved for the transient coordinator"
  prepare
  start_devnet
  rpc="http://127.0.0.1:8545"
  port="$(provider_port "$slot")"
  publish_slot "$rpc" "$slot" "wqpu://$advertise:$port"
  say "WQPU HOST READY: chain=http://$advertise:8545 provider-slot=$slot endpoint=wqpu://$advertise:$port"
  say "Keep this terminal open. On another machine run: network.sh join $advertise THIS_MACHINE_IP"
  run_node "$rpc" "$slot"
}

join_mode() {
  [ "$#" -ge 2 ] && [ "$#" -le 3 ] || { usage >&2; exit 2; }
  local chain_host="$1" advertise="$2" slot="${3:-2}" port rpc
  validate_host_token "$chain_host"
  validate_host_token "$advertise"
  validate_slot "$slot"
  [ "$slot" -ne 0 ] || fail "slot 0 is reserved for the transient coordinator"
  prepare
  rpc="http://$chain_host:8545"
  say "WQPU NETWORK: connecting to shared chain $rpc..."
  wait_rpc "$rpc"
  port="$(provider_port "$slot")"
  publish_slot "$rpc" "$slot" "wqpu://$advertise:$port"
  say "WQPU JOIN READY: provider-slot=$slot endpoint=wqpu://$advertise:$port"
  say "Keep this terminal open while distributed inference is running."
  run_node "$rpc" "$slot"
}

infer_mode() {
  [ "$#" -ge 2 ] || { usage >&2; exit 2; }
  local chain_host="$1" advertise="$2" rpc port
  shift 2
  validate_host_token "$chain_host"
  validate_host_token "$advertise"
  local remotes=("$@")
  if [ "${#remotes[@]}" -eq 0 ]; then remotes=(1 2); fi
  for slot in "${remotes[@]}"; do validate_slot "$slot"; [ "$slot" -ne 0 ] || fail "remote provider cannot use coordinator slot 0"; done
  prepare
  rpc="http://$chain_host:8545"
  wait_rpc "$rpc"
  port="$(provider_port 0)"
  publish_slot "$rpc" 0 "wqpu://$advertise:$port"
  say "WQPU NETWORK: running one LLM across physical provider slots ${remotes[*]}..."
  (cd "$SOURCE_DIR/client" && GOWORK=off go run ./cmd/wqpu-dev-infer "$rpc" 0 "wqpu://0.0.0.0:$port" "$WQPU_RUNTIME_BASE" "${remotes[@]}")
}

ci_mode() {
  prepare
  start_devnet
  local rpc="http://127.0.0.1:8545"
  publish_slot "$rpc" 1 "wqpu://127.0.0.1:17444"
  publish_slot "$rpc" 2 "wqpu://127.0.0.1:17445"
  local log1="$BASE/ci-node-1.log" log2="$BASE/ci-node-2.log"
  (cd "$SOURCE_DIR/client" && GOWORK=off go run ./cmd/wqpu-dev-node "$rpc" 1 "wqpu://127.0.0.1:17444" "$WQPU_RUNTIME_BASE") >"$log1" 2>&1 &
  NODE_PIDS+=("$!")
  (cd "$SOURCE_DIR/client" && GOWORK=off go run ./cmd/wqpu-dev-node "$rpc" 2 "wqpu://127.0.0.1:17445" "$WQPU_RUNTIME_BASE") >"$log2" 2>&1 &
  NODE_PIDS+=("$!")
  wait_node_ready "${NODE_PIDS[0]}" "$log1"
  wait_node_ready "${NODE_PIDS[1]}" "$log2"
  publish_slot "$rpc" 0 "wqpu://127.0.0.1:17443"
  (cd "$SOURCE_DIR/client" && GOWORK=off go run ./cmd/wqpu-dev-infer "$rpc" 0 "wqpu://127.0.0.1:17443" "$WQPU_RUNTIME_BASE" 1 2) 2>&1 | tee "$BASE/ci-infer.log"
  grep -Fq 'CROSS MACHINE COMPUTE PASSED' "$BASE/ci-infer.log" || fail "cross-machine inference proof was not produced"
  say "WQPU MULTI-MACHINE PASSED: shared chain + independent providers + distributed inference"
}

[ "$#" -ge 1 ] || { usage >&2; exit 2; }
mode="$1"; shift
case "$mode" in
  host) host_mode "$@" ;;
  join) join_mode "$@" ;;
  infer) infer_mode "$@" ;;
  ci) ci_mode "$@" ;;
  -h|--help|help) usage ;;
  *) usage >&2; exit 2 ;;
esac
