"""CoinbaseVenue: Coinbase Advanced Trade REST (real money, real orders).

Split out of the old vouch/execution.py. Every order passes the exposure
cap check. The adapter refuses to construct unless the venue key env vars
are set (read once from Settings; see vouch/config.py).

Layering: leaf - venues must not import engine or server.
"""

from __future__ import annotations

import base64
import time
import uuid
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePrivateKey

import httpx

from vouch.attest.receipts import VENUE_COINBASE
from vouch.config import load_settings
from vouch.exceptions import VenueError


class CoinbaseVenue:
    """Coinbase Advanced Trade. Every order passes the exposure cap check."""

    BASE = "https://api.coinbase.com"

    venue_id = "coinbase"
    receipt_venue = VENUE_COINBASE

    def __init__(self, product: str = "SOL-USD", max_usd: float = 10.0):
        venue = load_settings().venue
        self.key_name = venue.cb_key_name
        self.private_b64 = venue.cb_private_key
        if not self.key_name or not self.private_b64:
            raise VenueError("Coinbase venue requires VOUCH_CB_KEY_NAME and VOUCH_CB_PRIVATE_KEY")
        self.product = product
        self.instrument = self.product
        self.max_usd = max_usd

    def _jwt(self, method: str, path: str) -> str:
        import jwt
        from cryptography.hazmat.primitives import serialization

        # CDP API keys are EC (ES256); narrow the load for the jwt signer.
        private_key = cast(
            "EllipticCurvePrivateKey",
            serialization.load_der_private_key(base64.b64decode(self.private_b64), password=None),
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

    def _authed_request(
        self, client: httpx.Client, method: str, path: str, json_body: dict | None = None
    ) -> dict:
        token = self._jwt(method, path)
        r = client.request(
            method,
            f"{self.BASE}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=json_body,
            timeout=30,
        )
        if r.status_code >= 400:
            raise VenueError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return cast(dict, r.json())

    def market_order(self, client: httpx.Client, side: str, quote_usd: float, price: float) -> dict:
        if quote_usd > self.max_usd:
            raise VenueError(
                f"EXPOSURE CAP: order {quote_usd:.2f} USD exceeds the "
                f"{self.max_usd:.2f} cap. Refusing."
            )
        path = "/api/v3/brokerage/orders"
        body = {
            "client_order_id": uuid.uuid4().hex,
            "product_id": self.product,
            "side": side,
            "order_type": "market_market_ioc",
            "order_config": {"market_market_ioc": {"quote_size": f"{quote_usd:.2f}"}},
        }
        resp: dict = self._authed_request(client, "POST", path, body)
        success = resp.get("success", False)
        return {
            "venue": "coinbase",
            "side": side,
            "quote_usd": round(quote_usd, 2),
            "success": success,
            "order_id": resp.get("success_response", {}).get("order_id", ""),
            "raw": resp if not success else None,
        }

    def get_fill(self, client: httpx.Client, order_id: str, attempts: int = 5) -> dict:
        """Fetch the executed fills for an order: average price, total fee,
        filled size. Fills can lag the order by a second or two, hence the
        retries. Returns {} if nothing lands in time."""
        import time as _time

        path = "/api/v3/brokerage/orders/historical/fills"
        for _ in range(attempts):
            try:
                resp: dict = self._authed_request(client, "GET", f"{path}?order_id={order_id}")
                fills = resp.get("fills", [])
                if fills:
                    total_size = sum(float(f["size"]) for f in fills)
                    avg_price = (
                        sum(float(f["price"]) * float(f["size"]) for f in fills) / total_size
                        if total_size
                        else 0.0
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
