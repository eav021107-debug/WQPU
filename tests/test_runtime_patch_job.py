import unittest

from chain.runtime_patch_job import patch_app_go_job

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


class JobRuntimePatchTests(unittest.TestCase):
    def test_registers_job_contract(self):
        out = patch_app_go_job(SAMPLE)
        self.assertIn(
            "wqpuprecompile.WithWQPUJobNetwork(precompiletypes.DefaultStaticPrecompiles(",
            out,
        )

    def test_is_idempotent(self):
        once = patch_app_go_job(SAMPLE)
        self.assertEqual(once, patch_app_go_job(once))


if __name__ == "__main__":
    unittest.main()
