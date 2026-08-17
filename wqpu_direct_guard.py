#!/usr/bin/env python3
"""Fail-safe direct-P2P compatibility for WQPU dual metering.

The signed dual-meter prelude is understood by WQPU transport relays/workers. A direct
legacy-style dial reaches ggml-rpc-server without that interception, so sending the
prelude there would corrupt the RPC stream. When no bootstrap relay is configured we
therefore use the original requester meter only: compute still works, but the missing
worker attestation makes accounting refuse automatic vouchers.
"""

from __future__ import print_function

import wqpu_autopay
import wqpu_runtime


_ORIGINAL_AUTOPAY_PROXY = wqpu_autopay.AutoPayChainMesh.proxy_handler
_ORIGINAL_CHAIN_PROXY = wqpu_runtime.ChainMesh.proxy_handler


async def _guarded_proxy(self, target, cr, cw):
    if not getattr(self, "bootstrap_relays", None):
        return await _ORIGINAL_CHAIN_PROXY(self, target, cr, cw)
    return await _ORIGINAL_AUTOPAY_PROXY(self, target, cr, cw)


def install():
    wqpu_autopay.AutoPayChainMesh.proxy_handler = _guarded_proxy
