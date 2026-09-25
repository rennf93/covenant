"""covenant operator attest package: receipt ledger, evidence bundles, chain commits.

The package records every trading-bot fill as a canonical Covenant receipt
(mirroring sdk/src/receipt.ts and sdk/src/epoch.ts invariants), durably
persisted per epoch, and closes each epoch by writing a self-contained
evidence bundle (receipts, hashes, Merkle proofs) before optionally
committing equity, net flow and the trades root onchain through a small
Node bridge (bridge.mjs) that drives the Stylus contract via viem.

Interlocks rule: attestation is allowed in shadow and real mode alike, but
it is LEDGER-ONLY unless COVENANT_ATTEST=1 and the full connection env
(COVENANT_RPC_URL, COVENANT_PRIVATE_KEY, COVENANT_CONTRACT_ADDRESS) are set.
Attestation never places trades and must never be the trigger that lets an
autonomous loop enter real-money positions.

Layering: leaf. Attest must never import vouch.engine, vouch.venues, or
vouch.server; the exception classes come from vouch.exceptions (shared root).
"""

from __future__ import annotations

from vouch.attest import merkle, receipts
from vouch.attest.commit import AttestationConfig, Attestor
from vouch.attest.ledger import EpochLedger
from vouch.exceptions import CommitError, LedgerError

__all__ = [
    "EpochLedger",
    "LedgerError",
    "AttestationConfig",
    "Attestor",
    "CommitError",
    "receipts",
    "merkle",
]
