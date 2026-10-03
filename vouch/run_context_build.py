#!/usr/bin/env python
"""Build the System-1 context cache: funding, open interest, BTC lead, HTF
structure, one JSON row per minute (docs/v5-phase1-spec.md, pinned format).

Fetches --days-back of output window plus WARMUP_LOOKBACK_DAYS of extra
history so ema4h/range_pos_30d are computed from full lookback; rows whose
pinned lookback was not fetchable are marked "warmup": true in the cache.
Gaps refuse the build loudly (fetch_candles completeness policy for bars,
3-missing-5m-rows policy for open interest) - a silent hole here would
poison every SFT state built on top of it.

    .venv/bin/python run_context_build.py \
        --days-back 3 --product SOLUSDT --out out/context-sol.jsonl

Layering: root CLI over engine (candles + context_features).
"""

from __future__ import annotations

import argparse
import json
import time

import httpx

from vouch.engine.candles import fetch_candles
from vouch.engine.context_features import (
    WARMUP_LOOKBACK_DAYS,
    build_context_points,
    fetch_funding,
    fetch_oi_metrics,
    write_jsonl,
)


def candles_product(perp_symbol: str) -> str:
    """SOLUSDT (fapi / metrics naming) -> SOL-USD (Coinbase spot naming)."""
    base = perp_symbol.removesuffix("USDT")
    if base == perp_symbol:
        raise SystemExit(f"--product {perp_symbol}: expected a <BASE>USDT futures symbol")
    return f"{base}-USD"


def main() -> None:
    ap = argparse.ArgumentParser(description="build the System-1 context cache (JSONL)")
    ap.add_argument("--days-back", type=int, required=True, help="output window length, days")
    ap.add_argument("--product", default="SOLUSDT", help="perp symbol for funding + OI")
    ap.add_argument("--out", default="out/context-sol.jsonl")
    a = ap.parse_args()

    end = int(time.time()) // 60 * 60
    window_start = end - a.days_back * 86400
    lookback_minutes = (a.days_back + WARMUP_LOOKBACK_DAYS) * 1440

    with httpx.Client() as client:
        sol = fetch_candles(
            client,
            product=candles_product(a.product),
            minutes=lookback_minutes,
            end_ts=end,
            require_complete=True,
        )
        btc = fetch_candles(
            client, product="BTC-USD", minutes=lookback_minutes, end_ts=end, require_complete=True
        )
        funding = fetch_funding(
            client, symbol=a.product, start_ts=window_start - (WARMUP_LOOKBACK_DAYS + 1) * 86400,
            end_ts=end,
        )
        oi = fetch_oi_metrics(
            client, symbol=a.product, start_ts=window_start - WARMUP_LOOKBACK_DAYS * 86400,
            end_ts=end,
        )

    built = build_context_points(sol, btc, funding, oi, window_start, end)
    write_jsonl(built, a.out)
    warmup_rows = sum(1 for _, warm in built if warm)
    print(
        json.dumps(
            {
                "out": a.out,
                "rows": len(built),
                "warmup_rows": warmup_rows,
                "window_start": window_start,
                "end": end,
                "product": a.product,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
