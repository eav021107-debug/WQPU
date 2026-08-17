#!/usr/bin/env python3
"""Pinned llama.cpp runtime loader for WQPU public/private nodes.

All WQPU peers must speak the same ggml RPC protocol. The selected release lives in
network-config.json instead of silently following upstream `latest` on each machine.
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


DEFAULT_LLAMA_TAG = "b10456"
NETWORK_ATTEMPTS = 4

# Exact official release assets for the WQPU-pinned runtime. Keeping the asset name and
# SHA-256 in-repo removes a fragile GitHub Releases API lookup from every clean install
# while preserving cryptographic verification of the downloaded binary archive.
PINNED_ASSETS = {
    "b10456": {
        "-bin-ubuntu-x64.tar.gz": "d07b3f80f3a1ed1de46bfba5671b4af40a87417e1dbf35d0603ad2d623ddc577",
        "-bin-ubuntu-arm64.tar.gz": "7b59bce92d07f636c8137e481967ab4bfd677beb0668323d9352b2dbd1e3ea75",
        "-bin-macos-arm64.tar.gz": "5ab514e2b1c8b0276af2536ea2b58643952b6fe79c9bc83bb2e1a336b4ddeb6f",
        "-bin-macos-x64.tar.gz": "5913d3975299438980ea932b3538954e0c99a42ceae497dfb5085677ed21f489",
        "-bin-win-cpu-x64.zip": "52ea16a7c5de7230638fbd2e90a4f78185f6c47d06d65a328e3522823fbf2a2d",
        "-bin-win-cpu-arm64.zip": "351d9dd847b8c711cf0d8a73be1c22d2f0227738d7972a8fcb3729a731919efa",
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
    # Exact 4xx responses (missing tag, forbidden asset, etc.) will not improve by retrying.
    return code is None or code >= 500


def _backoff(attempt):
    # Keep installs responsive while smoothing over transient GitHub/CDN 5xx/timeouts.
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


def _static_asset(tag, suffix):
    digest = (PINNED_ASSETS.get(str(tag)) or {}).get(str(suffix))
    if not digest:
        return None
    name = "llama-{}{}".format(tag, suffix)
    return {
        "name": name,
        "digest": "sha256:" + digest,
        "browser_download_url": "https://github.com/ggml-org/llama.cpp/releases/download/{}/{}".format(tag, name),
        "source": "wqpu-pinned-manifest",
    }


def _asset_for(tag, suffix):
    static = _static_asset(tag, suffix)
    if static:
        return static

    # Development override for tags not published in the WQPU manifest. Production
    # network configs should pin only a manifest-backed tag so all peers share a known
    # archive digest without trusting mutable release metadata at install time.
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


def ensure_runtime():
    wqpu.ensure_home()
    tag = desired_tag()
    meta = wqpu.RUNTIME / "current.json"
    if meta.exists():
        try:
            cached = json.loads(meta.read_text())
            server = Path(cached["server"])
            rpc = Path(cached["rpc"])
            if cached.get("tag") == tag and server.exists() and rpc.exists():
                return server, rpc, tag
        except Exception:
            pass

    print("WQPU: downloading pinned llama.cpp {}...".format(tag))
    suffix = wqpu.asset_suffix()
    asset = _asset_for(tag, suffix)

    target = wqpu.RUNTIME / tag
    if target.exists():
        shutil.rmtree(str(target))
    target.mkdir(parents=True)
    archive = wqpu.RUNTIME / str(asset["name"])
    try:
        _download_asset(asset["browser_download_url"], archive)
        _verify_asset(archive, asset)
        if archive.suffix == ".zip":
            with zipfile.ZipFile(str(archive)) as bundle:
                bundle.extractall(str(target))
        else:
            with tarfile.open(str(archive), "r:gz") as bundle:
                bundle.extractall(str(target))
    finally:
        try:
            archive.unlink()
        except OSError:
            pass

    server = wqpu.find_binary(target, "llama-server")
    rpc = wqpu.find_binary(target, "ggml-rpc-server")
    meta.write_text(json.dumps({
        "tag": tag,
        "server": str(server),
        "rpc": str(rpc),
        "asset": asset.get("name"),
        "asset_digest": asset.get("digest"),
        "asset_source": asset.get("source", "release-api"),
    }, indent=2) + "\n")
    return server, rpc, tag
