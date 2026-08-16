import unittest

from wqpu_next.chain_state import ChainState
from wqpu_next.protocol import ProviderRecord, WorkReceipt, object_hash
from wqpu_next.scheduler import (
    aggregate_price_state,
    charge_for_units,
    select_least_busy,
)

MODEL = "model-sha256:abc"


def provider(
    wallet,
    peer,
    capacity,
    busy,
    memory,
    heartbeat=10,
    expires=100,
):
    return ProviderRecord(
        wallet=wallet,
        peer_id=peer,
        endpoints=["wqpu://{}:7443".format(peer)],
        model_hashes=[MODEL],
        capacity_units=capacity,
        busy_units=busy,
        free_memory_bytes=memory,
        heartbeat_height=heartbeat,
        expires_height=expires,
        capability_hash="cap-{}".format(peer),
    )


class PriceTests(unittest.TestCase):
    def test_high_utilization_increases_one_global_price(self):
        p = [provider("a", "p1", 100, 95, 1_000_000)]
        state = aggregate_price_state(p, 1000, 1)
        self.assertGreater(state.price_per_million_units, 1000)
        self.assertLessEqual(state.price_per_million_units, 1050)

    def test_low_utilization_decreases_price(self):
        p = [provider("a", "p1", 100, 10, 1_000_000)]
        state = aggregate_price_state(p, 1000, 1)
        self.assertLess(state.price_per_million_units, 1000)
        self.assertGreaterEqual(state.price_per_million_units, 950)

    def test_no_capacity_moves_price_up_but_is_bounded(self):
        state = aggregate_price_state([], 1000, 1)
        self.assertEqual(state.price_per_million_units, 1050)

    def test_integer_charge_rounds_up(self):
        self.assertEqual(charge_for_units(1000, 1), 1)
        self.assertEqual(charge_for_units(1000, 1_000_000), 1000)


class SchedulerTests(unittest.TestCase):
    def test_uses_multiple_least_busy_workers_for_one_model(self):
        providers = [
            provider("busy", "p1", 100, 80, 900),
            provider("free-a", "p2", 100, 10, 700),
            provider("free-b", "p3", 100, 20, 700),
        ]
        plan = select_least_busy(
            providers,
            MODEL,
            model_bytes=1000,
            at_height=20,
            max_workers=3,
        )
        self.assertEqual(plan.assigned_model_bytes, 1000)
        self.assertEqual([x.peer_id for x in plan.allocations], ["p2", "p3"])

    def test_expired_provider_is_not_selected(self):
        providers = [
            provider("old", "p1", 100, 0, 5000, heartbeat=1, expires=5),
            provider("live", "p2", 100, 20, 5000, heartbeat=10, expires=100),
        ]
        plan = select_least_busy(providers, MODEL, 100, at_height=20)
        self.assertEqual([x.peer_id for x in plan.allocations], ["p2"])

    def test_insufficient_collective_memory_fails(self):
        providers = [provider("a", "p1", 100, 0, 100)]
        with self.assertRaises(RuntimeError):
            select_least_busy(providers, MODEL, 1000, at_height=20)


class ChainStateTests(unittest.TestCase):
    def test_provider_presence_expires(self):
        chain = ChainState(chain_id="wqpu-test", height=10)
        chain.apply_provider_record(provider("a", "p1", 100, 10, 1000, 10, 12))
        self.assertEqual(len(chain.active_providers()), 1)
        chain.advance_height(2)
        self.assertEqual(chain.active_providers(), [])

    def test_stale_heartbeat_cannot_replace_newer_record(self):
        chain = ChainState(chain_id="wqpu-test", height=20)
        chain.apply_provider_record(provider("a", "p1", 100, 10, 1000, 10, 30))
        with self.assertRaises(ValueError):
            chain.apply_provider_record(provider("a", "p1", 100, 5, 1000, 10, 40))

    def test_price_epoch_uses_active_chain_state(self):
        chain = ChainState(chain_id="wqpu-test", height=20, price_per_million_units=1000)
        chain.apply_provider_record(provider("a", "p1", 100, 95, 1000, 20, 30))
        epoch = chain.close_price_epoch()
        self.assertEqual(epoch.epoch, 1)
        self.assertEqual(chain.price_per_million_units, epoch.price_per_million_units)


class ReceiptTests(unittest.TestCase):
    def test_receipt_hash_is_deterministic(self):
        receipt = WorkReceipt(
            job_id="job-1",
            provider_wallet="wallet-a",
            provider_peer_id="peer-a",
            sequence=1,
            compute_units=50,
            cumulative_compute_units=50,
            cumulative_payment_units=7,
            result_commitment="result-hash",
        )
        self.assertEqual(object_hash(receipt), object_hash(receipt))


if __name__ == "__main__":
    unittest.main()
