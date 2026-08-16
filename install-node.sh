#!/usr/bin/env sh
set -eu
TOKEN="${1:-${WQPU_JOIN:-}}"
RAW="https://raw.githubusercontent.com/eav021107-debug/WQPU/main"
ROOT="${HOME}/.local/share/wqpu"
BIN="${HOME}/.local/bin"

if [ -z "$TOKEN" ]; then echo "Missing WQPU join token." >&2; exit 1; fi
if ! command -v python3 >/dev/null 2>&1; then
  if command -v dnf >/dev/null 2>&1; then sudo dnf install -y python3 curl ca-certificates
  elif command -v apt-get >/dev/null 2>&1; then sudo apt-get update && sudo apt-get install -y python3 curl ca-certificates
  elif command -v brew >/dev/null 2>&1; then brew install python
  else echo "Python 3 is required." >&2; exit 1; fi
fi
command -v curl >/dev/null 2>&1 || { echo "curl is required." >&2; exit 1; }
mkdir -p "$ROOT" "$BIN"
curl -fsSL "$RAW/wqpu_net.py" -o "$ROOT/wqpu.py"
chmod 755 "$ROOT/wqpu.py"
cat > "$BIN/wqpu" <<EOF
#!/usr/bin/env sh
exec python3 "$ROOT/wqpu.py" "\$@"
EOF
chmod 755 "$BIN/wqpu"; export PATH="$BIN:$PATH"
shell_name="$(basename "${SHELL:-sh}")"; rc=""
[ "$shell_name" = "zsh" ] && rc="$HOME/.zshrc"; [ "$shell_name" = "bash" ] && rc="$HOME/.bashrc"
if [ -n "$rc" ]; then touch "$rc"; grep -F '$HOME/.local/bin' "$rc" >/dev/null 2>&1 || printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"; fi
"$BIN/wqpu" join "$TOKEN"
echo "WQPU installed in equal-peer mode. Keep this terminal open while this computer contributes."
echo "Ask from another terminal with: wqpu ask \"your question\""
exec "$BIN/wqpu" start
