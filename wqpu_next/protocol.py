"""Deterministic protocol objects for the next WQPU architecture."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Dict, List, Optional

PROTOCOL_VERSION = 1
BPS = 10_000

SESSION_PERM_PROVIDER = 1 << 0
SESSION_PERM_JOB = 1 << 1
SESSION_PERM_SETTLE = 1 << 2
SESSION_ALL_PERMISSIONS = SESSION_PERM_PROVIDER | SESSION_PERM_JOB | SESSION_PERM_SETTLE

_EVM_ADDRESS = re.compile(r"^0x[0-9a-f]{40}$")


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def object_hash(value: object) -> str:
    if hasattr(value, "to_signing_dict"):
        payload = value.to_signing_dict()  # type: ignore[attr-defined]
    elif hasattr(value, "__dataclass_fields__"):
        payload = asdict(value)  # type: ignore[arg-type]
    else:
        payload = value
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _require_nonempty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("{} must be non-empty".format(name))


def _require_nonnegative(name: str, value: int) -> None:
    if not isinstance(value, int) or value < 0:
        raise ValueError("{} must be a non-negative integer".format(name))


def _require_session_address(name: str, value: str) -> None:
    if not isinstance(value, str) or not _EVM_ADDRESS.fullmatch(value):
        raise ValueError("{} must be a canonical lowercase EVM address".format(name))


@dataclass(frozen=True)
class SessionDelegation:
    """Wallet-signed permission for one local ephemeral EVM session account."""

    chain_id: str
    wallet: str
    session_address: str
    issued_height: int
    expires_height: int
    max_spend_units: int
    max_job_units: int
    revocation_nonce: int
    permissions: int = 0
    protocol_version: int = PROTOCOL_VERSION

    def validate(self) -> None:
        _require_nonempty("chain_id", self.chain_id)
        _require_nonempty("wallet", self.wallet)
        _require_session_address("session_address", self.session_address)
        _require_nonnegative("issued_height", self.issued_height)
        _require_nonnegative("expires_height", self.expires_height)
        _require_nonnegative("max_spend_units", self.max_spend_units)
        _require_nonnegative("max_job_units", self.max_job_units)
        _require_nonnegative("revocation_nonce", self.revocation_nonce)
        _require_nonnegative("permissions", self.permissions)
        if self.permissions & ~SESSION_ALL_PERMISSIONS:
            raise ValueError("unknown session permission bit")
        if self.expires_height <= self.issued_height:
            raise ValueError("session expiry must be after issue height")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError("unsupported protocol version")

    def to_signing_dict(self) -> Dict[str, object]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class ProviderRecord:
    wallet: str
    peer_id: str
    endpoints: List[str]
    model_hashes: List[str]
    capacity_units: int
    busy_units: int
    free_memory_bytes: int
    heartbeat_height: int
    expires_height: int
    capability_hash: str
    protocol_version: int = PROTOCOL_VERSION

    def validate(self, at_height: Optional[int] = None) -> None:
        _require_nonempty("wallet", self.wallet)
        _require_nonempty("peer_id", self.peer_id)
        _require_nonempty("capability_hash", self.capability_hash)
        if not self.endpoints:
            raise ValueError("provider must advertise at least one endpoint")
        for endpoint in self.endpoints:
            _require_nonempty("endpoint", endpoint)
        for model_hash in self.model_hashes:
            _require_nonempty("model_hash", model_hash)
        _require_nonnegative("capacity_units", self.capacity_units)
        _require_nonnegative("busy_units", self.busy_units)
        _require_nonnegative("free_memory_bytes", self.free_memory_bytes)
        _require_nonnegative("heartbeat_height", self.heartbeat_height)
        _require_nonnegative("expires_height", self.expires_height)
        if self.busy_units > self.capacity_units:
            raise ValueError("busy_units cannot exceed capacity_units")
        if self.expires_height <= self.heartbeat_height:
            raise ValueError("provider expiry must be after heartbeat")
        if at_height is not None and at_height >= self.expires_height:
            raise ValueError("provider record is expired")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError("unsupported protocol version")

    @property
    def free_units(self) -> int:
        return max(0, self.capacity_units - self.busy_units)

    @property
    def utilization_bps(self) -> int:
        if self.capacity_units <= 0:
            return BPS
        return min(BPS, (self.busy_units * BPS) // self.capacity_units)

    def to_signing_dict(self) -> Dict[str, object]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class GlobalPriceState:
    epoch: int
    price_per_million_units: int
    aggregate_capacity_units: int
    aggregate_busy_units: int

    def validate(self) -> None:
        _require_nonnegative("epoch", self.epoch)
        if self.price_per_million_units <= 0:
            raise ValueError("price must be positive")
        _require_nonnegative("aggregate_capacity_units", self.aggregate_capacity_units)
        _require_nonnegative("aggregate_busy_units", self.aggregate_busy_units)
        if self.aggregate_busy_units > self.aggregate_capacity_units:
            raise ValueError("aggregate busy cannot exceed aggregate capacity")


@dataclass(frozen=True)
class JobManifest:
    job_id: str
    requester_wallet: str
    requester_session_address: str
    model_hash: str
    prompt_commitment: str
    price_epoch: int
    price_per_million_units: int
    max_compute_units: int
    max_charge_units: int
    created_height: int
    expires_height: int
    provider_peer_ids: List[str]
    protocol_version: int = PROTOCOL_VERSION

    def validate(self) -> None:
        for name in ("job_id", "requester_wallet", "model_hash", "prompt_commitment"):
            _require_nonempty(name, getattr(self, name))
        _require_session_address("requester_session_address", self.requester_session_address)
        for name in (
            "price_epoch",
            "price_per_million_units",
            "max_compute_units",
            "max_charge_units",
            "created_height",
            "expires_height",
        ):
            _require_nonnegative(name, getattr(self, name))
        if self.price_per_million_units <= 0:
            raise ValueError("job price must be positive")
        if self.expires_height <= self.created_height:
            raise ValueError("job expiry must be after creation")
        if not self.provider_peer_ids:
            raise ValueError("distributed job must contain at least one provider")
        if len(set(self.provider_peer_ids)) != len(self.provider_peer_ids):
            raise ValueError("duplicate provider in job")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError("unsupported protocol version")

    def to_signing_dict(self) -> Dict[str, object]:
        self.validate()
        return asdict(self)


@dataclass(frozen=True)
class WorkReceipt:
    job_id: str
    provider_wallet: str
    provider_peer_id: str
    sequence: int
    compute_units: int
    cumulative_compute_units: int
    cumulative_payment_units: int
    result_commitment: str
    protocol_version: int = PROTOCOL_VERSION

    def validate(self) -> None:
        for name in ("job_id", "provider_wallet", "provider_peer_id", "result_commitment"):
            _require_nonempty(name, getattr(self, name))
        for name in ("sequence", "compute_units", "cumulative_compute_units", "cumulative_payment_units"):
            _require_nonnegative(name, getattr(self, name))
        if self.compute_units <= 0:
            raise ValueError("receipt must contain positive work")
        if self.cumulative_compute_units < self.compute_units:
            raise ValueError("cumulative work cannot be smaller than receipt work")
        if self.protocol_version != PROTOCOL_VERSION:
            raise ValueError("unsupported protocol version")

    def to_signing_dict(self) -> Dict[str, object]:
        self.validate()
        return asdict(self)
