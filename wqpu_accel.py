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
# NVIDIA CUDA 12.4 GA release notes pair Windows x64 with driver 551.61+. Auto mode is
# deliberately conservative; an explicit WQPU_ACCEL=cuda12 remains available to advanced
# users relying on CUDA minor-version compatibility or custom compatibility packages.
CUDA12_WINDOWS_DRIVER_MIN = (551, 61)


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


def _nvidia_query(field):
    exe = shutil.which("nvidia-smi")
    if not exe:
        return ""
    try:
        proc = subprocess.run(
            [exe, "--query-gpu={}".format(field), "--format=csv,noheader"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=5,
        )
        if proc.returncode != 0:
            return ""
        return proc.stdout.strip()
    except Exception:
        return ""


def _nvidia_present():
    return bool(_nvidia_query("name"))


def _version_tuple(value):
    out = []
    for piece in str(value or "").strip().split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


def _version_at_least(value, minimum):
    parsed = _version_tuple(value)
    if not parsed:
        return False
    width = max(len(parsed), len(minimum))
    left = parsed + (0,) * (width - len(parsed))
    right = tuple(minimum) + (0,) * (width - len(minimum))
    return left >= right


def nvidia_driver_version():
    raw = _nvidia_query("driver_version")
    if not raw:
        return ""
    # Multi-GPU machines normally share one driver. Choose the lowest reported version so
    # mixed/virtualized environments cannot make auto mode optimistic.
    versions = [line.strip() for line in raw.splitlines() if line.strip()]
    if not versions:
        return ""
    return min(versions, key=lambda item: _version_tuple(item) or (0,))


def _cuda12_auto_supported():
    if _system() != "Windows" or not _x64() or not _nvidia_present():
        return False
    return _version_at_least(nvidia_driver_version(), CUDA12_WINDOWS_DRIVER_MIN)


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
        # Vulkan loader presence normally comes from an installed display driver. NVIDIA
        # with an old CUDA driver reaches this fallback before CPU; AMD/Intel use it as the
        # primary accelerator path.
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
        if _cuda12_auto_supported():
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
    raw = _nvidia_query("memory.total")
    if not raw:
        return 0
    try:
        values = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            # nvidia-smi may include a unit suffix depending on driver/tool version.
            number = line.split()[0]
            values.append(int(float(number)))
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
        "nvidia_driver": nvidia_driver_version() if _nvidia_present() else "",
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
