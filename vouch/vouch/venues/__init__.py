"""Execution venues: the shadow-to-live flip.

Three venues, two real trading surfaces:
- PaperVenue: simulated fills (the shadow mode default; always safe).
- CoinbaseVenue: Coinbase Advanced Trade REST (real money, real orders).
- ArbPaperVenue: Arbitrum DEX model with live prices and paper fills
  (the buildathon demo venue; honest about never being live).

Dual-venue story: Coinbase spot and the Arbitrum DEX are both first-class
venues. Every venue declares a venue_id, a receipt_venue (the int venue
class from attest.receipts that travels into the canonical Covenant
receipt), and the canonical instrument it trades. The venue class is
attested alongside the fill, so a receipt can never blur which market
produced it.

Hard guardrails that live in THIS package, not in prompts:
- max_usd exposure cap enforced on every order;
- the Coinbase adapter refuses to construct unless venue env vars are
  explicitly set;
- nothing here is reachable from the AutoClaw agent system (finance-policy.md
  forbids it); a real run is started by a human running run_real.py by hand.

Layering: leaf. Venues may use attest (receipt venue constants) but must
never import vouch.engine or vouch.server.
"""

from __future__ import annotations

from vouch.venues.arb_paper import ArbPaperVenue
from vouch.venues.coinbase import CoinbaseVenue
from vouch.venues.paper import PaperVenue
from vouch.venues.registry import VENUE_INFO, VENUES, available_venues, make_venue

__all__ = [
    "VENUES",
    "VENUE_INFO",
    "ArbPaperVenue",
    "CoinbaseVenue",
    "PaperVenue",
    "available_venues",
    "make_venue",
]
