"""run_register.py CLI tests: refusal gates, arg parsing defaults, the env
line output. The bridge is mocked; no chain, no network. Stdlib only.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import run_register  # noqa: E402
from vouch.config import load_settings  # noqa: E402

CONNECTED_ENV = {
    "COVENANT_ATTEST": "1",
    "COVENANT_RPC_URL": "https://rpc.example",
    "COVENANT_PRIVATE_KEY": "0x" + "11" * 32,
    "COVENANT_CONTRACT_ADDRESS": "0x" + "22" * 20,
    "COVENANT_CHAIN": "arbitrum-sepolia",
    "COVENANT_STRATEGY_ID": "0",
    "COVENANT_STRATEGY_NAME": "vouch-sol",
    "COVENANT_STRATEGY_METADATA_URI": "",
}


class RegisterCliTest(unittest.TestCase):
    def setUp(self):
        load_settings.cache_clear()
        self._env = patch.dict(os.environ, CONNECTED_ENV, clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        load_settings.cache_clear()

    def test_refuses_when_attest_off(self):
        os.environ.pop("COVENANT_ATTEST")
        load_settings.cache_clear()
        code, err = self._run()
        self.assertEqual(code, 2)
        self.assertIn("COVENANT_ATTEST", err)

    def test_refuses_when_connection_trio_incomplete(self):
        os.environ.pop("COVENANT_PRIVATE_KEY")
        load_settings.cache_clear()
        code, err = self._run()
        self.assertEqual(code, 2)
        self.assertIn("connection trio", err)

    def test_flags_and_config_defaults(self):
        parser = run_register.build_parser()
        args = parser.parse_args(["--name", "x", "--metadata-uri", "ipfs://y"])
        self.assertEqual((args.name, args.metadata_uri), ("x", "ipfs://y"))
        args = parser.parse_args([])
        self.assertIsNone(args.name)
        self.assertIsNone(args.metadata_uri)

    def test_success_prints_strategy_id_and_env_line(self):
        with patch(
            "run_register.call_bridge",
            return_value={"strategyId": "7", "hash": "0xreg", "chainId": 421614},
        ) as bridge:
            code, out = self._run()
        self.assertEqual(code, 0)
        self.assertIn("id 7", out)
        self.assertIn("export COVENANT_STRATEGY_ID=7", out)
        payload = bridge.call_args.args[0]
        self.assertEqual(payload["action"], "register")
        self.assertEqual(payload["name"], "vouch-sol")  # config default
        self.assertEqual(payload["metadataUri"], "")  # config default

    def test_flag_overrides_config_defaults(self):
        with patch(
            "run_register.call_bridge",
            return_value={"strategyId": "8", "hash": "0xreg"},
        ) as bridge:
            code, _ = self._run(["--name", "custom", "--metadata-uri", "ipfs://bafy"])
        self.assertEqual(code, 0)
        payload = bridge.call_args.args[0]
        self.assertEqual(payload["name"], "custom")
        self.assertEqual(payload["metadataUri"], "ipfs://bafy")

    def test_missing_strategy_id_in_receipt_is_an_error(self):
        with patch("run_register.call_bridge", return_value={"hash": "0xreg"}):
            code, err = self._run()
        self.assertEqual(code, 1)
        self.assertIn("StrategyRegistered", err)

    def _run(self, argv: list[str] | None = None) -> tuple[int, str]:
        buf, errbuf = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(errbuf):
            code = run_register.main(argv if argv is not None else [])
        return code, buf.getvalue() + errbuf.getvalue()


if __name__ == "__main__":
    unittest.main()
