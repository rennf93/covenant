"""Durable onchain commit bookkeeping: the retry/resync state machine.

The one invariant this module exists to protect: the local epoch index must
NEVER advance past the last epoch that actually committed onchain. A single
failed commit used to poison the sequential index forever; now every commit
outcome is recorded durably in out/<run_id>/onchain.json and:

  - on failure the epoch stays open locally (receipts keep accumulating) and
    the commit is retried at the next boundary;
  - on startup (connected mode) the chain's epoch count for the strategy is
    queried through the bridge and the local index is resynced:
      * fresh local ledger behind the chain -> fast-forward,
      * local history diverging from the chain -> refuse to commit with a
        loud, explicit error (ledger/chain mismatch).

Pure decision helpers (resync_plan, finalize_due) are separate from I/O so
the state machine is unit-testable with the bridge mocked.

Layering: leaf. Attest must never import vouch.engine, vouch.venues, or
vouch.server; this module imports only stdlib + vouch.logging.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from vouch.logging import get_logger

logger = get_logger(__name__)

# Checkpoint statuses as stored onchain (sdk/src/client.ts CheckpointStatus).
CHECKPOINT_PENDING = 0
CHECKPOINT_FINALIZED = 1
CHECKPOINT_CHALLENGED = 2
CHECKPOINT_INVALIDATED = 3

STATE_FILENAME = "onchain.json"


@dataclass
class OnchainState:
    """What ACTUALLY landed onchain for one run, durable across restarts.

    last_committed_epoch starts at -1 (nothing committed). committed_at maps
    epoch index -> unix seconds of the successful commit (feeds the
    auto-finalize timing decision). finalized lists epochs whose challenge
    window we already closed with a finalizeEpoch tx.
    """

    last_committed_epoch: int = -1
    retries: int = 0
    last_error: str = ""
    committed_at: dict[int, int] = field(default_factory=dict)
    finalized: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "last_committed_epoch": self.last_committed_epoch,
            "retries": self.retries,
            "last_error": self.last_error,
            "committed_at": {str(k): v for k, v in sorted(self.committed_at.items())},
            "finalized": sorted(self.finalized),
        }

    @classmethod
    def from_dict(cls, d: dict) -> OnchainState:
        committed = d.get("committed_at") or {}
        return cls(
            last_committed_epoch=int(d.get("last_committed_epoch", -1)),
            retries=int(d.get("retries", 0)),
            last_error=str(d.get("last_error", "")),
            committed_at={int(k): int(v) for k, v in committed.items()},
            finalized=[int(e) for e in (d.get("finalized") or [])],
        )

    @classmethod
    def load(cls, run_dir: Path) -> OnchainState:
        """Load out/<run_id>/onchain.json; missing file = fresh run.

        A corrupt tracker resets to "nothing committed" instead of guessing:
        the startup chain resync resolves the real index before anything is
        committed, and a fresh state can never claim a commit that did not
        happen.
        """
        path = Path(run_dir) / STATE_FILENAME
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            logger.warning(
                "attest.onchain: corrupt %s (%s); assuming nothing committed "
                "until the chain resync runs",
                path,
                exc,
            )
            return cls()

    def save(self, run_dir: Path) -> None:
        """Persist atomically (tmp file + rename) so a crash cannot tear it."""
        out = Path(run_dir)
        out.mkdir(parents=True, exist_ok=True)
        tmp = out / (STATE_FILENAME + ".tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        tmp.replace(out / STATE_FILENAME)

    @property
    def next_epoch(self) -> int:
        """The only epoch index allowed to commit next (never past a commit)."""
        return self.last_committed_epoch + 1

    def record_success(self, epoch_index: int, committed_at: int) -> None:
        self.last_committed_epoch = max(self.last_committed_epoch, epoch_index)
        self.committed_at[epoch_index] = committed_at
        self.last_error = ""

    def record_failure(self, epoch_index: int, error: str) -> None:
        """Bookkeeping only: the epoch index does NOT move; retry later."""
        self.retries += 1
        self.last_error = f"epoch {epoch_index}: {error}"

    def record_finalized(self, epoch_index: int) -> None:
        if epoch_index not in self.finalized:
            self.finalized.append(epoch_index)


@dataclass
class ResyncDecision:
    """Outcome of comparing the local epoch index with the chain's."""

    action: str  # "ok" | "fast_forward" | "refuse" | "skip"
    next_epoch: int  # the epoch index to use going forward
    message: str


def resync_plan(
    *,
    chain_epoch_count: int,
    next_local_epoch: int,
    has_local_history: bool,
    pending_receipt_epoch: int | None,
) -> ResyncDecision:
    """Decide how to align the local epoch index with the chain (pure).

    chain_epoch_count: how many epochs the contract has stored for this
    strategy (epochs 0..N-1 committed). next_local_epoch: the epoch index the
    local ledger would write next. pending_receipt_epoch: an open local
    ledger that still holds uncommitted receipts (its commit failed and it
    is waiting for retry), or None.

    The rules, in order:
      1. An open ledger with receipts for an epoch the chain already has
         (pending < chain) can never commit: a different ledger produced the
         onchain epoch. Refuse.
      2. An open ledger for an epoch ahead of the chain (pending > chain)
         means the chain was redeployed/reset under our feet. Refuse.
      3. pending == chain: the normal retry path after a failed commit; the
         open ledger IS the next epoch to commit. Ok.
      4. Chain ahead of a local ledger with no pending receipts: a fresh
         ledger fast-forwards to the chain's index; a ledger with prior
         history means epochs exist onchain that the local ledger lacks.
         Refuse the latter.
      5. Chain behind the local record: contract redeployed with a different
         history. Refuse.
      6. Equal: business as usual.
    """
    if pending_receipt_epoch is not None:
        if pending_receipt_epoch < chain_epoch_count:
            return ResyncDecision(
                "refuse",
                next_local_epoch,
                f"ledger/chain mismatch: the chain already has {chain_epoch_count} epochs "
                f"but the local ledger still holds uncommitted receipts for epoch "
                f"{pending_receipt_epoch}; a different ledger produced the onchain epoch, "
                f"so these receipts can never commit. Refusing to commit.",
            )
        if pending_receipt_epoch > chain_epoch_count:
            return ResyncDecision(
                "refuse",
                next_local_epoch,
                f"ledger/chain mismatch: the local ledger holds receipts for epoch "
                f"{pending_receipt_epoch} but the chain has only {chain_epoch_count} "
                f"epochs (redeployed or reset contract?). Refusing to commit.",
            )
        return ResyncDecision(
            "ok",
            next_local_epoch,
            f"open epoch {pending_receipt_epoch} is the chain's next epoch "
            f"({chain_epoch_count} committed); its commit can be retried.",
        )
    if chain_epoch_count > next_local_epoch:
        if has_local_history:
            return ResyncDecision(
                "refuse",
                next_local_epoch,
                f"ledger/chain mismatch: the chain has {chain_epoch_count} epochs but the "
                f"local ledger only knows {next_local_epoch}; the chain holds epochs the "
                f"local ledger lacks, so local receipts cannot be trusted to continue "
                f"this history. Refusing to commit; register/point at a fresh strategy "
                f"or restore the matching out/<run_id> directory.",
            )
        return ResyncDecision(
            "fast_forward",
            chain_epoch_count,
            f"fresh local ledger; fast-forwarding the epoch index to the chain's "
            f"{chain_epoch_count} committed epochs.",
        )
    if chain_epoch_count < next_local_epoch:
        return ResyncDecision(
            "refuse",
            next_local_epoch,
            f"ledger/chain mismatch: the local record is at epoch {next_local_epoch} but "
            f"the chain has only {chain_epoch_count} epochs (contract redeployed with a "
            f"different history?). Refusing to commit.",
        )
    return ResyncDecision("ok", next_local_epoch, "local epoch index matches the chain.")


def finalize_due(
    checkpoints: list[tuple[int, int, int]], *, now: int, window_seconds: int
) -> list[int]:
    """Pick the checkpoints whose challenge window has fully elapsed (pure).

    checkpoints: (epoch_index, committed_at, status) tuples, exactly what the
    bridge's getCheckpoint/getPerformance ops surface. A checkpoint is due
    when it is still Pending and committed_at + window_seconds <= now.
    finalizeEpoch is permissionless, so the agent can close its own windows
    without waiting for an external finalizer.
    """
    return [
        epoch
        for epoch, committed_at, status in checkpoints
        if status == CHECKPOINT_PENDING and committed_at + window_seconds <= now
    ]
