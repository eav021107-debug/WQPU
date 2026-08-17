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
        return
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
    release = _release_json(tag)
    actual_tag = str(release.get("tag_name") or "")
    if actual_tag != tag:
        raise RuntimeError("llama.cpp release tag mismatch")

    suffix = wqpu.asset_suffix()
    asset = next(
        (item for item in release.get("assets") or [] if str(item.get("name") or "").endswith(suffix)),
        None,
    )
    if not asset:
        raise RuntimeError("no llama.cpp {} asset for {}".format(tag, suffix))

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
    }, indent=2) + "\n")
    return server, rpc, tag
