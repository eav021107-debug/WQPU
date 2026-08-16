"""Deterministic WQPU global price controller and distributed scheduler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence

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
    providers: Iterable[ProviderRecord],
    previous_price_per_million: int,
    next_epoch: int,
) -> GlobalPriceState:
    """Calculate the one network-wide compute price for the next epoch.

    The controller is deliberately simple and consensus-friendly:
    - all arithmetic is integer-only;
    - target utilization is 70%;
    - price can move by at most 5% per epoch;
    - the same provider snapshot always produces the same answer.
    """
    if previous_price_per_million <= 0:
        raise ValueError("previous price must be positive")
    if next_epoch < 0:
        raise ValueError("epoch must be non-negative")

    items = list(providers)
    capacity = sum(max(0, p.capacity_units) for p in items)
    busy = sum(min(max(0, p.busy_units), max(0, p.capacity_units)) for p in items)

    if capacity <= 0:
        utilization_bps = BPS
    else:
        utilization_bps = min(BPS, (busy * BPS) // capacity)

    deviation = utilization_bps - TARGET_UTILIZATION_BPS
    # Quarter-strength response prevents oscillation; hard-bound to +/- 5%.
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


def compatible_providers(
    providers: Iterable[ProviderRecord],
    model_hash: str,
    at_height: int,
) -> List[ProviderRecord]:
    out: List[ProviderRecord] = []
    for provider in providers:
        try:
            provider.validate(at_height=at_height)
        except ValueError:
            continue
        if model_hash not in provider.model_hashes:
            continue
        if provider.free_units <= 0:
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
) -> SchedulePlan:
    """Select enough least-busy compatible peers to hold one model collectively.

    No worker is assumed to contain the whole model. The scheduler fills model
    bytes across workers, starting with the lowest utilization. Free capacity and
    memory are deterministic tie-breakers; wallet/peer IDs make the final order
    stable across coordinators reading the same chain snapshot.
    """
    if not model_hash:
        raise ValueError("model_hash must be non-empty")
    if model_bytes <= 0:
        raise ValueError("model_bytes must be positive")
    if max_workers <= 0:
        raise ValueError("max_workers must be positive")

    candidates = compatible_providers(providers, model_hash, at_height)
    candidates.sort(
        key=lambda p: (
            p.utilization_bps,
            -p.free_units,
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
                utilization_bps=provider.utilization_bps,
                free_units=provider.free_units,
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
