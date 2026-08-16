#!/usr/bin/env sh
set -eu

RAW="https://raw.githubusercontent.com/eav021107-debug/WQPU/main"
ROOT="${HOME}/.local/share/wqpu"
BIN="${HOME}/.local/bin"
JOIN="${WQPU_JOIN:-${1:-}}"
MIN_MAJOR=3
MIN_MINOR=6
FALLBACK_PY="3.8.20"

need() { command -v "$1" >/dev/null 2>&1; }

if [ "$(id -u)" = "0" ]; then
  SUDO=""
elif need sudo; then
  SUDO="sudo"
else
  SUDO=""
fi

python_ok() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,6) else 1)' >/dev/null 2>&1
}

find_python() {
  for p in \
    python3.14 python3.13 python3.12 python3.11 python3.10 \
    python3.9 python3.8 python3.7 python3.6 \
    python314 python313 python312 python311 python310 \
    python39 python38 python37 python36 python3
  do
    if need "$p" && python_ok "$p"; then
      command -v "$p"
      return 0
    fi
  done
  return 1
}

install_base_tools() {
  if need apt-get; then
    $SUDO apt-get update
    $SUDO apt-get install -y curl ca-certificates openssl >/dev/null 2>&1 || true
  elif need dnf; then
    $SUDO dnf install -y curl ca-certificates openssl >/dev/null 2>&1 || true
  elif need yum; then
    $SUDO yum install -y curl ca-certificates openssl >/dev/null 2>&1 || true
  elif need brew; then
    brew install curl openssl >/dev/null 2>&1 || true
  fi
}

install_packaged_python() {
  if need apt-get; then
    $SUDO apt-get update
    for pkg in python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python3.7 python3.6 python3; do
      $SUDO apt-get install -y "$pkg" >/dev/null 2>&1 || true
      PYTHON="$(find_python || true)"
      [ -n "$PYTHON" ] && return 0
    done
  elif need dnf; then
    $SUDO dnf install -y epel-release >/dev/null 2>&1 || true
    for pkg in python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python39 python38 python37 python36 python3; do
      $SUDO dnf install -y "$pkg" >/dev/null 2>&1 || true
      PYTHON="$(find_python || true)"
      [ -n "$PYTHON" ] && return 0
    done
  elif need yum; then
    $SUDO yum install -y epel-release >/dev/null 2>&1 || true
    for pkg in python3.13 python3.12 python3.11 python3.10 python3.9 python3.8 python39 python38 python37 python36 python3; do
      $SUDO yum install -y "$pkg" >/dev/null 2>&1 || true
      PYTHON="$(find_python || true)"
      [ -n "$PYTHON" ] && return 0
    done
  elif need brew; then
    brew install python >/dev/null 2>&1 || true
    PYTHON="$(find_python || true)"
    [ -n "$PYTHON" ] && return 0
  fi
  return 1
}

build_private_python() {
  echo "WQPU: system Python is too old; installing a private Python ${FALLBACK_PY} for WQPU only..."

  if need apt-get; then
    $SUDO apt-get update
    $SUDO apt-get install -y \
      build-essential curl ca-certificates \
      libssl-dev zlib1g-dev libbz2-dev libreadline-dev \
      libsqlite3-dev libffi-dev liblzma-dev >/dev/null 2>&1 || return 1
  elif need dnf; then
    $SUDO dnf groupinstall -y "Development Tools" >/dev/null 2>&1 || true
    $SUDO dnf install -y \
      gcc make curl ca-certificates openssl-devel zlib-devel bzip2-devel \
      readline-devel sqlite-devel libffi-devel xz-devel >/dev/null 2>&1 || return 1
  elif need yum; then
    $SUDO yum groupinstall -y "Development Tools" >/dev/null 2>&1 || true
    $SUDO yum install -y \
      gcc make curl ca-certificates openssl-devel zlib-devel bzip2-devel \
      readline-devel sqlite-devel libffi-devel xz-devel >/dev/null 2>&1 || return 1
  elif need brew; then
    brew install python >/dev/null 2>&1 || return 1
    PYTHON="$(find_python || true)"
    [ -n "$PYTHON" ] && return 0
    return 1
  else
    return 1
  fi

  SRC="/tmp/wqpu-python-${FALLBACK_PY}"
  TGZ="/tmp/Python-${FALLBACK_PY}.tgz"
  PREFIX="${ROOT}/python"

  rm -rf "$SRC" "$TGZ"
  mkdir -p "$SRC" "$ROOT"
  curl -fL --retry 3 \
    "https://www.python.org/ftp/python/${FALLBACK_PY}/Python-${FALLBACK_PY}.tgz" \
    -o "$TGZ"
  tar -xzf "$TGZ" -C "$SRC" --strip-components=1
  cd "$SRC"
  ./configure --prefix="$PREFIX" --with-ensurepip=no >/dev/null
  JOBS="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 1)"
  [ "$JOBS" -gt 2 ] 2>/dev/null && JOBS=2
  make -j"$JOBS" >/dev/null
  make install >/dev/null
  cd /
  rm -rf "$SRC" "$TGZ"

  PYTHON="${PREFIX}/bin/python3"
  python_ok "$PYTHON"
}

install_base_tools

if ! need curl; then
  echo "WQPU could not install curl." >&2
  exit 1
fi
if ! need openssl; then
  echo "WQPU could not install openssl." >&2
  exit 1
fi

PYTHON="$(find_python || true)"
if [ -z "$PYTHON" ]; then
  echo "WQPU: no compatible Python found; trying system packages..."
  install_packaged_python || true
  PYTHON="$(find_python || true)"
fi

if [ -z "$PYTHON" ]; then
  build_private_python || {
    CURRENT="$(python3 --version 2>&1 || true)"
    echo "WQPU could not prepare a compatible Python. Current: ${CURRENT:-none}" >&2
    exit 1
  }
fi

mkdir -p "$ROOT" "$BIN"

curl -fsSL --retry 3 \
  "${RAW}/wqpu.py?installer=0.5.3" \
  -o "$ROOT/wqpu.py"
chmod 755 "$ROOT/wqpu.py"

"$PYTHON" -m py_compile "$ROOT/wqpu.py" || {
  echo "WQPU downloaded correctly, but this Python cannot run it: $($PYTHON --version 2>&1)" >&2
  exit 1
}

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

# When install.sh is read from `curl | sh`, the shell itself still needs the
# pipe as stdin. Redirect only the final WQPU process to the real terminal.
if [ -r /dev/tty ]; then
  if [ -n "$JOIN" ]; then
    exec "$BIN/wqpu" --join "$JOIN" </dev/tty
  else
    exec "$BIN/wqpu" </dev/tty
  fi
else
  if [ -n "$JOIN" ]; then
    exec "$BIN/wqpu" --join "$JOIN"
  else
    exec "$BIN/wqpu"
  fi
fi
