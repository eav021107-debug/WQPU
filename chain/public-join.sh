#!/usr/bin/env bash
set -euo pipefail

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck disable=SC1091
. "$HERE/runtime.lock"

GENESIS_PATH="${1:-${WQPU_PUBLIC_GENESIS:-$HOME/Desktop/genesis.json}}"
INFO_PATH="${2:-${WQPU_PUBLIC_INFO:-$HOME/Desktop/info.txt}}"
BASE="${WQPU_PUBLIC_JOIN_HOME:-$HOME/.local/share/wqpu-public-testnet}"
CHAIN_HOME="$BASE/node"
BIN_DIR="$BASE/bin"
BIN="$BIN_DIR/wqpud"
BUILD_SRC="$BASE/cosmos-evm"
GO_VERSION="1.25.9"
GO_ROOT="$BASE/toolchains/go${GO_VERSION}"
LOG_DIR="$BASE/logs"

fail() { printf 'WQPU PUBLIC JOIN: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || fail "requires '$1'"; }

need curl
need git
need python3
need tar
[ -f "$GENESIS_PATH" ] || fail "genesis file not found: $GENESIS_PATH"
[ -f "$INFO_PATH" ] || fail "network info file not found: $INFO_PATH"

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

network_fields="$(python3 - "$INFO_PATH" <<'PY'
from pathlib import Path
import re, sys
p = Path(sys.argv[1])
values = {}
for raw in p.read_text(encoding='utf-8').splitlines():
    if '=' not in raw:
        continue
    k, v = raw.split('=', 1)
    values[k.strip()] = v.strip()
required = ('chain_id','evm_chain_id','bootstrap','genesis_sha256')
if any(not values.get(k) for k in required):
    raise SystemExit(1)
if not re.fullmatch(r'[A-Za-z0-9._-]{1,80}', values['chain_id']):
    raise SystemExit(1)
if not values['evm_chain_id'].isdigit():
    raise SystemExit(1)
if not re.fullmatch(r'[0-9a-fA-F]{40}@[A-Za-z0-9.:-]+', values['bootstrap']):
    raise SystemExit(1)
if not re.fullmatch(r'[0-9a-fA-F]{64}', values['genesis_sha256']):
    raise SystemExit(1)
print(values['chain_id'])
print(values['evm_chain_id'])
print(values['bootstrap'].lower())
print(values['genesis_sha256'].lower())
PY
)" || fail "invalid network info file"

CHAIN_ID="$(printf '%s\n' "$network_fields" | sed -n '1p')"
INFO_EVM_CHAIN_ID="$(printf '%s\n' "$network_fields" | sed -n '2p')"
BOOTSTRAP="$(printf '%s\n' "$network_fields" | sed -n '3p')"
EXPECTED_GENESIS_SHA="$(printf '%s\n' "$network_fields" | sed -n '4p')"
[ "$INFO_EVM_CHAIN_ID" = "$EVM_CHAIN_ID" ] || fail "network EVM chain id does not match pinned WQPU runtime"
ACTUAL_GENESIS_SHA="$(sha256_file "$GENESIS_PATH")"
[ "$ACTUAL_GENESIS_SHA" = "$EXPECTED_GENESIS_SHA" ] || fail "genesis checksum mismatch"

install_private_go() {
  local os arch asset expected archive tmp actual
  case "$(uname -s)" in
    Darwin) os=darwin ;;
    Linux) os=linux ;;
    *) fail "public join currently supports macOS and Linux" ;;
  esac
  case "$(uname -m)" in
    x86_64|amd64) arch=amd64 ;;
    arm64|aarch64) arch=arm64 ;;
    *) fail "unsupported CPU architecture: $(uname -m)" ;;
  esac
  case "${os}/${arch}" in
    linux/amd64) expected="00859d7bd6defe8bf84d9db9e57b9a4467b2887c18cd93ae7460e713db774bc1" ;;
    linux/arm64) expected="ec342e7389b7f489564ed5463c63b16cf8040023dabc7861256677165a8c0e2b" ;;
    darwin/amd64) expected="92cb78fba4796e218c1accb0ea0a214ef2094c382049a244ad6505505d015fbe" ;;
    darwin/arm64) expected="9528be7329b9770631a6bd09ca2f3a73ed7332bec01d87435e75e92d8f130363" ;;
  esac
  asset="go${GO_VERSION}.${os}-${arch}.tar.gz"
  if [ ! -x "$GO_ROOT/bin/go" ]; then
    mkdir -p "$(dirname "$GO_ROOT")"
    archive="$BASE/$asset"
    tmp="$GO_ROOT.tmp.$$"
    say "WQPU PUBLIC JOIN: installing private Go ${GO_VERSION}..."
    curl -fL --retry 3 "https://go.dev/dl/$asset" -o "$archive"
    actual="$(sha256_file "$archive")"
    [ "$actual" = "$expected" ] || fail "Go archive checksum mismatch"
    rm -rf "$tmp"
    mkdir -p "$tmp"
    tar -xzf "$archive" -C "$tmp"
    rm -f "$archive"
    [ -x "$tmp/go/bin/go" ] || fail "private Go extraction failed"
    rm -rf "$GO_ROOT"
    mv "$tmp/go" "$GO_ROOT"
    rm -rf "$tmp"
  fi
  export GOROOT="$GO_ROOT"
  export PATH="$GOROOT/bin:$PATH"
  export GOTOOLCHAIN=local
}

mkdir -p "$BASE" "$BIN_DIR" "$LOG_DIR"
install_private_go
[ "$(go version | awk '{print $3}')" = "go${GO_VERSION}" ] || fail "wrong Go toolchain"

export WQPU_CHAIN_SRC="$BUILD_SRC"
export WQPU_CHAIN_BIN_DIR="$BIN_DIR"
export WQPU_CHAIN_HOME="$CHAIN_HOME"

say "WQPU PUBLIC JOIN: building pinned WQPU runtime..."
bash "$HERE/devnet.sh" --build-only
[ -x "$BIN" ] || fail "wqpud build failed"

if [ ! -f "$CHAIN_HOME/config/config.toml" ]; then
  say "WQPU PUBLIC JOIN: creating local blockchain node..."
  mkdir -p "$CHAIN_HOME"
  (
    cd "$CHAIN_HOME"
    "$BIN" init wqpu-public-fullnode --chain-id "$CHAIN_ID" --home "$CHAIN_HOME" >/dev/null
  )
fi

cp "$GENESIS_PATH" "$CHAIN_HOME/config/genesis.json"
[ "$(sha256_file "$CHAIN_HOME/config/genesis.json")" = "$EXPECTED_GENESIS_SHA" ] || fail "copied genesis checksum mismatch"
"$BIN" config set client chain-id "$CHAIN_ID" --home "$CHAIN_HOME" >/dev/null
python3 "$HERE/devnet_config.py" app-toml "$CHAIN_HOME/config/app.toml" --evm-chain-id "$EVM_CHAIN_ID"
python3 "$HERE/devnet_config.py" config-toml "$CHAIN_HOME/config/config.toml"
SEEDS_FILE="$CHAIN_HOME/bootstrap-seeds.txt"
printf '%s\n' "$BOOTSTRAP" > "$SEEDS_FILE"
python3 "$HERE/p2p_config.py" "$CHAIN_HOME/config/config.toml" --seeds-file "$SEEDS_FILE" --seed-mode 0

start_args=("$BIN" start --home "$CHAIN_HOME" --chain-id "$CHAIN_ID" --minimum-gas-prices="0${BASE_DENOM}" --evm.min-tip=0)

if [ "$(uname -s)" = "Darwin" ]; then
  AGENT_DIR="$HOME/Library/LaunchAgents"
  PLIST="$AGENT_DIR/com.wqpu.public-testnet.plist"
  mkdir -p "$AGENT_DIR"
  python3 - "$PLIST" "$CHAIN_HOME" "$LOG_DIR" "${start_args[@]}" <<'PY'
import plistlib, sys
plist, cwd, logs, *args = sys.argv[1:]
data = {
    'Label': 'com.wqpu.public-testnet',
    'ProgramArguments': args,
    'WorkingDirectory': cwd,
    'RunAtLoad': True,
    'KeepAlive': True,
    'StandardOutPath': logs + '/node.log',
    'StandardErrorPath': logs + '/node.err.log',
}
with open(plist, 'wb') as f:
    plistlib.dump(data, f)
PY
  launchctl bootout "gui/$(id -u)/com.wqpu.public-testnet" >/dev/null 2>&1 || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
else
  nohup "${start_args[@]}" >"$LOG_DIR/node.log" 2>"$LOG_DIR/node.err.log" &
  printf '%s\n' "$!" > "$BASE/node.pid"
fi

ready=0
for _ in $(seq 1 120); do
  if curl -fsS --max-time 2 http://127.0.0.1:26657/status >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  tail -80 "$LOG_DIR/node.err.log" >&2 2>/dev/null || true
  tail -80 "$LOG_DIR/node.log" >&2 2>/dev/null || true
  fail "local full node did not become ready"
fi

status="$(curl -fsS http://127.0.0.1:26657/status)"
height="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["sync_info"]["latest_block_height"])' <<<"$status")"
catching="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["sync_info"]["catching_up"])' <<<"$status")"

say ""
say "WQPU PUBLIC NODE READY"
say "chain_id=$CHAIN_ID"
say "bootstrap=$BOOTSTRAP"
say "height=$height"
say "catching_up=$catching"
say "home=$CHAIN_HOME"
