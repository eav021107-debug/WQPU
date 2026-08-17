#!/usr/bin/env bash
set -euo pipefail

# First-node bootstrap for the PUBLIC WQPU TESTNET.
# This is intentionally not a mainnet launcher: it uses test-only operator keys
# and funds the existing public dev compute wallet so two-machine internet tests
# can be performed before production wallet economics are frozen.

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck disable=SC1091
. "$HERE/runtime.lock"

PUBLIC_CHAIN_ID="${WQPU_PUBLIC_CHAIN_ID:-wqpu-public-test-1}"
PUBLIC_IP="${WQPU_PUBLIC_IP:-}"
CHAIN_HOME="${WQPU_PUBLIC_HOME:-/var/lib/wqpu-public-testnet}"
BIN_DIR="${WQPU_PUBLIC_BIN_DIR:-/usr/local/lib/wqpu}"
BIN="$BIN_DIR/wqpud"
BUILD_SRC="${WQPU_PUBLIC_CHAIN_SRC:-/opt/wqpu/cosmos-evm}"
SERVICE="wqpu-public-testnet.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE"
KEYRING="test" # PUBLIC TESTNET ONLY. Never use this backend for mainnet funds.
GO_VERSION="1.25.9"
GO_ROOT="/opt/wqpu/go${GO_VERSION}"

fail() { printf 'WQPU PUBLIC TESTNET: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

[ "$(id -u)" -eq 0 ] || fail "run this first-node installer as root"
command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required"
command -v systemctl >/dev/null 2>&1 || fail "systemd is required"

if [ -z "$PUBLIC_IP" ]; then
  PUBLIC_IP="$(curl -4fsS --max-time 10 https://api.ipify.org || true)"
fi
python3 - "$PUBLIC_IP" <<'PY' || fail "could not determine a valid public IPv4 address; set WQPU_PUBLIC_IP"
import ipaddress, sys
try:
    ip = ipaddress.ip_address(sys.argv[1])
except ValueError:
    raise SystemExit(1)
if ip.version != 4 or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
    raise SystemExit(1)
PY

if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
  say "WQPU PUBLIC TESTNET: service already running."
  node_id="$(python3 "$HERE/node_id.py" "$CHAIN_HOME/config/node_key.json")"
  say "bootstrap=${node_id}@${PUBLIC_IP}:26656"
  exit 0
fi

if [ -e "$CHAIN_HOME/config/genesis.json" ]; then
  fail "$CHAIN_HOME already contains a genesis; refusing to silently recreate the first public network"
fi

say "WQPU PUBLIC TESTNET: installing build prerequisites..."
if command -v dnf >/dev/null 2>&1; then
  dnf -y install git curl tar gzip python3 gcc gcc-c++ make >/dev/null
elif command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y >/dev/null
  apt-get install -y git curl ca-certificates tar gzip python3 build-essential >/dev/null
else
  fail "supported package manager not found (dnf/apt-get)"
fi

install_private_go() {
  local arch asset expected archive tmp actual
  case "$(uname -s)" in
    Linux) ;;
    *) fail "first public bootstrap currently supports Linux only" ;;
  esac
  case "$(uname -m)" in
    x86_64|amd64)
      arch=amd64
      expected="00859d7bd6defe8bf84d9db9e57b9a4467b2887c18cd93ae7460e713db774bc1"
      ;;
    arm64|aarch64)
      arch=arm64
      expected="ec342e7389b7f489564ed5463c63b16cf8040023dabc7861256677165a8c0e2b"
      ;;
    *) fail "unsupported bootstrap CPU architecture: $(uname -m)" ;;
  esac
  asset="go${GO_VERSION}.linux-${arch}.tar.gz"
  if [ ! -x "$GO_ROOT/bin/go" ]; then
    mkdir -p /opt/wqpu
    archive="/opt/wqpu/$asset"
    tmp="/opt/wqpu/go.tmp.$$"
    say "WQPU PUBLIC TESTNET: installing private Go ${GO_VERSION}..."
    curl -fL --retry 3 "https://go.dev/dl/$asset" -o "$archive"
    actual="$(sha256sum "$archive" | awk '{print $1}')"
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

install_private_go
[ "$(go version | awk '{print $3}')" = "go${GO_VERSION}" ] || fail "wrong Go toolchain"

# Reuse the reviewed pinned-runtime builder, but install the resulting binary
# outside the devnet home. devnet.sh --build-only never creates or starts a chain.
mkdir -p "$BIN_DIR" "$(dirname "$BUILD_SRC")"
export WQPU_CHAIN_SRC="$BUILD_SRC"
export WQPU_CHAIN_BIN_DIR="$BIN_DIR"
export WQPU_CHAIN_HOME="/var/lib/wqpu-build-unused"

say "WQPU PUBLIC TESTNET: building pinned WQPU chain runtime..."
bash "$HERE/devnet.sh" --build-only
[ -x "$BIN" ] || fail "wqpud build failed"

if ! id wqpu >/dev/null 2>&1; then
  useradd --system --home-dir "$CHAIN_HOME" --create-home --shell /sbin/nologin wqpu
fi
mkdir -p "$CHAIN_HOME"
chown -R wqpu:wqpu "$CHAIN_HOME"
chmod 700 "$CHAIN_HOME"

run_wqpu() {
  runuser -u wqpu -- env HOME="$CHAIN_HOME" "$@"
}

say "WQPU PUBLIC TESTNET: creating first canonical genesis..."
run_wqpu "$BIN" init wqpu-public-bootstrap --chain-id "$PUBLIC_CHAIN_ID" --home "$CHAIN_HOME" >/dev/null
run_wqpu "$BIN" config set client chain-id "$PUBLIC_CHAIN_ID" --home "$CHAIN_HOME" >/dev/null
run_wqpu "$BIN" config set client keyring-backend "$KEYRING" --home "$CHAIN_HOME" >/dev/null

# Testnet operator key. Mainnet must use a separately designed protected key flow.
run_wqpu "$BIN" keys add validator \
  --algo eth_secp256k1 \
  --keyring-backend "$KEYRING" \
  --home "$CHAIN_HOME" \
  --output json > "$CHAIN_HOME/testnet-validator-key.json"
chmod 600 "$CHAIN_HOME/testnet-validator-key.json"
chown wqpu:wqpu "$CHAIN_HOME/testnet-validator-key.json"

python3 "$HERE/devnet_config.py" genesis "$CHAIN_HOME/config/genesis.json" \
  --base "$BASE_DENOM" \
  --display "$DISPLAY_DENOM" \
  --exponent "$DISPLAY_EXPONENT"
chown wqpu:wqpu "$CHAIN_HOME/config/genesis.json"

run_wqpu "$BIN" genesis add-genesis-account validator \
  "1000000000000000000000000${BASE_DENOM}" \
  --keyring-backend "$KEYRING" \
  --home "$CHAIN_HOME" >/dev/null

# Fund only the already-public test compute wallet. These are TESTNET coins.
DEV_EVM_ADDRESS="$(cd "$HERE" && GOWORK=off go run ./cmd/wqpu-compute-bootstrap address)"
DEV_BECH32="$(python3 "$HERE/devnet_config.py" bech32 "$DEV_EVM_ADDRESS" --prefix cosmos)"
run_wqpu "$BIN" genesis add-genesis-account "$DEV_BECH32" \
  "10000000000000000000000${BASE_DENOM}" \
  --home "$CHAIN_HOME" >/dev/null

run_wqpu "$BIN" genesis gentx validator \
  "10000000000000000000000${BASE_DENOM}" \
  --gas-prices "1${BASE_DENOM}" \
  --keyring-backend "$KEYRING" \
  --chain-id "$PUBLIC_CHAIN_ID" \
  --home "$CHAIN_HOME" >/dev/null
run_wqpu "$BIN" genesis collect-gentxs --home "$CHAIN_HOME" >/dev/null
run_wqpu "$BIN" genesis validate-genesis --home "$CHAIN_HOME" >/dev/null

python3 "$HERE/devnet_config.py" app-toml "$CHAIN_HOME/config/app.toml" \
  --evm-chain-id "$EVM_CHAIN_ID"
python3 "$HERE/devnet_config.py" config-toml "$CHAIN_HOME/config/config.toml"

empty_seeds="$CHAIN_HOME/empty-seeds.txt"
: > "$empty_seeds"
chown wqpu:wqpu "$empty_seeds"
python3 "$HERE/p2p_config.py" "$CHAIN_HOME/config/config.toml" \
  --seeds-file "$empty_seeds" \
  --seed-mode 0 \
  --external-address "tcp://${PUBLIC_IP}:26656"
rm -f "$empty_seeds"
chown wqpu:wqpu "$CHAIN_HOME/config/app.toml" "$CHAIN_HOME/config/config.toml"
chmod 600 "$CHAIN_HOME/config/priv_validator_key.json" "$CHAIN_HOME/config/node_key.json"

# JSON-RPC stays loopback-only. The only public port at this stage is authenticated
# CometBFT P2P 26656; do not expose 8545/8546/26657 to the Internet.
cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=WQPU Public Testnet Bootstrap Node
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=wqpu
Group=wqpu
ExecStart=$BIN start --home $CHAIN_HOME --chain-id $PUBLIC_CHAIN_ID --minimum-gas-prices=0${BASE_DENOM} --evm.min-tip=0
Restart=on-failure
RestartSec=5
LimitNOFILE=65535
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
ReadWritePaths=$CHAIN_HOME

[Install]
WantedBy=multi-user.target
EOF
chmod 644 "$SERVICE_PATH"

if command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --add-port=26656/tcp >/dev/null
  firewall-cmd --reload >/dev/null
fi

systemctl daemon-reload
systemctl enable --now "$SERVICE"

# Give CometBFT a moment to create its local RPC listener and first block.
ready=0
for _ in $(seq 1 90); do
  if curl -fsS --max-time 2 http://127.0.0.1:26657/status >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  journalctl -u "$SERVICE" -n 80 --no-pager >&2 || true
  fail "node did not become ready"
fi

node_id="$(python3 "$HERE/node_id.py" "$CHAIN_HOME/config/node_key.json")"
genesis_hash="$(sha256sum "$CHAIN_HOME/config/genesis.json" | awk '{print $1}')"
mkdir -p /root/wqpu-public-export
cp "$CHAIN_HOME/config/genesis.json" /root/wqpu-public-export/genesis.json
cat > /root/wqpu-public-export/info.txt <<EOF
chain_id=$PUBLIC_CHAIN_ID
evm_chain_id=$EVM_CHAIN_ID
public_ip=$PUBLIC_IP
node_id=$node_id
bootstrap=${node_id}@${PUBLIC_IP}:26656
genesis_sha256=$genesis_hash
EOF
chmod 600 /root/wqpu-public-export/genesis.json /root/wqpu-public-export/info.txt

say ""
say "WQPU PUBLIC TESTNET BOOTSTRAP READY"
say "bootstrap=${node_id}@${PUBLIC_IP}:26656"
say "genesis_sha256=${genesis_hash}"
say "export=/root/wqpu-public-export"
say "Only TCP/26656 should be public. JSON-RPC remains loopback-only."
