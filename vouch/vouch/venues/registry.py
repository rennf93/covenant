"""Venue registry: what the UI (and anything else) can list and pick.

Split out of the old vouch/execution.py. Adding a venue (e.g. a real
router-backed Arbitrum DEX adapter) means: implement get_price +
market_order (+ get_fill if fills must be sourced back, see
vouch.protocols.SourcingVenue), add a factory, register it here. The
exposure cap check lives in the venue adapter itself, as in CoinbaseVenue.

Layering: leaf - venues must not import engine or server.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from vouch.config import Settings, load_settings
from vouch.protocols import Venue
from vouch.venues.arb_paper import ArbPaperVenue
from vouch.venues.coinbase import CoinbaseVenue
from vouch.venues.paper import PaperVenue

__all__ = [
    "VENUES",
    "VENUE_INFO",
    "ArbPaperVenue",
    "CoinbaseVenue",
    "PaperVenue",
    "available_venues",
    "make_venue",
]


def make_venue(max_usd: float = 10.0) -> Venue:
    """The flip switch. VOUCH_VENUE=coinbase + keys = live; VOUCH_VENUE=arb-paper
    = live prices with paper fills; anything else = paper."""
    venue = load_settings().venue
    if venue.name in VENUES and venue.name != "paper":
        spec = VENUES[venue.name]
        factory = cast(Callable[..., Venue], spec["factory"])
        return factory(venue.cb_product, max_usd=max_usd)
    return PaperVenue()


VENUES: dict[str, dict[str, Any]] = {
    "paper": {
        "factory": lambda product, max_usd: PaperVenue(product),
    },
    "coinbase": {
        "factory": lambda product, max_usd: CoinbaseVenue(product, max_usd=max_usd),
    },
    "arb-paper": {
        # ArbPaperVenue only prices SOL/USDC (hardcoded spot source), so it
        # ignores VOUCH_CB_PRODUCT and takes its natural instrument.
        "factory": lambda product, max_usd: ArbPaperVenue("SOL/USDC"),
    },
}

VENUE_INFO: dict[str, dict[str, Any]] = {
    "paper": {
        "label": "Paper (simulated fills)",
        "live": False,
        "needs_env": [],
        "docs": "No keys, no orders. Safe default.",
    },
    "coinbase": {
        "label": "Coinbase Advanced Trade (spot, live)",
        "live": True,
        "needs_env": ["VOUCH_CB_KEY_NAME", "VOUCH_CB_PRIVATE_KEY"],
        "docs": "Real SOL-USD spot orders. Exposure cap per order enforced in the adapter.",
    },
    "arb-paper": {
        "label": "Arbitrum DEX (paper fills, live prices)",
        "live": False,
        "needs_env": [],
        "docs": "Live SOL/USDC pricing, simulated AMM taker fills. Books canonical "
        "receipts as venue class ArbitrumDex while fills remain paper; "
        "no bridge, no router.",
    },
}

# Env-var names a venue may declare in needs_env, resolved against Settings
# (the package no longer reads os.environ directly; this keeps the UI's
# "configured/missing env" display working off the single config source).
_ENV_PROBES: dict[str, Callable[[Settings], bool]] = {
    "VOUCH_CB_KEY_NAME": lambda s: bool(s.venue.cb_key_name),
    "VOUCH_CB_PRIVATE_KEY": lambda s: bool(s.venue.cb_private_key),
}


def available_venues() -> list[dict]:
    """Venue list for the UI: id, label, whether it trades real money, and
    whether its required env vars are currently set."""
    settings = load_settings()
    out = []
    for vid, info in VENUE_INFO.items():
        out.append(
            {
                "id": vid,
                "label": info["label"],
                "live": info["live"],
                "configured": all(
                    _ENV_PROBES.get(e, lambda s: False)(settings) for e in info["needs_env"]
                ),
                "missing_env": [
                    e
                    for e in info["needs_env"]
                    if not _ENV_PROBES.get(e, lambda s: False)(settings)
                ],
                "docs": info["docs"],
            }
        )
    return out
