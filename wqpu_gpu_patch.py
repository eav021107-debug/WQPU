#!/usr/bin/env python3
"""Small accelerator layer over the proven WQPU/llama.cpp transport runtime.

Do not fork or rewrite the old networking core merely to enable GPUs. The existing node
already starts ggml-rpc-server through `wqpu.start_proc`; intercept only that command,
remove the historical CPU-only restriction, and publish accelerator capacity/metadata.
"""
from __future__ import print_function

import os
from pathlib import Path

import wqpu_accel


def _is_rpc_server(command):
    if not command:
        return False
    try:
        name = Path(str(command[0])).name.lower()
    except Exception:
        return False
    return name in ("ggml-rpc-server", "ggml-rpc-server.exe")


def _without_device(command):
    out = []
    index = 0
    while index < len(command):
        item = str(command[index])
        if item == "--device":
            index += 2
            continue
        out.append(command[index])
        index += 1
    return out


def _rpc_command(command):
    if not _is_rpc_server(command):
        return list(command)
    out = _without_device(list(command))
    out.extend(wqpu_accel.rpc_device_args())
    return out


def install_wqpu(wqpu_module):
    if getattr(wqpu_module, "_wqpu_gpu_patch_installed", False):
        return wqpu_module

    original_start_proc = wqpu_module.start_proc
    original_my_info = wqpu_module.Mesh.my_info

    def start_proc(command, logname):
        return original_start_proc(_rpc_command(command), logname)

    def my_info(self):
        info = dict(original_my_info(self))
        accel = wqpu_accel.info(wqpu_module.total_ram_mb())
        info.update({
            "accelerator": accel["accelerator"],
            "runtime_variant": accel["runtime_variant"],
            "vram_mb": accel["vram_mb"],
        })
        return info

    wqpu_module.start_proc = start_proc
    wqpu_module.Mesh.my_info = my_info
    wqpu_module._wqpu_gpu_patch_installed = True
    return wqpu_module


def install_runtime(runtime_module, wqpu_module):
    """Use accelerator memory as provider capacity when it is more truthful than RAM."""
    if getattr(runtime_module, "_wqpu_gpu_patch_installed", False):
        return runtime_module

    original_capacity = runtime_module.capacity_units
    original_my_info = runtime_module.ChainMesh.my_info

    def capacity_units():
        total_ram = int(wqpu_module.total_ram_mb() or 0)
        accel = wqpu_accel.info(total_ram)
        # Discrete NVIDIA CUDA workers expose accelerator devices rather than CPU memory,
        # so Registry/scheduler capacity should be VRAM. Metal uses unified memory and
        # Vulkan VRAM is not safely queryable without backend-specific tooling, so those
        # retain the established system-memory capacity until a protocol-level probe is
        # available before wallet registration.
        if accel["accelerator"] == "cuda12" and accel["vram_mb"] > 0:
            return int(accel["vram_mb"])
        return int(original_capacity())

    def my_info(self):
        info = dict(original_my_info(self))
        accel = wqpu_accel.info(wqpu_module.total_ram_mb())
        info.update({
            "accelerator": accel["accelerator"],
            "runtime_variant": accel["runtime_variant"],
            "vram_mb": accel["vram_mb"],
            "capacity": capacity_units(),
        })
        return info

    runtime_module.capacity_units = capacity_units
    runtime_module.ChainMesh.my_info = my_info
    runtime_module._wqpu_gpu_patch_installed = True
    return runtime_module


def describe(wqpu_module):
    return wqpu_accel.label(wqpu_module.total_ram_mb())
