import os
import unittest
from unittest import mock

import wqpu_accel


class AcceleratorTests(unittest.TestCase):
    def test_auto_prefers_metal_on_macos(self):
        with mock.patch.object(wqpu_accel, "_system", return_value="Darwin"):
            self.assertEqual(wqpu_accel.auto_mode(), "metal")

    def test_auto_prefers_cuda12_for_supported_windows_nvidia(self):
        with mock.patch.object(wqpu_accel, "_system", return_value="Windows"), \
             mock.patch.object(wqpu_accel, "_cuda12_auto_supported", return_value=True):
            self.assertEqual(wqpu_accel.auto_mode(), "cuda12")

    def test_old_windows_nvidia_driver_falls_back_to_vulkan(self):
        with mock.patch.object(wqpu_accel, "_system", return_value="Windows"), \
             mock.patch.object(wqpu_accel, "_cuda12_auto_supported", return_value=False), \
             mock.patch.object(wqpu_accel, "_x64", return_value=True), \
             mock.patch.object(wqpu_accel, "_vulkan_gpu_present", return_value=True):
            self.assertEqual(wqpu_accel.auto_mode(), "vulkan")

    def test_cuda12_auto_driver_threshold(self):
        with mock.patch.object(wqpu_accel, "_system", return_value="Windows"), \
             mock.patch.object(wqpu_accel, "_x64", return_value=True), \
             mock.patch.object(wqpu_accel, "_nvidia_present", return_value=True):
            with mock.patch.object(wqpu_accel, "nvidia_driver_version", return_value="551.61"):
                self.assertTrue(wqpu_accel._cuda12_auto_supported())
            with mock.patch.object(wqpu_accel, "nvidia_driver_version", return_value="551.60"):
                self.assertFalse(wqpu_accel._cuda12_auto_supported())
            with mock.patch.object(wqpu_accel, "nvidia_driver_version", return_value="552.12"):
                self.assertTrue(wqpu_accel._cuda12_auto_supported())

    def test_version_parser_handles_driver_suffixes(self):
        self.assertTrue(wqpu_accel._version_at_least("551.61", (551, 61)))
        self.assertTrue(wqpu_accel._version_at_least("551.61.00", (551, 61)))
        self.assertFalse(wqpu_accel._version_at_least("550.99", (551, 61)))
        self.assertFalse(wqpu_accel._version_at_least("unknown", (551, 61)))

    def test_linux_vulkan_requires_available_gpu_path(self):
        with mock.patch.object(wqpu_accel, "_system", return_value="Linux"), \
             mock.patch.object(wqpu_accel, "_x64", return_value=True), \
             mock.patch.object(wqpu_accel, "_arm64", return_value=False), \
             mock.patch.object(wqpu_accel, "_vulkan_gpu_present", return_value=True):
            self.assertEqual(wqpu_accel.auto_mode(), "vulkan")
        with mock.patch.object(wqpu_accel, "_system", return_value="Linux"), \
             mock.patch.object(wqpu_accel, "_x64", return_value=True), \
             mock.patch.object(wqpu_accel, "_arm64", return_value=False), \
             mock.patch.object(wqpu_accel, "_vulkan_gpu_present", return_value=False):
            self.assertEqual(wqpu_accel.auto_mode(), "cpu")

    def test_explicit_cpu_keeps_rpc_device_override(self):
        with mock.patch.dict(os.environ, {"WQPU_ACCEL": "cpu"}, clear=False):
            self.assertEqual(wqpu_accel.rpc_device_args(), ["--device", "CPU"])

    def test_auto_does_not_limit_rpc_server_to_one_device(self):
        with mock.patch.dict(os.environ, {"WQPU_ACCEL": "auto"}, clear=False), \
             mock.patch.object(wqpu_accel, "auto_mode", return_value="vulkan"):
            self.assertEqual(wqpu_accel.rpc_device_args(), [])

    def test_explicit_rpc_device_wins(self):
        with mock.patch.dict(os.environ, {
            "WQPU_ACCEL": "auto",
            "WQPU_RPC_DEVICE": "CUDA1",
        }, clear=False):
            self.assertEqual(wqpu_accel.rpc_device_args(), ["--device", "CUDA1"])

    def test_asset_mapping(self):
        with mock.patch.object(wqpu_accel, "runtime_variant", return_value="vulkan"), \
             mock.patch.object(wqpu_accel, "_system", return_value="Linux"), \
             mock.patch.object(wqpu_accel, "_x64", return_value=True):
            self.assertEqual(
                wqpu_accel.main_asset_suffix("-bin-ubuntu-x64.tar.gz"),
                "-bin-ubuntu-vulkan-x64.tar.gz",
            )
        with mock.patch.object(wqpu_accel, "runtime_variant", return_value="cuda12"), \
             mock.patch.object(wqpu_accel, "_system", return_value="Windows"), \
             mock.patch.object(wqpu_accel, "_x64", return_value=True):
            self.assertEqual(
                wqpu_accel.main_asset_suffix("-bin-win-cpu-x64.zip"),
                "-bin-win-cuda-12.4-x64.zip",
            )

    def test_cuda_companion_bundle(self):
        with mock.patch.object(wqpu_accel, "runtime_variant", return_value="cuda12"):
            self.assertEqual(
                wqpu_accel.companion_asset_names("b10456"),
                ["cudart-llama-bin-win-cuda-12.4-x64.zip"],
            )

    def test_nvidia_vram_sums_visible_devices(self):
        with mock.patch.object(wqpu_accel, "_nvidia_query", return_value="15360 MiB\n16384 MiB"):
            self.assertEqual(wqpu_accel.nvidia_vram_mb(), 31744)

    def test_nvidia_driver_uses_lowest_visible_version(self):
        with mock.patch.object(wqpu_accel, "_nvidia_query", return_value="552.12\n551.61"):
            self.assertEqual(wqpu_accel.nvidia_driver_version(), "551.61")

    def test_invalid_mode_fails_closed(self):
        with mock.patch.dict(os.environ, {"WQPU_ACCEL": "magic"}, clear=False):
            with self.assertRaises(RuntimeError):
                wqpu_accel.requested_mode()


if __name__ == "__main__":
    unittest.main()
