#!/usr/bin/env sh
set -eu

RAW="${WQPU_RAW_BASE:-https://raw.githubusercontent.com/eav021107-debug/WQPU/main}"
SOURCE_REPO="${WQPU_SOURCE_REPO:-eav021107-debug/WQPU}"
SOURCE_REF="${WQPU_SOURCE_REF:-main}"
GITHUB_RAW_PREFIX="https://raw.githubusercontent.com/${SOURCE_REPO}/"
USE_ARCHIVE=0
if [ -z "${WQPU_RAW_BASE:-}" ]; then
  USE_ARCHIVE=1
elif [ "${RAW#${GITHUB_RAW_PREFIX}}" != "$RAW" ]; then
  SOURCE_REF="${RAW#${GITHUB_RAW_PREFIX}}"
  USE_ARCHIVE=1
fi
ROOT="${HOME}/.local/share/wqpu"
BIN="${HOME}/.local/bin"
JOIN="${WQPU_JOIN:-${1:-}}"
EXPECTED_WQPU="WQPU 0.6.0"
CHAIN_STATE="${HOME}/.wqpu/chain.json"
FILES="wqpu.py wqpu_accel.py wqpu_gpu_patch.py wqpu_chain.py wqpu_wallet.py wqpu_session.py wqpu_meter.py wqpu_accounting.py wqpu_attestation.py wqpu_payments.py wqpu_claim.py wqpu_vouchers.py wqpu_runtime.py wqpu_autopay.py wqpu_multistream.py wqpu_runtime_pin.py wqpu_network_guard.py wqpu_node_identity.py wqpu_node_status.py wqpu_public_config.py wqpu_public_security.py wqpu_entry.py network-config.json"

need() { command -v "$1" >/dev/null 2>&1; }
python_ok() { "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,6) else 1)' >/dev/null 2>&1; }

if [ "$(id -u)" = "0" ]; then SUDO=""; elif need sudo; then SUDO="sudo"; else SUDO=""; fi

install_base_tools() {
  if need curl && need openssl && need tar; then return 0; fi
  if need apt-get; then
    $SUDO apt-get update
    $SUDO apt-get install -y curl ca-certificates openssl tar gzip >/dev/null 2>&1 || true
  elif need dnf; then $SUDO dnf install -y curl ca-certificates openssl tar gzip >/dev/null 2>&1 || true
  elif need yum; then $SUDO yum install -y curl ca-certificates openssl tar gzip >/dev/null 2>&1 || true
  elif need brew; then
    need curl || brew install curl >/dev/null 2>&1 || true
    need openssl || brew install openssl >/dev/null 2>&1 || true
  fi
}

find_python() {
  for p in python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python3.7 python3.6 python3; do
    if need "$p" && python_ok "$p"; then command -v "$p"; return 0; fi
  done
  return 1
}

install_python() {
  if need apt-get; then $SUDO apt-get update; $SUDO apt-get install -y python3 >/dev/null 2>&1 || true
  elif need dnf; then $SUDO dnf install -y python3 >/dev/null 2>&1 || true
  elif need yum; then $SUDO yum install -y python3 >/dev/null 2>&1 || true
  elif need brew; then brew install python >/dev/null 2>&1 || true
  fi
}

download_one() {
  url="$1"; dest="$2"
  curl -fsSL --retry 4 --retry-delay 1 "$url" -o "$dest"
}

install_from_archive() {
  [ "$USE_ARCHIVE" = "1" ] || return 1
  need tar || return 1
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/wqpu-client.XXXXXX")" || return 1
  archive="$tmp/wqpu.tar.gz"
  extract="$tmp/src"
  mkdir -p "$extract"
  url="https://codeload.github.com/${SOURCE_REPO}/tar.gz/${SOURCE_REF}"
  if ! download_one "$url" "$archive"; then rm -rf "$tmp"; return 1; fi
  if ! tar -xzf "$archive" -C "$extract"; then rm -rf "$tmp"; return 1; fi
  source_dir="$(find "$extract" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
  [ -n "$source_dir" ] || { rm -rf "$tmp"; return 1; }
  for file in $FILES; do
    [ -f "$source_dir/$file" ] || { rm -rf "$tmp"; return 1; }
  done
  for file in $FILES; do cp "$source_dir/$file" "$ROOT/$file"; done
  rm -rf "$tmp"
  return 0
}

install_base_tools
need curl || { echo "WQPU could not install curl." >&2; exit 1; }
need openssl || { echo "WQPU could not install OpenSSL." >&2; exit 1; }
PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then echo "WQPU: installing Python..."; install_python; PYTHON="$(find_python || true)"; fi
[ -n "$PYTHON" ] || { echo "WQPU needs Python 3.6 or newer." >&2; exit 1; }

mkdir -p "$ROOT" "$BIN"
echo "WQPU: downloading runtime..."
if ! install_from_archive; then
  echo "WQPU: source archive unavailable; falling back to individual files."
  for file in $FILES; do download_one "${RAW}/${file}" "$ROOT/$file"; done
fi
chmod 755 "$ROOT"/*.py

"$PYTHON" -m py_compile \
  "$ROOT/wqpu.py" "$ROOT/wqpu_accel.py" "$ROOT/wqpu_gpu_patch.py" "$ROOT/wqpu_chain.py" "$ROOT/wqpu_wallet.py" "$ROOT/wqpu_session.py" \
  "$ROOT/wqpu_meter.py" "$ROOT/wqpu_accounting.py" "$ROOT/wqpu_attestation.py" "$ROOT/wqpu_payments.py" "$ROOT/wqpu_claim.py" "$ROOT/wqpu_vouchers.py" \
  "$ROOT/wqpu_runtime.py" "$ROOT/wqpu_autopay.py" "$ROOT/wqpu_multistream.py" "$ROOT/wqpu_runtime_pin.py" "$ROOT/wqpu_network_guard.py" "$ROOT/wqpu_node_identity.py" "$ROOT/wqpu_node_status.py" "$ROOT/wqpu_public_config.py" "$ROOT/wqpu_public_security.py" "$ROOT/wqpu_entry.py" || {
    echo "WQPU files were downloaded but did not pass the Python compatibility check." >&2; exit 1;
  }
"$PYTHON" -c 'import json,sys; json.load(open(sys.argv[1]))' "$ROOT/network-config.json" || { echo "WQPU network configuration is invalid." >&2; exit 1; }

CORE_VERSION="$("$PYTHON" "$ROOT/wqpu_entry.py" --version 2>&1 || true)"
[ "$CORE_VERSION" = "$EXPECTED_WQPU" ] || { echo "WQPU version mismatch: expected '$EXPECTED_WQPU', got '${CORE_VERSION:-unknown}'." >&2; exit 1; }
PUBLIC_ENABLED="$("$PYTHON" -c 'import json,sys; d=json.load(open(sys.argv[1])); print("1" if (d.get("public") or {}).get("enabled") else "0")' "$ROOT/network-config.json")"

cat > "$BIN/wqpu" <<EOF
#!/usr/bin/env sh
exec "$PYTHON" "$ROOT/wqpu_entry.py" "\$@"
EOF
chmod 755 "$BIN/wqpu"
export PATH="$BIN:$PATH"

shell_name="$(basename "${SHELL:-sh}")"; rc=""
[ "$shell_name" = "zsh" ] && rc="$HOME/.zshrc"
[ "$shell_name" = "bash" ] && rc="$HOME/.bashrc"
if [ -n "$rc" ]; then touch "$rc"; grep -F '$HOME/.local/bin' "$rc" >/dev/null 2>&1 || printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"; fi

echo "WQPU installed: $CORE_VERSION with $($PYTHON --version 2>&1)."
if [ "${WQPU_NO_START:-0}" = "1" ]; then echo "WQPU install-only mode: not starting the node."; exit 0; fi
if [ -n "$JOIN" ]; then exec "$BIN/wqpu" --join "$JOIN"
elif { [ -n "${WQPU_RPC_URL:-}" ] && [ -n "${WQPU_REGISTRY:-}" ]; } || [ -f "$CHAIN_STATE" ] || [ "$PUBLIC_ENABLED" = "1" ]; then exec "$BIN/wqpu"
else echo "WQPU public chain is not published yet; starting the existing private mesh."; exec "$BIN/wqpu" --legacy
fi