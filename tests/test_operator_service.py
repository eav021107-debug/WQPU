import os
import unittest

import wqpu_operator_service as service


class OperatorServiceTests(unittest.TestCase):
    def test_start_args_preserve_operator_network_settings(self):
        state = {
            "public_host": "testnet.example",
            "bind_host": "0.0.0.0",
            "ports": {
                "internal_rpc": 28546,
                "rpc": 18545,
                "relayer": 18787,
                "relay": 17443,
            },
            "tls_cert": "/etc/wqpu/fullchain.pem",
            "tls_key": "/etc/wqpu/privkey.pem",
        }
        network = {"public": {"payments_enabled": True, "faucet_url": None}}
        args = service.build_start_args(state, network)
        self.assertEqual(args[0], "start")
        self.assertEqual(args[args.index("--public-host") + 1], "testnet.example")
        self.assertEqual(args[args.index("--rpc-port") + 1], "18545")
        self.assertEqual(args[args.index("--relayer-port") + 1], "18787")
        self.assertEqual(args[args.index("--relay-port") + 1], "17443")
        self.assertIn("--payments", args)
        self.assertIn("--no-faucet", args)
        self.assertEqual(args[args.index("--tls-cert") + 1], "/etc/wqpu/fullchain.pem")
        self.assertEqual(args[args.index("--tls-key") + 1], "/etc/wqpu/privkey.pem")

    def test_faucet_enabled_and_payments_disabled_need_no_extra_flags(self):
        state = {"public_host": "10.0.0.5", "ports": {"rpc": 8545}}
        network = {"public": {"payments_enabled": False, "faucet_url": "http://10.0.0.5:8787/faucet"}}
        args = service.build_start_args(state, network)
        self.assertNotIn("--payments", args)
        self.assertNotIn("--no-faucet", args)

    def test_systemd_unit_is_oneshot_and_uses_exact_start_args(self):
        args = ["start", "--public-host", "testnet.example", "--rpc-port", "18545"]
        unit = service.render_systemd_unit(
            "/usr/bin/python3",
            "/home/alice/wqpu/scripts/testnet_stack.py",
            args,
            home="/home/alice",
            path_env="/usr/local/bin:/usr/bin:/bin",
        )
        self.assertIn("Type=oneshot", unit)
        self.assertIn("RemainAfterExit=yes", unit)
        self.assertIn('Environment="HOME=/home/alice"', unit)
        self.assertIn("/home/alice/.foundry/bin", unit)
        self.assertIn('"--public-host" "testnet.example"', unit)
        self.assertIn('"--rpc-port" "18545"', unit)
        self.assertIn('ExecStop="/usr/bin/python3" "/home/alice/wqpu/scripts/testnet_stack.py" "stop"', unit)
        self.assertIn("WantedBy=default.target", unit)

    def test_systemd_quote_escapes_quotes_and_backslashes(self):
        quoted = service._quote_systemd('a\\b"c')
        self.assertEqual(quoted, '"a\\\\b\\"c"')


if __name__ == "__main__":
    unittest.main()
