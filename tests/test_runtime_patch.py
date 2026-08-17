import unittest

from chain.runtime_patch import patch_app_go


SAMPLE = r'''
package evmd

import (
    "fmt"
    precompiletypes "github.com/cosmos/evm/precompiles/types"
)

func build() {
    app.EVMKeeper = evmkeeper.NewKeeper().WithStaticPrecompiles(
        precompiletypes.DefaultStaticPrecompiles(
            app.BankKeeper,
            helper.Call("text with ) inside"),
        ),
    )
}
'''


class RuntimePatchTests(unittest.TestCase):
    def test_adds_import_and_wraps_only_default_map(self):
        out = patch_app_go(SAMPLE)
        self.assertIn(
            'wqpuprecompile "github.com/cosmos/evm/precompiles/wqpu"', out
        )
        self.assertIn(
            "wqpuprecompile.WithWQPU(precompiletypes.DefaultStaticPrecompiles(", out
        )
        self.assertIn('helper.Call("text with ) inside")', out)

    def test_is_idempotent(self):
        once = patch_app_go(SAMPLE)
        twice = patch_app_go(once)
        self.assertEqual(once, twice)

    def test_rejects_unknown_upstream_shape(self):
        with self.assertRaises(ValueError):
            patch_app_go("package evmd\n")

    def test_rejects_multiple_default_calls(self):
        sample = SAMPLE.replace(
            "func build() {",
            "func build() {\n_ = precompiletypes.DefaultStaticPrecompiles()",
        )
        with self.assertRaises(ValueError):
            patch_app_go(sample)


if __name__ == "__main__":
    unittest.main()
