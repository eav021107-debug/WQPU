#!/usr/bin/env sh
set -eu
RAW="https://raw.githubusercontent.com/eav021107-debug/WQPU/main"
ROOT="${HOME}/.local/share/wqpu"
BIN="${HOME}/.local/bin"
JOIN="${WQPU_JOIN:-${1:-}}"

need() { command -v "$1" >/dev/null 2>&1; }

if ! need python3; then
  echo "WQPU needs Python 3.10+." >&2
  exit 1
fi
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' || {
  echo "WQPU needs Python 3.10+." >&2; exit 1;
}
if ! need curl; then echo "WQPU needs curl." >&2; exit 1; fi
if ! need openssl; then echo "WQPU needs openssl." >&2; exit 1; fi

mkdir -p "$ROOT" "$BIN"
curl -fsSL "$RAW/wqpu.py" -o "$ROOT/wqpu.py"
chmod 755 "$ROOT/wqpu.py"
cat > "$BIN/wqpu" <<EOF
#!/usr/bin/env sh
exec python3 "$ROOT/wqpu.py" "\$@"
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

echo "WQPU installed. Starting this computer as an equal peer..."
if [ -n "$JOIN" ]; then
  exec "$BIN/wqpu" --join "$JOIN"
else
  exec "$BIN/wqpu"
fi
