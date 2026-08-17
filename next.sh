#!/usr/bin/env bash
set -euo pipefail

REPO="eav021107-debug/WQPU"
REF="${WQPU_REF:-next-foundation}"
BASE="${WQPU_NEXT_HOME:-$HOME/.local/share/wqpu-next}"
SOURCE_DIR="${WQPU_SOURCE_DIR:-}"
GO_VERSION="1.25.9"
RPC="http://127.0.0.1:8545"
CHAIN_PID=""

say() { printf '%s\n' "$*"; }
fail() { printf 'WQPU NEXT: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "requires '$1'"; }

cleanup() {
  if [ -n "$CHAIN_PID" ]; then
    kill "$CHAIN_PID" 2>/dev/null || true
    wait "$CHAIN_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

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
    *) fail "one-command next smoke currently supports Linux and macOS" ;;
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
    say "WQPU NEXT: installing private Go ${GO_VERSION}..."
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

if [ "${WQPU_USE_SYSTEM_GO:-0}" = "1" ]; then
  need go
else
  install_private_go
fi

case "$(go version)" in
  *"go${GO_VERSION}"*) ;;
  *) fail "Go ${GO_VERSION} is required; got: $(go version)" ;;
esac
export GOTOOLCHAIN=local
export GOMODCACHE="${GOMODCACHE:-$BASE/go/pkg/mod}"
export GOCACHE="${GOCACHE:-$BASE/go/cache}"
mkdir -p "$GOMODCACHE" "$GOCACHE"

if [ -z "$SOURCE_DIR" ]; then
  SOURCE_DIR="$BASE/source"
  archive="$BASE/wqpu-${REF}.tar.gz"
  stage="$BASE/source.new.$$"
  say "WQPU NEXT: downloading ${REPO}@${REF}..."
  rm -rf "$stage" "$archive"
  mkdir -p "$stage"
  curl -fL --retry 3 "https://codeload.github.com/${REPO}/tar.gz/refs/heads/${REF}" -o "$archive"
  tar -xzf "$archive" -C "$stage" --strip-components=1
  rm -f "$archive"
  [ -f "$stage/chain/devnet.sh" ] || fail "downloaded source is incomplete"
  rm -rf "$SOURCE_DIR"
  mv "$stage" "$SOURCE_DIR"
fi

[ -f "$SOURCE_DIR/chain/devnet.sh" ] || fail "WQPU source directory is invalid: $SOURCE_DIR"

export WQPU_CHAIN_SRC="${WQPU_CHAIN_SRC:-$BASE/chain-src/cosmos-evm}"
export WQPU_CHAIN_BIN_DIR="${WQPU_CHAIN_BIN_DIR:-$BASE/chain-bin}"
export WQPU_CHAIN_HOME="${WQPU_CHAIN_HOME:-$BASE/devnet-home}"
RUNTIME_BASE="${WQPU_RUNTIME_BASE:-$BASE/llama-runtime}"
CHAIN_LOG="${WQPU_CHAIN_LOG:-$BASE/devnet.log}"
COMPUTE_LOG="${WQPU_COMPUTE_LOG:-$BASE/compute.log}"
mkdir -p "$WQPU_CHAIN_BIN_DIR" "$RUNTIME_BASE"

say "WQPU NEXT: preparing deterministic devnet compute identity..."
WQPU_DEVNET_TEST_ADDRESS="$(cd "$SOURCE_DIR/chain" && go run ./cmd/wqpu-compute-bootstrap address)"
export WQPU_DEVNET_TEST_ADDRESS

say "WQPU NEXT: building and starting sovereign devnet..."
bash "$SOURCE_DIR/chain/devnet.sh" --reset >"$CHAIN_LOG" 2>&1 &
CHAIN_PID=$!

ready=0
attempts="${WQPU_READY_ATTEMPTS:-600}"
i=0
while [ "$i" -lt "$attempts" ]; do
  if ! kill -0 "$CHAIN_PID" 2>/dev/null; then
    cat "$CHAIN_LOG" >&2 || true
    fail "devnet exited before becoming ready"
  fi
  response="$(curl -fsS -H 'content-type: application/json' --data '{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}' "$RPC" 2>/dev/null || true)"
  if python3 - "$response" <<'PY'
import json, sys
try:
    value = int(json.loads(sys.argv[1]).get('result', '0x0'), 16)
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if value >= 1 else 1)
PY
  then
    ready=1
    break
  fi
  i=$((i + 1))
  sleep 2
done
[ "$ready" -eq 1 ] || { cat "$CHAIN_LOG" >&2 || true; fail "devnet did not produce block 1"; }

say "WQPU NEXT: publishing three equal compute nodes on-chain..."
(cd "$SOURCE_DIR/chain" && go run ./cmd/wqpu-compute-bootstrap publish "$RPC")

say "WQPU NEXT: splitting one tiny LLM across two remote compute nodes..."
(cd "$SOURCE_DIR/client" && go run ./cmd/wqpu-live-chain-compute-smoke "$RPC" "$RUNTIME_BASE") 2>&1 | tee "$COMPUTE_LOG"

grep -Fq 'LIVE CHAIN COMPUTE PASSED' "$COMPUTE_LOG" || fail "distributed compute proof was not produced"
grep -Fq '[alloc_buffer]' "$RUNTIME_BASE/live-chain-rpc-1.log" || fail "remote node RPC0 did not allocate model memory"
grep -Fq '[alloc_buffer]' "$RUNTIME_BASE/live-chain-rpc-2.log" || fail "remote node RPC1 did not allocate model memory"
grep -Fq '0x0000000000000000000000000000000000000900' "$CHAIN_LOG" || fail "native WQPU precompile was not started"

say "WQPU NEXT PASSED: chain + registry + 3 nodes + distributed model + inference"
