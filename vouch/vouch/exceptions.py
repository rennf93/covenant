"""Vouch exception hierarchy.

One root so callers can catch every vouch failure with `except VouchError`,
and one leaf per subsystem so runners can be precise. The classes used to
live scattered across the modules that raised them (execution.py,
ledger.py, commit.py, s1_backends.py); the names and the messages are
unchanged, only the import moved here.

Layering: leaf module - imports nothing from the package.
"""

from __future__ import annotations


class VouchError(Exception):
    """Root of every vouch-raised error."""


class VenueError(VouchError):
    """Venue adapter failure: missing keys, refused order, HTTP error."""


class LedgerError(VouchError):
    """Receipt-ledger invariant violation or unrecoverable on-disk corruption."""


class CommitError(VouchError):
    """Chain commit failed after the evidence bundle was written."""


class BackendError(VouchError):
    """System-1 backend failure: unreachable server, bad key, bad answer."""


class S2Error(VouchError):
    """System-2 rewriter failure. Reserved for callers that want to surface
    rewriter errors explicitly; rewrite() itself degrades to the heuristic
    backend instead of raising, exactly as before."""
