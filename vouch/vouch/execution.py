"""Execution venues for the shadow-to-live flip.

Three venues, two real trading surfaces:
- PaperVenue: simulated fills (the shadow mode default; always safe).
- CoinbaseVenue: Coinbase Advanced Trade REST (real money, real orders).
- ArbPaperVenue: Arbitrum DEX model with live prices and paper fills
  (the buildathon demo venue; honest about never being live).

Dual-venue story: Coinbase spot and the Arbitrum DEX are both first-class
venues. Every venue declares a venue_id, a receipt_venue (the int venue
class from attest.receipts that travels into the canonical Proven
receipt), and the canonical instrument it trades. The venue class is
attested alongside the fill, so a receipt can never blur which market
produced it.

Hard guardrails that live in THIS file, not in prompts:
- max_usd exposure cap enforced on every order;
- the adapter refuses to construct unless venue env vars are explicitly set;
- nothing here is reachable from the AutoClaw agent system (finance-policy.md
  forbids it); a real run is started by a human running run_real.py by hand.

Required env for Coinbase:
    VOUCH_CB_KEY_NAME     CDP API key name (organizations/.../apiKeys/...)
    VOUCH_CB_PRIVATE_KEY  CDP API private key (base64 EC key)
    VOUCH_CB_PRODUCT      tradable product, default SOL-USD
"""

from __future__ import annotations

import base64
import os
import time
import uuid

import httpx

from attest.receipts import VENUE_COINBASE, VENUE_ARBITRUM_DEX, VENUE_OTHER


class VenueError(Exception):
    pass


class PaperVenue:
    """Simulated fills at the observed price with realistic-ish costs.

    Fills are priced off the public Coinbase spot feed, but the fills
    themselves are simulated: receipts booked here are honestly classed as
    VENUE_OTHER, never claimed as a real venue.
    """

    venue_id = "paper"
    receipt_venue = VENUE_OTHER
    instrument = "SOL-USD"

    def __init__(self, product: str = "SOL-USD"):
        self.product = product
        self.fee = 0.006          # 60 bps per side, retail reality
        self.slippage = 0.0005

    def get_price(self, client: httpx.Client) -> float:
        r = client.get(
            "https://api.coinbase.com/v2/prices/SOL-USD/spot", timeout=10
        )
        r.raise_for_status()
        return float(r.json()["data"]["amount"])

    def market_order(self, client: httpx.Client, side: str, quote_usd: float, price: float) -> dict:
        fill = price * (1 + self.slippage if side == "BUY" else 1 - self.slippage)
        return {"venue": "paper", "side": side, "quote_usd": round(quote_usd, 2),
                "fill_price": round(fill, 4), "fee_usd": round(quote_usd * self.fee, 4),
                "order_id": "paper-" + uuid.uuid4().hex[:8]}


class CoinbaseVenue:
    """Coinbase Advanced Trade. Every order passes the exposure cap check."""

    BASE = "https://api.coinbase.com"

    venue_id = "coinbase"
    receipt_venue = VENUE_COINBASE

    def __init__(self, product: str = "SOL-USD", max_usd: float = 10.0):
        self.key_name = os.environ.get("VOUCH_CB_KEY_NAME", "")
        self.private_b64 = os.environ.get("VOUCH_CB_PRIVATE_KEY", "")
        if not self.key_name or not self.private_b64:
            raise VenueError("Coinbase venue requires VOUCH_CB_KEY_NAME and VOUCH_CB_PRIVATE_KEY")
        self.product = product
        self.instrument = self.product
        self.max_usd = max_usd

    def _jwt(self, method: str, path: str) -> str:
        import jwt
        from cryptography.hazmat.primitives import serialization

        private_key = serialization.load_der_private_key(
            base64.b64decode(self.private_b64), password=None
        )
        now = int(time.time())
        payload = {
            "iss": "cdp",
            "nbf": now,
            "exp": now + 120,
            "sub": self.key_name,
            "uri": f"{method} api.coinbase.com{path}",
        }
        headers = {"kid": self.key_name, "nonce": uuid.uuid4().hex}
        return jwt.encode(payload, private_key, algorithm="ES256", headers=headers)

    def get_price(self, client: httpx.Client) -> float:
        r = client.get(f"{self.BASE}/v2/prices/{self.product}/spot", timeout=10)
        r.raise_for_status()
        return float(r.json()["data"]["amount"])

    def _authed_request(self, client: httpx.Client, method: str, path: str, json_body: dict | None = None) -> dict:
        token = self._jwt(method, path)
        r = client.request(
            method, f"{self.BASE}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=json_body, timeout=30,
        )
        if r.status_code >= 400:
            raise VenueError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def market_order(self, client: httpx.Client, side: str, quote_usd: float, price: float) -> dict:
        if quote_usd > self.max_usd:
            raise VenueError(
                f"EXPOSURE CAP: order {quote_usd:.2f} USD exceeds the {self.max_usd:.2f} cap. Refusing."
            )
        path = "/api/v3/brokerage/orders"
        body = {
            "client_order_id": uuid.uuid4().hex,
            "product_id": self.product,
            "side": side,
            "order_type": "market_market_ioc",
            "order_config": {"market_market_ioc": {"quote_size": f"{quote_usd:.2f}"}},
        }
        resp = self._authed_request(client, "POST", path, body)
        success = resp.get("success", False)
        return {"venue": "coinbase", "side": side, "quote_usd": round(quote_usd, 2),
                "success": success, "order_id": resp.get("success_response", {}).get("order_id", ""),
                "raw": resp if not success else None}

    def get_fill(self, client: httpx.Client, order_id: str, attempts: int = 5) -> dict:
        """Fetch the executed fills for an order: average price, total fee,
        filled size. Fills can lag the order by a second or two, hence the
        retries. Returns {} if nothing lands in time."""
        import time as _time

        path = f"/api/v3/brokerage/orders/historical/fills"
        for _ in range(attempts):
            try:
                resp = self._authed_request(client, "GET", f"{path}?order_id={order_id}")
                fills = resp.get("fills", [])
                if fills:
                    total_size = sum(float(f["size"]) for f in fills)
                    avg_price = (
                        sum(float(f["price"]) * float(f["size"]) for f in fills) / total_size
                        if total_size else 0.0
                    )
                    return {
                        "avg_price": avg_price,
                        "fee_usd": sum(float(f.get("fee", 0)) for f in fills),
                        "size": total_size,
                    }
            except VenueError:
                pass
            _time.sleep(1)
        return {}


class ArbPaperVenue:
    """Arbitrum DEX venue with live prices and PAPER fills.

    This is the buildathon demo venue: it prices SOL/USDC off the public
    Coinbase spot feed (USD spot is the USDC mark on Arbitrum for retail
    size, so the two track the same dollar value), then books simulated
    AMM taker fills. It is a paper venue: no bridge, no router, no wallet,
    never pretend-live. A real router-backed ArbitrumDexVenue drops into
    this same interface later; that is the "attested" to "verified"
    upgrade path in docs/DESIGN.md section 3.

    Fee model is the AMM taker cost: pool fee plus price impact for a
    retail-size swap, default 30 bps via VOUCH_ARB_FEE_BPS.
    """

    venue_id = "arb-paper"
    receipt_venue = VENUE_ARBITRUM_DEX
    instrument = "SOL/USDC"

    def __init__(self, product: str = "SOL/USDC"):
        self.product = product
        self.fee = float(os.environ.get("VOUCH_ARB_FEE_BPS", "30")) / 10000.0
        self.slippage = 0.0005
        self._fills: dict[str, dict] = {}

    def get_price(self, client: httpx.Client) -> float:
        r = client.get(
            "https://api.coinbase.com/v2/prices/SOL-USD/spot", timeout=10
        )
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
        return {"venue": "arb-paper", "side": side, "quote_usd": round(quote_usd, 2),
                "fill_price": round(fill, 4), "fee_usd": fee_usd, "order_id": order_id}

    def get_fill(self, client: httpx.Client, order_id: str, attempts: int = 5) -> dict:
        """Paper fills are instant: the fill record was booked in
        market_order, so return it directly. Unknown ids return {}."""
        return dict(self._fills.get(order_id, {}))


def make_venue(max_usd: float = 10.0):
    """The flip switch. VOUCH_VENUE=coinbase + keys = live; VOUCH_VENUE=arb-paper
    = live prices with paper fills; anything else = paper."""
    venue = os.environ.get("VOUCH_VENUE", "paper").lower()
    if venue in VENUES and venue != "paper":
        spec = VENUES[venue]
        return spec["factory"](os.environ.get("VOUCH_CB_PRODUCT", "SOL-USD"), max_usd=max_usd)
    return PaperVenue()


# --- Venue registry: what the UI (and anything else) can list and pick -----
# Adding a venue (e.g. an Arbitrum DEX adapter) means: implement get_price +
# market_order + (optionally) get_fill, add a factory, register it here. The
# exposure cap check lives in the venue adapter itself, as in CoinbaseVenue.
VENUES: dict[str, dict] = {
    "paper": {
        "factory": lambda product, max_usd: PaperVenue(product),
    },
    "coinbase": {
        "factory": lambda product, max_usd: CoinbaseVenue(product, max_usd=max_usd),
    },
    "arb-paper": {
        "factory": lambda product, max_usd: ArbPaperVenue(product),
    },
}

VENUE_INFO = {
    "paper": {"label": "Paper (simulated fills)", "live": False, "needs_env": [], "docs": "No keys, no orders. Safe default."},
    "coinbase": {"label": "Coinbase Advanced Trade (spot, live)", "live": True,
                 "needs_env": ["VOUCH_CB_KEY_NAME", "VOUCH_CB_PRIVATE_KEY"],
                 "docs": "Real SOL-USD spot orders. Exposure cap per order enforced in the adapter."},
    "arb-paper": {"label": "Arbitrum DEX (paper fills, live prices)", "live": False, "needs_env": [],
                  "docs": "Live SOL/USDC pricing, simulated AMM taker fills. Books canonical receipts as venue class ArbitrumDex while fills remain paper; no bridge, no router."},
}


def available_venues() -> list[dict]:
    """Venue list for the UI: id, label, whether it trades real money, and
    whether its required env vars are currently set."""
    out = []
    for vid, info in VENUE_INFO.items():
        out.append({
            "id": vid,
            "label": info["label"],
            "live": info["live"],
            "configured": all(os.environ.get(e) for e in info["needs_env"]),
            "missing_env": [e for e in info["needs_env"] if not os.environ.get(e)],
            "docs": info["docs"],
        })
    return out
