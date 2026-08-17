"""Deterministic application state for the sovereign WQPU chain.

Consensus/networking are intentionally separate. A future consensus engine feeds
verified signed transactions into this state machine; every honest node must
produce the same resulting state from the same ordered transactions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .protocol import GlobalPriceState, ProviderRecord
from .scheduler import aggregate_price_state


@dataclass
class ChainState:
    chain_id: str
    height: int = 0
    epoch: int = 0
    price_per_million_units: int = 1_000
    providers: Dict[str, ProviderRecord] = field(default_factory=dict)
    reserved_units_by_peer: Dict[str, int] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.chain_id:
            raise ValueError("chain_id must be non-empty")
        if self.height < 0 or self.epoch < 0:
            raise ValueError("height/epoch cannot be negative")
        if self.price_per_million_units <= 0:
            raise ValueError("global price must be positive")
        for peer_id, units in self.reserved_units_by_peer.items():
            if not peer_id or units < 0:
                raise ValueError("invalid reservation state")

    def advance_height(self, blocks: int = 1) -> int:
        if blocks <= 0:
            raise ValueError("blocks must be positive")
        self.height += blocks
        self.expire_providers()
        return self.height

    def apply_provider_record(self, record: ProviderRecord) -> None:
        """Apply a signature-verified provider heartbeat/update.

        Signature verification belongs before this state transition. This method
        enforces deterministic state rules only.
        """
        record.validate()
        existing = self.providers.get(record.wallet)
        if existing is not None and record.heartbeat_height <= existing.heartbeat_height:
            raise ValueError("provider heartbeat must strictly increase")
        if record.heartbeat_height > self.height:
            raise ValueError("provider heartbeat cannot be from the future")
        if record.expires_height <= self.height:
            raise ValueError("cannot publish an already expired provider record")
        self.providers[record.wallet] = record

    def _provider_by_peer(self, peer_id: str) -> ProviderRecord:
        for record in self.active_providers():
            if record.peer_id == peer_id:
                return record
        raise ValueError("unknown or expired provider peer")

    def reserve_compute(self, peer_id: str, units: int) -> None:
        """Reserve capacity after a job reservation transaction is accepted."""
        if units <= 0:
            raise ValueError("reservation units must be positive")
        provider = self._provider_by_peer(peer_id)
        current = self.reserved_units_by_peer.get(peer_id, 0)
        if current + units > provider.capacity_units:
            raise ValueError("reservation exceeds provider capacity")
        self.reserved_units_by_peer[peer_id] = current + units

    def release_compute(self, peer_id: str, units: int) -> None:
        """Release reservation after completion, timeout or cancellation."""
        if units <= 0:
            raise ValueError("release units must be positive")
        current = self.reserved_units_by_peer.get(peer_id, 0)
        if units > current:
            raise ValueError("cannot release more than reserved")
        remaining = current - units
        if remaining:
            self.reserved_units_by_peer[peer_id] = remaining
        else:
            self.reserved_units_by_peer.pop(peer_id, None)

    def expire_providers(self) -> None:
        stale = [
            wallet
            for wallet, record in self.providers.items()
            if record.expires_height <= self.height
        ]
        stale_peer_ids = [self.providers[wallet].peer_id for wallet in stale]
        for wallet in stale:
            self.providers.pop(wallet, None)
        for peer_id in stale_peer_ids:
            self.reserved_units_by_peer.pop(peer_id, None)

    def active_providers(self) -> List[ProviderRecord]:
        self.expire_providers()
        return [
            self.providers[wallet]
            for wallet in sorted(self.providers)
            if self.providers[wallet].expires_height > self.height
        ]

    def active_capacity_units(self) -> int:
        return sum(record.capacity_units for record in self.active_providers())

    def active_reserved_units(self) -> int:
        active_peers = {record.peer_id for record in self.active_providers()}
        return sum(
            units
            for peer_id, units in self.reserved_units_by_peer.items()
            if peer_id in active_peers
        )

    def close_price_epoch(self) -> GlobalPriceState:
        """Advance the one global price from chain-confirmed reservations."""
        next_epoch = self.epoch + 1
        state = aggregate_price_state(
            network_capacity_units=self.active_capacity_units(),
            reserved_demand_units=self.active_reserved_units(),
            previous_price_per_million=self.price_per_million_units,
            next_epoch=next_epoch,
        )
        self.epoch = state.epoch
        self.price_per_million_units = state.price_per_million_units
        return state
