"""Optional websocket price feed: real trades, real volume, ~1s latency.

Subscribes to Coinbase Exchange's public ticker channel (no auth). Runs a
background thread with the sync websockets client; if the `websockets`
package is missing or the socket dies, callers fall back to HTTP polling
gracefully. Each trade observation feeds a shared BarAggregator, so live
mode gets correct wall-clock bars and real volume_ratio instead of the
old constant 1.0.
"""

from __future__ import annotations

import json
import queue
import threading
import time

WS_URL = "wss://ws-feed.exchange.coinbase.com"


class WsPriceFeed:
    """Background thread streaming (ts, price, size) trades for one product."""

    def __init__(self, product: str = "SOL-USD"):
        self.product = product
        self.observations: queue.Queue[tuple[float, float, float]] = queue.Queue(maxsize=10000)
        self.alive = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "WsPriceFeed":
        try:
            import websockets  # noqa: F401
        except ImportError:
            return self  # stay not-alive; caller polls instead
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            from websockets.sync.client import connect
            while not self._stop.is_set():
                try:
                    with connect(WS_URL, close_timeout=5) as ws:
                        ws.send(json.dumps({
                            "type": "subscribe",
                            "product_ids": [self.product],
                            "channels": ["ticker"],
                        }))
                        self.alive = True
                        while not self._stop.is_set():
                            msg = json.loads(ws.recv(timeout=15))
                            if msg.get("type") != "ticker" or not msg.get("price"):
                                continue
                            obs = (float(msg.get("time", 0) or time.time()),
                                   float(msg["price"]), float(msg.get("last_size", 0) or 0))
                            try:
                                self.observations.put_nowait(obs)
                            except queue.Full:
                                pass
                except Exception:  # noqa: BLE001 - drop and reconnect
                    self.alive = False
                    time.sleep(2)
        finally:
            self.alive = False

    def drain(self) -> list[tuple[float, float, float]]:
        """Pop all queued observations (oldest first) without blocking."""
        out = []
        while True:
            try:
                out.append(self.observations.get_nowait())
            except queue.Empty:
                return out
