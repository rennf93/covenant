"""proven operator attest package: receipt ledger, evidence bundles, chain commits.

The package records every trading-bot fill as a canonical Proven receipt
(mirroring sdk/src/receipt.ts and sdk/src/epoch.ts invariants), durably
persisted per epoch, and closes each epoch by writing a self-contained
evidence bundle (receipts, hashes, Merkle proofs) before optionally
committing equity, net flow and the trades root onchain through a small
Node bridge (bridge.mjs) that drives the Stylus contract via viem.

Interlocks rule: attestation is allowed in shadow and real mode alike, but
it is LEDGER-ONLY unless PROVEN_ATTEST=1 and the full connection env
(PROVEN_RPC_URL, PROVEN_PRIVATE_KEY, PROVEN_CONTRACT_ADDRESS) are set.
Attestation never places trades and must never be the trigger that lets an
autonomous loop enter real-money positions.
"""

from __future__ import annotations

from .ledger import EpochLedger, LedgerError
from .commit import AttestationConfig, Attestor, CommitError
from . import receipts
from . import merkle

__all__ = [
    "EpochLedger",
    "LedgerError",
    "AttestationConfig",
    "Attestor",
    "CommitError",
    "receipts",
    "merkle",
]
