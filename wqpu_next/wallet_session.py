"""Wallet-facing authorization for a bounded WQPU session."""

from __future__ import annotations

import json
import re
from typing import Dict, Any

from .protocol import SessionDelegation

_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


def validate_wallet_address(address: str) -> str:
    if not isinstance(address, str) or not _ADDRESS.fullmatch(address):
        raise ValueError("wallet must be a 20-byte 0x-prefixed address")
    return address


def validate_session_address(address: str) -> str:
    if not isinstance(address, str) or not re.fullmatch(r"0x[0-9a-f]{40}", address):
        raise ValueError("session address must be a canonical lowercase EVM address")
    return address


def build_session_typed_data(delegation: SessionDelegation, evm_chain_id: int) -> Dict[str, Any]:
    delegation.validate()
    validate_wallet_address(delegation.wallet)
    validate_session_address(delegation.session_address)
    if not isinstance(evm_chain_id, int) or evm_chain_id <= 0:
        raise ValueError("EVM chain id must be a positive integer")

    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
            ],
            "WQPUSession": [
                {"name": "wallet", "type": "address"},
                {"name": "sessionAddress", "type": "address"},
                {"name": "wqpuChainId", "type": "string"},
                {"name": "issuedHeight", "type": "uint64"},
                {"name": "expiresHeight", "type": "uint64"},
                {"name": "maxSpendUnits", "type": "uint256"},
                {"name": "maxJobUnits", "type": "uint256"},
                {"name": "revocationNonce", "type": "uint64"},
                {"name": "permissions", "type": "uint64"},
                {"name": "protocolVersion", "type": "uint32"},
            ],
        },
        "primaryType": "WQPUSession",
        "domain": {"name": "WQPU Session", "version": "1", "chainId": evm_chain_id},
        "message": {
            "wallet": delegation.wallet,
            "sessionAddress": delegation.session_address,
            "wqpuChainId": delegation.chain_id,
            "issuedHeight": delegation.issued_height,
            "expiresHeight": delegation.expires_height,
            "maxSpendUnits": delegation.max_spend_units,
            "maxJobUnits": delegation.max_job_units,
            "revocationNonce": delegation.revocation_nonce,
            "permissions": delegation.permissions,
            "protocolVersion": delegation.protocol_version,
        },
    }


def build_wallet_request(delegation: SessionDelegation, evm_chain_id: int) -> Dict[str, Any]:
    typed_data = build_session_typed_data(delegation, evm_chain_id)
    return {
        "method": "eth_signTypedData_v4",
        "params": [
            delegation.wallet,
            json.dumps(typed_data, separators=(",", ":"), ensure_ascii=False),
        ],
    }
