#!/usr/bin/env python
"""vouch backtester: real historical candles through the exact live path.

    .venv/bin/python run_backtest.py --minutes 4320 --epoch-len 120 --tag sept3d
    .venv/bin/python run_backtest.py --minutes 1440 --fee-bps 0 --tag maker

Outputs out/backtest-<tag>/: decisions.jsonl (full laya probabilities per
bar), trades.jsonl, s2-history.jsonl, candles.jsonl, summary.json. Feed
decisions.jsonl to run_analysis.py for the IC report and calibration fit.
"""

import argparse
import json
import os

from vouch.analysis import run_backtest
from vouch.config import load_settings

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="vouch backtest over real 1m candles")
    ap.add_argument("--product", type=str, default="SOL-USD")
    ap.add_argument("--minutes", type=int, default=1440, help="history length, 1m bars")
    ap.add_argument(
        "--epoch-len", type=int, default=60, help="bars between System-2 rewrites (0 = never)"
    )
    ap.add_argument("--cash", type=float, default=1000.0)
    ap.add_argument("--warmup", type=int, default=60, help="bars burned to fill 60m windows")
    ap.add_argument(
        "--fee-bps",
        type=float,
        default=None,
        help="per-side fee override (default: VOUCH_FEE_BPS env or 60)",
    )
    ap.add_argument("--tag", type=str, default=None)
    ap.add_argument("--no-s2", action="store_true", help="skip System-2 rewrites")
    ap.add_argument(
        "--data-harvest",
        action="store_true",
        help="force-close positions after entry so every tick stays a flat-state "
        "ENTRY decision: maximum state diversity for calibration/SFT; PnL is "
        "meaningless in this mode",
    )
    ap.add_argument(
        "--days-back",
        type=float,
        default=0,
        help="end the window N days in the past (0 = now; fractions allowed "
        "for splitting around data holes); record historical chunks with "
        "--data-harvest without overlapping newer ones",
    )
    a = ap.parse_args()

    if a.fee_bps is not None:
        os.environ["VOUCH_FEE_BPS"] = str(a.fee_bps)
        # Settings are read once per process (lru-cached); the override must
        # land before the first load, so drop any earlier cached snapshot.
        load_settings.cache_clear()
    summary = run_backtest(
        product=a.product,
        minutes=a.minutes,
        cash=a.cash,
        epoch_len=a.epoch_len,
        tag=a.tag,
        warmup=a.warmup,
        use_s2=not a.no_s2,
        data_harvest=a.data_harvest,
        days_back=a.days_back,
    )
    print(json.dumps(summary, indent=2))
