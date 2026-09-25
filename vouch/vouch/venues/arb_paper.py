"""ArbPaperVenue: Arbitrum DEX venue with live prices and PAPER fills.

Split out of the old vouch/execution.py. This is the buildathon demo venue:
it prices SOL/USDC off the public Coinbase spot feed (USD spot is the USDC
mark on Arbitrum for retail size, so the two track the same dollar value),
then books simulated AMM taker fills. It is a paper venue: no bridge, no
router, no wallet, never pretend-live. A real router-backed ArbitrumDexVenue
drops into this same interface later; that is the "attested" to "verified"
upgrade path in docs/DESIGN.md section 3.

Fee model is the AMM taker cost: pool fee plus price impact for a
retail-size swap, default 30 bps via VOUCH_ARB_FEE_BPS (read from Settings).

Layering: leaf - venues must not import engine or server.
"""

from __future__ import annotations

import uuid

import httpx

from vouch.attest.receipts import VENUE_ARBITRUM_DEX
from vouch.config import load_settings


class ArbPaperVenue:
    """Live SOL/USDC pricing, simulated AMM taker fills."""

    venue_id = "arb-paper"
    receipt_venue = VENUE_ARBITRUM_DEX
    instrument = "SOL/USDC"

    def __init__(self, product: str = "SOL/USDC"):
        self.product = product
        self.fee = load_settings().venue.arb_fee_bps / 10000.0
        self.slippage = 0.0005
        self._fills: dict[str, dict] = {}

    def get_price(self, client: httpx.Client) -> float:
        r = client.get("https://api.coinbase.com/v2/prices/SOL-USD/spot", timeout=10)
        r.raise_for_status()
        return float(r.json()["data"]["amount"])

    def market_order(self, client: httpx.Client, side: str, quote_usd: float, price: float) -> dict:
        fill = price * (1 + self.slippage if side == "BUY" else 1 - self.slippage)
        fee_usd = round(quote_usd * self.fee, 4)
        order_id = "arbpaper-" + uuid.uuid4().hex[:8]
        self._fills[order_id] = {
            "avg_price": round(fill, 4),
            "fee_usd": fee_usd,
            "size": round(quote_usd / fill, 6),
        }
        return {
            "venue": "arb-paper",
            "side": side,
            "quote_usd": round(quote_usd, 2),
            "fill_price": round(fill, 4),
            "fee_usd": fee_usd,
            "order_id": order_id,
        }

    def get_fill(self, client: httpx.Client, order_id: str, attempts: int = 5) -> dict:
        """Paper fills are instant: the fill record was booked in
        market_order, so return it directly. Unknown ids return {}."""
        return dict(self._fills.get(order_id, {}))
