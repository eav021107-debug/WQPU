#!/usr/bin/env python3
"""Register the hardened multi-peer WQPU network precompile in pinned wqpud."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from .runtime_patch import apply as apply_probe, patch_app_go, copy_overlay
except ImportError:  # direct `python chain/runtime_patch_network.py`
    from runtime_patch import apply as apply_probe, patch_app_go, copy_overlay

PROBE_WRAPPER = "wqpuprecompile.WithWQPU(precompiletypes.DefaultStaticPrecompiles("
NETWORK_WRAPPER = "wqpuprecompile.WithWQPUNetwork(precompiletypes.DefaultStaticPrecompiles("


def patch_app_go_network(text: str) -> str:
    if NETWORK_WRAPPER in text:
        return text
    patched = patch_app_go(text)
    if PROBE_WRAPPER not in patched:
        raise ValueError("probe wrapper was not produced by base WQPU patcher")
    return patched.replace(PROBE_WRAPPER, NETWORK_WRAPPER, 1)


def apply_network(source_root: Path, overlay: Path) -> None:
    app_go = source_root / "evmd" / "app.go"
    if not app_go.is_file():
        raise FileNotFoundError("pinned Cosmos EVM app.go not found: {}".format(app_go))
    copy_overlay(overlay, source_root / "precompiles" / "wqpu")
    original = app_go.read_text(encoding="utf-8")
    app_go.write_text(patch_app_go_network(original), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--overlay", required=True, type=Path)
    args = parser.parse_args()
    apply_network(args.source.resolve(), args.overlay.resolve())
    print("WQPU chain: target multi-peer native precompile registered at 0x0900")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
