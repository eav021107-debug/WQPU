import os
import unittest
from unittest import mock

import wqpu_accel


class AcceleratorTests(unittest.TestCase):
    def test_auto_prefers_metal_on_macos(self):
        with mock.patch.object(wqpu_accel, "_system", return_value="Darwin"):
            self.assertEqual(wqpu_accel.auto_mode(), "metal")

    def test_auto_prefers_cuda12_for_windows_nvidia(self):
        with mock.patch.object(wqpu_accel, "_system", return_value="Windows"), \
             mock.patch.object(wqpu_accel, "_x64", return_value=True), \
             mock.patch.object(wqpu_accel, "_nvidia_present", return_value=True):
            self.assertEqual(wqpu_accel.auto_mode(), "cuda12")

    def test_windows_vulkan_is_fallback_after_cuda(self):
        with mock.patch.object(wqpu_accel, "_system", return_value="Windows"), \
             mock.patch.object(wqpu_accel, "_x64", return_value=True), \
             mock.patch.object(wqpu_accel, "_nvidia_present", return_value=False), \
             mock.patch.object(wqpu_accel, "_vulkan_gpu_present", return_value=True):
            self.assertEqual(wqpu_accel.auto_mode(), "vulkan")

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
        completed = mock.Mock(returncode=0, stdout="15360\n16384\n", stderr="")
        with mock.patch.object(wqpu_accel.shutil, "which", return_value="nvidia-smi"), \
             mock.patch.object(wqpu_accel.subprocess, "run", return_value=completed):
            self.assertEqual(wqpu_accel.nvidia_vram_mb(), 31744)

    def test_invalid_mode_fails_closed(self):
        with mock.patch.dict(os.environ, {"WQPU_ACCEL": "magic"}, clear=False):
            with self.assertRaises(RuntimeError):
                wqpu_accel.requested_mode()


if __name__ == "__main__":
    unittest.main()
