#!/usr/bin/env python
"""vouch analysis: does laya predict anything? Measure before trading.

    .venv/bin/python run_analysis.py --glob "out/backtest-*/decisions.jsonl"
    .venv/bin/python run_analysis.py --glob "out/live-*/decisions.jsonl" \
        --horizon 15 --fit

Reports the Spearman IC of each laya output against forward returns and,
with --fit, writes out/calibration.json, which System-1 picks up to gate
entries on expected value over round-trip costs.
"""

import argparse
import json

from vouch.analysis import add_forward_returns, load_decisions, report

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="measure laya's predictive power")
    ap.add_argument("--glob", action="append", required=True,
                    help="decisions.jsonl glob; repeatable")
    ap.add_argument("--horizon", type=int, default=15,
                    help="decisions ahead for the forward return")
    ap.add_argument("--fit", action="store_true",
                    help="fit calibration and write out/calibration.json")
    a = ap.parse_args()

    rows = load_decisions(a.glob)
    add_forward_returns(rows, a.horizon)
    rep, cal = report(rows, horizon=a.horizon)
    if a.fit and cal:
        from pathlib import Path
        out = Path("out/calibration.json")
        cal.save(out)
        rep["calibration_saved"] = str(out)
    print(json.dumps(rep, indent=2))
