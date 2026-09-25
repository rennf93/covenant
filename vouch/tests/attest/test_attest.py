"""Conformance and behavior tests for vouch.attest (stdlib unittest only).

Cross-language conformance: the receipt and merkle fixtures are generated
from the TypeScript SDK (tests/attest/fixtures/receipts.json) and shared
with the Rust contract crate (contracts/merkle-core/tests/fixtures/merkle.json).
If these pass, the Python mirror is byte-identical to the SDK encoder.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

# The test module lives in <project>/tests/attest; the vouch package lives in
# <project>/vouch. parents[2] is the project root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vouch.attest import EpochLedger, LedgerError, merkle, receipts  # noqa: E402
from vouch.attest._keccak import keccak256_hex  # noqa: E402
from vouch.attest.commit import AttestationConfig, Attestor  # noqa: E402
from vouch.attest.receipts import Receipt, ReceiptError, make_receipt, parse_fixed8  # noqa: E402

# Repo root (covenant/), holding the shared Rust merkle fixtures.
REPO_ROOT = Path(__file__).resolve().parents[3]
RECEIPTS_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "receipts.json"
MERKLE_FIXTURE = REPO_ROOT / "contracts" / "merkle-core" / "tests" / "fixtures" / "merkle.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fill_kwargs(i: int) -> dict:
    """A valid, distinct fill; bump the index for each successive fill."""
    return {
        "venue_order_id": f"order-{i}",
        "side": receipts.SIDE_BUY if i % 2 == 0 else receipts.SIDE_SELL,
        "size_base": 0.5 + i,
        "price": 147.32,
        "fee": 0.11,
        "filled_at": 1_758_800_000 + i,
    }


class KeccakFixtureTest(unittest.TestCase):
    def test_keccak_fixture_vectors(self):
        vectors = _load(RECEIPTS_FIXTURE)["keccakVectors"]
        self.assertTrue(vectors)
        for v in vectors:
            self.assertEqual(
                keccak256_hex(v["input"].encode("utf-8")),
                v["hash"],
                f"keccak vector failed for input {v['input']!r}",
            )


class ReceiptFixtureTest(unittest.TestCase):
    def setUp(self):
        self.cases = _load(RECEIPTS_FIXTURE)["cases"]
        self.assertTrue(self.cases)

    def test_receipt_fixture_vectors(self):
        for c in self.cases:
            r = make_receipt(
                strategy_id=1,
                epoch_index=c["epochIndex"],
                venue=c["venue"],
                venue_order_id=c["venueOrderId"],
                instrument=c["instrument"],
                side=c["side"],
                size_base=c["sizeBase"],
                price=c["price"],
                fee=c["fee"],
                filled_at=c["filledAt"],
            )
            self.assertEqual(
                receipts.encode_receipt(r),
                c["encoded"],
                f"encoding mismatch for {c['venueOrderId']}",
            )
            self.assertEqual(
                receipts.receipt_hash(r), c["hash"], f"hash mismatch for {c['venueOrderId']}"
            )
            self.assertEqual(r.venue_order_id_hash, c["venueOrderIdHash"])
            self.assertEqual(r.instrument_hash, c["instrumentHash"])


class ReceiptValidationTest(unittest.TestCase):
    def _receipt(self, **overrides) -> Receipt:
        base = dict(
            strategy_id=1,
            epoch_index=0,
            venue=receipts.VENUE_OTHER,
            venue_order_id_hash=receipts.hash_of_string("o"),
            instrument_hash=receipts.hash_of_string("SOL-USD"),
            side=receipts.SIDE_BUY,
            size_signed_base=10_000_000,
            price_quote=147 * 100_000_000,
            fee_quote=11_000_000,
            filled_at=1_758_800_000,
        )
        base.update(overrides)
        return Receipt(**base)

    def test_receipt_rejects_invalid(self):
        # sign/price invariants surface as validate_receipt problems
        self.assertTrue(
            receipts.validate_receipt(self._receipt(side=receipts.SIDE_BUY, size_signed_base=-1))
        )
        self.assertTrue(
            receipts.validate_receipt(self._receipt(side=receipts.SIDE_SELL, size_signed_base=1))
        )
        self.assertTrue(receipts.validate_receipt(self._receipt(price_quote=0)))
        self.assertFalse(receipts.validate_receipt(self._receipt()))

        # unparseable / over-precision decimals raise from parse_fixed8
        with self.assertRaises(ReceiptError):
            parse_fixed8("1.123456789")  # 9 fractional digits
        with self.assertRaises(ReceiptError):
            parse_fixed8("not a number")
        with self.assertRaises(ReceiptError):
            parse_fixed8("")

    def test_make_receipt_rejects_bad_side(self):
        with self.assertRaises(ReceiptError):
            make_receipt(
                strategy_id=1,
                epoch_index=0,
                venue=0,
                venue_order_id="o",
                instrument="SOL-USD",
                side=7,
                size_base="1",
                price="1",
                fee="0",
                filled_at=0,
            )


class MerkleFixtureTest(unittest.TestCase):
    def test_merkle_fixture_vectors(self):
        fixtures = _load(MERKLE_FIXTURE)["fixtures"]
        self.assertTrue(fixtures)
        for fx in fixtures:
            leaves = fx["leaves"]
            tree = merkle.build_merkle_tree(leaves)
            self.assertEqual(tree.root, fx["root"], f"root mismatch at size {fx['size']}")
            for case in fx["cases"]:
                self.assertEqual(
                    merkle.verify_proof(case["leaf"], case["proof"], fx["root"]),
                    case["valid"],
                    f"proof case mismatch at size {fx['size']}",
                )
            # every freshly recomputed proof must verify against the root
            for i in range(len(leaves)):
                self.assertTrue(
                    merkle.verify_proof(leaves[i], tree.proof(i), tree.root),
                    f"recomputed proof {i} failed at size {fx['size']}",
                )


class LedgerInvariantTest(unittest.TestCase):
    def test_ledger_invariants(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = EpochLedger(Path(tmp), 1, 0, receipts.VENUE_OTHER, "SOL-USD")
            for i in range(3):
                ledger.add_fill(**_fill_kwargs(i))
            self.assertEqual(len(ledger.receipt_hashes()), 3)

            # duplicate venue_order_id
            dup = _fill_kwargs(0)
            with self.assertRaises(LedgerError):
                ledger.add_fill(**dup)

            # filled_at going backwards
            back = _fill_kwargs(10)
            back["filled_at"] = 1
            with self.assertRaises(LedgerError):
                ledger.add_fill(**back)

            # invalid side/size pairing (Buy with negative size)
            bad = _fill_kwargs(11)
            bad["side"] = receipts.SIDE_BUY
            bad["size_base"] = -1.0
            with self.assertRaises(LedgerError):
                ledger.add_fill(**bad)

    def test_ledger_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "receipts-epoch-0000.jsonl"
            ledger = EpochLedger(Path(tmp), 1, 0, receipts.VENUE_OTHER, "SOL-USD")
            for i in range(3):
                ledger.add_fill(**_fill_kwargs(i))
            hashes = ledger.receipt_hashes()

            # clean reopen: state rebuilt from disk
            reopened = EpochLedger(Path(tmp), 1, 0, receipts.VENUE_OTHER, "SOL-USD")
            self.assertEqual(len(reopened), 3)
            self.assertEqual(reopened.receipt_hashes(), hashes)

            # torn final line: warned about, dropped, file repaired
            with open(path, "ab") as f:
                f.write(b'{"receipt": {"trunc')
            healed = EpochLedger(Path(tmp), 1, 0, receipts.VENUE_OTHER, "SOL-USD")
            self.assertEqual(len(healed), 3)
            self.assertEqual(healed.receipt_hashes(), hashes)
            data = path.read_bytes()
            self.assertTrue(data.endswith(b"\n"))
            for line in data.splitlines():
                json.loads(line)  # every remaining line is valid JSON


class CommitLedgerOnlyTest(unittest.TestCase):
    def _config(self, evidence_dir: Path) -> AttestationConfig:
        return AttestationConfig(
            enabled=True,
            rpc_url="",
            private_key="",
            contract_address="",
            chain="arbitrum-sepolia",
            evidence_dir=evidence_dir,
        )

    def _three_fill_ledger(self, attestor: Attestor) -> EpochLedger:
        ledger = attestor.start_epoch(0)
        for i in range(3):
            ledger.add_fill(**_fill_kwargs(i))
        return ledger

    def test_commit_ledger_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = self._config(root / "evidence")
            attestor = Attestor(
                cfg, run_dir=root, strategy_id=1, venue=receipts.VENUE_OTHER, instrument="SOL-USD"
            )
            ledger = self._three_fill_ledger(attestor)
            res = attestor.commit_epoch(
                ledger, equity_usd=100.0, net_flow_usd=0, strategy_name="test"
            )
            self.assertFalse(res["committed"])
            self.assertNotEqual(res["tradesRoot"], merkle.ZERO32)

            bundle_path = cfg.evidence_dir / "epoch-0.json"
            self.assertTrue(bundle_path.exists())
            bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
            self.assertEqual(bundle["tradesRoot"], res["tradesRoot"])

            expected_root = merkle.build_merkle_tree(ledger.receipt_hashes()).root
            self.assertEqual(bundle["tradesRoot"], expected_root)
            self.assertEqual(len(bundle["receipts"]), 3)
            for entry in bundle["receipts"]:
                self.assertTrue(
                    merkle.verify_proof(entry["hash"], entry["proof"], bundle["tradesRoot"])
                )
                self.assertEqual(
                    entry["hash"], receipts.receipt_hash(Receipt.from_dict(entry["receipt"]))
                )

    def test_commit_empty_epoch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = self._config(root / "evidence")
            attestor = Attestor(
                cfg, run_dir=root, strategy_id=1, venue=receipts.VENUE_OTHER, instrument="SOL-USD"
            )
            ledger = attestor.start_epoch(0)
            res = attestor.commit_epoch(
                ledger, equity_usd=10.0, net_flow_usd=0, strategy_name="test"
            )
            self.assertFalse(res["committed"])
            self.assertEqual(res["tradesRoot"], merkle.ZERO32)
            bundle = json.loads((cfg.evidence_dir / "epoch-0.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle["receipts"], [])


class UsdgConversionTest(unittest.TestCase):
    def test_usdg_conversion(self):
        self.assertEqual(receipts.usdg_from_usd(1000.0), 1_000_000_000)
        self.assertEqual(receipts.usdg_from_usd("0.5"), 500_000)
        self.assertEqual(receipts.usdg_from_usd(-12.34), -12_340_000)


if __name__ == "__main__":
    unittest.main()
