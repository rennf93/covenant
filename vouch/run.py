#!/usr/bin/env python
"""vouch runner. See README.md. Paper only: no exchange, no keys, no network
except the optional System-2 LLM endpoint you configure yourself."""

import argparse
import json

from vouch.engine.session import run_session

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="vouch: self-rewriting paper-trading simulation")
    ap.add_argument("--ticks", type=int, default=300, help="simulated 1m ticks")
    ap.add_argument("--epoch", type=int, default=50, help="ticks between System-2 rewrites")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--cash", type=float, default=1000.0)
    ap.add_argument("--out", type=str, default="out")
    a = ap.parse_args()
    summary = run_session(
        ticks=a.ticks, epoch_len=a.epoch, seed=a.seed, starting_cash=a.cash, out=a.out
    )
    print(json.dumps(summary, indent=2))
