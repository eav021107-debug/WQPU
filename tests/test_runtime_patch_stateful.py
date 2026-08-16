import unittest

from chain.runtime_patch_stateful import patch_app_go_stateful

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


class StatefulRuntimePatchTests(unittest.TestCase):
    def test_registers_stateful_contract(self):
        out = patch_app_go_stateful(SAMPLE)
        self.assertIn(
            "wqpuprecompile.WithWQPUStatefulNetwork(precompiletypes.DefaultStaticPrecompiles(",
            out,
        )

    def test_is_idempotent(self):
        once = patch_app_go_stateful(SAMPLE)
        self.assertEqual(once, patch_app_go_stateful(once))


if __name__ == "__main__":
    unittest.main()
