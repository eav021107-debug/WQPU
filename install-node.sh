#!/usr/bin/env sh
set -eu

TOKEN="${1:-${WQPU_JOIN:-}}"
RAW="https://raw.githubusercontent.com/eav021107-debug/WQPU/main"
ROOT="${HOME}/.local/share/wqpu"
BIN="${HOME}/.local/bin"

if [ -z "$TOKEN" ]; then
  echo "Missing WQPU join token." >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.10+ is required." >&2
  exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)'; then
  echo "WQPU requires Python 3.10 or newer." >&2
  exit 1
fi

command -v curl >/dev/null 2>&1 || { echo "curl is required." >&2; exit 1; }

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

"$BIN/wqpu" join "$TOKEN"
echo "WQPU installed. This computer is an equal peer."
echo "Keep this terminal open to contribute."
echo "In another terminal: wqpu ask \"your question\""
exec "$BIN/wqpu" start
