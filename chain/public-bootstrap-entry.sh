#!/usr/bin/env bash
set -euo pipefail

# Safe entrypoint for the first public WQPU testnet node.
# Cosmos EVM initializes some application data relative to the process working
# directory, so both bootstrap commands and the systemd service must start from
# the dedicated writable chain home.

HERE="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
CHAIN_HOME="${WQPU_PUBLIC_HOME:-/var/lib/wqpu-public-testnet}"
SERVICE="wqpu-public-testnet.service"
DROPIN_DIR="/etc/systemd/system/${SERVICE}.d"
RESET_PARTIAL="${WQPU_PUBLIC_RESET_PARTIAL:-0}"

[ "$(id -u)" -eq 0 ] || { echo "WQPU PUBLIC TESTNET: run as root" >&2; exit 1; }

if [ "$RESET_PARTIAL" = "1" ]; then
  if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
    echo "WQPU PUBLIC TESTNET: refusing to reset while service is active" >&2
    exit 1
  fi
  rm -rf "$CHAIN_HOME"
elif [ "$RESET_PARTIAL" != "0" ]; then
  echo "WQPU PUBLIC TESTNET: WQPU_PUBLIC_RESET_PARTIAL must be 0 or 1" >&2
  exit 2
fi

mkdir -p "$CHAIN_HOME" "$DROPIN_DIR"
chmod 700 "$CHAIN_HOME"

cat > "$DROPIN_DIR/working-directory.conf" <<EOF
[Service]
WorkingDirectory=$CHAIN_HOME
EOF
chmod 644 "$DROPIN_DIR/working-directory.conf"

# public-bootstrap.sh creates/chowns the service account before invoking wqpud.
# Starting the installer from CHAIN_HOME makes runuser inherit this writable cwd.
cd "$CHAIN_HOME"
exec bash "$HERE/public-bootstrap.sh"
