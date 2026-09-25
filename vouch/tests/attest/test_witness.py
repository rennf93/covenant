"""Price witness tests: Hermes assembly (pure), fetch failure modes, and the
additive bundle field. The witness is an unsigned top-level annotation: these
tests pin that the signer/signature fields stay byte-identical whether or not
a witness was captured, and that any witness failure never fails a commit.
Stdlib unittest + mock only; urlopen is mocked.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import mock_open, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vouch.attest.commit import (  # noqa: E402
    UNSIGNED_SIGNATURE,
    UNSIGNED_SIGNER,
    AttestationConfig,
    Attestor,
)
from vouch.attest.receipts import VENUE_OTHER  # noqa: E402
from vouch.attest.witness import assemble_witness, fetch_witness  # noqa: E402

# A SOL/USD-style 32-byte feed id for the canned Hermes payload (an id must be
# 0x + 64 hex; operators point COVENANT_PYTH_PRICE_ID at the live feed id they
# want witnessed).
SOL_USD_ID = "0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b121"

# Canned Hermes latest-price payload, SOL/USD-shaped: the human price is
# price * 10^expo = 14732000000 * 10^-8 = 147.32.
CANNED_HERMES = {
    "binary": {"data": "<irrelevant for the parsed path>"},
    "parsed": [
        {
            "id": SOL_USD_ID,
            "price": {
                "price": "14732000000",
                "conf": "120000",
                "expo": -8,
                "publish_time": 1_758_800_000,
            },
            "ema_price": {
                "price": "14731500000",
                "conf": "110000",
                "expo": -8,
                "publish_time": 1_758_800_000,
            },
        }
    ],
}

EXPECTED_WITNESS = {
    "source": "pyth",
    "priceId": SOL_USD_ID,
    "price": "14732000000",
    "expo": -8,
    "confidence": "120000",
    "publishTime": 1_758_800_000,
}


def _parsed(
    price: object = "14732000000",
    conf: object = "120000",
    expo: object = -8,
    publish_time: object = 0,
) -> dict:
    return {
        "parsed": [
            {"price": {"price": price, "conf": conf, "expo": expo, "publish_time": publish_time}}
        ]
    }


class WitnessAssemblyTest(unittest.TestCase):
    def test_happy_path_exact_dict(self):
        self.assertEqual(assemble_witness(SOL_USD_ID, CANNED_HERMES), EXPECTED_WITNESS)

    def test_bare_parsed_list_accepted_too(self):
        self.assertEqual(assemble_witness(SOL_USD_ID, CANNED_HERMES["parsed"]), EXPECTED_WITNESS)

    def test_negative_price_round_trips_as_string(self):
        witness = assemble_witness(SOL_USD_ID, _parsed(price=-15701, conf="12"))
        assert witness is not None
        self.assertEqual(witness["price"], "-15701")
        self.assertEqual(witness["confidence"], "12")

    def test_integer_strings_accepted_for_numeric_fields(self):
        payload = _parsed(expo="-8", publish_time="1758800000")
        witness = assemble_witness(SOL_USD_ID, payload)
        assert witness is not None
        self.assertEqual(witness["expo"], -8)
        self.assertEqual(witness["publishTime"], 1758800000)

    def test_malformed_payloads_return_none(self):
        malformed: list[object] = [
            None,
            {},
            {"parsed": []},
            {"parsed": "nope"},
            {"parsed": [None]},
            {"parsed": ["nope"]},
            {"parsed": [{}]},
            {"parsed": [{"price": "not-a-dict"}]},
            {"parsed": [{"price": {}}]},
            _parsed(price=147.32),  # float price: the bundle carries integers, exactly
            _parsed(price=True),  # bool is an int subclass; rejected anyway
            _parsed(price="abc"),
            _parsed(conf=None),
            _parsed(expo=-8.0),  # float expo
            _parsed(publish_time="yesterday"),
        ]
        for payload in malformed:
            with self.subTest(payload=payload):
                self.assertIsNone(assemble_witness(SOL_USD_ID, payload))


class WitnessFetchTest(unittest.TestCase):
    def test_fetch_happy_path(self):
        data = json.dumps(CANNED_HERMES).encode()
        with patch("urllib.request.urlopen", mock_open(read_data=data)):
            self.assertEqual(fetch_witness(SOL_USD_ID, "https://hermes.example"), EXPECTED_WITNESS)

    def test_no_id_configured_is_none_without_network(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("no network")):
            self.assertIsNone(fetch_witness("", "https://hermes.example"))

    def test_malformed_id_is_none_without_network(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("no network")):
            self.assertIsNone(fetch_witness("sol-usd", "https://hermes.example"))

    def test_timeout_returns_none(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            self.assertIsNone(fetch_witness(SOL_USD_ID, "https://hermes.example"))

    def test_network_error_returns_none(self):
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("name resolution failed"),
        ):
            self.assertIsNone(fetch_witness(SOL_USD_ID, "https://hermes.example"))

    def test_unparseable_body_returns_none(self):
        with patch("urllib.request.urlopen", mock_open(read_data=b"<html>gateway</html>")):
            self.assertIsNone(fetch_witness(SOL_USD_ID, "https://hermes.example"))


class WitnessBundleFieldTest(unittest.TestCase):
    def _config(self, evidence_dir: Path, *, price_id: str = "") -> AttestationConfig:
        return AttestationConfig(
            enabled=True,
            rpc_url="",
            private_key="",
            contract_address="",
            chain="arbitrum-sepolia",
            evidence_dir=evidence_dir,
            pyth_price_id=price_id,
        )

    def _attestor(self, root: Path, cfg: AttestationConfig) -> Attestor:
        return Attestor(
            cfg,
            run_dir=root / "out" / "live-x",
            strategy_id=1,
            venue=VENUE_OTHER,
            instrument="SOL-USD",
        )

    def _commit(self, root: Path, cfg: AttestationConfig) -> dict:
        attestor = self._attestor(root, cfg)
        ledger = attestor.start_epoch(0)
        return attestor.commit_epoch(ledger, equity_usd=100.0)

    def _stable_bundle(self, root: Path) -> dict:
        return json.loads(
            (root / "evidence" / "live-x" / "epoch-0.json").read_text(encoding="utf-8")
        )

    def test_bundle_includes_witness_when_fetched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = self._config(root / "evidence", price_id=SOL_USD_ID)
            data = json.dumps(CANNED_HERMES).encode()
            with patch("urllib.request.urlopen", mock_open(read_data=data)):
                self._commit(root, cfg)
            bundle = self._stable_bundle(root)
            self.assertEqual(bundle["witness"], EXPECTED_WITNESS)
            # The annotation stays outside the signature envelope: the
            # deterministic unsigned fallback is unchanged.
            self.assertEqual(bundle["signer"], UNSIGNED_SIGNER)
            self.assertEqual(bundle["signature"], UNSIGNED_SIGNATURE)

    def test_bundle_has_no_witness_key_when_unconfigured(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                "urllib.request.urlopen", side_effect=AssertionError("witness must not run")
            ):
                self._commit(root, self._config(root / "evidence"))
            self.assertNotIn("witness", self._stable_bundle(root))

    def test_witness_failure_never_fails_the_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = self._config(root / "evidence", price_id=SOL_USD_ID)
            with patch("urllib.request.urlopen", side_effect=OSError("hermes down")):
                res = self._commit(root, cfg)
            self.assertFalse(res["committed"])  # ledger-only mode: written, not committed
            self.assertNotIn("witness", self._stable_bundle(root))


if __name__ == "__main__":
    unittest.main()
