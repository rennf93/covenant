"""Structural contracts (typing.Protocol, runtime-checkable).

These are the seams between the layers, written as Protocols rather than
ABCs on purpose: venues and backends are duck-typed today and stay that
way, but callers can now annotate against the contract and mypy checks
the shape.

- `Venue`: any trading surface. `get_fill` is deliberately NOT part of it:
  only venues whose fills must be SOURCED back from the market after an
  order (real money, or the arb-paper book) implement it. That is the
  separate `SourcingVenue` protocol below; PaperVenue is a plain Venue and
  that is honest, since nothing can be fetched back from it.
- `System1Backend`: the decision-head contract every provider matches
  (local Router, laya HTTP server, OpenAI-compatible chat).
- `FillObserver`: the broker's per-fill hook (one dict per booked fill).

Layering: leaf module - imports nothing from the package.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import httpx


@runtime_checkable
class Venue(Protocol):
    """A trading surface: an identity that travels into receipts, a price
    source, and a market-order entry point."""

    venue_id: str
    receipt_venue: int  # attest.receipts venue class (see receipts.VENUE_*)
    instrument: str  # canonical instrument traded, e.g. "SOL-USD"

    def get_price(self, client: httpx.Client) -> float:
        """Current spot price of `instrument`."""
        ...

    def market_order(self, client: httpx.Client, side: str, quote_usd: float, price: float) -> dict:
        """Place a market order for `quote_usd` notional; returns a fill
        descriptor dict (venue, side, order_id, ...)."""
        ...


@runtime_checkable
class SourcingVenue(Venue, Protocol):
    """A venue that can source the executed fills of a placed order (average
    price, fee, size). `attempts` exists because real fills can lag the
    order by a second or two; a sourcing venue returns {} if nothing lands."""

    def get_fill(self, client: httpx.Client, order_id: str, attempts: int = 5) -> dict: ...


@runtime_checkable
class System1Backend(Protocol):
    """Any System-1 decision provider. All backends return the Router.predict
    shape: {"model": str, "answers": {q: {"choice"/"score"/"noul"/"probabilities"}}}."""

    name: str

    def predict(self, state: dict, questions: dict) -> dict: ...


@runtime_checkable
class FillObserver(Protocol):
    """Hook called once per booked fill with a dict describing it
    (kind, side, price, size_usd, fee, tick, reason)."""

    def __call__(self, fill: dict) -> None: ...
