"""Commit retry/resync state machine tests, bridge fully mocked.

The headline property: the local epoch index never advances past the last
epoch that actually committed onchain. A failed commit leaves the epoch
open (recorded in onchain.json), the next boundary retries, and a ledger
whose history diverges from the chain is frozen with a loud error instead
of writing garbage. Stdlib unittest + unittest.mock only.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vouch.attest import receipts  # noqa: E402
from vouch.attest.commit import AttestationConfig, Attestor  # noqa: E402
from vouch.attest.onchain import (  # noqa: E402
    CHECKPOINT_CHALLENGED,
    CHECKPOINT_PENDING,
    OnchainState,
    finalize_due,
    resync_plan,
)
from vouch.exceptions import CommitError  # noqa: E402


def _fill_kwargs(i: int) -> dict:
    return {
        "venue_order_id": f"order-{i}",
        "side": receipts.SIDE_BUY if i % 2 == 0 else receipts.SIDE_SELL,
        "size_base": 0.5 + i,
        "price": 147.32,
        "fee": 0.11,
        "filled_at": 1_758_800_000 + i,
    }


def _connected_config(evidence_dir: Path, **overrides) -> AttestationConfig:
    return AttestationConfig(
        enabled=True,
        rpc_url="https://rpc.example",
        private_key="0x" + "11" * 32,
        contract_address="0x" + "22" * 20,
        chain="arbitrum-sepolia",
        evidence_dir=evidence_dir,
        **overrides,
    )


class _FakeBridge:
    """Scripted bridge: maps action -> response or exception, records calls.

    Patched in place of vouch.attest.commit.call_bridge so the REAL
    Attestor._call_bridge wrapper (chainId caching) is exercised."""

    def __init__(self, script: dict, fail_first_commit: int = 0):
        self.script = script
        self.fail_first_commit = fail_first_commit
        self.calls: list[dict] = []

    def __call__(self, payload: dict) -> dict:
        self.calls.append(payload)
        action = payload["action"]
        if action == "commit" and self.fail_first_commit > 0:
            self.fail_first_commit -= 1
            raise CommitError("bridge error: rpc down")
        response = self.script.get(action)
        if isinstance(response, Exception):
            raise response
        return dict(response or {}, chainId=421614)


class OnchainStateTest(unittest.TestCase):
    def test_missing_file_is_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = OnchainState.load(Path(tmp))
            self.assertEqual(state.last_committed_epoch, -1)
            self.assertEqual(state.next_epoch, 0)
            self.assertEqual(state.retries, 0)

    def test_roundtrip_preserves_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = OnchainState()
            state.record_success(0, 1_758_800_000)
            state.record_success(1, 1_758_800_100)
            state.record_failure(2, "rpc down")
            state.record_finalized(0)
            state.save(Path(tmp))
            loaded = OnchainState.load(Path(tmp))
            self.assertEqual(loaded.last_committed_epoch, 1)
            self.assertEqual(loaded.committed_at, {0: 1_758_800_000, 1: 1_758_800_100})
            self.assertEqual(loaded.retries, 1)
            self.assertIn("rpc down", loaded.last_error)
            self.assertEqual(loaded.finalized, [0])
            self.assertEqual(loaded.next_epoch, 2)

    def test_corrupt_file_resets_to_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "onchain.json").write_text("{not json", encoding="utf-8")
            state = OnchainState.load(Path(tmp))
            self.assertEqual(state.last_committed_epoch, -1)

    def test_record_success_never_goes_backwards(self):
        state = OnchainState()
        state.record_success(5, 100)
        state.record_success(3, 90)
        self.assertEqual(state.last_committed_epoch, 5)


class ResyncPlanTest(unittest.TestCase):
    def test_fresh_local_chain_empty_ok(self):
        d = resync_plan(
            chain_epoch_count=0,
            next_local_epoch=0,
            has_local_history=False,
            pending_receipt_epoch=None,
        )
        self.assertEqual(d.action, "ok")

    def test_fresh_local_fast_forwards_to_chain(self):
        d = resync_plan(
            chain_epoch_count=7,
            next_local_epoch=0,
            has_local_history=False,
            pending_receipt_epoch=None,
        )
        self.assertEqual(d.action, "fast_forward")
        self.assertEqual(d.next_epoch, 7)

    def test_history_chain_ahead_refuses(self):
        d = resync_plan(
            chain_epoch_count=7,
            next_local_epoch=3,
            has_local_history=True,
            pending_receipt_epoch=None,
        )
        self.assertEqual(d.action, "refuse")
        self.assertIn("mismatch", d.message)

    def test_chain_behind_local_refuses(self):
        d = resync_plan(
            chain_epoch_count=2,
            next_local_epoch=5,
            has_local_history=True,
            pending_receipt_epoch=None,
        )
        self.assertEqual(d.action, "refuse")
        self.assertIn("redeployed", d.message)

    def test_pending_equals_chain_is_retryable(self):
        d = resync_plan(
            chain_epoch_count=3,
            next_local_epoch=3,
            has_local_history=True,
            pending_receipt_epoch=3,
        )
        self.assertEqual(d.action, "ok")
        self.assertIn("retried", d.message)

    def test_pending_behind_chain_refuses(self):
        d = resync_plan(
            chain_epoch_count=5,
            next_local_epoch=5,
            has_local_history=True,
            pending_receipt_epoch=3,
        )
        self.assertEqual(d.action, "refuse")
        self.assertIn("never commit", d.message)

    def test_pending_ahead_of_chain_refuses(self):
        d = resync_plan(
            chain_epoch_count=1,
            next_local_epoch=3,
            has_local_history=True,
            pending_receipt_epoch=3,
        )
        self.assertEqual(d.action, "refuse")


class FinalizeDueTest(unittest.TestCase):
    NOW = 10_000
    WINDOW = 600

    def test_only_pending_past_window(self):
        cps = [
            (0, self.NOW - self.WINDOW, CHECKPOINT_PENDING),  # exactly elapsed: due
            (1, self.NOW - self.WINDOW + 1, CHECKPOINT_PENDING),  # one second short
            (2, self.NOW - 10_000, CHECKPOINT_PENDING),  # long past: due
            (3, self.NOW - 10_000, CHECKPOINT_CHALLENGED),  # challenged: never ours
        ]
        self.assertEqual(finalize_due(cps, now=self.NOW, window_seconds=self.WINDOW), [0, 2])

    def test_nothing_due(self):
        self.assertEqual(
            finalize_due(
                [(0, self.NOW, CHECKPOINT_PENDING)], now=self.NOW, window_seconds=self.WINDOW
            ),
            [],
        )

    def test_first_receipt_ever_is_epoch_zero_at_genesis(self):
        # committed at 0, window 600, now 600: due exactly at the boundary
        self.assertEqual(
            finalize_due([(0, 0, CHECKPOINT_PENDING)], now=600, window_seconds=600), [0]
        )


class CommitRetryTest(unittest.TestCase):
    def _attestor(self, root: Path) -> Attestor:
        return Attestor(
            _connected_config(root / "evidence"),
            run_dir=root / "out" / "live-x",
            strategy_id=1,
            venue=receipts.VENUE_OTHER,
            instrument="SOL-USD",
        )

    def test_failed_commit_keeps_epoch_pending_then_retry_lands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bridge = _FakeBridge(
                {
                    "getPerformance": {"epochCount": "0"},
                    "commit": {"hash": "0xabc", "blockNumber": "9"},
                    "verify": {"valid": True},
                },
                fail_first_commit=1,
            )
            attestor = self._attestor(root)
            ledger = attestor.start_epoch(0)
            for i in range(2):
                ledger.add_fill(**_fill_kwargs(i))

            with patch("vouch.attest.commit.call_bridge", bridge):
                with self.assertRaises(CommitError):
                    attestor.commit_epoch(ledger, equity_usd=100.0)
                # epoch 0 is still pending locally: the index did NOT advance
                self.assertEqual(attestor.tracker.last_committed_epoch, -1)
                self.assertEqual(attestor.tracker.retries, 1)
                persisted = json.loads((root / "out" / "live-x" / "onchain.json").read_text())
                self.assertEqual(persisted["last_committed_epoch"], -1)
                self.assertEqual(persisted["retries"], 1)

                # retry at the next boundary: same ledger, now with a third fill
                ledger.add_fill(**_fill_kwargs(2))
                res = attestor.commit_epoch(ledger, equity_usd=101.0)
            self.assertTrue(res["committed"])
            self.assertTrue(res["selfAudit"]["valid"])
            self.assertEqual(attestor.tracker.last_committed_epoch, 0)
            persisted = json.loads((root / "out" / "live-x" / "onchain.json").read_text())
            self.assertEqual(persisted["last_committed_epoch"], 0)
            self.assertIsInstance(persisted["committed_at"]["0"], int)
            self.assertEqual(persisted["retries"], 1)  # the failure is still on record

    def test_recommit_of_landed_epoch_reports_already_committed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bridge = _FakeBridge(
                {
                    "getPerformance": {"epochCount": "0"},
                    "commit": {"hash": "0xabc", "blockNumber": "9"},
                    "verify": {"valid": True},
                }
            )
            attestor = self._attestor(root)
            ledger = attestor.start_epoch(0)
            ledger.add_fill(**_fill_kwargs(0))
            with patch("vouch.attest.commit.call_bridge", bridge):
                attestor.commit_epoch(ledger, equity_usd=100.0)
                res = attestor.commit_epoch(ledger, equity_usd=100.0)
            self.assertTrue(res["committed"])
            self.assertTrue(res["alreadyCommitted"])

    def test_ledger_only_commit_never_touches_tracker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = _connected_config(root / "evidence")
            cfg.enabled = True
            cfg.rpc_url = ""
            attestor = Attestor(
                cfg,
                run_dir=root / "out" / "live-x",
                strategy_id=1,
                venue=receipts.VENUE_OTHER,
                instrument="SOL-USD",
            )
            ledger = attestor.start_epoch(0)
            ledger.add_fill(**_fill_kwargs(0))
            res = attestor.commit_epoch(ledger, equity_usd=10.0)
            self.assertFalse(res["committed"])
            self.assertFalse(res["signed"])
            self.assertEqual(attestor.tracker.last_committed_epoch, -1)
            self.assertFalse((root / "out" / "live-x" / "onchain.json").exists())

    def test_resync_fast_forward_moves_index_and_refuse_freezes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attestor = self._attestor(root)
            bridge = _FakeBridge({"getPerformance": {"epochCount": "7"}})
            with patch("vouch.attest.commit.call_bridge", bridge):
                decision = attestor.resync_with_chain(
                    next_local_epoch=0, pending_receipt_epoch=None, has_local_history=False
                )
            self.assertEqual(decision["action"], "fast_forward")
            self.assertEqual(decision["next_epoch"], 7)
            self.assertEqual(attestor._chain_id, 421614)  # primed for the EIP-712 domain

            # now the divergence case: local history behind the chain
            bridge2 = _FakeBridge({"getPerformance": {"epochCount": "7"}})
            attestor2 = self._attestor(root)
            with patch("vouch.attest.commit.call_bridge", bridge2):
                decision = attestor2.resync_with_chain(
                    next_local_epoch=3, pending_receipt_epoch=None, has_local_history=True
                )
            self.assertEqual(decision["action"], "refuse")
            attestor2.freeze(decision["message"])
            ledger = attestor2.start_epoch(3)
            ledger.add_fill(**_fill_kwargs(0))
            with (
                patch("vouch.attest.commit.call_bridge", bridge2),
                self.assertRaisesRegex(CommitError, "frozen"),
            ):
                attestor2.commit_epoch(ledger, equity_usd=10.0)

    def test_evidence_signed_when_bridge_available(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sig = "0x" + "ab" * 65
            bridge = _FakeBridge(
                {
                    "getPerformance": {"epochCount": "0"},
                    "sign_evidence": {"signature": sig, "signer": "0x" + "33" * 20},
                    "commit": {"hash": "0xabc"},
                    "verify": {"valid": True},
                }
            )
            attestor = self._attestor(root)
            ledger = attestor.start_epoch(0)
            ledger.add_fill(**_fill_kwargs(0))
            with patch("vouch.attest.commit.call_bridge", bridge):
                res = attestor.commit_epoch(ledger, equity_usd=100.0)
            self.assertTrue(res["signed"])
            sign_calls = [c for c in bridge.calls if c["action"] == "sign_evidence"]
            self.assertEqual(len(sign_calls), 1)
            td = sign_calls[0]["typedData"]
            self.assertEqual(td["domain"]["chainId"], 421614)
            self.assertEqual(td["message"]["tradesRoot"], res["tradesRoot"])

    def test_auto_finalize_only_due_epochs_and_never_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attestor = self._attestor(root)
            attestor.tracker.record_success(0, 1_000)
            attestor.tracker.record_success(1, 1_000)
            attestor.tracker.record_success(2, 9_900)  # not due yet at now=10_000
            bridge = _FakeBridge(
                {"getConfig": {"challengeWindow": "600"}, "finalize": {"hash": "0xf1"}}
            )
            with patch("vouch.attest.commit.call_bridge", bridge):
                done = attestor.auto_finalize(now=10_000)
            self.assertEqual(done, [0, 1])
            finalize_calls = [c for c in bridge.calls if c["action"] == "finalize"]
            self.assertEqual([c["epochIndex"] for c in finalize_calls], ["0", "1"])
            self.assertEqual(attestor.tracker.finalized, [0, 1])
            # second pass: nothing left to finalize, no new finalize txs
            with patch("vouch.attest.commit.call_bridge", bridge):
                self.assertEqual(attestor.auto_finalize(now=10_100), [])
            finalize_calls = [c for c in bridge.calls if c["action"] == "finalize"]
            self.assertEqual(len(finalize_calls), 2)

    def test_auto_finalize_failure_is_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            attestor = self._attestor(root)
            attestor.tracker.record_success(0, 1_000)
            bridge = _FakeBridge(
                {"getConfig": {"challengeWindow": "600"}, "finalize": CommitError("rpc 429")}
            )
            with patch("vouch.attest.commit.call_bridge", bridge):
                self.assertEqual(attestor.auto_finalize(now=10_000), [])
            self.assertEqual(attestor.tracker.finalized, [])


if __name__ == "__main__":
    unittest.main()
