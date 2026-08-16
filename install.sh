#!/usr/bin/env sh
set -eu
REPO="https://raw.githubusercontent.com/eav021107-debug/WQPU/main"
ROOT="${HOME}/.local/share/wqpu"
BIN="${HOME}/.local/bin"
mkdir -p "$ROOT" "$BIN"

if ! command -v python3 >/dev/null 2>&1; then
  echo "WQPU: Python 3 not found; trying to install it..."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y python3 ca-certificates curl
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y python3 ca-certificates curl
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --needed --noconfirm python curl ca-certificates
  elif command -v brew >/dev/null 2>&1; then
    brew install python
  else
    echo "WQPU: Python 3 is required and no supported package manager was found." >&2
    exit 1
  fi
fi

if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$REPO/wqpu.py" -o "$ROOT/wqpu.py"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$ROOT/wqpu.py" "$REPO/wqpu.py"
else
  echo "WQPU: curl or wget is required." >&2
  exit 1
fi
chmod +x "$ROOT/wqpu.py"

cat > "$BIN/wqpu" <<EOF
#!/usr/bin/env sh
exec python3 "$ROOT/wqpu.py" "\$@"
EOF
chmod +x "$BIN/wqpu"

case ":$PATH:" in
  *":$BIN:"*) : ;;
  *)
    export PATH="$BIN:$PATH"
    rc=""
    shell_name="$(basename "${SHELL:-sh}")"
    if [ "$shell_name" = "zsh" ]; then rc="$HOME/.zshrc"; elif [ "$shell_name" = "bash" ]; then rc="$HOME/.bashrc"; fi
    if [ -n "$rc" ]; then
      touch "$rc"
      if ! grep -F "$BIN" "$rc" >/dev/null 2>&1; then
        printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
      fi
    fi
    ;;
esac

echo ""
echo "WQPU installed. This terminal will now join the cluster."
echo "Keep it open while this computer contributes resources."
echo ""
exec "$BIN/wqpu" start
