#!/usr/bin/env bash
set -euo pipefail

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
# shellcheck disable=SC1091
. "$HERE/runtime.lock"

SRC_DIR="${WQPU_CHAIN_SRC:-$HOME/.cache/wqpu-chain/cosmos-evm-$COSMOS_EVM_TAG}"
BIN_DIR="${WQPU_CHAIN_BIN_DIR:-$HOME/.local/share/wqpu-chain/bin}"
CHAIN_HOME="${WQPU_CHAIN_HOME:-$HOME/.wqpu-chain-dev}"
DEV_TEST_ADDRESS="${WQPU_DEVNET_TEST_ADDRESS:-}"
BIN="$BIN_DIR/wqpud"
KEYRING="test" # local devnet only; never use this backend for a public validator
RESET=0
BUILD_ONLY=0

usage() {
  cat <<'EOF'
Usage: chain/devnet.sh [--reset] [--build-only]

--reset       delete only the local WQPU devnet state and create a fresh genesis
--build-only  fetch/verify/patch/build the pinned WQPU chain runtime, then exit
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --reset) RESET=1 ;;
    --build-only) BUILD_ONLY=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "WQPU devnet requires '$1'." >&2
    exit 1
  }
}

need git
need go
need python3

mkdir -p "$(dirname "$SRC_DIR")" "$BIN_DIR"

if [ ! -d "$SRC_DIR/.git" ]; then
  echo "WQPU chain: fetching pinned consensus/runtime source $COSMOS_EVM_TAG..."
  rm -rf "$SRC_DIR"
  git clone --filter=blob:none --no-checkout https://github.com/cosmos/evm.git "$SRC_DIR"
fi

git -C "$SRC_DIR" fetch --depth=1 origin "$COSMOS_EVM_COMMIT"
git -C "$SRC_DIR" checkout --detach --force "$COSMOS_EVM_COMMIT" >/dev/null
ACTUAL_COMMIT="$(git -C "$SRC_DIR" rev-parse HEAD)"
if [ "$ACTUAL_COMMIT" != "$COSMOS_EVM_COMMIT" ]; then
  echo "WQPU chain runtime verification failed." >&2
  exit 1
fi

echo "WQPU chain: applying native 0x0900 wallet/provider/job/receipt/escrow/bond/price overlay..."
python3 "$HERE/runtime_patch_settlement.py" \
  --source "$SRC_DIR" \
  --overlay "$HERE/x/wqpu/precompile"

if ! grep -q 'WithWQPUSettlementNetwork' "$SRC_DIR/evmd/app.go"; then
  echo "WQPU chain overlay registration verification failed." >&2
  exit 1
fi

echo "WQPU chain: building pinned WQPU runtime..."
(
  cd "$SRC_DIR/evmd"
  GOWORK=off go build -trimpath -o "$BIN" ./cmd/evmd
)
chmod 755 "$BIN"

if [ "$BUILD_ONLY" -eq 1 ]; then
  echo "WQPU chain runtime built: $BIN"
  exit 0
fi

if [ "$RESET" -eq 1 ]; then
  rm -rf "$CHAIN_HOME"
fi

if [ ! -f "$CHAIN_HOME/config/genesis.json" ]; then
  echo "WQPU chain: creating fresh local genesis..."
  mkdir -p "$CHAIN_HOME"

  "$BIN" init wqpu-dev-validator --chain-id "$CHAIN_ID" --home "$CHAIN_HOME" >/dev/null
  "$BIN" config set client chain-id "$CHAIN_ID" --home "$CHAIN_HOME" >/dev/null
  "$BIN" config set client keyring-backend "$KEYRING" --home "$CHAIN_HOME" >/dev/null

  # This is an operational validator key for the isolated local devnet, not a
  # user wallet. User wallets are never generated or imported by WQPU.
  VALIDATOR_KEY_JSON="$CHAIN_HOME/dev-validator-key.json"
  umask 077
  "$BIN" keys add validator \
    --algo eth_secp256k1 \
    --keyring-backend "$KEYRING" \
    --home "$CHAIN_HOME" \
    --output json > "$VALIDATOR_KEY_JSON"

  python3 "$HERE/devnet_config.py" genesis "$CHAIN_HOME/config/genesis.json" \
    --base "$BASE_DENOM" \
    --display "$DISPLAY_DENOM" \
    --exponent "$DISPLAY_EXPONENT"

  python3 "$HERE/devnet_config.py" app-toml "$CHAIN_HOME/config/app.toml" \
    --evm-chain-id "$EVM_CHAIN_ID"

  # 1,000,000 WQPU for the dev validator; 10,000 WQPU bonded at genesis.
  "$BIN" genesis add-genesis-account validator \
    "1000000000000000000000000${BASE_DENOM}" \
    --keyring-backend "$KEYRING" \
    --home "$CHAIN_HOME" >/dev/null

  # Optional deterministic test-only EVM address for CI/live RPC smoke tests.
  # The address is public test infrastructure and must never be used for real funds.
  if [ -n "$DEV_TEST_ADDRESS" ]; then
    "$BIN" genesis add-genesis-account "$DEV_TEST_ADDRESS" \
      "1000000000000000000000${BASE_DENOM}" \
      --home "$CHAIN_HOME" >/dev/null
  fi

  "$BIN" genesis gentx validator \
    "10000000000000000000000${BASE_DENOM}" \
    --gas-prices "1${BASE_DENOM}" \
    --keyring-backend "$KEYRING" \
    --chain-id "$CHAIN_ID" \
    --home "$CHAIN_HOME" >/dev/null

  "$BIN" genesis collect-gentxs --home "$CHAIN_HOME" >/dev/null
  "$BIN" genesis validate-genesis --home "$CHAIN_HOME" >/dev/null
fi

echo "WQPU sovereign devnet"
echo "  chain-id:      $CHAIN_ID"
echo "  EVM chain-id:  $EVM_CHAIN_ID"
echo "  native coin:   $DISPLAY_DENOM ($BASE_DENOM)"
echo "  precompile:    0x0000000000000000000000000000000000000900"
echo "  JSON-RPC:      http://127.0.0.1:8545"
echo "  data:          $CHAIN_HOME"
echo

echo "Starting wqpud..."
exec "$BIN" start \
  --home "$CHAIN_HOME" \
  --chain-id "$CHAIN_ID" \
  --minimum-gas-prices="0${BASE_DENOM}" \
  --evm.min-tip=0
