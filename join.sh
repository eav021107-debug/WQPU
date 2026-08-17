#!/usr/bin/env bash
set -euo pipefail

REPO="eav021107-debug/WQPU"
REF="${WQPU_REF:-next-foundation}"
BASE="${WQPU_JOIN_BOOTSTRAP_HOME:-$HOME/.local/share/wqpu-public-join-bootstrap}"
SOURCE="$BASE/source"
GENESIS="${WQPU_PUBLIC_GENESIS:-$HOME/Desktop/genesis.json}"
INFO="${WQPU_PUBLIC_INFO:-$HOME/Desktop/info.txt}"

fail() { printf 'WQPU JOIN: %s\n' "$*" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || fail "curl is required"
command -v tar >/dev/null 2>&1 || fail "tar is required"
[ -f "$GENESIS" ] || fail "expected canonical genesis at $GENESIS"
[ -f "$INFO" ] || fail "expected network info at $INFO"

mkdir -p "$BASE"
archive="$BASE/source.tar.gz"
stage="$BASE/source.new.$$"
rm -rf "$stage" "$archive"
mkdir -p "$stage"
curl -fL --retry 3 "https://codeload.github.com/${REPO}/tar.gz/${REF}" -o "$archive"
tar -xzf "$archive" -C "$stage" --strip-components=1
rm -f "$archive"
[ -f "$stage/chain/public-join.sh" ] || fail "downloaded source is missing public joiner"
rm -rf "$SOURCE"
mv "$stage" "$SOURCE"

exec bash "$SOURCE/chain/public-join.sh" "$GENESIS" "$INFO"
