#!/usr/bin/env sh
set -eu
WQPU_INSTALL='https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install.sh'
OS="$(uname -s)"

if [ "$OS" = "Linux" ]; then
  if ! command -v tailscale >/dev/null 2>&1; then
    echo 'WQPU mesh: installing Tailscale...'
    curl -fsSL https://tailscale.com/install.sh | sh
  fi
  echo 'WQPU mesh: connect this VPS/computer to Tailscale.'
  sudo tailscale up
elif [ "$OS" = "Darwin" ]; then
  TS='/Applications/Tailscale.app/Contents/MacOS/Tailscale'
  if [ ! -x "$TS" ]; then
    echo 'WQPU mesh: installing Tailscale for macOS...'
    page="$(mktemp)"
    pkgfile="$(mktemp -d)"
    curl -fsSL https://pkgs.tailscale.com/stable/ -o "$page"
    pkg="$(grep -Eo 'Tailscale-[0-9.]+-macos\.pkg' "$page" | head -n 1 || true)"
    rm -f "$page"
    if [ -z "$pkg" ]; then
      echo 'Could not find the current macOS Tailscale package.' >&2
      exit 1
    fi
    curl -fsSL "https://pkgs.tailscale.com/stable/$pkg" -o "$pkgfile/$pkg"
    sudo installer -pkg "$pkgfile/$pkg" -target /
    rm -rf "$pkgfile"
  fi
  echo 'WQPU mesh: connect this Mac to Tailscale (a browser login may open).'
  TAILSCALE_BE_CLI=1 "$TS" up
else
  echo "Unsupported OS for this helper: $OS" >&2
  exit 1
fi

echo 'WQPU mesh: Tailscale connected. Updating and starting WQPU...'
curl -fsSL "$WQPU_INSTALL" | sh
