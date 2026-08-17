import unittest

import wqpu_chain
import wqpu_public_config


class PublicConfigCompatTests(unittest.TestCase):
    def _public(self):
        return {
            "enabled": True,
            "chain_id": "0x7a69",
            "rpc_url": "https://rpc.example",
            "token": "0x" + "11" * 20,
            "registry": "0x" + "22" * 20,
            "market": "0x" + "33" * 20,
            "relays": [],
            "llama_cpp_tag": wqpu_chain.EXPECTED_LLAMA_CPP_TAG,
            "llama_rpc_protocol_major": wqpu_chain.EXPECTED_LLAMA_RPC_PROTOCOL_MAJOR,
            "llama_rpc_op_count": wqpu_chain.EXPECTED_LLAMA_RPC_OP_COUNT,
        }

    def test_missing_redundant_identity_is_derived(self):
        public = self._public()
        fixed = wqpu_public_config.normalize_public(
            wqpu_chain, {"version": 3, "public": public}, public
        )
        self.assertEqual(fixed["protocol"], wqpu_chain.PUBLIC_PROTOCOL)
        self.assertEqual(
            fixed["network_uid"],
            wqpu_chain.compute_network_uid(
                public["chain_id"], public["token"], public["registry"], public["market"]
            ),
        )
        # The strict validator must accept only the derived, contract-bound identity.
        wqpu_chain.validate_network_config({"version": 3}, fixed)

    def test_explicit_wrong_uid_is_not_overwritten(self):
        public = self._public()
        public["protocol"] = wqpu_chain.PUBLIC_PROTOCOL
        public["network_uid"] = "wqpu-" + "00" * 16
        fixed = wqpu_public_config.normalize_public(
            wqpu_chain, {"version": 3, "public": public}, public
        )
        self.assertEqual(fixed["network_uid"], public["network_uid"])
        with self.assertRaises(wqpu_chain.ChainError):
            wqpu_chain.validate_network_config({"version": 3}, fixed)


if __name__ == "__main__":
    unittest.main()
