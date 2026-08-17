#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run as root: sudo ./scripts/install.sh"
  exit 1
fi

APP_DIR=/opt/vps-control
SERVICE_USER=${VPS_CONTROL_USER:-vpscontrol}
SOURCE_DIR=$(cd "$(dirname "$0")/.." && pwd)
PROJECT_ROOT=$(cd "$SOURCE_DIR/../.." && pwd)
WORKSPACE=${VPS_CONTROL_ROOT:-$PROJECT_ROOT}
ACTION_TOKEN=${VPS_CONTROL_ACTION_TOKEN:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')}
PUBLIC_URL=${VPS_CONTROL_PUBLIC_URL:-https://CHANGE-ME.example.com}

command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --create-home --shell /bin/bash "$SERVICE_USER"
fi

systemctl stop vps-control vps-control-actions 2>/dev/null || true
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR" "$WORKSPACE"
cp -a "$SOURCE_DIR/." "$APP_DIR/"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

# The coding user must be able to edit the selected project. Keep ownership unchanged
# when the workspace is an existing checkout; only claim a freshly-created empty path.
if [[ -z "$(ls -A "$WORKSPACE" 2>/dev/null)" ]]; then
  chown -R "$SERVICE_USER:$SERVICE_USER" "$WORKSPACE"
fi

if ! python3 -m venv "$APP_DIR/.venv"; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y python3-venv
    python3 -m venv "$APP_DIR/.venv"
  else
    echo "python3 venv support is required"
    exit 1
  fi
fi
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install "$APP_DIR"

cat >/etc/vps-control.env <<ENV
VPS_CONTROL_ROOT=$WORKSPACE
VPS_CONTROL_HOST=127.0.0.1
VPS_CONTROL_PORT=8765
VPS_CONTROL_ACTION_HOST=127.0.0.1
VPS_CONTROL_ACTION_PORT=8766
VPS_CONTROL_ACTION_TOKEN=$ACTION_TOKEN
VPS_CONTROL_PUBLIC_URL=$PUBLIC_URL
VPS_CONTROL_COMMAND_TIMEOUT=120
PYTHONDONTWRITEBYTECODE=1
ENV
chmod 0600 /etc/vps-control.env

cat >/etc/systemd/system/vps-control.service <<SERVICE
[Unit]
Description=VPS Control MCP server
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$WORKSPACE
EnvironmentFile=/etc/vps-control.env
ExecStart=$APP_DIR/.venv/bin/vps-control
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=$WORKSPACE

[Install]
WantedBy=multi-user.target
SERVICE

cat >/etc/systemd/system/vps-control-actions.service <<SERVICE
[Unit]
Description=VPS Control GPT Actions server
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$WORKSPACE
EnvironmentFile=/etc/vps-control.env
ExecStart=$APP_DIR/.venv/bin/vps-control-actions
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=$WORKSPACE

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable --now vps-control vps-control-actions

echo
echo "VPS Control installed"
echo "Workspace: $WORKSPACE"
echo "MCP local endpoint: http://127.0.0.1:8765/mcp"
echo "GPT Actions local endpoint: http://127.0.0.1:8766"
echo "GPT Actions access token (NOT an OpenAI API key):"
echo "$ACTION_TOKEN"
echo
echo "Keep this token private. It is also stored root-only in /etc/vps-control.env"
