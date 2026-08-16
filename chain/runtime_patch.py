#!/usr/bin/env python3
"""Apply the tiny WQPU runtime overlay to an exact pinned Cosmos EVM tree.

The upstream source stays external and reproducible. WQPU copies only its native
precompile package into the build tree and wraps the upstream static-precompile
map with one collision-checking registration call.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil

IMPORT_ANCHOR = 'precompiletypes "github.com/cosmos/evm/precompiles/types"'
WQPU_IMPORT = 'wqpuprecompile "github.com/cosmos/evm/precompiles/wqpu"'
DEFAULT_CALL = "precompiletypes.DefaultStaticPrecompiles("
WRAPPED_CALL = "wqpuprecompile.WithWQPU(" + DEFAULT_CALL


def _matching_paren(text: str, opening: int) -> int:
    if opening >= len(text) or text[opening] != "(":
        raise ValueError("opening index does not point to '('")

    depth = 0
    i = opening
    mode = "code"
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""

        if mode == "line-comment":
            if ch == "\n":
                mode = "code"
            i += 1
            continue
        if mode == "block-comment":
            if ch == "*" and nxt == "/":
                mode = "code"
                i += 2
            else:
                i += 1
            continue
        if mode == "string":
            if ch == "\\":
                i += 2
            elif ch == '"':
                mode = "code"
                i += 1
            else:
                i += 1
            continue
        if mode == "rune":
            if ch == "\\":
                i += 2
            elif ch == "'":
                mode = "code"
                i += 1
            else:
                i += 1
            continue
        if mode == "raw":
            if ch == "`":
                mode = "code"
            i += 1
            continue

        if ch == "/" and nxt == "/":
            mode = "line-comment"
            i += 2
            continue
        if ch == "/" and nxt == "*":
            mode = "block-comment"
            i += 2
            continue
        if ch == '"':
            mode = "string"
            i += 1
            continue
        if ch == "'":
            mode = "rune"
            i += 1
            continue
        if ch == "`":
            mode = "raw"
            i += 1
            continue

        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
            if depth < 0:
                break
        i += 1

    raise ValueError("could not find matching ')' in upstream app.go")


def patch_app_go(text: str) -> str:
    if WQPU_IMPORT not in text:
        count = text.count(IMPORT_ANCHOR)
        if count != 1:
            raise ValueError("expected exactly one Cosmos precompile import anchor")
        text = text.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + "\n\t" + WQPU_IMPORT, 1)

    if WRAPPED_CALL in text:
        return text

    starts = []
    offset = 0
    while True:
        pos = text.find(DEFAULT_CALL, offset)
        if pos < 0:
            break
        starts.append(pos)
        offset = pos + len(DEFAULT_CALL)
    if len(starts) != 1:
        raise ValueError("expected exactly one DefaultStaticPrecompiles call")

    start = starts[0]
    opening = start + len(DEFAULT_CALL) - 1
    closing = _matching_paren(text, opening)
    return (
        text[:start]
        + "wqpuprecompile.WithWQPU("
        + text[start : closing + 1]
        + ")"
        + text[closing + 1 :]
    )


def copy_overlay(overlay: Path, destination: Path) -> None:
    if not overlay.is_dir():
        raise FileNotFoundError("WQPU precompile overlay is missing: {}".format(overlay))
    go_files = sorted(
        p for p in overlay.glob("*.go") if p.is_file() and not p.name.endswith("_test.go")
    )
    if not go_files:
        raise RuntimeError("WQPU precompile overlay contains no Go source files")

    if destination.exists():
        shutil.rmtree(str(destination))
    destination.mkdir(parents=True)
    for source in go_files:
        shutil.copy2(str(source), str(destination / source.name))


def apply(source_root: Path, overlay: Path) -> None:
    app_go = source_root / "evmd" / "app.go"
    if not app_go.is_file():
        raise FileNotFoundError("pinned Cosmos EVM app.go not found: {}".format(app_go))

    copy_overlay(overlay, source_root / "precompiles" / "wqpu")
    original = app_go.read_text(encoding="utf-8")
    patched = patch_app_go(original)
    app_go.write_text(patched, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--overlay", required=True, type=Path)
    args = parser.parse_args()
    apply(args.source.resolve(), args.overlay.resolve())
    print("WQPU chain: native precompile overlay applied at 0x0900")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
