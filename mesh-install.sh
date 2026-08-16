#!/usr/bin/env sh
set -eu
WQPU_INSTALL='https://raw.githubusercontent.com/eav021107-debug/WQPU/main/install.sh'
OS="$(uname -s)"
TSIP=''

if [ "$OS" = "Linux" ]; then
  if ! command -v tailscale >/dev/null 2>&1; then
    echo 'WQPU mesh: installing Tailscale...'
    curl -fsSL https://tailscale.com/install.sh | sh
  fi
  echo 'WQPU mesh: connect this VPS/computer to Tailscale.'
  sudo tailscale up
  TSIP="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
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

  echo 'WQPU mesh: opening Tailscale. Sign in and approve the VPN/system extension if macOS asks.'
  open -a Tailscale || true
  echo 'Waiting for the Mac to join Tailscale...'
  i=0
  while [ "$i" -lt 90 ]; do
    TSIP="$(TAILSCALE_BE_CLI=1 "$TS" ip -4 2>/dev/null | head -n 1 || true)"
    case "$TSIP" in
      100.*) break ;;
    esac
    i=$((i + 1))
    sleep 2
  done
else
  echo "Unsupported OS for this helper: $OS" >&2
  exit 1
fi

case "$TSIP" in
  100.*) echo "WQPU mesh: connected as $TSIP" ;;
  *)
    echo 'WQPU mesh: Tailscale is installed but not connected yet. Complete Tailscale sign-in, then run this command again.' >&2
    exit 1
    ;;
esac

# Conservative remote-test setting so a ~2 GB VPS can still contribute.
export WQPU_RAM_RESERVE_FRACTION=0.10

echo 'WQPU mesh: updating and starting WQPU...'
curl -fsSL "$WQPU_INSTALL" | sh
