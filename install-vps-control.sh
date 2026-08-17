#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Run through sudo/root"
  exit 1
fi

REF=${1:-next-foundation}
REPO=${WQPU_REPO:-eav021107-debug/WQPU}
WORKSPACE=${VPS_CONTROL_ROOT:-/srv/wqpu}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

if ! command -v git >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y git
  else
    echo "git is required"
    exit 1
  fi
fi

# Keep the actual project on disk permanently. The plugin indexes this checkout and
# works inside it; it never points at a temporary download.
if [[ ! -d "$WORKSPACE/.git" ]]; then
  if [[ -d "$WORKSPACE" && -n "$(ls -A "$WORKSPACE" 2>/dev/null)" ]]; then
    echo "Workspace exists and is not a git repository: $WORKSPACE"
    echo "Set VPS_CONTROL_ROOT to the project you want to control."
    exit 1
  fi
  rm -rf "$WORKSPACE"
  git clone --depth 1 --branch "$REF" "https://github.com/${REPO}.git" "$WORKSPACE"
fi

# Fetch the plugin source separately so this installer can also attach to another
# existing project selected with VPS_CONTROL_ROOT.
git clone --depth 1 --branch "$REF" "https://github.com/${REPO}.git" "$TMP/source"
VPS_CONTROL_ROOT="$WORKSPACE" bash "$TMP/source/tools/vps-control/scripts/install.sh"

echo
echo "Living project attached: $WORKSPACE"
