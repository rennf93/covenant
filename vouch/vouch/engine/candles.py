"""Market data: real SOL candles from Coinbase's public API, no keys.

Two pieces:
- fetch_candles(): historical 1m bars for backtesting (Exchange API,
  public, 300 bars per request, paginated).
- BarAggregator(): turns wall-clock price polls (or websocket trades) into
  true 1m bars so live ret_1m/ret_15m/ret_60m windows are correct. The old
  deque-index math claimed a "60m return" over a 30-minute deque; this
  replaces it with timestamped bars.
- volume_ratio(): real volume vs its rolling mean, replacing the constant
  1.0 that was fed to laya in live mode.

Layering: engine. May use venues and attest; must not import server.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import httpx

from vouch.engine.market import Tick

EXCHANGE_CANDLES = "https://api.exchange.coinbase.com/products/{product}/candles"
GRANULARITY_1M = 60


@dataclass
class Bar:
    ts: int  # unix seconds, bar open
    open: float
    high: float
    low: float
    close: float
    volume: float


def fetch_candles(
    client: httpx.Client,
    product: str = "SOL-USD",
    minutes: int = 1440,
    end_ts: int | None = None,
    require_complete: bool = False,
) -> list[Bar]:
    """Pull `minutes` of 1m candles ending at end_ts (default: now).

    Each 300-bar chunk is retried on a short/empty response: a silent hole
    in the middle of a recording session poisons everything downstream
    (labels, calibration, SFT), so holes are never acceptable by default.
    With require_complete=True, any missing minute raises instead of
    returning a gapped series - used by recording runs."""
    end = end_ts or int(time.time())
    start = end - minutes * 60
    bars_by_ts: dict[int, Bar] = {}
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + 300 * 60, end)
        rows: list[list[Any]] = []
        for attempt in range(3):
            r = client.get(
                EXCHANGE_CANDLES.format(product=product),
                params={"granularity": GRANULARITY_1M, "start": cursor, "end": chunk_end},
                timeout=30,
            )
            r.raise_for_status()
            rows = r.json()
            expected = max(1, (chunk_end - cursor) // 60)
            if len(rows) >= expected * 0.95:
                break
            time.sleep(2.0 * (attempt + 1))  # short chunk: throttle or hiccup, back off
        for row in rows:
            # rows are [time, low, high, open, close, volume], newest first
            bars_by_ts[int(row[0])] = Bar(
                ts=int(row[0]),
                open=float(row[3]),
                high=float(row[2]),
                low=float(row[1]),
                close=float(row[4]),
                volume=float(row[5]),
            )
        cursor = chunk_end
    bars = [bars_by_ts[ts] for ts in sorted(bars_by_ts)]
    if require_complete:
        # Dead minutes (no trades -> venue emits no bar) are real market
        # events: reconstruct them as flat zero-volume bars at the last known
        # close, capped at gap_fill_max per stretch. A dead stretch BIGGER
        # than the cap is a feed outage, not a quiet market, and refuses the
        # series. Filled minutes are always counted out loud - silent holes
        # are never acceptable downstream.
        gap_fill_max = 15
        filled: list[Bar] = []
        skipped = 0
        run = 0
        idx = 0
        ts = start
        prev_close = bars[0].open if bars else 0.0
        while ts < end:
            if idx < len(bars) and bars[idx].ts == ts:
                filled.append(bars[idx])
                prev_close = bars[idx].close
                idx += 1
                run = 0
            else:
                run += 1
                if run > gap_fill_max:
                    raise SystemExit(
                        f"fetch_candles: {run}-minute dead stretch at ts {ts} in "
                        f"{product}; refusing incomplete data"
                    )
                skipped += 1
                filled.append(
                    Bar(
                        ts=ts,
                        open=prev_close,
                        high=prev_close,
                        low=prev_close,
                        close=prev_close,
                        volume=0.0,
                    )
                )
            ts += 60
        if skipped:
            print(
                f"fetch_candles: filled {skipped} dead minutes as flat zero-volume "
                f"bars ({product}, {minutes}m window)",
                flush=True,
            )
        bars = filled
    return bars


class BarAggregator:
    """Accumulates (ts, price, size) observations into true 1m bars.

    Live polling can fire at any cadence and can skip minutes after network
    hiccups; bars are keyed by floor(ts/60) so windows are wall-clock honest.
    """

    def __init__(self, max_bars: int = 480):
        self.max_bars = max_bars
        self._cur_key: int | None = None
        self._cur_open: float = 0.0
        self._cur_high: float = 0.0
        self._cur_low: float = float("inf")
        self._cur_close: float = 0.0
        self._cur_vol: float = 0.0
        self.bars: deque[Bar] = deque(maxlen=max_bars)

    def add(self, ts: float, price: float, size: float = 0.0) -> Bar | None:
        """Add an observation; returns a COMPLETED bar when a minute rolls over."""
        key = int(ts) // 60
        done = None
        if self._cur_key is not None and key != self._cur_key:
            done = Bar(
                ts=self._cur_key * 60,
                open=self._cur_open,
                high=self._cur_high,
                low=self._cur_low,
                close=self._cur_close,
                volume=self._cur_vol,
            )
            self.bars.append(done)
            self._cur_key, self._cur_vol = None, 0.0
        if self._cur_key is None:
            self._cur_key = key
            self._cur_open = self._cur_high = self._cur_low = self._cur_close = price
        else:
            self._cur_high = max(self._cur_high, price)
            self._cur_low = min(self._cur_low, price)
            self._cur_close = price
        self._cur_vol += size
        return done

    def flush(self) -> Bar | None:
        """Close out the in-progress bar (call at session end)."""
        if self._cur_key is None:
            return None
        done = Bar(
            ts=self._cur_key * 60,
            open=self._cur_open,
            high=self._cur_high,
            low=self._cur_low,
            close=self._cur_close,
            volume=self._cur_vol,
        )
        self.bars.append(done)
        self._cur_key = None
        return done


def volume_ratio(bars: deque[Bar] | list[Bar], lookback: int = 60) -> float:
    """Latest completed bar's volume vs the mean of the prior `lookback` bars.
    Returns 1.0 until there is a real baseline."""
    if len(bars) < 5:
        return 1.0
    recent = bars[-1].volume
    base = [b.volume for b in list(bars)[-1 - lookback : -1]]
    base = [v for v in base if v > 0]
    if not base or recent <= 0:
        return 1.0
    return recent / (sum(base) / len(base))


def inject_volume(bars: deque[Bar] | list[Bar], bar_ts: int, volume: float) -> None:
    """Set the volume of the completed bar opened at bar_ts (unix seconds of
    the bar's minute key). Live polling gives prices but no size; the public
    candles endpoint fills that in a minute later."""
    key = (bar_ts // 60) * 60
    for b in reversed(bars):
        if b.ts == key:
            if volume > 0:
                b.volume = volume
            return
        if b.ts < key:
            return


def latest_candle_volume(
    client: httpx.Client, product: str = "SOL-USD"
) -> tuple[int, float] | None:
    """(bar_ts, volume) for the most recent 1m candle, or None on any failure.
    Never raises: callers use it opportunistically once a minute."""
    try:
        r = client.get(
            EXCHANGE_CANDLES.format(product=product),
            params={"granularity": GRANULARITY_1M},
            timeout=10,
        )
        r.raise_for_status()
        rows = r.json()
        if not rows:
            return None
        row = rows[0]  # newest first
        return int(row[0]), float(row[5])
    except Exception:  # noqa: BLE001
        return None


def state_snapshot(tick: Tick, broker: Any) -> dict:
    """Compact market state per decision row: what analysis/SFT conditions on.
    `broker` is duck-typed: the paper Broker or the real-mode RealBook shim."""
    return {
        "ret_1m": round(tick.ret_1m, 6),
        "ret_15m": round(tick.ret_15m, 6),
        "ret_60m": round(tick.ret_60m, 6),
        "volume_ratio": round(tick.volume_ratio, 4),
        "range_pos_60m": round(
            (tick.price - tick.low_60m) / max(tick.high_60m - tick.low_60m, 1e-9), 4
        ),
        "in_position": broker.position is not None,
    }


def tick_from_bars(i: int, bars: list[Bar] | deque[Bar], regime: str = "live") -> Tick:
    """Build the Tick state laya sees from real 1m bars. Correct windows by
    construction: 1m/15m/60m returns and the 60m high-low range all come off
    timestamped bars, and volume_ratio is real."""
    closes = [b.close for b in bars]
    price = closes[-1]

    def past(n: int) -> float:
        return closes[-1 - n] if len(closes) > n else closes[0]

    window = list(bars)[-60:]
    return Tick(
        i=i,
        price=price,
        regime=regime,
        ret_1m=(price - past(1)) / past(1),
        ret_15m=(price - past(15)) / past(15),
        ret_60m=(price - past(60)) / past(60),
        volume_ratio=volume_ratio(bars),
        high_60m=max(b.high for b in window),
        low_60m=min(b.low for b in window),
    )
