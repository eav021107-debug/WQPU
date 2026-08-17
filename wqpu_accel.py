#!/usr/bin/env python3
"""Platform-safe accelerator selection for WQPU's pinned llama.cpp runtime.

The public protocol must not assume that every provider is CPU-only. This module chooses
one runtime *build* per machine, while leaving ggml-rpc-server itself free to expose all
devices contained in that build. Explicit environment overrides remain available for
operators who need deterministic testing or driver workarounds.
"""
from __future__ import print_function

import ctypes.util
import glob
import os
import platform
import shutil
import subprocess


VALID_MODES = ("auto", "cpu", "metal", "vulkan", "cuda12")


def _machine():
    return platform.machine().lower()


def _system():
    return platform.system()


def _x64():
    return _machine() in ("x86_64", "amd64", "x64")


def _arm64():
    return _machine() in ("arm64", "aarch64")


def _command_exists(name):
    return bool(shutil.which(name))


def _nvidia_present():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False
    try:
        proc = subprocess.run(
            [exe, "--query-gpu=name", "--format=csv,noheader"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=5,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except Exception:
        return False


def _vulkan_loader_present():
    system = _system()
    if system == "Windows":
        windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot") or r"C:\Windows"
        return os.path.exists(os.path.join(windir, "System32", "vulkan-1.dll"))
    if system == "Linux":
        return bool(ctypes.util.find_library("vulkan"))
    return False


def _linux_render_device_present():
    return bool(glob.glob("/dev/dri/renderD*"))


def _vulkan_gpu_present():
    if not _vulkan_loader_present():
        return False
    if _command_exists("vulkaninfo"):
        try:
            proc = subprocess.run(
                [shutil.which("vulkaninfo"), "--summary"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=8,
            )
            if proc.returncode == 0:
                return True
        except Exception:
            pass
    if _system() == "Linux":
        return _nvidia_present() or _linux_render_device_present()
    if _system() == "Windows":
        # Presence of vulkan-1.dll means an installed ICD/loader path is available. Auto
        # selection still prefers CUDA for NVIDIA, so this mainly covers AMD/Intel GPUs.
        return True
    return False


def requested_mode():
    value = str(os.environ.get("WQPU_ACCEL", "auto") or "auto").strip().lower()
    aliases = {
        "cuda": "cuda12",
        "cuda12.4": "cuda12",
        "gpu": "auto",
        "off": "cpu",
        "none": "cpu",
    }
    value = aliases.get(value, value)
    if value not in VALID_MODES:
        raise RuntimeError(
            "invalid WQPU_ACCEL={!r}; use auto, cpu, metal, vulkan or cuda12".format(value)
        )
    return value


def auto_mode():
    system = _system()
    if system == "Darwin":
        # Official macOS llama.cpp builds include Metal. Apple Silicon uses unified memory;
        # Intel Macs can still fall back internally when Metal is not usable.
        return "metal"
    if system == "Windows":
        if _x64() and _nvidia_present():
            return "cuda12"
        if _x64() and _vulkan_gpu_present():
            return "vulkan"
        return "cpu"
    if system == "Linux":
        if (_x64() or _arm64()) and _vulkan_gpu_present():
            return "vulkan"
        return "cpu"
    return "cpu"


def mode():
    requested = requested_mode()
    if requested == "auto":
        return auto_mode()

    system = _system()
    if requested == "metal" and system != "Darwin":
        raise RuntimeError("WQPU_ACCEL=metal is supported only on macOS")
    if requested == "cuda12" and not (system == "Windows" and _x64()):
        raise RuntimeError("WQPU_ACCEL=cuda12 currently requires Windows x64")
    if requested == "vulkan" and not (
        (system == "Windows" and _x64())
        or (system == "Linux" and (_x64() or _arm64()))
    ):
        raise RuntimeError("WQPU_ACCEL=vulkan is unsupported on this platform")
    return requested


def runtime_variant():
    selected = mode()
    if selected == "metal":
        # Metal is already part of the official macOS archive.
        return "default"
    return selected


def main_asset_suffix(cpu_suffix):
    selected = runtime_variant()
    system = _system()
    if selected in ("default", "cpu"):
        return cpu_suffix
    if selected == "vulkan":
        if system == "Linux" and _x64():
            return "-bin-ubuntu-vulkan-x64.tar.gz"
        if system == "Linux" and _arm64():
            return "-bin-ubuntu-vulkan-arm64.tar.gz"
        if system == "Windows" and _x64():
            return "-bin-win-vulkan-x64.zip"
    if selected == "cuda12" and system == "Windows" and _x64():
        return "-bin-win-cuda-12.4-x64.zip"
    raise RuntimeError("no pinned WQPU runtime asset for accelerator mode {}".format(selected))


def companion_asset_names(tag):
    if runtime_variant() == "cuda12":
        return ["cudart-llama-bin-win-cuda-12.4-x64.zip"]
    return []


def rpc_device_args():
    """Return only an explicit device override; auto mode must expose all accelerators."""
    explicit = str(os.environ.get("WQPU_RPC_DEVICE", "") or "").strip()
    if explicit:
        return ["--device", explicit]
    if requested_mode() == "cpu":
        return ["--device", "CPU"]
    return []


def nvidia_vram_mb():
    exe = shutil.which("nvidia-smi")
    if not exe:
        return 0
    try:
        proc = subprocess.run(
            [exe, "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return 0
        values = []
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line:
                values.append(int(float(line)))
        return sum(values)
    except Exception:
        return 0


def info(total_ram_mb=None):
    selected = mode()
    vram = nvidia_vram_mb() if selected == "cuda12" else 0
    unified = selected == "metal"
    capacity = int(vram or (total_ram_mb or 0))
    return {
        "accelerator": selected,
        "runtime_variant": runtime_variant(),
        "vram_mb": int(vram),
        "unified_memory": bool(unified),
        "capacity_mb": capacity,
    }


def label(total_ram_mb=None):
    data = info(total_ram_mb)
    if data["accelerator"] == "cuda12" and data["vram_mb"]:
        return "CUDA 12.4 / {} MiB VRAM".format(data["vram_mb"])
    if data["accelerator"] == "metal":
        return "Metal / unified memory"
    if data["accelerator"] == "vulkan":
        return "Vulkan"
    return "CPU"
