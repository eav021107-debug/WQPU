import json
import unittest

from wqpu_next.protocol import (
    SESSION_ALL_PERMISSIONS,
    SESSION_PERM_JOB,
    SessionDelegation,
)
from wqpu_next.wallet_session import build_session_typed_data, build_wallet_request


WALLET = "0x1111111111111111111111111111111111111111"
SESSION_KEY = "0x" + "22" * 32


def delegation(**changes):
    values = dict(
        chain_id="wqpu-dev-1",
        wallet=WALLET,
        session_pubkey=SESSION_KEY,
        issued_height=100,
        expires_height=200,
        max_spend_units=1_000_000,
        max_job_units=100_000,
        revocation_nonce=7,
        permissions=SESSION_PERM_JOB,
    )
    values.update(changes)
    return SessionDelegation(**values)


class WalletSessionTests(unittest.TestCase):
    def test_typed_data_binds_both_chain_ids_and_limits(self):
        typed = build_session_typed_data(delegation(), 711711)
        self.assertEqual(typed["domain"]["chainId"], 711711)
        self.assertEqual(typed["message"]["wqpuChainId"], "wqpu-dev-1")
        self.assertEqual(typed["message"]["maxSpendUnits"], 1_000_000)
        self.assertEqual(typed["message"]["maxJobUnits"], 100_000)
        self.assertEqual(typed["message"]["permissions"], SESSION_PERM_JOB)

    def test_wallet_request_contains_no_secret_material(self):
        request = build_wallet_request(delegation(), 711711)
        self.assertEqual(request["method"], "eth_signTypedData_v4")
        serialized = json.dumps(request).lower()
        for forbidden in ("privatekey", "private_key", "mnemonic", "seed phrase", "seed_phrase"):
            self.assertNotIn(forbidden, serialized)

    def test_unknown_permission_is_rejected(self):
        with self.assertRaises(ValueError):
            delegation(permissions=SESSION_ALL_PERMISSIONS | (1 << 10)).validate()

    def test_invalid_wallet_is_rejected(self):
        with self.assertRaises(ValueError):
            build_session_typed_data(delegation(wallet="not-a-wallet"), 711711)

    def test_invalid_session_key_is_rejected(self):
        with self.assertRaises(ValueError):
            build_session_typed_data(delegation(session_pubkey="0x1234"), 711711)

    def test_expired_shape_is_rejected_before_wallet_prompt(self):
        with self.assertRaises(ValueError):
            build_wallet_request(delegation(expires_height=100), 711711)


class ConsensusVectorTests(unittest.TestCase):
    def test_python_negative_floor_price_vector(self):
        from wqpu_next.scheduler import aggregate_price_state

        state = aggregate_price_state(10_000, 6_999, 1000, 1)
        self.assertEqual(state.price_per_million_units, 999)


if __name__ == "__main__":
    unittest.main()
