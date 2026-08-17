#!/usr/bin/env sh
set -eu

REPO="${WQPU_OPERATOR_REPO:-eav021107-debug/WQPU}"
REF="${WQPU_OPERATOR_REF:-main}"
ROOT="${WQPU_OPERATOR_ROOT:-${HOME}/.local/share/wqpu-operator}"
BIN="${HOME}/.local/bin"
ARCHIVE_URL="${WQPU_OPERATOR_ARCHIVE_URL:-https://api.github.com/repos/${REPO}/tarball/${REF}}"

need() { command -v "$1" >/dev/null 2>&1; }
python_ok() { "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,8) else 1)' >/dev/null 2>&1; }

if [ "$(id -u)" = "0" ]; then SUDO=""; elif need sudo; then SUDO="sudo"; else SUDO=""; fi

install_base_tools() {
  if need curl && need tar && need openssl; then return 0; fi
  if need apt-get; then
    $SUDO apt-get update
    $SUDO apt-get install -y curl ca-certificates tar gzip openssl >/dev/null
  elif need dnf; then
    $SUDO dnf install -y curl ca-certificates tar gzip openssl >/dev/null
  elif need yum; then
    $SUDO yum install -y curl ca-certificates tar gzip openssl >/dev/null
  elif need brew; then
    need curl || brew install curl >/dev/null
    need openssl || brew install openssl >/dev/null
  else
    echo "WQPU operator: install curl, tar and OpenSSL, then rerun this command." >&2
    exit 1
  fi
}

find_python() {
  for p in python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python3; do
    if need "$p" && python_ok "$p"; then command -v "$p"; return 0; fi
  done
  return 1
}

install_python() {
  if need apt-get; then $SUDO apt-get update; $SUDO apt-get install -y python3 >/dev/null
  elif need dnf; then $SUDO dnf install -y python3 >/dev/null
  elif need yum; then $SUDO yum install -y python3 >/dev/null
  elif need brew; then brew install python >/dev/null
  fi
}

install_foundry() {
  export PATH="$HOME/.foundry/bin:$PATH"
  if need anvil && need forge && need cast; then return 0; fi
  if [ "${WQPU_OPERATOR_SKIP_FOUNDRY:-0}" = "1" ]; then
    echo "WQPU operator: Foundry tools are missing while WQPU_OPERATOR_SKIP_FOUNDRY=1." >&2
    exit 1
  fi
  echo "WQPU operator: installing Foundry in user space..."
  curl -fsSL --proto '=https' --tlsv1.2 https://foundry.paradigm.xyz | sh >/dev/null
  export PATH="$HOME/.foundry/bin:$PATH"
  need foundryup || { echo "WQPU operator: foundryup installation failed." >&2; exit 1; }
  foundryup >/dev/null
  need anvil && need forge && need cast || { echo "WQPU operator: Foundry tools are unavailable after install." >&2; exit 1; }
}

install_base_tools
need curl && need tar && need openssl || { echo "WQPU operator prerequisites are unavailable." >&2; exit 1; }
PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
  echo "WQPU operator: installing Python 3.8+..."
  install_python
  PYTHON="$(find_python || true)"
fi
[ -n "$PYTHON" ] || { echo "WQPU testnet operator needs Python 3.8 or newer." >&2; exit 1; }

install_foundry

mkdir -p "$ROOT" "$BIN"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/wqpu-operator.XXXXXX")"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT INT TERM

echo "WQPU operator: downloading source ref ${REF}..."
curl -fsSL --retry 3 -H 'Accept: application/vnd.github+json' "$ARCHIVE_URL" -o "$TMP/wqpu.tar.gz"
tar -xzf "$TMP/wqpu.tar.gz" -C "$TMP"
SRC="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
[ -n "$SRC" ] && [ -f "$SRC/scripts/testnet_stack.py" ] || { echo "WQPU operator archive is invalid." >&2; exit 1; }

# Copy over code without deleting ROOT. This intentionally preserves ROOT/.wqpu-testnet
# (chain state, operator key, deployment and relay identity) on upgrades/reinstalls.
cp -R "$SRC"/. "$ROOT"/
chmod 755 "$ROOT/scripts/testnet_stack.py" "$ROOT/wqpu_rpc_gateway.py" "$ROOT/wqpu_relayer.py" "$ROOT/wqpu_transport_relay.py"

"$PYTHON" -m py_compile \
  "$ROOT/scripts/testnet_stack.py" \
  "$ROOT/scripts/devnet.py" \
  "$ROOT/wqpu_rpc_gateway.py" \
  "$ROOT/wqpu_relayer.py" \
  "$ROOT/wqpu_transport_relay.py" \
  "$ROOT/wqpu_wallet.py"
(cd "$ROOT" && forge build >/dev/null)

cat > "$BIN/wqpu-testnet" <<EOF
#!/usr/bin/env sh
export PATH="$HOME/.foundry/bin:\$PATH"
export WQPU_TESTNET_CLIENT_REF='${REF}'
exec "$PYTHON" "$ROOT/scripts/testnet_stack.py" "\$@"
EOF
chmod 755 "$BIN/wqpu-testnet"
export PATH="$BIN:$HOME/.foundry/bin:$PATH"

shell_name="$(basename "${SHELL:-sh}")"
rc=""
[ "$shell_name" = "zsh" ] && rc="$HOME/.zshrc"
[ "$shell_name" = "bash" ] && rc="$HOME/.bashrc"
if [ -n "$rc" ]; then
  touch "$rc"
  grep -F '$HOME/.local/bin' "$rc" >/dev/null 2>&1 || printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
  grep -F '$HOME/.foundry/bin' "$rc" >/dev/null 2>&1 || printf 'export PATH="$HOME/.foundry/bin:$PATH"\n' >> "$rc"
fi

echo "WQPU testnet operator installed at $ROOT"
echo "Command: $BIN/wqpu-testnet"
if [ "${WQPU_OPERATOR_NO_START:-0}" = "1" ]; then
  echo "WQPU operator install-only mode: stack was not started."
  exit 0
fi

exec "$BIN/wqpu-testnet" start "$@"
