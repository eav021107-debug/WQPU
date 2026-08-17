#!/usr/bin/env bash
set -euo pipefail

REPO="eav021107-debug/WQPU"
REF="${WQPU_REF:-next-foundation}"
BASE="${WQPU_NETWORK_HOME:-$HOME/.local/share/wqpu-network}"
SOURCE_DIR="${WQPU_SOURCE_DIR:-$BASE/source}"
BOOTSTRAP_CACHE="${WQPU_BOOTSTRAP_CACHE:-$BASE/bootstrap/verified-rpcs.txt}"
GO_VERSION="1.25.9"
CHAIN_PID=""
NODE_PID=""

say() { printf '%s\n' "$*"; }
fail() { printf 'WQPU UP: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "requires '$1'"; }

cleanup() {
  for pid in "$NODE_PID" "$CHAIN_PID"; do
    if [ -n "$pid" ]; then kill "$pid" 2>/dev/null || true; fi
  done
  for pid in "$NODE_PID" "$CHAIN_PID"; do
    if [ -n "$pid" ]; then wait "$pid" 2>/dev/null || true; fi
  done
}
trap cleanup EXIT INT TERM

usage() {
  cat <<'EOF'
WQPU one-command physical-machine client

Run the SAME command on every Linux/macOS machine:
  curl -fsSL https://raw.githubusercontent.com/eav021107-debug/WQPU/next-foundation/up.sh | bash

Normal mode has no host/join role, peer IP, RPC, or compute slot arguments.
Bootstrap is intentionally invisible: the client first tries its locally saved
verified WQPU RPC addrbook, then repository-shipped trusted bootstrap RPCs. Every
production candidate must use TLS and is re-verified against the WQPU chain id,
native protocol and pinned canonical block checkpoint before use.
After blockchain access, compute peers are discovered only from the on-chain registry.

WQPU chain nodes use CometBFT PEX + persistent addrbook.json so public seed nodes
are only first-contact helpers, never permanent coordinators. There is no LAN
broadcast discovery and no arbitrary network-supplied RPC. Local-chain fallback
exists only when WQPU_DEV_LOCAL_FALLBACK=1 is explicitly set.
EOF
}

[ "$#" -eq 0 ] || { usage >&2; exit 2; }

need curl
need tar
need git
need python3
mkdir -p "$BASE" "$(dirname "$BOOTSTRAP_CACHE")"

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

say "WQPU UP: preparing client..."
archive="$BASE/wqpu-up.tar.gz"
stage="$BASE/source.new.$$"
rm -rf "$stage" "$archive"
mkdir -p "$stage"
curl -fL --retry 3 "https://codeload.github.com/${REPO}/tar.gz/${REF}" -o "$archive"
tar -xzf "$archive" -C "$stage" --strip-components=1
rm -f "$archive"
[ -f "$stage/chain/devnet.sh" ] || fail "downloaded source is incomplete"
[ -f "$stage/bootstrap-rpcs.txt" ] || fail "trusted bootstrap manifest is missing"
[ -f "$stage/bootstrap-p2p.txt" ] || fail "P2P bootstrap manifest is missing"
[ -f "$stage/chain-checkpoint.txt" ] || fail "canonical WQPU checkpoint manifest is missing"
rm -rf "$SOURCE_DIR"
mv "$stage" "$SOURCE_DIR"

say "WQPU UP: preparing runtime..."
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
for target in [("1.1.1.1", 53), ("8.8.8.8", 53)]:
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
raise SystemExit(1)
PY
)" || fail "could not determine this machine IPv4 address"
fi
case "$local_ip" in
  ""|*[!0-9.]*) fail "invalid local IPv4 address: $local_ip" ;;
esac

trusted_scheme_allowed() {
  python3 - "$1" <<'PY'
import sys
from urllib.parse import urlparse
u = urlparse(sys.argv[1])
if u.username or u.password or u.fragment or not u.hostname:
    raise SystemExit(1)
raise SystemExit(0 if u.scheme in ("https", "wss") else 1)
PY
}

dev_scheme_allowed() {
  python3 - "$1" "${WQPU_DEV_MODE:-0}" "${WQPU_DEV_ALLOW_INSECURE_RPC:-0}" <<'PY'
import sys
from urllib.parse import urlparse
u = urlparse(sys.argv[1])
dev_mode = sys.argv[2] == "1"
allow_insecure = sys.argv[3] == "1"
if u.username or u.password or u.fragment or not u.hostname:
    raise SystemExit(1)
if u.scheme in ("https", "wss"):
    raise SystemExit(0)
if dev_mode and allow_insecure and u.scheme in ("http", "ws"):
    raise SystemExit(0)
raise SystemExit(1)
PY
}

load_chain_checkpoint() {
  python3 - "$SOURCE_DIR/chain-checkpoint.txt" <<'PY'
from pathlib import Path
import re, sys
path = Path(sys.argv[1])
entries = []
for raw in path.read_text(encoding="utf-8").splitlines():
    value = raw.split("#", 1)[0].strip()
    if value:
        entries.append(value)
if len(entries) != 1:
    raise SystemExit(1)
parts = entries[0].split()
if len(parts) != 2:
    raise SystemExit(1)
block, block_hash = parts
if not block.isdigit() or int(block) <= 0 or int(block) > (2**64 - 1):
    raise SystemExit(1)
if not re.fullmatch(r"0x[0-9a-fA-F]{64}", block_hash):
    raise SystemExit(1)
print(block, block_hash.lower())
PY
}

verify_dev_rpc() {
  local candidate="$1"
  dev_scheme_allowed "$candidate" || return 1
  (cd "$SOURCE_DIR/client" && GOWORK=off go run ./cmd/wqpu-rpc-verify "$candidate") >/dev/null 2>&1
}

verify_trusted_rpc() {
  local candidate="$1" checkpoint block block_hash
  trusted_scheme_allowed "$candidate" || return 1
  checkpoint="$(load_chain_checkpoint 2>/dev/null)" || return 1
  block="${checkpoint%% *}"
  block_hash="${checkpoint#* }"
  (cd "$SOURCE_DIR/client" && GOWORK=off go run ./cmd/wqpu-rpc-verify "$candidate" "$block" "$block_hash") >/dev/null 2>&1
}

try_rpc_file() {
  local file="$1" line candidate
  [ -f "$file" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    line="$(printf '%s' "$line" | tr -d '[:space:]')"
    [ -n "$line" ] || continue
    candidate="$line"
    if verify_trusted_rpc "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done < "$file"
  return 1
}

remember_verified_rpc() {
  local candidate="$1"
  python3 - "$BOOTSTRAP_CACHE" "$candidate" <<'PY'
from pathlib import Path
import os, sys
path = Path(sys.argv[1])
candidate = sys.argv[2]
path.parent.mkdir(parents=True, exist_ok=True)
existing = []
if path.exists():
    for raw in path.read_text(encoding="utf-8").splitlines():
        value = raw.strip()
        if value and value != candidate and value not in existing:
            existing.append(value)
entries = [candidate] + existing[:31]
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text("\n".join(entries) + "\n", encoding="utf-8")
os.chmod(tmp, 0o600)
tmp.replace(path)
PY
}

find_trusted_rpc() {
  # Manual RPC injection is disabled in normal mode. It exists only for explicit
  # local development and still has to pass WQPU chain/protocol verification.
  if [ "${WQPU_DEV_MODE:-0}" = "1" ] && [ -n "${WQPU_CHAIN_RPC:-}" ]; then
    if verify_dev_rpc "$WQPU_CHAIN_RPC"; then
      printf '%s\n' "$WQPU_CHAIN_RPC"
      return 0
    fi
  fi

  # Bitcoin/Cosmos-style behavior: prefer gateways this machine already
  # verified successfully, then fall back to the shipped first-contact set.
  # Both paths are rechecked against the pinned canonical WQPU checkpoint.
  try_rpc_file "$BOOTSTRAP_CACHE" && return 0
  try_rpc_file "$SOURCE_DIR/bootstrap-rpcs.txt" && return 0
  return 1
}

wait_local_rpc() {
  local candidate="$1" pid="$2" i=0
  while [ "$i" -lt 900 ]; do
    if (cd "$SOURCE_DIR/client" && GOWORK=off go run ./cmd/wqpu-rpc-verify "$candidate") >/dev/null 2>&1; then
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      cat "$BASE/devnet.log" >&2 2>/dev/null || true
      fail "WQPU chain exited before becoming ready"
    fi
    i=$((i + 1))
    sleep 1
  done
  fail "local WQPU chain did not become ready"
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
  (cd "$SOURCE_DIR/client" && GOWORK=off go run ./cmd/wqpu-dev-node "$rpc" "$slot" "wqpu://0.0.0.0:$port" "$WQPU_RUNTIME_BASE") >"$log" 2>&1 &
  NODE_PID=$!
  wait_node_ready "$NODE_PID" "$log"
}

rpc=""
if rpc="$(find_trusted_rpc 2>/dev/null)"; then
  if [ "${WQPU_DEV_MODE:-0}" != "1" ] || [ "$rpc" != "${WQPU_CHAIN_RPC:-}" ]; then
    remember_verified_rpc "$rpc"
  fi
  say "WQPU UP: connected to verified WQPU network."
elif [ "${WQPU_DEV_LOCAL_FALLBACK:-0}" = "1" ]; then
  say "WQPU UP: development fallback enabled; starting isolated local WQPU devnet."
  export WQPU_DEV_MODE=1
  export WQPU_DEV_ALLOW_INSECURE_RPC=1
  WQPU_DEVNET_TEST_ADDRESS="$(cd "$SOURCE_DIR/chain" && GOWORK=off go run ./cmd/wqpu-compute-bootstrap address)"
  export WQPU_DEVNET_TEST_ADDRESS
  export WQPU_DEVNET_PUBLIC_RPC=0
  bash "$SOURCE_DIR/chain/devnet.sh" --reset >"$BASE/devnet.log" 2>&1 &
  CHAIN_PID=$!
  rpc="http://127.0.0.1:8545"
  wait_local_rpc "$rpc" "$CHAIN_PID"
else
  fail "trusted WQPU bootstrap/checkpoint is unavailable; refusing LAN discovery, arbitrary RPCs, or automatic network creation"
fi

slot="$(slot_helper free "$rpc")"
[ -n "$slot" ] || fail "no free devnet compute identity is available"
port="$(provider_port "$slot")"
publish_slot "$rpc" "$slot" "wqpu://$local_ip:$port"
say "WQPU UP: node registered; peer discovery is blockchain-only."

if [ -n "$CHAIN_PID" ]; then
  start_provider_background "$rpc" "$slot"
  say "WQPU UP: local development node ready."
  wait "$NODE_PID"
else
  exec bash -c 'cd "$1" && exec env GOWORK=off go run ./cmd/wqpu-dev-node "$2" "$3" "$4" "$5"' _ \
    "$SOURCE_DIR/client" "$rpc" "$slot" "wqpu://0.0.0.0:$port" "$WQPU_RUNTIME_BASE"
fi
