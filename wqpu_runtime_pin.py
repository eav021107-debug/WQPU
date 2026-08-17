#!/usr/bin/env python3
"""Pinned llama.cpp runtime loader for WQPU CPU/GPU workers.

All WQPU peers must speak the same ggml RPC protocol. The selected release lives in
network-config.json instead of silently following upstream `latest` on each machine.
The accelerator build can vary per provider without changing the pinned RPC version.
"""

from __future__ import print_function

import hashlib
import json
import os
import shutil
import tarfile
import time
import zipfile
from pathlib import Path

import wqpu
import wqpu_accel


DEFAULT_LLAMA_TAG = "b10456"
NETWORK_ATTEMPTS = 4

# Exact official b10456 release assets. CPU plus GPU-capable builds share the same pinned
# llama.cpp/RPC protocol; only the local backend plugin set changes per provider machine.
PINNED_ASSETS = {
    "b10456": {
        "-bin-ubuntu-x64.tar.gz": "d07b3f80f3a1ed1de46bfba5671b4af40a87417e1dbf35d0603ad2d623ddc577",
        "-bin-ubuntu-arm64.tar.gz": "7b59bce92d07f636c8137e481967ab4bfd677beb0668323d9352b2dbd1e3ea75",
        "-bin-ubuntu-vulkan-x64.tar.gz": "856fcfe9b273e6e813c8d5745396693080ce1cca8134b1180f0e8e2f22b21772",
        "-bin-ubuntu-vulkan-arm64.tar.gz": "2b2d4b1f9216b4e51d1a9ed7a22aaba2fa8f92be453c7837098290ee1c8c2c40",
        "-bin-macos-arm64.tar.gz": "5ab514e2b1c8b0276af2536ea2b58643952b6fe79c9bc83bb2e1a336b4ddeb6f",
        "-bin-macos-x64.tar.gz": "5913d3975299438980ea932b3538954e0c99a42ceae497dfb5085677ed21f489",
        "-bin-win-cpu-x64.zip": "52ea16a7c5de7230638fbd2e90a4f78185f6c47d06d65a328e3522823fbf2a2d",
        "-bin-win-cpu-arm64.zip": "351d9dd847b8c711cf0d8a73be1c22d2f0227738d7972a8fcb3729a731919efa",
        "-bin-win-vulkan-x64.zip": "60f3d31cc7c2fe62de8f34f8d75ffd06655b4de83bcc5aa6f08df56be42ebb91",
        "-bin-win-cuda-12.4-x64.zip": "10da43a0ac6b0ca67ebfcc5afb9778861819e9c0cd9f1374183e1c1cf6271dfa",
    },
}

# CUDA Windows builds ship the NVIDIA runtime DLLs separately. WQPU pins and verifies
# that companion bundle too, then places the DLLs next to llama-server/ggml-rpc-server.
PINNED_NAMED_ASSETS = {
    "b10456": {
        "cudart-llama-bin-win-cuda-12.4-x64.zip": "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6",
    },
}


def network_runtime_config():
    try:
        path = Path(__file__).resolve().with_name("network-config.json")
        root = json.loads(path.read_text())
        public = root.get("public") if isinstance(root, dict) else {}
        return dict(public or {})
    except Exception:
        return {}


def desired_tag():
    return (
        os.environ.get("WQPU_LLAMA_TAG", "").strip()
        or str(network_runtime_config().get("llama_cpp_tag") or "").strip()
        or DEFAULT_LLAMA_TAG
    )


def desired_variant():
    return wqpu_accel.runtime_variant()


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _verify_asset(path, asset):
    expected = str(asset.get("digest") or "").strip().lower()
    if not expected:
        raise RuntimeError("pinned llama.cpp asset has no SHA-256 digest")
    if not expected.startswith("sha256:"):
        raise RuntimeError("unsupported llama.cpp asset digest format")
    actual = _sha256(path)
    if actual != expected.split(":", 1)[1]:
        raise RuntimeError("llama.cpp asset SHA-256 mismatch")


def _retryable(exc):
    code = getattr(exc, "code", None)
    if code is not None:
        try:
            code = int(code)
        except Exception:
            code = None
    return code is None or code >= 500


def _backoff(attempt):
    time.sleep(min(8, 1 << int(attempt)))


def _release_json(tag, attempts=NETWORK_ATTEMPTS):
    url = "https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{}".format(tag)
    last = None
    for attempt in range(int(attempts)):
        try:
            return wqpu.api_json(url)
        except Exception as exc:
            last = exc
            if not _retryable(exc) or attempt + 1 >= int(attempts):
                break
            _backoff(attempt)
    raise RuntimeError("could not fetch pinned llama.cpp {} release metadata: {}".format(tag, last))


def _download_asset(url, path, attempts=NETWORK_ATTEMPTS):
    last = None
    for attempt in range(int(attempts)):
        try:
            try:
                path.unlink()
            except OSError:
                pass
            wqpu.download(url, path)
            return
        except Exception as exc:
            last = exc
            try:
                path.unlink()
            except OSError:
                pass
            if not _retryable(exc) or attempt + 1 >= int(attempts):
                break
            _backoff(attempt)
    raise RuntimeError("could not download pinned llama.cpp runtime: {}".format(last))


def _release_url(tag, name):
    return "https://github.com/ggml-org/llama.cpp/releases/download/{}/{}".format(tag, name)


def _static_asset(tag, suffix):
    digest = (PINNED_ASSETS.get(str(tag)) or {}).get(str(suffix))
    if not digest:
        return None
    name = "llama-{}{}".format(tag, suffix)
    return {
        "name": name,
        "digest": "sha256:" + digest,
        "browser_download_url": _release_url(tag, name),
        "source": "wqpu-pinned-manifest",
    }


def _static_named_asset(tag, name):
    digest = (PINNED_NAMED_ASSETS.get(str(tag)) or {}).get(str(name))
    if not digest:
        return None
    return {
        "name": name,
        "digest": "sha256:" + digest,
        "browser_download_url": _release_url(tag, name),
        "source": "wqpu-pinned-manifest",
    }


def _asset_for(tag, suffix):
    static = _static_asset(tag, suffix)
    if static:
        return static
    release = _release_json(tag)
    actual_tag = str(release.get("tag_name") or "")
    if actual_tag != tag:
        raise RuntimeError("llama.cpp release tag mismatch")
    asset = next(
        (item for item in release.get("assets") or [] if str(item.get("name") or "").endswith(suffix)),
        None,
    )
    if not asset:
        raise RuntimeError("no llama.cpp {} asset for {}".format(tag, suffix))
    if not str(asset.get("digest") or "").lower().startswith("sha256:"):
        raise RuntimeError("llama.cpp development override asset is missing SHA-256")
    return asset


def _named_asset_for(tag, name):
    static = _static_named_asset(tag, name)
    if static:
        return static
    release = _release_json(tag)
    asset = next(
        (item for item in release.get("assets") or [] if str(item.get("name") or "") == str(name)),
        None,
    )
    if not asset:
        raise RuntimeError("no llama.cpp {} companion asset {}".format(tag, name))
    if not str(asset.get("digest") or "").lower().startswith("sha256:"):
        raise RuntimeError("llama.cpp companion asset is missing SHA-256")
    return asset


def _extract_main(archive, target):
    if archive.suffix == ".zip":
        with zipfile.ZipFile(str(archive)) as bundle:
            bundle.extractall(str(target))
    else:
        with tarfile.open(str(archive), "r:gz") as bundle:
            bundle.extractall(str(target))


def _download_verified(asset, archive):
    try:
        _download_asset(asset["browser_download_url"], archive)
        _verify_asset(archive, asset)
    except Exception:
        try:
            archive.unlink()
        except OSError:
            pass
        raise


def _install_companion(asset, target, binary_dir):
    archive = wqpu.RUNTIME / str(asset["name"])
    staging = target / "_wqpu_companion"
    if staging.exists():
        shutil.rmtree(str(staging))
    staging.mkdir(parents=True)
    try:
        _download_verified(asset, archive)
        if archive.suffix == ".zip":
            with zipfile.ZipFile(str(archive)) as bundle:
                bundle.extractall(str(staging))
        else:
            with tarfile.open(str(archive), "r:gz") as bundle:
                bundle.extractall(str(staging))
        for source in staging.rglob("*"):
            if source.is_file():
                shutil.copy2(str(source), str(binary_dir / source.name))
    finally:
        try:
            archive.unlink()
        except OSError:
            pass
        if staging.exists():
            shutil.rmtree(str(staging))


def ensure_runtime():
    wqpu.ensure_home()
    tag = desired_tag()
    variant = desired_variant()
    meta = wqpu.RUNTIME / "current.json"
    if meta.exists():
        try:
            cached = json.loads(meta.read_text())
            server = Path(cached["server"])
            rpc = Path(cached["rpc"])
            cached_variant = str(cached.get("variant") or "cpu")
            if cached.get("tag") == tag and cached_variant == variant and server.exists() and rpc.exists():
                return server, rpc, tag
        except Exception:
            pass

    cpu_suffix = wqpu.asset_suffix()
    suffix = wqpu_accel.main_asset_suffix(cpu_suffix)
    asset = _asset_for(tag, suffix)
    companions = [
        _named_asset_for(tag, name)
        for name in wqpu_accel.companion_asset_names(tag)
    ]

    print("WQPU: downloading pinned llama.cpp {} [{}]...".format(tag, variant))
    target = wqpu.RUNTIME / "{}-{}".format(tag, variant)
    if target.exists():
        shutil.rmtree(str(target))
    target.mkdir(parents=True)
    archive = wqpu.RUNTIME / str(asset["name"])
    try:
        _download_verified(asset, archive)
        _extract_main(archive, target)
    finally:
        try:
            archive.unlink()
        except OSError:
            pass

    server = wqpu.find_binary(target, "llama-server")
    rpc = wqpu.find_binary(target, "ggml-rpc-server")
    for companion in companions:
        _install_companion(companion, target, server.parent)

    meta.write_text(json.dumps({
        "tag": tag,
        "variant": variant,
        "accelerator": wqpu_accel.mode(),
        "server": str(server),
        "rpc": str(rpc),
        "asset": asset.get("name"),
        "asset_digest": asset.get("digest"),
        "asset_source": asset.get("source", "release-api"),
        "companions": [item.get("name") for item in companions],
    }, indent=2) + "\n")
    return server, rpc, tag
