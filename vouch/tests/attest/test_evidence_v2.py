"""Evidence bundle v2 tests: typed data pin, schema, per-run paths, IPFS.

The EIP-712 typed data fixture below is THE pin: the web team implements the
exact same verification against this shape, so any deviation here is a
protocol break. Bundle-level equity/netFlow are USDG base-unit integer
strings (6 decimals); receipts are conformance-pinned to the SDK fixtures
and stay untouched. Stdlib unittest + mock only; the bridge is mocked.
"""

from __future__ import annotations

import contextlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vouch.attest import (
    merkle,  # noqa: E402
    receipts,  # noqa: E402
)
from vouch.attest.commit import (  # noqa: E402
    UNSIGNED_SIGNATURE,
    UNSIGNED_SIGNER,
    AttestationConfig,
    Attestor,
    evidence_typed_data,
)
from vouch.attest.onchain import OnchainState  # noqa: E402

EXPECTED_TYPED_DATA = {
    "domain": {
        "name": "Covenant Evidence",
        "version": "1",
        "chainId": 421614,
        "verifyingContract": "0x2222222222222222222222222222222222222222",
    },
    "primaryType": "Evidence",
    "types": {
        "Evidence": [
            {"name": "protocol", "type": "string"},
            {"name": "strategyId", "type": "uint256"},
            {"name": "epochIndex", "type": "uint256"},
            {"name": "committedAt", "type": "uint64"},
            {"name": "equityUsdg", "type": "int256"},
            {"name": "netFlowUsdg", "type": "int256"},
            {"name": "tradesRoot", "type": "bytes32"},
        ]
    },
    "message": {
        "protocol": "covenant-v1",
        "strategyId": "1",
        "epochIndex": "3",
        "committedAt": "1758800000",
        "equityUsdg": "100000000",
        "netFlowUsdg": "-5000000",
        "tradesRoot": "0x" + "aa" * 32,
    },
}


def _fill_kwargs(i: int) -> dict:
    return {
        "venue_order_id": f"order-{i}",
        "side": receipts.SIDE_BUY,
        "size_base": 0.5 + i,
        "price": 147.32,
        "fee": 0.11,
        "filled_at": 1_758_800_000 + i,
    }


class TypedDataTest(unittest.TestCase):
    def test_typed_data_exact_fixture_shape(self):
        td = evidence_typed_data(
            chain_id=421614,
            contract="0x" + "22" * 20,
            strategy_id=1,
            epoch_index=3,
            committed_at=1_758_800_000,
            equity_base=100_000_000,
            net_flow_base=-5_000_000,
            trades_root="0x" + "aa" * 32,
        )
        # The exact JSON the web verifier must implement against.
        self.assertEqual(td, EXPECTED_TYPED_DATA)
        # and it must round-trip through JSON unchanged
        self.assertEqual(json.loads(json.dumps(td)), EXPECTED_TYPED_DATA)

    def test_negative_equity_uses_minus_prefix_not_parentheses(self):
        td = evidence_typed_data(
            chain_id=1,
            contract="0x" + "22" * 20,
            strategy_id=1,
            epoch_index=0,
            committed_at=0,
            equity_base=-12_340_000,
            net_flow_base=0,
            trades_root=merkle.ZERO32,
        )
        self.assertEqual(td["message"]["equityUsdg"], "-12340000")
        self.assertEqual(td["message"]["netFlowUsdg"], "0")


class EvidenceV2Test(unittest.TestCase):
    def _ledger_only_config(self, evidence_dir: Path) -> AttestationConfig:
        return AttestationConfig(
            enabled=True,
            rpc_url="",
            private_key="",
            contract_address="",
            chain="arbitrum-sepolia",
            evidence_dir=evidence_dir,
        )

    def _attestor(self, root: Path, cfg: AttestationConfig) -> Attestor:
        return Attestor(
            cfg,
            run_dir=root / "out" / "live-x",
            strategy_id=1,
            venue=receipts.VENUE_OTHER,
            instrument="SOL-USD",
        )

    def test_bundle_v2_schema_and_base_units(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
            root = Path(tmp)
            cfg = self._ledger_only_config(root / "evidence")
            attestor = self._attestor(root, cfg)
            ledger = attestor.start_epoch(0)
            for i in range(2):
                ledger.add_fill(**_fill_kwargs(i))
            res = attestor.commit_epoch(ledger, equity_usd=100.0, net_flow_usd=-12.34)

            # per-run paths: run copy under out/<run_id>/evidence, stable copy
            # under the evidence root, no cross-run filename collisions
            run_copy = root / "out" / "live-x" / "evidence" / "epoch-0.json"
            stable_copy = root / "evidence" / "live-x" / "epoch-0.json"
            self.assertTrue(run_copy.exists())
            self.assertTrue(stable_copy.exists())
            self.assertEqual(res["evidenceUri"], "evidence/live-x/epoch-0.json")

            bundle = json.loads(stable_copy.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(run_copy.read_text(encoding="utf-8")), bundle)

            # v2 top-level schema
            for key in ("signer", "chainId", "contract", "signature", "signedAt"):
                self.assertIn(key, bundle)
            self.assertEqual(bundle["signer"], UNSIGNED_SIGNER)
            self.assertEqual(bundle["signature"], UNSIGNED_SIGNATURE)
            self.assertEqual(len(bundle["signature"]), 2 + 130)  # 65 bytes
            self.assertEqual(bundle["chainId"], 421614)  # from the chain name map

            # bundle-level amounts are integer base-unit STRINGS (6 decimals)
            self.assertEqual(bundle["equityUsdg"], "100000000")
            self.assertEqual(bundle["netFlowUsdg"], "-12340000")
            self.assertIsInstance(bundle["equityUsdg"], str)

            # receipts stay conformance-pinned: ints, 8-decimal fixed point
            self.assertEqual(len(bundle["receipts"]), 2)
            entry = bundle["receipts"][0]
            self.assertIsInstance(entry["receipt"]["priceQuote"], int)
            self.assertIsInstance(entry["receipt"]["feeQuote"], int)
            self.assertEqual(entry["receipt"]["priceQuote"], int(147.32 * 100_000_000))
            self.assertTrue(
                merkle.verify_proof(entry["hash"], entry["proof"], bundle["tradesRoot"])
            )

    def test_bundle_unpublished_when_no_ipfs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attestor = self._attestor(root, self._ledger_only_config(root / "evidence"))
            ledger = attestor.start_epoch(0)
            res = attestor.commit_epoch(ledger, equity_usd=10.0)
            self.assertFalse(res["published"])
            self.assertFalse(res["evidenceUri"].startswith("ipfs://"))

    def test_ipfs_publish_used_when_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = self._ledger_only_config(root / "evidence")
            cfg.ipfs_api = "http://127.0.0.1:5001"
            attestor = self._attestor(root, cfg)
            ledger = attestor.start_epoch(0)
            ledger.add_fill(**_fill_kwargs(0))
            payload = json.dumps({"Hash": {"/": "bafy-test-cid"}}).encode()
            with patch("urllib.request.urlopen", mock_open(read_data=payload)):
                res = attestor.commit_epoch(ledger, equity_usd=10.0)
            self.assertTrue(res["published"])
            self.assertEqual(res["evidenceUri"], "ipfs://bafy-test-cid")

    def test_ipfs_failure_never_fails_the_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = self._ledger_only_config(root / "evidence")
            cfg.ipfs_api = "http://127.0.0.1:5001"
            attestor = self._attestor(root, cfg)
            ledger = attestor.start_epoch(0)
            ledger.add_fill(**_fill_kwargs(0))
            with patch("urllib.request.urlopen", side_effect=OSError("kubo down")):
                res = attestor.commit_epoch(ledger, equity_usd=10.0)
            self.assertFalse(res["published"])
            self.assertIn("live-x", res["evidenceUri"])

    def test_plain_cid_shape_accepted_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = self._ledger_only_config(root / "evidence")
            cfg.ipfs_api = "http://127.0.0.1:5001"
            attestor = self._attestor(root, cfg)
            ledger = attestor.start_epoch(0)
            payload = json.dumps(
                {"Name": "epoch-0.json", "Hash": "QmPlain", "Size": "382"}
            ).encode()
            with patch("urllib.request.urlopen", mock_open(read_data=payload)):
                res = attestor.commit_epoch(ledger, equity_usd=10.0)
            self.assertEqual(res["evidenceUri"], "ipfs://QmPlain")

    def test_tracker_state_reloaded_from_disk(self):
        # A fresh Attestor on an existing run dir continues the tracker, so a
        # resumed process cannot re-commit an epoch that already landed.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "out" / "live-x"
            state = OnchainState()
            state.record_success(0, 1_758_800_000)
            state.record_success(1, 1_758_800_100)
            state.save(run_dir)
            attestor = self._attestor(root, self._ledger_only_config(root / "evidence"))
            self.assertEqual(attestor.tracker.last_committed_epoch, 1)


if __name__ == "__main__":
    unittest.main()
