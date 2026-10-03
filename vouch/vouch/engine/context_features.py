"""Context features for System-1: the information that can move 4h prices.

Perp funding, open interest, BTC lead, and higher-timeframe structure on the
same 1m grid as the bars (docs/v5-phase1-spec.md, the pinned interface).
Three public sources, all keyless:

- funding: Binance fapi /fapi/v1/fundingRate (8h entries, full history)
- open interest: data.binance.vision daily metrics zips (5m rows, full
  history; the openInterestHist REST API only serves 30 days and is NOT
  used). Verified live 2026-10-03: one CSV per day, columns create_time,
  sum_open_interest, sum_open_interest_value, ..., rows in arbitrary order
  on the complete 5m UTC grid; sum_open_interest_value is USD notional.
- BTC lead: Coinbase BTC-USD 1m candles via the existing fetch_candles.

The builder stitches everything onto the 1m grid and the CLI writes a
sorted JSONL cache; ContextSeries loads that cache for replays. Refusals
are loud (SystemExit, same policy as fetch_candles): a silent hole in the
context cache poisons SFT states exactly like a hole in the bars.

Layering: engine. May use venues (fetch_candles); must not import server.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from bisect import bisect_left, bisect_right
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import pstdev
from typing import Any

import httpx

from vouch.engine.candles import Bar, fetch_candles

FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
OI_METRICS_URL = (
    "https://data.binance.vision/data/futures/um/daily/metrics/{symbol}/{symbol}-metrics-{day}.zip"
)

FUNDING_LIMIT = 1000  # fapi page size
OI_PERIOD = 300  # metrics rows land every 5 minutes
MAX_MISSING_OI_ROWS = 3  # longer stretches refuse (pre-registered policy)
HOUR = 3600
DAY = 86400
FOUR_HOURS = 14400
EMA4H_SPAN = 30
VOL_REGIME_HOURS = 24  # std of the last 24 hourly returns
RANGE_LOOKBACK = 30 * DAY  # trailing window for range_pos_30d
WARMUP_LOOKBACK_DAYS = 30  # extra history the CLI fetches ahead of the window
CACHE_MAX_GAP_MINUTES = 15  # mirror of fetch_candles' gap_fill_max

MINUTE = 60


@dataclass(frozen=True)
class ContextPoint:
    ts: int  # unix seconds, aligned to the 1m bar open
    funding_rate_8h: float  # decimal per 8h, e.g. 0.0001 = 0.01%
    funding_24h_ago: float  # decimal
    oi_usd_m: float  # open interest, USD millions (sum_open_interest_value / 1e6)
    oi_chg_1h_pct: float  # percent vs 12 OI periods (5m) ago
    oi_chg_24h_pct: float  # percent vs 288 periods ago
    btc_ret_1m: float  # decimal
    btc_ret_60m: float
    btc_ret_24h: float
    ema4h_dist_pct: float  # (price / ema4h - 1) * 100, ema span 30 4h-bars
    range_pos_30d: float  # 0..1 within trailing 30d high-low
    vol_regime: str  # quiet | normal | elevated | extreme


@dataclass(frozen=True)
class FundingEntry:
    ts: int  # unix seconds (fundingTime ms floored; live entries run up to +4ms late)
    rate: float  # decimal per 8h


@dataclass(frozen=True)
class OiPoint:
    ts: int  # unix seconds, on the 5m grid
    oi_usd: float  # sum_open_interest_value, USD notional


def parse_funding(rows: list[dict[str, Any]]) -> list[FundingEntry]:
    """Funding API rows -> sorted entries, one per 8h boundary (deduped)."""
    entries: dict[int, FundingEntry] = {}
    for row in rows:
        entry = FundingEntry(ts=int(row["fundingTime"]) // 1000, rate=float(row["fundingRate"]))
        entries[entry.ts] = entry
    return [entries[ts] for ts in sorted(entries)]


def fetch_funding(
    client: httpx.Client, symbol: str = "SOLUSDT", start_ts: int = 0, end_ts: int | None = None
) -> list[FundingEntry]:
    """Funding history from start_ts (default: the symbol's first entry) to
    end_ts, paginated at 1000 per page. endTime is deliberately omitted: the
    live feed stamps entries up to a few ms past the boundary, and an exact
    endTime would silently drop the boundary entry itself."""
    end = int(end_ts) if end_ts else 0
    page: list[dict[str, Any]] = []
    cursor = start_ts * 1000
    seen: list[dict[str, Any]] = []
    while True:
        r = client.get(
            FUNDING_URL,
            params={"symbol": symbol, "startTime": cursor, "limit": FUNDING_LIMIT},
            timeout=30,
        )
        r.raise_for_status()
        page = r.json()
        if not page:
            break
        seen.extend(page)
        if len(page) < FUNDING_LIMIT:
            break
        last_ms = int(page[-1]["fundingTime"])
        if last_ms < cursor:  # defensive: no forward progress
            break
        cursor = last_ms + 1
    entries = parse_funding(seen)
    if end:
        entries = [e for e in entries if e.ts <= end]
    return entries


def parse_oi_metrics_csv(text: str) -> list[OiPoint]:
    """One daily metrics CSV (real files arrive in arbitrary row order) ->
    OiPoints sorted by ts. create_time is a naive UTC stamp; the verified
    grid is the complete 5m UTC day (288 rows)."""
    points: dict[int, float] = {}
    for row in csv.DictReader(io.StringIO(text)):
        ts = int(
            datetime.strptime(row["create_time"], "%Y-%m-%d %H:%M:%S")
            .replace(tzinfo=UTC)
            .timestamp()
        )
        points[ts] = float(row["sum_open_interest_value"])
    return [OiPoint(ts=ts, oi_usd=points[ts]) for ts in sorted(points)]


def fetch_oi_metrics_day(client: httpx.Client, symbol: str, day: str) -> list[OiPoint] | None:
    """Download one day's metrics zip; None when the day is not published yet
    (the bucket lags: today's zip appears only after the UTC day completes)."""
    r = client.get(OI_METRICS_URL.format(symbol=symbol, day=day), timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise SystemExit(f"oi metrics zip for {day}: expected exactly one CSV, got {names}")
        return parse_oi_metrics_csv(zf.read(names[0]).decode("utf-8"))


def fetch_oi_metrics(
    client: httpx.Client, symbol: str, start_ts: int, end_ts: int
) -> list[OiPoint]:
    """Stitch the daily zips covering [start_ts, end_ts] (UTC days). A day
    zip missing in the middle is tolerated here on purpose: the resulting
    5m gap trips the builder's refusal, which names the hole."""
    first_day = start_ts // DAY
    last_day = end_ts // DAY
    points: list[OiPoint] = []
    for day in range(first_day, last_day + 1):
        stamp = (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(days=day)).strftime("%Y-%m-%d")
        got = fetch_oi_metrics_day(client, symbol, stamp)
        if got:
            points.extend(got)
    return sorted(points, key=lambda p: p.ts)


def funding_rate_at(entries: list[FundingEntry], ts: int) -> float | None:
    """Forward-fill: latest entry with ts_entry <= ts; None before the first."""
    if not entries or entries[0].ts > ts:
        return None
    return entries[bisect_right([e.ts for e in entries], ts) - 1].rate


def funding_rate_24h_ago(entries: list[FundingEntry], ts: int) -> float | None:
    """The entry closest to ts - 86400, never interpolated. On an exact tie
    the earlier entry wins (deterministic)."""
    if not entries:
        return None
    target = ts - DAY
    stamps = [e.ts for e in entries]
    i = bisect_left(stamps, target)
    best: FundingEntry | None = None
    if i < len(entries):
        best = entries[i]
    if i > 0:
        prev = entries[i - 1]
        if best is None or abs(prev.ts - target) <= abs(best.ts - target):
            best = prev
    return best.rate if best else None


def vol_regime_for(std_pct: float) -> str:
    """Fixed thresholds v1 (pre-registered): revisit only via a new spec."""
    if std_pct < 0.4:
        return "quiet"
    if std_pct < 0.9:
        return "normal"
    if std_pct < 1.8:
        return "elevated"
    return "extreme"


def _require_grid(bars: list[Bar], name: str) -> None:
    if not bars:
        raise SystemExit(f"context build: empty {name}")
    for prev, cur in zip(bars, bars[1:], strict=False):
        if cur.ts - prev.ts != MINUTE:
            raise SystemExit(
                f"context build: {name} grid broken at ts {prev.ts} "
                f"(gap {cur.ts - prev.ts}s); refusing incomplete data"
            )


def _require_oi_grid(oi: list[OiPoint]) -> None:
    for prev, cur in zip(oi, oi[1:], strict=False):
        if cur.ts - prev.ts > (1 + MAX_MISSING_OI_ROWS) * OI_PERIOD:
            raise SystemExit(
                f"context build: open-interest 5m grid has a "
                f"{(cur.ts - prev.ts) // OI_PERIOD - 1}-row hole at ts {prev.ts}; refusing"
            )


def _bucket_closes(bars: list[Bar], span: int) -> tuple[list[int], list[float]]:
    """Close of each COMPLETED span-bucket (span in seconds, epoch-aligned).
    The bucket's close is its last 1m bar; buckets that end within the data
    have their true close even when the data starts mid-bucket."""
    first, last = bars[0].ts, bars[-1].ts
    keys: list[int] = []
    closes: list[float] = []
    for key in range(first // span, last // span + 1):
        close_ts = key * span + span - MINUTE
        if close_ts > last:
            break  # forming bucket: no close yet
        if close_ts < first:
            continue  # bucket ended before the data starts
        keys.append(key)
        closes.append(bars[(close_ts - first) // MINUTE].close)
    return keys, closes


def _ema_seed_sma(closes: list[float], span: int = EMA4H_SPAN) -> list[float | None]:
    """EMA with span `span`, seeded with the SMA of the first `span` closes;
    None while fewer than `span` closes exist (warm-up regime)."""
    alpha = 2.0 / (span + 1)
    out: list[float | None] = []
    running = 0.0
    ema = 0.0
    for i, close in enumerate(closes):
        if i < span - 1:
            running += close
            out.append(None)
        elif i == span - 1:
            running += close
            ema = running / span
            out.append(ema)
        else:
            ema = alpha * close + (1.0 - alpha) * ema
            out.append(ema)
    return out


class _RollingHighLow:
    """Max(high)/min(low) over the trailing lookback window of a complete 1m
    grid, via monotonic deques: O(1) per minute after an O(n) sweep."""

    def __init__(self, highs: list[float], lows: list[float]):
        self.highs = highs
        self.lows = lows
        self._max: deque[int] = deque()
        self._min: deque[int] = deque()
        self._pushed = 0

    def query(self, lo: int, hi: int) -> tuple[float, float] | None:
        """(max_high, min_low) over bar indices [lo, hi); bars must arrive in
        order (lo never moves backwards, hi advances one bar per minute)."""
        while self._pushed < hi:
            i = self._pushed
            while self._max and self.highs[self._max[-1]] <= self.highs[i]:
                self._max.pop()
            self._max.append(i)
            while self._min and self.lows[self._min[-1]] >= self.lows[i]:
                self._min.pop()
            self._min.append(i)
            self._pushed += 1
        while self._max and self._max[0] < lo:
            self._max.popleft()
        while self._min and self._min[0] < lo:
            self._min.popleft()
        if not self._max or not self._min:
            return None
        return self.highs[self._max[0]], self.lows[self._min[0]]


def build_context_points(
    sol_bars: list[Bar],
    btc_bars: list[Bar],
    funding: list[FundingEntry],
    oi: list[OiPoint],
    window_start: int,
    window_end: int,
) -> list[tuple[ContextPoint, bool]]:
    """One ContextPoint per minute of [window_start, window_end), each paired
    with its warm-up mark: True when any field's pinned lookback reached past
    the available data and the value was computed from whatever earlier data
    exists (shorter SMA, truncated range window, closest available baseline).

    Refuses loudly on broken bar grids, OI holes longer than 3 missing 5m
    rows, or a window not covered by the bars."""
    _require_grid(sol_bars, "SOL bars")
    _require_grid(btc_bars, "BTC bars")
    oi_rows = sorted(oi, key=lambda o: o.ts)
    _require_oi_grid(oi_rows)
    if window_start % MINUTE or window_end % MINUTE or window_end <= window_start:
        raise SystemExit(
            f"context build: bad window [{window_start}, {window_end}); minutes required"
        )
    first, last = sol_bars[0].ts, sol_bars[-1].ts
    if window_start < first or window_end - MINUTE > last:
        raise SystemExit(
            f"context build: window [{window_start}, {window_end}) not covered by "
            f"SOL bars [{first}, {last}]"
        )

    sol_close = [b.close for b in sol_bars]
    sol_high = [b.high for b in sol_bars]
    sol_low = [b.low for b in sol_bars]
    btc_first = btc_bars[0].ts
    btc_close = [b.close for b in btc_bars]

    entries = sorted(funding, key=lambda e: e.ts)
    oi_stamps = [o.ts for o in oi_rows]
    oi_usd = [o.oi_usd for o in oi_rows]

    bucket_keys, bucket_closes = _bucket_closes(sol_bars, FOUR_HOURS)
    ema = _ema_seed_sma(bucket_closes)
    hour_keys, hour_closes = _bucket_closes(sol_bars, HOUR)

    def sol_idx(ts: int) -> int:
        return (ts - first) // MINUTE

    def oi_at(query: int) -> float | None:
        i = bisect_right(oi_stamps, query) - 1
        return oi_usd[i] if i >= 0 else None

    def btc_ret(ts: int, back: int) -> tuple[float, bool]:
        past_ts = ts - back
        if past_ts < btc_first:
            return 0.0, False
        past = btc_close[(past_ts - btc_first) // MINUTE]
        return btc_close[(ts - btc_first) // MINUTE] / past - 1.0, True

    range_dq = _RollingHighLow(sol_high, sol_low)

    def ema4h(ts: int) -> tuple[float, bool]:
        """Distance vs the last COMPLETED 4h bar (no look-ahead). Before 30
        completed bars exist the SMA of whatever bars exist stands in."""
        i = bisect_left(bucket_keys, ts // FOUR_HOURS - 1)
        if i >= len(bucket_keys) or bucket_keys[i] != ts // FOUR_HOURS - 1:
            return 0.0, False  # no completed 4h bar at all yet
        seeded = ema[i]
        if seeded is not None:
            return (sol_close[sol_idx(ts)] / seeded - 1.0) * 100.0, True
        seed = sum(bucket_closes[: i + 1]) / (i + 1)
        return (sol_close[sol_idx(ts)] / seed - 1.0) * 100.0, False

    def vol_std_pct(ts: int) -> tuple[float, bool]:
        """Population std (percent) of the last 24 hourly returns; the last
        COMPLETED hour closes before the current hour begins."""
        cur_hour = ts // HOUR
        j = bisect_left(hour_keys, cur_hour - (VOL_REGIME_HOURS + 1))
        if (
            j + VOL_REGIME_HOURS < len(hour_keys)
            and hour_keys[j] == cur_hour - (VOL_REGIME_HOURS + 1)
            and hour_keys[j + VOL_REGIME_HOURS] == cur_hour - 1
        ):
            closes = hour_closes[j : j + VOL_REGIME_HOURS + 1]
            full = True
        else:
            k = bisect_left(hour_keys, cur_hour)
            closes = hour_closes[max(0, k - (VOL_REGIME_HOURS + 1)) : k]
            full = False
        if len(closes) < 2:
            return 0.0, False
        rets = [(closes[m + 1] / closes[m] - 1.0) * 100.0 for m in range(len(closes) - 1)]
        return pstdev(rets[-VOL_REGIME_HOURS:]), full

    points: list[tuple[ContextPoint, bool]] = []
    for ts in range(window_start, window_end, MINUTE):
        warmup = False
        price = sol_close[sol_idx(ts)]

        rate = funding_rate_at(entries, ts)
        if rate is None:
            rate, warmup = 0.0, True
        rate_24h = funding_rate_24h_ago(entries, ts)
        if rate_24h is None:
            rate_24h, warmup = 0.0, True

        oi_now = oi_at(ts)
        if oi_now is None:
            oi_now, warmup = 0.0, True
        oi_past_1h = oi_at(ts - HOUR)
        oi_past_24h = oi_at(ts - DAY)
        oi_chg_1h = 0.0
        if oi_past_1h is None:
            warmup = True
        else:
            oi_chg_1h = (oi_now / oi_past_1h - 1.0) * 100.0
        oi_chg_24h = 0.0
        if oi_past_24h is None:
            warmup = True
        else:
            oi_chg_24h = (oi_now / oi_past_24h - 1.0) * 100.0

        r1, ok1 = btc_ret(ts, MINUTE)
        r60, ok60 = btc_ret(ts, 60 * MINUTE)
        r24h, ok24h = btc_ret(ts, DAY)
        if not (ok1 and ok60 and ok24h):
            warmup = True

        dist, ema_full = ema4h(ts)
        if not ema_full:
            warmup = True

        lo_bound = max(first, ts - RANGE_LOOKBACK)
        window = range_dq.query((lo_bound - first) // MINUTE, sol_idx(ts))
        range_pos = 0.0
        if window is None or ts - RANGE_LOOKBACK < first:
            warmup = True  # less than the full trailing 30d exists
        if window is not None:
            high, low = window
            range_pos = min(max((price - low) / max(high - low, 1e-9), 0.0), 1.0)

        std_pct, vol_full = vol_std_pct(ts)
        if not vol_full:
            warmup = True

        points.append(
            (
                ContextPoint(
                    ts=ts,
                    funding_rate_8h=rate,
                    funding_24h_ago=rate_24h,
                    oi_usd_m=oi_now / 1e6,
                    oi_chg_1h_pct=oi_chg_1h,
                    oi_chg_24h_pct=oi_chg_24h,
                    btc_ret_1m=r1,
                    btc_ret_60m=r60,
                    btc_ret_24h=r24h,
                    ema4h_dist_pct=dist,
                    range_pos_30d=range_pos,
                    vol_regime=vol_regime_for(std_pct),
                ),
                warmup,
            )
        )
    return points


def _round8(x: float) -> float:
    return round(x, 8) + 0.0  # + 0.0 normalizes -0.0 so rebuilds stay byte-identical


def context_row(point: ContextPoint, warmup: bool = False) -> dict[str, Any]:
    """One cache row: pinned key order, round(x, 8) floats, warm-up mark only
    when set."""
    row: dict[str, Any] = {
        "ts": point.ts,
        "funding_rate_8h": _round8(point.funding_rate_8h),
        "funding_24h_ago": _round8(point.funding_24h_ago),
        "oi_usd_m": _round8(point.oi_usd_m),
        "oi_chg_1h_pct": _round8(point.oi_chg_1h_pct),
        "oi_chg_24h_pct": _round8(point.oi_chg_24h_pct),
        "btc_ret_1m": _round8(point.btc_ret_1m),
        "btc_ret_60m": _round8(point.btc_ret_60m),
        "btc_ret_24h": _round8(point.btc_ret_24h),
        "ema4h_dist_pct": _round8(point.ema4h_dist_pct),
        "range_pos_30d": _round8(point.range_pos_30d),
        "vol_regime": point.vol_regime,
    }
    if warmup:
        row["warmup"] = True
    return row


def write_jsonl(built: list[tuple[ContextPoint, bool]], path: str | Path) -> None:
    """Write the cache sorted by ts (the builder already emits in ts order)."""
    with open(path, "w") as f:
        for point, warmup in built:
            f.write(json.dumps(context_row(point, warmup)) + "\n")


class ContextSeries:
    """A loaded context cache. at() answers exactly one minute; a minute the
    cache does not cover is stale and returns None."""

    def __init__(self, points: list[ContextPoint]):
        self.points = points
        self._by_ts = {p.ts: p for p in points}

    @classmethod
    def load_jsonl(cls, path: str | Path) -> ContextSeries:
        points: list[ContextPoint] = []
        prev_ts: int | None = None
        for n, line in enumerate(Path(path).read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                row: dict[str, Any] = json.loads(line)
                point = ContextPoint(
                    ts=int(row["ts"]),
                    funding_rate_8h=float(row["funding_rate_8h"]),
                    funding_24h_ago=float(row["funding_24h_ago"]),
                    oi_usd_m=float(row["oi_usd_m"]),
                    oi_chg_1h_pct=float(row["oi_chg_1h_pct"]),
                    oi_chg_24h_pct=float(row["oi_chg_24h_pct"]),
                    btc_ret_1m=float(row["btc_ret_1m"]),
                    btc_ret_60m=float(row["btc_ret_60m"]),
                    btc_ret_24h=float(row["btc_ret_24h"]),
                    ema4h_dist_pct=float(row["ema4h_dist_pct"]),
                    range_pos_30d=float(row["range_pos_30d"]),
                    vol_regime=str(row["vol_regime"]),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise SystemExit(f"context cache {path}: malformed line {n}: {exc}") from exc
            if prev_ts is not None:
                if point.ts <= prev_ts:
                    raise SystemExit(f"context cache {path}: lines not sorted by ts (line {n})")
                if (point.ts - prev_ts) // MINUTE - 1 > CACHE_MAX_GAP_MINUTES:
                    raise SystemExit(
                        f"context cache {path}: {(point.ts - prev_ts) // MINUTE - 1}-minute "
                        f"hole after ts {prev_ts}; refusing incomplete cache"
                    )
            points.append(point)
            prev_ts = point.ts
        return cls(points)

    def at(self, ts: int) -> ContextPoint | None:
        """The row covering ts's minute (row.ts <= ts < row.ts + 60); None if
        the cache has no row there (stale)."""
        return self._by_ts.get((ts // MINUTE) * MINUTE)


__all__ = [
    "ContextPoint",
    "ContextSeries",
    "FundingEntry",
    "OiPoint",
    "WARMUP_LOOKBACK_DAYS",
    "build_context_points",
    "context_row",
    "fetch_candles",
    "fetch_funding",
    "fetch_oi_metrics",
    "fetch_oi_metrics_day",
    "funding_rate_24h_ago",
    "funding_rate_at",
    "parse_funding",
    "parse_oi_metrics_csv",
    "vol_regime_for",
    "write_jsonl",
]
