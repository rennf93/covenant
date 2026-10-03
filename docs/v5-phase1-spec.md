# v5 Phase 1 spec: context features + wire v2 (pinned interface)

Written 2026-10-03. Implements Track A of docs/v5-plan.md. This document is
the interface contract for parallel implementation: the exact dataclass, the
exact wire string, and the module boundaries are PINNED. Changes require
editing this file first.

## Goal

Give System-1 information that can actually move 4h prices: perp funding,
open interest, BTC lead, higher-timeframe structure, time-of-day. All five
are historically backfillable for the 7-month re-record window, so the
training states get them (news cannot be backfilled via RSS and stays in the
navigator lane; see docs/v5-plan.md Track B).

## Sources (verified 2026-10-03, re-verify live during build)

| Feature | Source | Cadence | History |
| --- | --- | --- | --- |
| Funding rate | Binance fapi GET /fapi/v1/fundingRate?symbol=SOLUSDT (public, no key) | 8h | full |
| Open interest | data.binance.vision futures/um/daily/metrics/SOLUSDT/ daily zips (public, no key; 5m rows: create_time, sum_open_interest, sum_open_interest_value, ratios) | 5m | full (openInterestHist API only holds 30 days - do NOT use it for history) |
| BTC candles | Coinbase BTC-USD via existing fetch_candles | 1m | full |
| HTF structure | computed locally from SOL 1m bars | derived | full |
| Time-of-day | computed locally (UTC) | derived | full |

No new dependencies: stdlib (zipfile, csv, json, math, datetime) + httpx.
Respect the house layering: this is engine-level code; it must not import
server; config via pyproject/env per existing conventions.

## Pinned interface (chunk A implements, chunk B consumes)

```python
# vouch/engine/context_features.py
@dataclass(frozen=True)
class ContextPoint:
    ts: int                 # unix seconds, aligned to the 1m bar open
    funding_rate_8h: float  # decimal per 8h, e.g. 0.0001 = 0.01%
    funding_24h_ago: float  # decimal
    oi_usd_m: float         # open interest, USD millions (sum_open_interest_value / 1e6)
    oi_chg_1h_pct: float    # percent vs 12 OI periods (5m) ago
    oi_chg_24h_pct: float   # percent vs 288 periods ago
    btc_ret_1m: float       # decimal
    btc_ret_60m: float
    btc_ret_24h: float
    ema4h_dist_pct: float   # (price / ema4h - 1) * 100, ema span 30 4h-bars
    range_pos_30d: float    # 0..1 within trailing 30d high-low
    vol_regime: str         # quiet | normal | elevated | extreme

class ContextSeries:
    @classmethod
    def load_jsonl(cls, path) -> ContextSeries  # the cache file format below
    def at(self, ts: int) -> ContextPoint | None  # row with row.ts <= ts < row.ts+60; None if stale
```

Staleness policy for `at()`: the row must be aligned to the queried minute.
The cache is built on the same 1m grid as the bars; a hole > 15 minutes in
the cache makes load_jsonl raise (same refuse-loudly policy as fetch_candles;
funding is stored forward-filled on the grid so its 8h cadence is not a hole).

### Derived-field definitions (pre-registered, fixed)

- funding forward-fill: latest funding entry with fundingTime <= ts; 24h_ago
  uses the entry closest to ts-86400 (never interpolate).
- OI 1h/24h changes: ((now / past) - 1) * 100 from the 5m metrics rows,
  forward-filled to the 1m grid; missing 5m rows <= 3 tolerated (carry last),
  longer stretches refuse the file.
- BTC returns: off Coinbase BTC-USD 1m closes via fetch_candles with
  require_complete policy identical to SOL (own gap-fill accounting).
- ema4h: resample SOL 1m closes to 4h bars (close of bar), EMA span 30,
  seeded with SMA of the first 30 bars; distance uses the LAST COMPLETED 4h
  bar (no look-ahead).
- range_pos_30d: (price - min(low_30d)) / (max(high_30d) - min(low_30d)),
  trailing 30d EXCLUDING the forming bar, clipped to [0,1].
- vol_regime: std of the last 24 hourly returns (from 1m closes resampled to
  1h, last COMPLETED hour), in percent: < 0.4 quiet, < 0.9 normal,
  < 1.8 elevated, else extreme. Fixed thresholds v1; revisit only via a new
  pre-registered spec version.

### Cache file format

`run_context_build.py --days-back N --product SOLUSDT --out out/context-sol.jsonl`
writes one JSON object per minute, sorted by ts, fixed rounding (floats via
round(x, 8), vol_regime as string):
{"ts": ..., "funding_rate_8h": ..., "funding_24h_ago": ..., "oi_usd_m": ...,
 "oi_chg_1h_pct": ..., "oi_chg_24h_pct": ..., "btc_ret_1m": ...,
 "btc_ret_60m": ..., "btc_ret_24h": ..., "ema4h_dist_pct": ...,
 "range_pos_30d": ..., "vol_regime": "..."}
Rebuilding for the same window MUST be byte-identical (determinism is tested).
The first 30d of the window have ema4h/range_pos_30d warm-up values computed
from whatever earlier data is fetchable; the CLI fetches 30d extra lookback
and marks warm-up rows in a "warmup": true field (consumers may keep them;
labels for those minutes exist regardless).

## Pinned wire v2 state string (chunk B implements)

In System1.decide(), when a ContextPoint is supplied for this tick, the
`market` state becomes EXACTLY (fixed field order, fixed precision):

```
SOL/USDC tick {i}. Price {price:.2f}. 1m return {ret_1m:+.2%}, 15m {ret_15m:+.2%}, 60m {ret_60m:+.2%}. Volume ratio {volume_ratio:.2f} vs average. 60m range {low_60m:.2f}-{high_60m:.2f}. Funding {funding_rate_8h*100:.4f}%/8h, 24h change {funding_rate_8h*100 - funding_24h_ago*100:+.4f}pp. Open interest ${oi_usd_m:.0f}M, 1h {oi_chg_1h_pct:+.1f}%, 24h {oi_chg_24h_pct:+.1f}%. BTC 1m {btc_ret_1m:+.2%}, 60m {btc_ret_60m:+.2%}, 24h {btc_ret_24h:+.2%}. Price {ema4h_dist_pct:+.1f}% vs 4h EMA, 30d range position {range_pos_30d*100:.0f}%, vol regime {vol_regime}. {Weekday} {HH:MM} UTC.
```

Rules:
- No context point -> the EXACT v1 string, byte-for-byte (sim mode and
  legacy replays must not change; a golden test pins both).
- decision rows gain "wire_version": 2 when the context block is present,
  else 1. run_sft_prep must assert a single wire_version per baked dataset
  and refuse a mix (the gold-bug lesson: format drift fails loudly).
- Token budget: measure the v2 string with the stock tokenizer; requirement
  <= 300 tokens for the market field (whole prompt stays well under 512).
  If over, STOP and renegotiate the spec, do not trim fields silently.

## Wiring (chunk B)

- Tick stays as-is; ContextPoint rides alongside (decide() gains an optional
  `context: ContextPoint | None = None` parameter; session/backtest callers
  pass it or None).
- run_backtest --data-harvest gains --context-file PATH; when provided, the
  replay attaches ContextSeries.at(bar.ts) per tick and records wire v2;
  without it, behavior is exactly today's (wire v1). Refuses a context file
  whose coverage does not span the replay window.
- Shadow/live path: context fetch is a Phase 2 concern (recorder first);
  chunk B does NOT wire live fetching.

## Chunk A (data acquisition) deliverables

1. vouch/engine/context_features.py: ContextPoint, ContextSeries, fetchers
   (funding, OI metrics zips, BTC candles), derived-field math, JSONL cache
   IO. Live-verify each endpoint shape with a curl probe BEFORE writing the
   parser; commit a trimmed real-data fixture (2 days, 1m grid) for tests.
2. vouch/run_context_build.py CLI per the format above.
3. Tests: endpoint parsers against fixtures; at() + staleness; determinism
   (build twice, byte-identical); OI gap refusal; funding forward-fill
   correctness across an 8h boundary; warm-up marking.
4. Update the calibration runbook (docs/calibration.md) with a short
   "context cache" section.

## Chunk B (engine wiring) deliverables

1. system1.py: v2 state string per the pinned format + wire_version tagging
   + decide(context=...) parameter (defaults preserve v1 behavior).
2. run_backtest --data-harvest --context-file wiring + coverage check.
3. run_sft_prep: wire_version assertion.
4. Tests: golden v1 string unchanged; golden v2 string from a fixture
   ContextPoint; sim-mode regression (v1); version-mix refusal in sft_prep;
   token budget measurement (marked skip when the tokenizer is unavailable).

## Acceptance for Phase 1

- make check / project gates green (ruff, mypy, tests, layering).
- A real 3-day smoke: run_context_build for the last 3 days, then a
  --data-harvest replay over the same window with --context-file; inspect
  10 sampled decisions for sane v2 strings; token count printed.
- No changes to labels, brackets, or acceptance criteria (h240/tp3/sl1.5/
  taker stays pre-registered for comparability).
