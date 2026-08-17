#!/usr/bin/env bash
set -euo pipefail

REF=${1:-next-foundation}
REPO=${WQPU_REPO:-eav021107-debug/WQPU}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

command -v curl >/dev/null || { echo "curl is required"; exit 1; }
command -v tar >/dev/null || { echo "tar is required"; exit 1; }

curl -fsSL "https://github.com/${REPO}/archive/refs/heads/${REF}.tar.gz" -o "$TMP/wqpu.tar.gz"
tar -xzf "$TMP/wqpu.tar.gz" -C "$TMP"
SOURCE=$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)

if [[ -z "$SOURCE" || ! -f "$SOURCE/tools/vps-control/scripts/install.sh" ]]; then
  echo "VPS Control files not found in ref: $REF"
  exit 1
fi

bash "$SOURCE/tools/vps-control/scripts/install.sh"
