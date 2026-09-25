"""Synthetic market simulator. No network, no exchange, no keys.

A seeded geometric random walk with regime shifts, tuned to feel like a
SOL/USDC 1m chart (a few percent daily vol, trends, chops, shocks).
This is a paper-trading research artifact: the "market" is fake on purpose.

Layering: engine (leaf: imports nothing from the package).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

REGIMES = {
    "trend_up": {"mu": 0.00004, "sigma": 0.0016},
    "trend_down": {"mu": -0.00004, "sigma": 0.0018},
    "chop": {"mu": 0.0, "sigma": 0.0026},
    "shock": {"mu": -0.0004, "sigma": 0.0100},
}
REGIME_NAMES = list(REGIMES)
# regime transition probabilities (rows: from, cols: to)
TRANSITIONS = {
    "trend_up": [0.985, 0.005, 0.008, 0.002],
    "trend_down": [0.006, 0.985, 0.006, 0.003],
    "chop": [0.010, 0.010, 0.978, 0.002],
    "shock": [0.050, 0.050, 0.900, 0.000],
}


@dataclass
class Tick:
    i: int
    price: float
    regime: str
    ret_1m: float
    ret_15m: float
    ret_60m: float
    volume_ratio: float  # vs rolling average
    high_60m: float
    low_60m: float


class Market:
    def __init__(self, seed: int = 42, start_price: float = 148.0):
        self.rng = __import__("random").Random(seed)
        self.price = start_price
        self.regime = "chop"
        self.history: list[float] = [start_price]
        self._base_volume = 1.0

    def _next_regime(self) -> str:
        row = TRANSITIONS[self.regime]
        r = self.rng.random()
        acc = 0.0
        for name, p in zip(REGIME_NAMES, row, strict=False):
            acc += p
            if r < acc:
                return name
        return "chop"

    def tick(self) -> Tick:
        i = len(self.history)
        self.regime = self._next_regime()
        p = REGIMES[self.regime]
        # normal draw via Box-Muller on the seeded rng
        u1, u2 = self.rng.random(), self.rng.random()
        z = math.sqrt(-2.0 * math.log(u1 + 1e-12)) * math.cos(2 * math.pi * u2)
        ret = p["mu"] + p["sigma"] * z
        self.price = max(0.01, self.price * (1.0 + ret))
        self.history.append(self.price)

        def past(n: int) -> float:
            if len(self.history) > n:
                return self.history[-1 - n]
            return self.history[0]

        self._base_volume = 0.9 * self._base_volume + 0.1 * (0.5 + abs(z))
        return Tick(
            i=i,
            price=self.price,
            regime=self.regime,  # "ground truth" is NOT shown to System-1
            ret_1m=ret,
            ret_15m=(self.price - past(15)) / past(15),
            ret_60m=(self.price - past(60)) / past(60),
            volume_ratio=self._base_volume,
            high_60m=max(self.history[-60:]) if len(self.history) >= 2 else self.price,
            low_60m=min(self.history[-60:]) if len(self.history) >= 2 else self.price,
        )
