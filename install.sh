#!/usr/bin/env sh
set -eu

RAW="https://raw.githubusercontent.com/eav021107-debug/WQPU/main"
ROOT="${HOME}/.local/share/wqpu"
BIN="${HOME}/.local/bin"
JOIN="${WQPU_JOIN:-${1:-}}"
MIN_PY="3.7"

need() { command -v "$1" >/dev/null 2>&1; }

if [ "$(id -u)" = "0" ]; then
  SUDO=""
elif need sudo; then
  SUDO="sudo"
else
  SUDO=""
fi

python_ok() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,7) else 1)' >/dev/null 2>&1
}

find_python() {
  for p in python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python3.7 python39 python38 python37 python3; do
    if need "$p" && python_ok "$p"; then
      command -v "$p"
      return 0
    fi
  done
  return 1
}

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
  echo "WQPU: compatible Python not found; trying to install one..."

  if need apt-get; then
    $SUDO apt-get update
    for pkg in python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python3.7 python3; do
      $SUDO apt-get install -y "$pkg" >/dev/null 2>&1 || true
      PYTHON="$(find_python || true)"
      [ -n "$PYTHON" ] && break
    done
  elif need dnf; then
    for pkg in python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python39 python38 python37 python3; do
      $SUDO dnf install -y "$pkg" >/dev/null 2>&1 || true
      PYTHON="$(find_python || true)"
      [ -n "$PYTHON" ] && break
    done
  elif need yum; then
    for pkg in python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python39 python38 python37 python3; do
      $SUDO yum install -y "$pkg" >/dev/null 2>&1 || true
      PYTHON="$(find_python || true)"
      [ -n "$PYTHON" ] && break
    done
  elif need brew; then
    brew install python
    PYTHON="$(find_python || true)"
  fi
fi

if [ -z "$PYTHON" ]; then
  CURRENT="$(python3 --version 2>&1 || true)"
  echo "WQPU could not find/install Python ${MIN_PY}+ on this system. Current: ${CURRENT:-none}" >&2
  exit 1
fi

if ! need curl; then
  if need apt-get; then $SUDO apt-get install -y curl ca-certificates
  elif need dnf; then $SUDO dnf install -y curl ca-certificates
  elif need yum; then $SUDO yum install -y curl ca-certificates
  elif need brew; then brew install curl
  else echo "WQPU needs curl." >&2; exit 1
  fi
fi

if ! need openssl; then
  if need apt-get; then $SUDO apt-get install -y openssl
  elif need dnf; then $SUDO dnf install -y openssl
  elif need yum; then $SUDO yum install -y openssl
  elif need brew; then brew install openssl
  else echo "WQPU needs openssl." >&2; exit 1
  fi
fi

mkdir -p "$ROOT" "$BIN"
curl -fsSL "$RAW/wqpu.py" -o "$ROOT/wqpu.py"
chmod 755 "$ROOT/wqpu.py"

cat > "$BIN/wqpu" <<EOF
#!/usr/bin/env sh
exec "$PYTHON" "$ROOT/wqpu.py" "\$@"
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

echo "WQPU installed with $($PYTHON --version 2>&1). Starting this computer as an equal peer..."
if [ -n "$JOIN" ]; then
  exec "$BIN/wqpu" --join "$JOIN"
else
  exec "$BIN/wqpu"
fi
