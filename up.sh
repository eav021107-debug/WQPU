#!/usr/bin/env bash
set -euo pipefail

REPO="eav021107-debug/WQPU"
REF="${WQPU_REF:-next-foundation}"
BASE="${WQPU_NETWORK_HOME:-$HOME/.local/share/wqpu-network}"
SOURCE_DIR="${WQPU_SOURCE_DIR:-$BASE/source}"
GO_VERSION="1.25.9"
HOST_PID=""

say() { printf '%s\n' "$*"; }
fail() { printf 'WQPU UP: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "requires '$1'"; }

cleanup() {
  if [ -n "$HOST_PID" ]; then
    kill "$HOST_PID" 2>/dev/null || true
    wait "$HOST_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

usage() {
  cat <<'EOF'
One-command WQPU physical-machine startup

Run the SAME command on every machine, starting with the bootstrap machine:
  curl -fsSL RAW_UP_SH_URL | bash -s -- BOOTSTRAP_IP

The machine that owns BOOTSTRAP_IP becomes the shared devnet host. Every other
machine waits for it, chooses a free compute slot, registers on-chain and starts
its compute node. When the second provider appears, the host automatically runs
a distributed inference proof across the two physical providers.

This devnet mode is for a private LAN/VPN. Do not expose ports 8545 or 17443-17450
directly to the public Internet.
EOF
}

[ "$#" -eq 1 ] || { usage >&2; exit 2; }
BOOTSTRAP="$1"
case "$BOOTSTRAP" in
  ""|*[!A-Za-z0-9.-]*) fail "bootstrap must be an IPv4 address or DNS hostname" ;;
esac

need curl
need tar
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
[ -f "$stage/network.sh" ] || fail "downloaded source is incomplete"
rm -rf "$SOURCE_DIR"
mv "$stage" "$SOURCE_DIR"

export WQPU_SOURCE_DIR="$SOURCE_DIR"
export WQPU_USE_SYSTEM_GO=1

local_ip="${WQPU_ADVERTISE_IP:-}"
if [ -z "$local_ip" ]; then
  local_ip="$(python3 - "$BOOTSTRAP" <<'PY'
import socket, sys
host = sys.argv[1]
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    s.connect((host, 9))
    print(s.getsockname()[0])
finally:
    s.close()
PY
)"
fi
case "$local_ip" in
  ""|*[!0-9.]*) fail "could not determine this machine IPv4 address; set WQPU_ADVERTISE_IP" ;;
esac

bootstrap_ip="$(python3 - "$BOOTSTRAP" <<'PY'
import socket, sys
print(socket.gethostbyname(sys.argv[1]))
PY
)"
rpc="http://$BOOTSTRAP:8545"

rpc_ready() {
  curl -fsS --connect-timeout 2 -H 'content-type: application/json' \
    --data '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}' \
    "$rpc" >/dev/null 2>&1
}

wait_rpc() {
  local i=0
  while [ "$i" -lt 600 ]; do
    if rpc_ready; then return 0; fi
    i=$((i + 1))
    sleep 1
  done
  fail "bootstrap WQPU chain did not become reachable at $rpc"
}

is_bootstrap_machine=0
if [ "${WQPU_FORCE_ROLE:-}" = "host" ]; then
  is_bootstrap_machine=1
elif [ "${WQPU_FORCE_ROLE:-}" = "join" ]; then
  is_bootstrap_machine=0
elif [ "$local_ip" = "$bootstrap_ip" ] || [ "$BOOTSTRAP" = "127.0.0.1" ] || [ "$BOOTSTRAP" = "localhost" ]; then
  is_bootstrap_machine=1
fi

slot_helper() {
  (cd "$SOURCE_DIR/chain" && GOWORK=off go run ./cmd/wqpu-slot-helper "$@")
}

if [ "$is_bootstrap_machine" -eq 1 ]; then
  say "WQPU UP: this machine is bootstrap host ($local_ip)."
  say "WQPU UP: starting shared chain + provider automatically..."
  bash "$SOURCE_DIR/network.sh" host "$local_ip" 1 &
  HOST_PID=$!

  local_rpc="http://127.0.0.1:8545"
  i=0
  while [ "$i" -lt 900 ]; do
    if curl -fsS --connect-timeout 2 -H 'content-type: application/json' \
      --data '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}' \
      "$local_rpc" >/dev/null 2>&1; then break; fi
    if ! kill -0 "$HOST_PID" 2>/dev/null; then
      wait "$HOST_PID" || true
      fail "bootstrap host exited before chain became ready"
    fi
    i=$((i + 1)); sleep 1
  done
  [ "$i" -lt 900 ] || fail "local bootstrap chain did not become ready"

  say "WQPU UP: host ready. Waiting for another physical compute machine..."
  remote_slots=""
  i=0
  while [ "$i" -lt 1800 ]; do
    slots="$(slot_helper active "$local_rpc" 2>/dev/null || true)"
    count=0
    for slot in $slots; do count=$((count + 1)); done
    if [ "$count" -ge 2 ]; then
      remote_slots="$slots"
      break
    fi
    if ! kill -0 "$HOST_PID" 2>/dev/null; then
      wait "$HOST_PID" || true
      fail "bootstrap host exited while waiting for providers"
    fi
    i=$((i + 1)); sleep 2
  done
  [ -n "$remote_slots" ] || fail "no second WQPU provider joined within the wait window"

  set -- $remote_slots
  first="$1"
  second="$2"
  say "WQPU UP: providers $first and $second are online; running distributed inference automatically..."
  bash "$SOURCE_DIR/network.sh" infer 127.0.0.1 "$local_ip" "$first" "$second"
  say "WQPU AUTO NETWORK PASSED: two physical providers + shared chain + distributed inference"
  wait "$HOST_PID"
else
  say "WQPU UP: this machine is a compute joiner ($local_ip)."
  say "WQPU UP: waiting for bootstrap $rpc..."
  wait_rpc
  slot="$(slot_helper free "$rpc")"
  [ -n "$slot" ] || fail "could not allocate a free compute slot"
  say "WQPU UP: automatically assigned compute slot $slot."
  exec bash "$SOURCE_DIR/network.sh" join "$BOOTSTRAP" "$local_ip" "$slot"
fi
