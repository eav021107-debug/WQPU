import unittest

from chain.runtime_patch_network import patch_app_go_network
from tests.test_runtime_patch import SAMPLE


class RuntimeNetworkPatchTests(unittest.TestCase):
    def test_registers_target_network_precompile(self):
        out = patch_app_go_network(SAMPLE)
        self.assertIn(
            "wqpuprecompile.WithWQPUNetwork(precompiletypes.DefaultStaticPrecompiles(",
            out,
        )
        self.assertNotIn(
            "wqpuprecompile.WithWQPU(precompiletypes.DefaultStaticPrecompiles(",
            out,
        )

    def test_network_patch_is_idempotent(self):
        once = patch_app_go_network(SAMPLE)
        twice = patch_app_go_network(once)
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main()
