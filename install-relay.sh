#!/usr/bin/env sh
set -eu

PORT="${WQPU_RELAY_PORT:-7443}"
HOST="${1:-}"
ROOT="/opt/wqpu-relay"
RAW="https://raw.githubusercontent.com/eav021107-debug/WQPU/main"

need_pkg() {
  cmd="$1"; shift
  command -v "$cmd" >/dev/null 2>&1 && return 0
  if command -v dnf >/dev/null 2>&1; then dnf install -y "$@"
  elif command -v apt-get >/dev/null 2>&1; then apt-get update && apt-get install -y "$@"
  elif command -v yum >/dev/null 2>&1; then yum install -y "$@"
  else echo "Install $* first." >&2; exit 1
  fi
}

need_pkg python3 python3
need_pkg openssl openssl
need_pkg curl curl ca-certificates

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'; then
  echo "WQPU relay requires Python 3.10 or newer." >&2
  exit 1
fi

command -v systemctl >/dev/null 2>&1 || { echo "systemd is required by this installer." >&2; exit 1; }

mkdir -p "$ROOT"
curl -fsSL "$RAW/relay.py" -o "$ROOT/relay.py"
chmod 755 "$ROOT/relay.py"

if [ ! -f "$ROOT/secret" ]; then
  python3 - <<'PY' > "$ROOT/secret"
import secrets
print(secrets.token_urlsafe(32))
PY
  chmod 600 "$ROOT/secret"
fi

if [ ! -f "$ROOT/cert.pem" ] || [ ! -f "$ROOT/key.pem" ]; then
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 \
    -subj "/CN=WQPU Relay" -keyout "$ROOT/key.pem" -out "$ROOT/cert.pem" >/dev/null 2>&1
  chmod 600 "$ROOT/key.pem"
fi

if [ -z "$HOST" ]; then
  HOST="$(curl -fsS https://api.ipify.org || true)"
fi
if [ -z "$HOST" ]; then
  echo "Could not detect public IP. Run again with it: install-relay.sh PUBLIC_IP" >&2
  exit 1
fi

cat > /etc/systemd/system/wqpu-relay.service <<EOF
[Unit]
Description=WQPU equal-peer relay
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $ROOT/relay.py --host 0.0.0.0 --port $PORT --cert $ROOT/cert.pem --key $ROOT/key.pem --secret-file $ROOT/secret
Restart=always
RestartSec=2
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now wqpu-relay

if command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --add-port="${PORT}/tcp" >/dev/null
  firewall-cmd --reload >/dev/null
fi
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then
  ufw allow "${PORT}/tcp" >/dev/null
fi

FP="$(openssl x509 -in "$ROOT/cert.pem" -outform DER | openssl dgst -sha256 | awk '{print $NF}')"
SECRET="$(cat "$ROOT/secret")"
TOKEN="$(python3 - "$HOST" "$PORT" "$SECRET" "$FP" <<'PY'
import base64, json, sys
payload = {
    "host": sys.argv[1],
    "port": int(sys.argv[2]),
    "secret": sys.argv[3],
    "fingerprint": sys.argv[4],
}
raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
print("WQPU1." + raw)
PY
)"
printf '%s\n' "$TOKEN" > "$ROOT/join-token"
chmod 600 "$ROOT/join-token"

echo ""
echo "WQPU relay is running on ${HOST}:${PORT}"
echo "The VPS is relay-only: it does not run or coordinate the model."
echo ""
echo "Linux/macOS contributor command:"
echo "curl -fsSL $RAW/install-node.sh | sh -s -- '$TOKEN'"
echo ""
echo "Windows PowerShell contributor command:"
echo "\$env:WQPU_JOIN='$TOKEN'; irm $RAW/install-node.ps1 | iex"
echo ""
echo "Keep the join token private."
