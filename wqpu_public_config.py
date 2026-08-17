#!/usr/bin/env python3
"""Compatibility normalizer for early WQPU v3 operator configs.

Some operator builds emitted all contract/runtime fields but omitted the two redundant
identity labels `protocol` and `network_uid`. Both are deterministic from the remaining
trusted config fields, so clients may derive them when absent. Explicit conflicting values
are never overwritten and continue to fail strict validation.
"""
from __future__ import print_function


def install(chain_module):
    if getattr(chain_module, "_wqpu_public_config_normalizer_installed", False):
        return chain_module
    original = chain_module.validate_network_config

    def validate_network_config(root, public):
        try:
            version = int((root or {}).get("version"))
        except Exception:
            version = None
        if version == chain_module.NETWORK_CONFIG_VERSION and isinstance(public, dict):
            fixed = dict(public)
            if not str(fixed.get("protocol") or "").strip():
                fixed["protocol"] = chain_module.PUBLIC_PROTOCOL
            if not str(fixed.get("network_uid") or "").strip():
                fixed["network_uid"] = chain_module.compute_network_uid(
                    fixed.get("chain_id"),
                    fixed.get("token"),
                    fixed.get("registry"),
                    fixed.get("market"),
                )
            public = fixed
        return original(root, public)

    chain_module.validate_network_config = validate_network_config
    chain_module._wqpu_public_config_normalizer_installed = True
    return chain_module
