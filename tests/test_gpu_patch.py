import unittest
from unittest import mock

import wqpu_gpu_patch


class GpuPatchTests(unittest.TestCase):
    def test_rpc_command_removes_historical_cpu_lock_in_auto_mode(self):
        command = [
            "/tmp/ggml-rpc-server", "--host", "127.0.0.1", "--port", "50052",
            "--threads", "4", "--device", "CPU", "--cache",
        ]
        with mock.patch.object(wqpu_gpu_patch.wqpu_accel, "rpc_device_args", return_value=[]):
            patched = wqpu_gpu_patch._rpc_command(command)
        self.assertNotIn("--device", patched)
        self.assertEqual(patched[0], command[0])
        self.assertIn("--cache", patched)

    def test_explicit_device_override_is_reinserted(self):
        command = ["ggml-rpc-server.exe", "--device", "CPU", "--cache"]
        with mock.patch.object(
            wqpu_gpu_patch.wqpu_accel, "rpc_device_args", return_value=["--device", "CUDA1"]
        ):
            patched = wqpu_gpu_patch._rpc_command(command)
        self.assertEqual(patched.count("--device"), 1)
        self.assertEqual(patched[-2:], ["--device", "CUDA1"])

    def test_non_rpc_process_is_untouched(self):
        command = ["llama-server", "--device", "CPU"]
        self.assertEqual(wqpu_gpu_patch._rpc_command(command), command)

    def test_cuda_capacity_prefers_vram(self):
        class Runtime(object):
            _wqpu_gpu_patch_installed = False
            @staticmethod
            def capacity_units():
                return 64000

            class ChainMesh(object):
                def my_info(self):
                    return {"capacity": 64000}

        class Wqpu(object):
            @staticmethod
            def total_ram_mb():
                return 64000

        with mock.patch.object(wqpu_gpu_patch.wqpu_accel, "info", return_value={
            "accelerator": "cuda12",
            "runtime_variant": "cuda12",
            "vram_mb": 30720,
        }):
            wqpu_gpu_patch.install_runtime(Runtime, Wqpu)
            self.assertEqual(Runtime.capacity_units(), 30720)
            info = Runtime.ChainMesh().my_info()
            self.assertEqual(info["capacity"], 30720)
            self.assertEqual(info["vram_mb"], 30720)
            self.assertEqual(info["accelerator"], "cuda12")


if __name__ == "__main__":
    unittest.main()
