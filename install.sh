#!/usr/bin/env sh
set -eu

RAW="${WQPU_RAW_BASE:-https://raw.githubusercontent.com/eav021107-debug/WQPU/main}"
ROOT="${HOME}/.local/share/wqpu"
BIN="${HOME}/.local/bin"
JOIN="${WQPU_JOIN:-${1:-}}"
EXPECTED_WQPU="WQPU 0.6.0-dev"
CACHE_BUSTER="chain-0.6.0-dev-r1"

need() { command -v "$1" >/dev/null 2>&1; }
python_ok() { "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,6) else 1)' >/dev/null 2>&1; }

if [ "$(id -u)" = "0" ]; then
  SUDO=""
elif need sudo; then
  SUDO="sudo"
else
  SUDO=""
fi

install_base_tools() {
  if need curl && need openssl; then return 0; fi
  if need apt-get; then
    $SUDO apt-get update
    $SUDO apt-get install -y curl ca-certificates openssl >/dev/null 2>&1 || true
  elif need dnf; then
    $SUDO dnf install -y curl ca-certificates openssl >/dev/null 2>&1 || true
  elif need yum; then
    $SUDO yum install -y curl ca-certificates openssl >/dev/null 2>&1 || true
  elif need brew; then
    need curl || brew install curl >/dev/null 2>&1 || true
    need openssl || brew install openssl >/dev/null 2>&1 || true
  fi
}

find_python() {
  for p in python3.14 python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python3.7 python3.6 python3; do
    if need "$p" && python_ok "$p"; then
      command -v "$p"
      return 0
    fi
  done
  return 1
}

install_python() {
  if need apt-get; then
    $SUDO apt-get update
    $SUDO apt-get install -y python3 >/dev/null 2>&1 || true
  elif need dnf; then
    $SUDO dnf install -y python3 >/dev/null 2>&1 || true
  elif need yum; then
    $SUDO yum install -y python3 >/dev/null 2>&1 || true
  elif need brew; then
    brew install python >/dev/null 2>&1 || true
  fi
}

install_base_tools
need curl || { echo "WQPU could not install curl." >&2; exit 1; }
need openssl || { echo "WQPU could not install OpenSSL." >&2; exit 1; }

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
  echo "WQPU: installing Python..."
  install_python
  PYTHON="$(find_python || true)"
fi
if [ -z "$PYTHON" ]; then
  echo "WQPU needs Python 3.6 or newer." >&2
  exit 1
fi

mkdir -p "$ROOT" "$BIN"

echo "WQPU: downloading runtime..."
for file in wqpu.py wqpu_chain.py wqpu_wallet.py wqpu_runtime.py; do
  curl -fsSL --retry 3 "${RAW}/${file}?installer=${CACHE_BUSTER}" -o "$ROOT/$file"
done
chmod 755 "$ROOT/wqpu.py" "$ROOT/wqpu_chain.py" "$ROOT/wqpu_wallet.py" "$ROOT/wqpu_runtime.py"

"$PYTHON" -m py_compile \
  "$ROOT/wqpu.py" \
  "$ROOT/wqpu_chain.py" \
  "$ROOT/wqpu_wallet.py" \
  "$ROOT/wqpu_runtime.py" || {
    echo "WQPU files were downloaded but did not pass the Python compatibility check." >&2
    exit 1
  }

CORE_VERSION="$("$PYTHON" "$ROOT/wqpu_runtime.py" --version 2>&1 || true)"
if [ "$CORE_VERSION" != "$EXPECTED_WQPU" ]; then
  echo "WQPU version mismatch: expected '$EXPECTED_WQPU', got '${CORE_VERSION:-unknown}'." >&2
  exit 1
fi

cat > "$BIN/wqpu" <<EOF
#!/usr/bin/env sh
exec "$PYTHON" "$ROOT/wqpu_runtime.py" "\$@"
EOF
chmod 755 "$BIN/wqpu"
export PATH="$BIN:$PATH"

shell_name="$(basename "${SHELL:-sh}")"
rc=""
[ "$shell_name" = "zsh" ] && rc="$HOME/.zshrc"
[ "$shell_name" = "bash" ] && rc="$HOME/.bashrc"
if [ -n "$rc" ]; then
  touch "$rc"
  grep -F '$HOME/.local/bin' "$rc" >/dev/null 2>&1 || printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
fi

echo "WQPU installed: $CORE_VERSION with $($PYTHON --version 2>&1)."
if [ -n "$JOIN" ]; then
  exec "$BIN/wqpu" --join "$JOIN"
else
  exec "$BIN/wqpu"
fi
