#!/usr/bin/env python3
"""Fail-closed WQPU public-network identity guard.

A v3 public network has a deterministic network_uid derived from chain id plus the
Token/Registry/Market addresses. This module binds peer advertisements and routed worker
selection to that UID so nodes from an old/reset WQPU testnet cannot be mixed into the
current network merely because they share the same chain id or relay transport.
"""
from __future__ import print_function

from wqpu_chain import PUBLIC_PROTOCOL


def normalized_uid(value):
    value = str(value or "").strip().lower()
    return value


def network_uid_for(mesh):
    chain = getattr(mesh, "chain", None)
    network = getattr(chain, "network", {}) or {}
    return normalized_uid(network.get("network_uid"))


def peer_matches_network(expected_uid, info):
    expected_uid = normalized_uid(expected_uid)
    if not expected_uid:
        # v1/v2/legacy mode remains backward compatible.
        return True
    if not isinstance(info, dict):
        return False
    return (
        normalized_uid(info.get("network_uid")) == expected_uid
        and str(info.get("network") or "") == PUBLIC_PROTOCOL
    )


def install(runtime):
    """Patch ChainMesh once, preserving the existing runtime implementation."""
    cls = runtime.ChainMesh
    if getattr(cls, "_wqpu_network_guard_installed", False):
        return cls

    original_my_info = cls.my_info
    original_merge_nodes = cls.merge_nodes
    original_peers = cls.peers

    def my_info(self):
        info = dict(original_my_info(self))
        uid = network_uid_for(self)
        if uid:
            info["network"] = PUBLIC_PROTOCOL
            info["network_uid"] = uid
        return info

    def merge_nodes(self, route_key, nodes):
        uid = network_uid_for(self)
        if uid:
            nodes = [node for node in (nodes or []) if peer_matches_network(uid, node)]
        return original_merge_nodes(self, route_key, nodes)

    def peers(self):
        values = original_peers(self)
        uid = network_uid_for(self)
        if not uid:
            return values
        return [item for item in values if peer_matches_network(uid, item[1])]

    cls.my_info = my_info
    cls.merge_nodes = merge_nodes
    cls.peers = peers
    cls._wqpu_network_guard_installed = True
    return cls
