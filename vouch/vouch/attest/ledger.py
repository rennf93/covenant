"""Append-only per-epoch receipt ledger, crash-safe.

Invariants mirror sdk/src/epoch.ts EpochBuilder exactly:
  - venueOrderIdHash must be unique within the epoch
    ("duplicate venueOrderIdHash in epoch <N>").
  - filledAt must be monotonically non-decreasing across additions
    ("receipt filledAt <X> precedes previously added <Y>").

Durability: each fill is one JSONL line appended, flushed and fsynced
before the call returns, so a fill either is fully persisted or absent.
On recovery, a torn final line left by a crash is truncated away; a
corrupt line anywhere else is a hard error (the ledger must never
silently drop a mid-file receipt).

Layering: leaf. Attest must never import vouch.engine, vouch.venues, or
vouch.server; LedgerError comes from vouch.exceptions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from vouch.attest.receipts import Receipt, make_receipt, receipt_hash, validate_receipt
from vouch.exceptions import LedgerError
from vouch.logging import get_logger

logger = get_logger(__name__)


def _epoch_filename(epoch_index: int) -> str:
    return f"receipts-epoch-{epoch_index:04d}.jsonl"


class EpochLedger:
    """Append-only receipt ledger for a single (strategy, epoch) pair."""

    def __init__(
        self,
        run_dir: Path,
        strategy_id: int,
        epoch_index: int,
        venue: int,
        instrument: str,
    ) -> None:
        self._run_dir = Path(run_dir)
        self._strategy_id = strategy_id
        self._epoch_index = epoch_index
        self._venue = venue
        self._instrument = instrument
        self.path = self._run_dir / _epoch_filename(epoch_index)
        self._receipts: list[Receipt] = []
        self._seen_order_hashes: set[str] = set()
        self._last_filled_at: int | None = None
        self._load()

    def _load(self) -> None:
        """Rebuild in-memory state from an existing file, tolerating a torn tail."""
        if not self.path.exists():
            return
        data = self.path.read_bytes()
        good_offset = 0
        lines: list[tuple[int, bytes]] = []  # (end offset, line bytes)
        for raw in data.splitlines(keepends=True):
            body = raw.rstrip(b"\n").rstrip(b"\r")
            if not body.strip():
                good_offset += len(raw)
                continue
            try:
                obj = json.loads(body)
            except json.JSONDecodeError:
                is_last = good_offset + len(raw) >= len(data)
                if is_last:
                    logger.warning(
                        "attest.ledger: warning: torn final line in %s, "
                        "truncating to last good offset %d",
                        self.path,
                        good_offset,
                    )
                    break
                raise LedgerError(
                    f"corrupt line in {self.path} at offset {good_offset}: not valid JSON"
                ) from None
            lines.append((good_offset + len(raw), body))
            good_offset += len(raw)

        for _, body in lines:
            obj = json.loads(body)
            try:
                r = Receipt.from_dict(obj["receipt"])
            except (KeyError, TypeError, ValueError) as exc:
                raise LedgerError(f"malformed receipt record in {self.path}: {exc}") from exc
            problems = validate_receipt(r)
            if problems:
                raise LedgerError(
                    f"invalid persisted receipt in {self.path}: " + "; ".join(problems)
                )
            if r.venue_order_id_hash in self._seen_order_hashes:
                raise LedgerError(f"duplicate venueOrderIdHash in epoch {self._epoch_index}")
            if self._last_filled_at is not None and r.filled_at < self._last_filled_at:
                raise LedgerError(
                    f"receipt filledAt {r.filled_at} precedes previously added "
                    f"{self._last_filled_at}"
                )
            self._seen_order_hashes.add(r.venue_order_id_hash)
            self._last_filled_at = r.filled_at
            self._receipts.append(r)

        # Drop any torn tail so subsequent appends start from a clean boundary.
        if good_offset < len(data):
            with open(self.path, "r+b") as f:
                f.truncate(good_offset)
                f.flush()
                os.fsync(f.fileno())

    def add_fill(
        self,
        *,
        venue_order_id: str,
        side: int,
        size_base: int | float | str,
        price: int | float | str,
        fee: int | float | str,
        filled_at: int,
    ) -> dict:
        """Validate invariants, then durably append one fill; returns hash + receipt."""
        r = make_receipt(
            strategy_id=self._strategy_id,
            epoch_index=self._epoch_index,
            venue=self._venue,
            venue_order_id=venue_order_id,
            instrument=self._instrument,
            side=side,
            size_base=size_base,
            price=price,
            fee=fee,
            filled_at=filled_at,
        )
        problems = validate_receipt(r)
        if problems:
            raise LedgerError("invalid receipt: " + "; ".join(problems))
        if r.venue_order_id_hash in self._seen_order_hashes:
            raise LedgerError(f"duplicate venueOrderIdHash in epoch {self._epoch_index}")
        if self._last_filled_at is not None and filled_at < self._last_filled_at:
            raise LedgerError(
                f"receipt filledAt {filled_at} precedes previously added {self._last_filled_at}"
            )

        record = {
            "receipt": r.to_dict(),
            "fill": {
                "venueOrderId": venue_order_id,
                "side": side,
                "sizeBase": size_base,
                "price": price,
                "fee": fee,
                "filledAt": filled_at,
            },
        }
        line = (json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "ab") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

        self._seen_order_hashes.add(r.venue_order_id_hash)
        self._last_filled_at = filled_at
        self._receipts.append(r)
        return {"hash": receipt_hash(r), "receipt": r.to_dict()}

    @property
    def epoch_index(self) -> int:
        return self._epoch_index

    @property
    def receipts(self) -> list[Receipt]:
        return list(self._receipts)

    def receipt_hashes(self) -> list[str]:
        """Leaf hashes in insertion order (matches the persisted file order)."""
        return [receipt_hash(r) for r in self._receipts]

    def __len__(self) -> int:
        return len(self._receipts)
