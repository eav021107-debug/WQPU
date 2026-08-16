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

    def validate(self) -> None:
        if not self.chain_id:
            raise ValueError("chain_id must be non-empty")
        if self.height < 0 or self.epoch < 0:
            raise ValueError("height/epoch cannot be negative")
        if self.price_per_million_units <= 0:
            raise ValueError("global price must be positive")

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

    def expire_providers(self) -> None:
        stale = [
            wallet
            for wallet, record in self.providers.items()
            if record.expires_height <= self.height
        ]
        for wallet in stale:
            self.providers.pop(wallet, None)

    def active_providers(self) -> List[ProviderRecord]:
        self.expire_providers()
        return [
            self.providers[wallet]
            for wallet in sorted(self.providers)
            if self.providers[wallet].expires_height > self.height
        ]

    def close_price_epoch(self) -> GlobalPriceState:
        """Advance the single network-wide price using current active capacity."""
        self.expire_providers()
        next_epoch = self.epoch + 1
        state = aggregate_price_state(
            self.active_providers(),
            previous_price_per_million=self.price_per_million_units,
            next_epoch=next_epoch,
        )
        self.epoch = state.epoch
        self.price_per_million_units = state.price_per_million_units
        return state
