"""Deterministic WQPU global price controller and distributed scheduler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from .protocol import BPS, GlobalPriceState, ProviderRecord

TARGET_UTILIZATION_BPS = 7000
MAX_PRICE_MOVE_BPS = 500
MIN_PRICE_PER_MILLION = 1
DEFAULT_MAX_WORKERS = 8
MODEL_HEADROOM_BPS = 1200  # keep 12% advertised memory unused


@dataclass(frozen=True)
class WorkerAllocation:
    wallet: str
    peer_id: str
    endpoint: str
    assigned_model_bytes: int
    utilization_bps: int
    free_units: int


@dataclass(frozen=True)
class SchedulePlan:
    model_hash: str
    total_model_bytes: int
    allocations: List[WorkerAllocation]

    @property
    def assigned_model_bytes(self) -> int:
        return sum(x.assigned_model_bytes for x in self.allocations)


def aggregate_price_state(
    network_capacity_units: int,
    reserved_demand_units: int,
    previous_price_per_million: int,
    next_epoch: int,
) -> GlobalPriceState:
    """Calculate the one network-wide compute price for the next epoch.

    Price demand MUST come from chain-accepted reservations/work, not a provider's
    self-reported `busy_units`. Otherwise a provider could lie about load to move
    the market price. The controller is deliberately simple and deterministic:
    integer-only math, a 70% target, and a maximum 5% change per epoch.
    """
    if network_capacity_units < 0:
        raise ValueError("network capacity must be non-negative")
    if reserved_demand_units < 0:
        raise ValueError("reserved demand must be non-negative")
    if previous_price_per_million <= 0:
        raise ValueError("previous price must be positive")
    if next_epoch < 0:
        raise ValueError("epoch must be non-negative")

    capacity = network_capacity_units
    busy = min(reserved_demand_units, capacity) if capacity > 0 else 0

    if capacity <= 0:
        utilization_bps = BPS
    else:
        utilization_bps = min(BPS, (busy * BPS) // capacity)

    deviation = utilization_bps - TARGET_UTILIZATION_BPS
    move_bps = deviation // 4
    move_bps = max(-MAX_PRICE_MOVE_BPS, min(MAX_PRICE_MOVE_BPS, move_bps))

    numerator = previous_price_per_million * (BPS + move_bps)
    price = max(MIN_PRICE_PER_MILLION, numerator // BPS)

    return GlobalPriceState(
        epoch=next_epoch,
        price_per_million_units=price,
        aggregate_capacity_units=capacity,
        aggregate_busy_units=busy,
    )


def _usable_memory_bytes(provider: ProviderRecord) -> int:
    headroom = (provider.free_memory_bytes * MODEL_HEADROOM_BPS) // BPS
    return max(0, provider.free_memory_bytes - headroom)


def _effective_busy_units(provider: ProviderRecord, reserved_by_peer: Dict[str, int]) -> int:
    chain_reserved = max(0, reserved_by_peer.get(provider.peer_id, 0))
    return min(provider.capacity_units, max(provider.busy_units, chain_reserved))


def _effective_free_units(provider: ProviderRecord, reserved_by_peer: Dict[str, int]) -> int:
    return max(0, provider.capacity_units - _effective_busy_units(provider, reserved_by_peer))


def _effective_utilization_bps(provider: ProviderRecord, reserved_by_peer: Dict[str, int]) -> int:
    if provider.capacity_units <= 0:
        return BPS
    busy = _effective_busy_units(provider, reserved_by_peer)
    return min(BPS, (busy * BPS) // provider.capacity_units)


def compatible_providers(
    providers: Iterable[ProviderRecord],
    model_hash: str,
    at_height: int,
    reserved_by_peer: Optional[Dict[str, int]] = None,
) -> List[ProviderRecord]:
    reserved = reserved_by_peer or {}
    out: List[ProviderRecord] = []
    for provider in providers:
        try:
            provider.validate(at_height=at_height)
        except ValueError:
            continue
        if model_hash not in provider.model_hashes:
            continue
        if _effective_free_units(provider, reserved) <= 0:
            continue
        if _usable_memory_bytes(provider) <= 0:
            continue
        out.append(provider)
    return out


def select_least_busy(
    providers: Iterable[ProviderRecord],
    model_hash: str,
    model_bytes: int,
    at_height: int,
    max_workers: int = DEFAULT_MAX_WORKERS,
    reserved_by_peer: Optional[Dict[str, int]] = None,
) -> SchedulePlan:
    """Select enough least-busy compatible peers to hold one model collectively.

    No worker is assumed to contain the whole model. Chain reservations are an
    authoritative floor for load; signed provider telemetry may report an even
    higher local load. The scheduler therefore cannot make a provider look freer
    than the work already reserved to it on-chain.
    """
    if not model_hash:
        raise ValueError("model_hash must be non-empty")
    if model_bytes <= 0:
        raise ValueError("model_bytes must be positive")
    if max_workers <= 0:
        raise ValueError("max_workers must be positive")

    reserved = reserved_by_peer or {}
    candidates = compatible_providers(providers, model_hash, at_height, reserved)
    candidates.sort(
        key=lambda p: (
            _effective_utilization_bps(p, reserved),
            -_effective_free_units(p, reserved),
            -_usable_memory_bytes(p),
            p.wallet,
            p.peer_id,
        )
    )

    allocations: List[WorkerAllocation] = []
    remaining = model_bytes

    for provider in candidates[:max_workers]:
        if remaining <= 0:
            break
        usable_memory = _usable_memory_bytes(provider)
        assigned = min(remaining, usable_memory)
        if assigned <= 0:
            continue
        allocations.append(
            WorkerAllocation(
                wallet=provider.wallet,
                peer_id=provider.peer_id,
                endpoint=provider.endpoints[0],
                assigned_model_bytes=assigned,
                utilization_bps=_effective_utilization_bps(provider, reserved),
                free_units=_effective_free_units(provider, reserved),
            )
        )
        remaining -= assigned

    if remaining > 0:
        raise RuntimeError(
            "insufficient compatible free memory: missing {} bytes".format(remaining)
        )

    return SchedulePlan(
        model_hash=model_hash,
        total_model_bytes=model_bytes,
        allocations=allocations,
    )


def charge_for_units(price_per_million_units: int, compute_units: int) -> int:
    """Deterministic integer charge, rounded up so tiny jobs are not free."""
    if price_per_million_units <= 0:
        raise ValueError("price must be positive")
    if compute_units < 0:
        raise ValueError("compute units must be non-negative")
    if compute_units == 0:
        return 0
    numerator = price_per_million_units * compute_units
    return (numerator + 999_999) // 1_000_000
