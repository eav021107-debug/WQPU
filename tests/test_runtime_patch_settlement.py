import unittest

from chain.runtime_patch_settlement import patch_app_go_settlement

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


class SettlementRuntimePatchTests(unittest.TestCase):
    def test_registers_full_settlement_contract(self):
        out = patch_app_go_settlement(SAMPLE)
        self.assertIn(
            "wqpuprecompile.WithWQPUSettlementNetwork(precompiletypes.DefaultStaticPrecompiles(",
            out,
        )

    def test_is_idempotent(self):
        once = patch_app_go_settlement(SAMPLE)
        self.assertEqual(once, patch_app_go_settlement(once))


if __name__ == "__main__":
    unittest.main()
