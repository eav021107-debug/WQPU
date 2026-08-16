#!/usr/bin/env python3
"""Register the stateful WQPU network precompile in the exact pinned runtime."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from .runtime_patch import copy_overlay, patch_app_go
except ImportError:
    from runtime_patch import copy_overlay, patch_app_go

PROBE_WRAPPER = "wqpuprecompile.WithWQPU(precompiletypes.DefaultStaticPrecompiles("
STATEFUL_WRAPPER = "wqpuprecompile.WithWQPUStatefulNetwork(precompiletypes.DefaultStaticPrecompiles("


def patch_app_go_stateful(text: str) -> str:
    if STATEFUL_WRAPPER in text:
        return text
    patched = patch_app_go(text)
    if PROBE_WRAPPER not in patched:
        raise ValueError("base WQPU wrapper was not produced")
    return patched.replace(PROBE_WRAPPER, STATEFUL_WRAPPER, 1)


def apply_stateful(source_root: Path, overlay: Path) -> None:
    app_go = source_root / "evmd" / "app.go"
    if not app_go.is_file():
        raise FileNotFoundError("pinned Cosmos EVM app.go not found: {}".format(app_go))
    copy_overlay(overlay, source_root / "precompiles" / "wqpu")
    original = app_go.read_text(encoding="utf-8")
    app_go.write_text(patch_app_go_stateful(original), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--overlay", required=True, type=Path)
    args = parser.parse_args()
    apply_stateful(args.source.resolve(), args.overlay.resolve())
    print("WQPU chain: stateful native precompile registered at 0x0900")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
