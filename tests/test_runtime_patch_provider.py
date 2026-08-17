import unittest

from chain.runtime_patch_provider import patch_app_go_provider

SAMPLE = r'''
package evmd

import (
    precompiletypes "github.com/cosmos/evm/precompiles/types"
)

func build() {
    app.EVMKeeper = evmkeeper.NewKeeper().WithStaticPrecompiles(
        precompiletypes.DefaultStaticPrecompiles(app.BankKeeper),
    )
}
'''


class ProviderRuntimePatchTests(unittest.TestCase):
    def test_registers_provider_contract(self):
        out = patch_app_go_provider(SAMPLE)
        self.assertIn(
            "wqpuprecompile.WithWQPUProviderNetwork(precompiletypes.DefaultStaticPrecompiles(",
            out,
        )

    def test_is_idempotent(self):
        once = patch_app_go_provider(SAMPLE)
        self.assertEqual(once, patch_app_go_provider(once))


if __name__ == "__main__":
    unittest.main()
