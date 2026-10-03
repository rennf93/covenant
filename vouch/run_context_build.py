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

# warmup-lookback fills up to this many dead minutes per stretch are filled
# (announced + warmup-flagged downstream); longer stretches refuse the build
WARMUP_GAP_FILL_MAX = 720


def candles_product(perp_symbol: str) -> str:
    """SOLUSDT (fapi / metrics naming) -> SOL-USD (Coinbase spot naming)."""
    base = perp_symbol.removesuffix("USDT")
    if base == perp_symbol:
        raise SystemExit(f"--product {perp_symbol}: expected a <BASE>USDT futures symbol")
    return f"{base}-USD"


def main() -> None:
    ap = argparse.ArgumentParser(description="build the System-1 context cache (JSONL)")
    ap.add_argument("--days-back", type=int, required=True, help="output window length, days")
    ap.add_argument(
        "--end-ts",
        type=int,
        default=None,
        help="window end, unix seconds (default: now). For building historical "
        "segments between data holes; the window is then [end-ts - days-back, end-ts]",
    )
    ap.add_argument("--product", default="SOLUSDT", help="perp symbol for funding + OI")
    ap.add_argument("--out", default="out/context-sol.jsonl")
    a = ap.parse_args()

    end = a.end_ts if a.end_ts else int(time.time()) // 60 * 60
    window_start = end - a.days_back * 86400
    fills: list[tuple[int, int]] = []

    def fetch_product(client: httpx.Client, product: str) -> list:
        """Warmup-lookback history allows bigger announced fills (it only
        feeds EMA/range/vol baselines and rows are warmup-flagged for it);
        the output window stays strict - it becomes training states."""
        out = fetch_candles(
            client,
            product=product,
            minutes=a.days_back * 1440,
            end_ts=end,
            require_complete=True,
        )
        back = fetch_candles(
            client,
            product=product,
            minutes=WARMUP_LOOKBACK_DAYS * 1440,
            end_ts=window_start,
            require_complete=True,
            gap_fill_max=WARMUP_GAP_FILL_MAX,
            fill_report=fills,
        )
        return back + out

    with httpx.Client() as client:
        sol = fetch_product(client, candles_product(a.product))
        btc = fetch_product(client, "BTC-USD")
        funding = fetch_funding(
            client,
            symbol=a.product,
            start_ts=window_start - (WARMUP_LOOKBACK_DAYS + 1) * 86400,
            end_ts=end,
        )
        oi = fetch_oi_metrics(
            client,
            symbol=a.product,
            start_ts=window_start - WARMUP_LOOKBACK_DAYS * 86400,
            end_ts=end,
        )

    built = build_context_points(sol, btc, funding, oi, window_start, end)
    if fills:
        span = WARMUP_LOOKBACK_DAYS * 86400
        built = [
            (p, w or any(fs < p.ts and fe > p.ts - span for fs, fe in fills)) for p, w in built
        ]
    write_jsonl(built, a.out)
    warmup_rows = sum(1 for _, warm in built if warm)
    print(
        json.dumps(
            {
                "out": a.out,
                "rows": len(built),
                "warmup_rows": warmup_rows,
                "warmup_fill_stretches": fills,
                "window_start": window_start,
                "end": end,
                "product": a.product,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
