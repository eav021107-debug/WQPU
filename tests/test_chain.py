import unittest

import wqpu_chain


def word(value):
    if isinstance(value, str):
        value = int(value, 16)
    return "{:064x}".format(int(value))


def encode_member(wallet, endpoint, fingerprint, capacity, load_bps, updated_at, active=True):
    # Return encoding for (address wallet, Node node), where Node contains a dynamic string.
    wallet_word = word(int(wallet, 16))
    tuple_offset = word(64)
    endpoint_bytes = endpoint.encode("utf-8")
    endpoint_hex = endpoint_bytes.hex()
    endpoint_padded = endpoint_hex.ljust(((len(endpoint_bytes) + 31) // 32) * 64, "0")
    tuple_head = "".join([
        word(32 * 6),
        fingerprint.replace("0x", "").rjust(64, "0"),
        word(capacity),
        word(load_bps),
        word(updated_at),
        word(1 if active else 0),
    ])
    tuple_tail = word(len(endpoint_bytes)) + endpoint_padded
    return wallet_word + tuple_offset + tuple_head + tuple_tail


class DecodeClient(wqpu_chain.RegistryClient):
    def __init__(self, encoded):
        self.rpc_url = "mock"
        self.registry = "0x" + "11" * 20
        self.timeout = 1
        self._rpc_id = 0
        self.encoded = encoded

    def eth_call(self, data):
        return self.encoded


class ChainTests(unittest.TestCase):
    def test_member_at_decodes_registry_tuple(self):
        wallet = "0x" + "ab" * 20
        fingerprint = "0x" + "cd" * 32
        raw = encode_member(wallet, "10.0.0.7:7443", fingerprint, 32000, 1250, 123456, True)
        node = DecodeClient(raw).member_at(0)
        self.assertEqual(node["wallet"], wallet)
        self.assertEqual(node["endpoint"], "10.0.0.7:7443")
        self.assertEqual(node["fingerprint"], fingerprint)
        self.assertEqual(node["capacity"], 32000)
        self.assertEqual(node["load_bps"], 1250)
        self.assertEqual(node["updated_at"], 123456)
        self.assertTrue(node["active"])

    def test_endpoint_parser(self):
        self.assertEqual(wqpu_chain.parse_endpoint("host.test:7443"), ("host.test", 7443))
        self.assertEqual(wqpu_chain.parse_endpoint("[::1]:7443"), ("::1", 7443))
        with self.assertRaises(wqpu_chain.ChainError):
            wqpu_chain.parse_endpoint("missing-port")

    def test_choose_workers_can_split_one_request(self):
        class Scheduler(wqpu_chain.RegistryClient):
            def __init__(self):
                pass

            def discover(self, *args, **kwargs):
                return [
                    {"wallet": "a", "available_capacity": 7, "load_bps": 500},
                    {"wallet": "b", "available_capacity": 6, "load_bps": 900},
                    {"wallet": "c", "available_capacity": 20, "load_bps": 5000},
                ]

        selected, total = Scheduler().choose_workers(10)
        self.assertEqual([n["wallet"] for n in selected], ["a", "b"])
        self.assertEqual(total, 13)


if __name__ == "__main__":
    unittest.main()
