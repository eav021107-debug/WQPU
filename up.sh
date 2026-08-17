#!/usr/bin/env bash
set -euo pipefail

REPO="eav021107-debug/WQPU"
REF="${WQPU_REF:-next-foundation}"
BASE="${WQPU_NETWORK_HOME:-$HOME/.local/share/wqpu-network}"
SOURCE_DIR="${WQPU_SOURCE_DIR:-$BASE/source}"
GO_VERSION="1.25.9"
DISCOVERY_PORT=37117
CHAIN_PID=""
NODE_PID=""
BEACON_PID=""

say() { printf '%s\n' "$*"; }
fail() { printf 'WQPU UP: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "requires '$1'"; }

cleanup() {
  for pid in "$BEACON_PID" "$NODE_PID" "$CHAIN_PID"; do
    if [ -n "$pid" ]; then kill "$pid" 2>/dev/null || true; fi
  done
  for pid in "$BEACON_PID" "$NODE_PID" "$CHAIN_PID"; do
    if [ -n "$pid" ]; then wait "$pid" 2>/dev/null || true; fi
  done
}
trap cleanup EXIT INT TERM

usage() {
  cat <<'EOF'
WQPU one-command physical-machine devnet

Run the SAME command on every Linux/macOS machine:
  curl -fsSL https://raw.githubusercontent.com/eav021107-debug/WQPU/next-foundation/up.sh | bash

No host/join role, peer IP or compute slot is supplied by the user.
The client first locates the WQPU blockchain. Compute peers are then enumerated
only from the WQPU on-chain registry (peerCount/peerAt/provider records).

For development only: if no configured blockchain RPC is reachable, machines on
the same LAN can use a small UDP bootstrap exchange to locate the chain itself.
That UDP exchange never supplies compute peers. Provider discovery remains
blockchain-only.
EOF
}

[ "$#" -eq 0 ] || { usage >&2; exit 2; }

need curl
need tar
need git
need python3
mkdir -p "$BASE"

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
    *) fail "one-command physical-machine mode currently supports Linux and macOS" ;;
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
    say "WQPU UP: installing private Go ${GO_VERSION}..."
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

install_private_go
export GOTOOLCHAIN=local
export GOFLAGS="-mod=mod"
export GOMODCACHE="${GOMODCACHE:-$BASE/go/pkg/mod}"
export GOCACHE="${GOCACHE:-$BASE/go/cache}"
mkdir -p "$GOMODCACHE" "$GOCACHE"

say "WQPU UP: downloading ${REPO}@${REF}..."
archive="$BASE/wqpu-up.tar.gz"
stage="$BASE/source.new.$$"
rm -rf "$stage" "$archive"
mkdir -p "$stage"
curl -fL --retry 3 "https://codeload.github.com/${REPO}/tar.gz/${REF}" -o "$archive"
tar -xzf "$archive" -C "$stage" --strip-components=1
rm -f "$archive"
[ -f "$stage/chain/devnet.sh" ] || fail "downloaded source is incomplete"
rm -rf "$SOURCE_DIR"
mv "$stage" "$SOURCE_DIR"

say "WQPU UP: resolving Go dependencies..."
(cd "$SOURCE_DIR/chain" && GOWORK=off go mod tidy && GOWORK=off go mod download all)
(cd "$SOURCE_DIR/client" && GOWORK=off go mod tidy && GOWORK=off go mod download all)

export WQPU_SOURCE_DIR="$SOURCE_DIR"
export WQPU_USE_SYSTEM_GO=1
export WQPU_CHAIN_SRC="${WQPU_CHAIN_SRC:-$BASE/chain-src/cosmos-evm}"
export WQPU_CHAIN_BIN_DIR="${WQPU_CHAIN_BIN_DIR:-$BASE/chain-bin}"
export WQPU_CHAIN_HOME="${WQPU_CHAIN_HOME:-$BASE/devnet-home}"
export WQPU_RUNTIME_BASE="${WQPU_RUNTIME_BASE:-$BASE/llama-runtime}"
mkdir -p "$WQPU_CHAIN_BIN_DIR" "$WQPU_RUNTIME_BASE"

local_ip="${WQPU_ADVERTISE_IP:-}"
if [ -z "$local_ip" ]; then
  local_ip="$(python3 - <<'PY'
import socket
candidates = [("1.1.1.1", 53), ("8.8.8.8", 53)]
for target in candidates:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(target)
        ip = s.getsockname()[0]
        if ip and not ip.startswith("127."):
            print(ip)
            raise SystemExit(0)
    except OSError:
        pass
    finally:
        s.close()
try:
    ip = socket.gethostbyname(socket.gethostname())
    if ip and not ip.startswith("127."):
        print(ip)
        raise SystemExit(0)
except OSError:
    pass
raise SystemExit(1)
PY
)" || fail "could not determine this machine IPv4 address"
fi
case "$local_ip" in
  ""|*[!0-9.]*) fail "invalid local IPv4 address: $local_ip" ;;
esac

rpc_ready() {
  local candidate="$1"
  curl -fsS --connect-timeout 2 --max-time 4 -H 'content-type: application/json' \
    --data '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}' \
    "$candidate" 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); int(d["result"],16)' >/dev/null 2>&1
}

wait_rpc() {
  local candidate="$1" pid="${2:-}" i=0
  while [ "$i" -lt 900 ]; do
    if rpc_ready "$candidate"; then return 0; fi
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
      cat "$BASE/devnet.log" >&2 2>/dev/null || true
      fail "WQPU chain exited before becoming ready"
    fi
    i=$((i + 1))
    sleep 1
  done
  fail "WQPU chain RPC did not become ready: $candidate"
}

find_seed_rpc() {
  local candidate line
  if [ -n "${WQPU_CHAIN_RPC:-}" ] && rpc_ready "$WQPU_CHAIN_RPC"; then
    printf '%s\n' "$WQPU_CHAIN_RPC"
    return 0
  fi
  if [ -f "$SOURCE_DIR/bootstrap-rpcs.txt" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      line="${line%%#*}"
      line="$(printf '%s' "$line" | tr -d '[:space:]')"
      [ -n "$line" ] || continue
      candidate="$line"
      if rpc_ready "$candidate"; then
        printf '%s\n' "$candidate"
        return 0
      fi
    done < "$SOURCE_DIR/bootstrap-rpcs.txt"
  fi
  return 1
}

find_lan_rpc() {
  python3 - "$DISCOVERY_PORT" <<'PY'
import socket, sys, time
port = int(sys.argv[1])
message = b"WQPU_CHAIN_DISCOVER_V1"
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("", 0))
s.settimeout(0.25)
deadline = time.time() + 3.0
while time.time() < deadline:
    try:
        s.sendto(message, ("255.255.255.255", port))
    except OSError:
        pass
    until = min(deadline, time.time() + 0.5)
    while time.time() < until:
        try:
            data, _ = s.recvfrom(2048)
        except socket.timeout:
            break
        text = data.decode("utf-8", "ignore")
        prefix = "WQPU_CHAIN_V1 "
        if text.startswith(prefix):
            rpc = text[len(prefix):].strip()
            if rpc.startswith("http://") or rpc.startswith("https://"):
                print(rpc)
                raise SystemExit(0)
raise SystemExit(1)
PY
}

start_lan_beacon() {
  python3 - "$local_ip" "$DISCOVERY_PORT" <<'PY' &
import socket, sys
ip, port = sys.argv[1], int(sys.argv[2])
request = b"WQPU_CHAIN_DISCOVER_V1"
reply = ("WQPU_CHAIN_V1 http://%s:8545" % ip).encode()
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("", port))
while True:
    data, addr = s.recvfrom(2048)
    if data == request:
        s.sendto(reply, addr)
PY
  BEACON_PID=$!
}

slot_helper() {
  (cd "$SOURCE_DIR/chain" && GOWORK=off go run ./cmd/wqpu-slot-helper "$@")
}

publish_slot() {
  local rpc="$1" slot="$2" endpoint="$3"
  (cd "$SOURCE_DIR/chain" && GOWORK=off go run ./cmd/wqpu-compute-bootstrap publish-one "$rpc" "$slot" "$endpoint")
}

provider_port() { printf '%d' "$((17443 + $1))"; }

wait_node_ready() {
  local pid="$1" log="$2" i=0
  while [ "$i" -lt 180 ]; do
    if grep -Fq 'WQPU PHYSICAL NODE READY' "$log" 2>/dev/null; then return 0; fi
    if ! kill -0 "$pid" 2>/dev/null; then
      cat "$log" >&2 2>/dev/null || true
      fail "physical compute node exited before ready"
    fi
    i=$((i + 1))
    sleep 1
  done
  cat "$log" >&2 2>/dev/null || true
  fail "physical compute node did not become ready"
}

start_provider_background() {
  local rpc="$1" slot="$2" port log
  port="$(provider_port "$slot")"
  log="$BASE/node-$slot.log"
  publish_slot "$rpc" "$slot" "wqpu://$local_ip:$port"
  say "WQPU UP: registered this computer in blockchain: peer-slot=$slot endpoint=wqpu://$local_ip:$port"
  (cd "$SOURCE_DIR/client" && GOWORK=off go run ./cmd/wqpu-dev-node "$rpc" "$slot" "wqpu://0.0.0.0:$port" "$WQPU_RUNTIME_BASE") >"$log" 2>&1 &
  NODE_PID=$!
  wait_node_ready "$NODE_PID" "$log"
}

rpc=""
if rpc="$(find_seed_rpc 2>/dev/null)"; then
  say "WQPU UP: found configured WQPU blockchain: $rpc"
elif rpc="$(find_lan_rpc 2>/dev/null)"; then
  if rpc_ready "$rpc"; then
    say "WQPU UP: found WQPU blockchain bootstrap on LAN: $rpc"
  else
    rpc=""
  fi
fi

if [ -z "$rpc" ]; then
  say "WQPU UP: no WQPU blockchain bootstrap is reachable; starting local shared devnet."
  WQPU_DEVNET_TEST_ADDRESS="$(cd "$SOURCE_DIR/chain" && GOWORK=off go run ./cmd/wqpu-compute-bootstrap address)"
  export WQPU_DEVNET_TEST_ADDRESS
  export WQPU_DEVNET_PUBLIC_RPC=1
  bash "$SOURCE_DIR/chain/devnet.sh" --reset >"$BASE/devnet.log" 2>&1 &
  CHAIN_PID=$!
  rpc="http://127.0.0.1:8545"
  wait_rpc "$rpc" "$CHAIN_PID"
  start_lan_beacon

  slot="$(slot_helper free "$rpc")"
  [ -n "$slot" ] || fail "could not allocate local compute identity"
  start_provider_background "$rpc" "$slot"
  say "WQPU UP: chain + local provider ready. Waiting for another computer to register on-chain..."

  i=0
  while [ "$i" -lt 1800 ]; do
    slots="$(slot_helper active "$rpc" 2>/dev/null || true)"
    count=0
    for candidate in $slots; do count=$((count + 1)); done
    if [ "$count" -ge 2 ]; then break; fi
    if ! kill -0 "$CHAIN_PID" 2>/dev/null || ! kill -0 "$NODE_PID" 2>/dev/null; then
      fail "WQPU host stopped while waiting for another provider"
    fi
    i=$((i + 1))
    sleep 2
  done
  [ "$count" -ge 2 ] || fail "no second compute provider registered in the wait window"

  say "WQPU UP: blockchain registry now contains at least two active compute providers."
  publish_slot "$rpc" 0 "wqpu://$local_ip:17443"
  say "WQPU UP: running distributed inference; executors will be selected from blockchain registry..."
  (cd "$SOURCE_DIR/client" && GOWORK=off go run ./cmd/wqpu-dev-infer "$rpc" 0 "wqpu://0.0.0.0:17443" "$WQPU_RUNTIME_BASE")
  say "WQPU AUTO NETWORK PASSED: executor discovery came from blockchain registry"
  wait "$NODE_PID"
else
  slot="$(slot_helper free "$rpc")"
  [ -n "$slot" ] || fail "no free devnet compute identity is available"
  port="$(provider_port "$slot")"
  publish_slot "$rpc" "$slot" "wqpu://$local_ip:$port"
  say "WQPU UP: this computer registered itself on-chain as compute provider."
  say "WQPU UP: peer discovery is now blockchain-only."
  exec bash -c 'cd "$1" && exec env GOWORK=off go run ./cmd/wqpu-dev-node "$2" "$3" "$4" "$5"' _ \
    "$SOURCE_DIR/client" "$rpc" "$slot" "wqpu://0.0.0.0:$port" "$WQPU_RUNTIME_BASE"
fi
