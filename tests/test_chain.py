import json
import tempfile
import unittest
from pathlib import Path

import wqpu_chain


def word(value):
    if isinstance(value, str):
        value = int(value, 16)
    return "{:064x}".format(int(value))


def encode_member(wallet, endpoint, fingerprint, capacity, load_bps, updated_at, active=True):
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


def v3_config(registry=None, **public_overrides):
    token = "0x" + "11" * 20
    registry = registry or ("0x" + "22" * 20)
    market = "0x" + "33" * 20
    chain_id = "0x7a69"
    public = {
        "enabled": True,
        "testnet": True,
        "protocol": wqpu_chain.PUBLIC_PROTOCOL,
        "network_uid": wqpu_chain.compute_network_uid(chain_id, token, registry, market),
        "chain_id": chain_id,
        "chain_name": "WQPU Testnet",
        "native_symbol": "ETH",
        "rpc_url": "https://rpc.example",
        "token": token,
        "registry": registry,
        "market": market,
        "relayer_url": "https://relay.example/relay",
        "faucet_url": "https://relay.example/faucet",
        "relays": [{
            "host": "relay.example",
            "port": 7443,
            "fingerprint": "ab" * 32,
        }],
        "payments_enabled": False,
        "llama_cpp_tag": wqpu_chain.EXPECTED_LLAMA_CPP_TAG,
        "llama_rpc_protocol_major": wqpu_chain.EXPECTED_LLAMA_RPC_PROTOCOL_MAJOR,
        "llama_rpc_op_count": wqpu_chain.EXPECTED_LLAMA_RPC_OP_COUNT,
    }
    public.update(public_overrides)
    return {"version": 3, "public": public}


class DecodeClient(wqpu_chain.RegistryClient):
    def __init__(self, encoded):
        self.rpc_url = "mock"
        self.registry = "0x" + "11" * 20
        self.expected_chain_id = None
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

    def test_chain_id_is_validated(self):
        class Client(wqpu_chain.RegistryClient):
            def __init__(self):
                self.rpc_url = "mock"
                self.registry = "0x" + "11" * 20
                self.expected_chain_id = "0x7a69"
                self.timeout = 1
                self._rpc_id = 0

            def rpc(self, method, params):
                self.assertion = (method, params)
                return "0x7A69"

        client = Client()
        self.assertEqual(client.chain_id(), "0x7a69")
        self.assertEqual(client.assertion, ("eth_chainId", []))

    def test_wrong_chain_is_rejected(self):
        class Client(wqpu_chain.RegistryClient):
            def __init__(self):
                self.rpc_url = "mock"
                self.registry = "0x" + "11" * 20
                self.expected_chain_id = "0x1"
                self.timeout = 1
                self._rpc_id = 0

            def rpc(self, method, params):
                return "0x7a69"

        with self.assertRaises(wqpu_chain.ChainError):
            Client().chain_id()

    def test_published_network_config_only_loads_when_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "network.json"
            path.write_text(json.dumps({
                "public": {
                    "enabled": False,
                    "rpc_url": "https://ignored.example",
                    "registry": "0x" + "11" * 20,
                }
            }))
            self.assertEqual(wqpu_chain.load_network_config(path), {})

            # Legacy config without a root version remains readable for old/private
            # workflows; strict compatibility begins at published v3.
            path.write_text(json.dumps({
                "public": {
                    "enabled": True,
                    "chain_id": 31337,
                    "rpc_url": "https://rpc.example",
                    "registry": "0x" + "11" * 20,
                }
            }))
            loaded = wqpu_chain.load_network_config(path)
            self.assertEqual(loaded["chain_id"], 31337)
            self.assertEqual(loaded["rpc_url"], "https://rpc.example")

    def test_valid_v3_network_config_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "network.json"
            config = v3_config()
            path.write_text(json.dumps(config))
            loaded = wqpu_chain.load_network_config(path)
            self.assertEqual(loaded["protocol"], wqpu_chain.PUBLIC_PROTOCOL)
            self.assertEqual(
                loaded["network_uid"],
                wqpu_chain.compute_network_uid(
                    loaded["chain_id"], loaded["token"], loaded["registry"], loaded["market"]
                ),
            )

    def test_v3_wrong_network_uid_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "network.json"
            config = v3_config(network_uid="wqpu-" + "00" * 16)
            path.write_text(json.dumps(config))
            with self.assertRaises(wqpu_chain.ChainError):
                wqpu_chain.load_network_config(path)

    def test_v3_incompatible_llama_runtime_is_rejected(self):
        cases = [
            {"llama_cpp_tag": "b99999"},
            {"llama_rpc_protocol_major": wqpu_chain.EXPECTED_LLAMA_RPC_PROTOCOL_MAJOR + 1},
            {"llama_rpc_op_count": wqpu_chain.EXPECTED_LLAMA_RPC_OP_COUNT + 1},
        ]
        for override in cases:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "network.json"
                path.write_text(json.dumps(v3_config(**override)))
                with self.assertRaises(wqpu_chain.ChainError, msg=override):
                    wqpu_chain.load_network_config(path)

    def test_future_network_config_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "network.json"
            config = v3_config()
            config["version"] = wqpu_chain.NETWORK_CONFIG_VERSION + 1
            path.write_text(json.dumps(config))
            with self.assertRaises(wqpu_chain.ChainError):
                wqpu_chain.load_network_config(path)

    def test_v3_bad_relay_fingerprint_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "network.json"
            path.write_text(json.dumps(v3_config(relays=[{
                "host": "relay.example",
                "port": 7443,
                "fingerprint": "not-a-fingerprint",
            }])))
            with self.assertRaises(wqpu_chain.ChainError):
                wqpu_chain.load_network_config(path)

    def test_network_uid_is_deterministic_and_contract_bound(self):
        token = "0x" + "11" * 20
        registry = "0x" + "22" * 20
        market = "0x" + "33" * 20
        uid = wqpu_chain.compute_network_uid(31337, token, registry, market)
        self.assertEqual(uid, wqpu_chain.compute_network_uid("0x7A69", token.upper().replace("0X", "0x"), registry, market))
        changed = wqpu_chain.compute_network_uid(31337, token, "0x" + "44" * 20, market)
        self.assertNotEqual(uid, changed)

    def test_find_wallet(self):
        wanted = "0x" + "22" * 20

        class Client(wqpu_chain.RegistryClient):
            def __init__(self):
                pass

            def member_count(self):
                return 3

            def member_at(self, index):
                wallets = ["0x" + "11" * 20, wanted, "0x" + "33" * 20]
                return {"wallet": wallets[index], "active": True}

        self.assertEqual(Client().find_wallet(wanted)["wallet"], wanted)

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
