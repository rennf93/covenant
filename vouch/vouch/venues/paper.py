"""PaperVenue: simulated fills at the observed price, no keys, no orders.

Split out of the old vouch/execution.py. Fills are priced off the public
Coinbase spot feed, but the fills themselves are simulated: receipts booked
here are honestly classed as VENUE_OTHER, never claimed as a real venue.

Layering: leaf - venues must not import engine, server, or attest modules
beyond receipts' venue constants.
"""

from __future__ import annotations

import uuid

import httpx

from vouch.attest.receipts import VENUE_OTHER


class PaperVenue:
    """Simulated fills at the observed price with realistic-ish costs."""

    venue_id = "paper"
    receipt_venue = VENUE_OTHER
    instrument = "SOL-USD"

    def __init__(self, product: str = "SOL-USD"):
        self.product = product
        self.fee = 0.006  # 60 bps per side, retail reality
        self.slippage = 0.0005

    def get_price(self, client: httpx.Client) -> float:
        r = client.get("https://api.coinbase.com/v2/prices/SOL-USD/spot", timeout=10)
        r.raise_for_status()
        return float(r.json()["data"]["amount"])

    def market_order(self, client: httpx.Client, side: str, quote_usd: float, price: float) -> dict:
        fill = price * (1 + self.slippage if side == "BUY" else 1 - self.slippage)
        return {
            "venue": "paper",
            "side": side,
            "quote_usd": round(quote_usd, 2),
            "fill_price": round(fill, 4),
            "fee_usd": round(quote_usd * self.fee, 4),
            "order_id": "paper-" + uuid.uuid4().hex[:8],
        }
