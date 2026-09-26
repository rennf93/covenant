#!/usr/bin/env python
"""Stage 2 acceptance test: fine-tuned head vs stock head on the held-out window.

    .venv/bin/python run_holdout_eval.py \
        --stock out/backtest-calbase30d/decisions.jsonl \
        --ft out/backtest-calft-vouch/decisions.jsonl

Both decision logs are sliced to the never-trained holdout window (the last
10% of the SFT dataset, 2026-09-22T23:50Z .. 2026-09-25T23:43Z), labeled with
the same triple barrier as run_calibration.py, and compared. NOTHING here is
fitted on the window: ECE is the raw served enter_p against barrier outcomes,
and the gate simulation prices each trade at the head's own directional
probability (long/(long+short), recoverable exactly from the logged features).
The window has only ~50-60 non-flat barrier rows in 3 days - a fitted
calibrator would be pure noise, and the fine-tuned head's refit temperatures
ARE its calibration. The stock side needs no server: it is the recorded
stage-0 replay. The fine-tuned side is a fresh replay against :9988 over the
same window (run_backtest.py --data-harvest --minutes 5200 with
VOUCH_S1_URL=http://127.0.0.1:9988).

Ships only if the fine-tuned head has BOTH:
  1. lower raw enter_p ECE than stock on identical holdout states (0.16 was
     the stock head's measured full-window number - the anchor), and
  2. a viable gate edge (>= 30 gated trades, positive total PnL) that beats
     whatever stock shows on the same window.

Exit code 0 = PASS (ship), 1 = FAIL (do not ship). Layering: root CLI over
run_calibration (which layers over analysis + calibration leaves).
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from run_calibration import FEATURES, build_rows, ece, gate_simulation
from vouch.analysis.analysis import rank_ic

# Defaults = the measured 90/10 chronological split of out/sft-laya-barrier.jsonl.
DEFAULT_START = "2026-09-22T23:50:00+00:00"
DEFAULT_END = "2026-09-25T23:43:00+00:00"
ECE_ANCHOR = 0.16  # stock head's measured full-window raw ECE (stage 0)
EDGES = [0.0, 0.002, 0.004, 0.006, 0.008, 0.010, 0.012, 0.016, 0.020]


def slice_window(path: str, start: datetime, end: datetime) -> list[dict]:
    """All decision rows with start <= ts <= end (keep every mode: build_rows
    needs the full price trail, it picks the entry rows itself)."""
    out = []
    with Path(path).open() as f:
        for line in f:
            if not line.strip():
                continue
            r = json.loads(line)
            ts = datetime.fromisoformat(r["ts"])
            if start <= ts <= end:
                out.append(r)
    return out


class RawUp:
    """Pass-through p_up from the head's own probabilities, no fitting.

    The logged features pin the probs exactly: enter_p = 1 - flat and
    ls_spread = long - short, so long/(long+short) = 0.5 + ls_spread/(2*enter_p).
    A logistic fitted on this window's ~30-60 tradeable rows would be pure
    noise; the fine-tuned head's refit temperatures ARE its calibration, and
    this tests them raw. Same sim for stock = the fair baseline."""

    def p_up(self, conviction: float, enter_p: float, ls_spread: float) -> float:
        if enter_p <= 0:
            return 0.5
        return min(1.0, max(0.0, 0.5 + ls_spread / (2.0 * enter_p)))


def evaluate(rows: list[dict], label: str, horizon: int, tp: float, sl: float, fee: float) -> dict:
    """Barrier labels + raw-prob ECE/IC/gate-sim on already-sliced rows.
    Nothing here is fitted, so every window row is usable - no train/valid
    split to waste the scarce non-flat labels on."""
    res: dict = {"label": label, "rows": len(rows)}
    feats = build_rows(rows, horizon, tp, sl, fee)
    res["entry_rows"] = len(feats)
    if len(feats) < 200:
        res["error"] = f"only {len(feats)} entry rows in window; need >= 200 for a verdict"
        return res
    tradeable = [r for r in feats if r["label"] != "flat"]
    res["tradeable_rows"] = len(tradeable)
    res["ece_raw_enter_p"] = round(
        ece([(r["enter_p"], 1.0 if r["label"] == "long" else 0.0) for r in tradeable]), 4
    )
    res["valid_ic"] = {
        f: round(rank_ic([r[f] for r in feats], [r["fwd_ret"] for r in feats]), 4) for f in FEATURES
    }
    res["gate_simulation"] = gate_simulation(tradeable, RawUp(), tp, sl, fee, EDGES)
    viable = [s for s in res["gate_simulation"] if s["gated_trades"] >= 30 and s["total_pnl"] > 0]
    res["viable_edges"] = [
        (s["edge"], s["gated_trades"], s["total_pnl"])
        for s in sorted(viable, key=lambda s: -s["total_pnl"])
    ]
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description="stage 2 acceptance: fine-tuned vs stock on holdout")
    ap.add_argument("--stock", required=True, help="stock-head decisions.jsonl (stage-0 replay)")
    ap.add_argument(
        "--ft", required=True, help="fine-tuned-head decisions.jsonl (fresh :9988 replay)"
    )
    ap.add_argument("--holdout-start", default=DEFAULT_START)
    ap.add_argument("--holdout-end", default=DEFAULT_END)
    ap.add_argument("--horizon", type=int, default=60)
    ap.add_argument("--tp", type=float, default=0.04)
    ap.add_argument("--sl", type=float, default=0.02)
    ap.add_argument("--fee-pct", type=float, default=0.012)
    a = ap.parse_args()

    start = datetime.fromisoformat(a.holdout_start)
    end = datetime.fromisoformat(a.holdout_end)
    start = start if start.tzinfo else start.replace(tzinfo=UTC)
    end = end if end.tzinfo else end.replace(tzinfo=UTC)

    stock = evaluate(slice_window(a.stock, start, end), "stock", a.horizon, a.tp, a.sl, a.fee_pct)
    ft = evaluate(slice_window(a.ft, start, end), "fine-tuned", a.horizon, a.tp, a.sl, a.fee_pct)

    print(json.dumps({"stock": stock, "fine_tuned": ft}, indent=2, default=str))

    print("\n=== stage 2 acceptance ===")
    print(f"window {a.holdout_start} .. {a.holdout_end} (never trained on)")
    if "error" in stock or "error" in ft:
        for r in (stock, ft):
            if "error" in r:
                print(f"FAIL  {r['label']}: {r['error']}")
        raise SystemExit(1)
    stock_viable = stock["viable_edges"]
    ft_viable = ft["viable_edges"]
    s_ece, f_ece = stock["ece_raw_enter_p"], ft["ece_raw_enter_p"]
    print(f"stock     ECE raw {s_ece:.4f}  viable edges {stock_viable or 'none'}")
    print(f"fine-tune ECE raw {f_ece:.4f}  viable edges {ft_viable or 'none'}")

    ece_ok = f_ece < min(s_ece, ECE_ANCHOR)
    ft_best = max((p for _, _, p in ft_viable), default=0.0)
    stock_best = max((p for _, _, p in stock_viable), default=0.0)
    pnl_ok = ft_best > 0 and ft_best > stock_best
    verdict = "PASS" if (ece_ok and pnl_ok) else "FAIL"
    anchor_txt = "ok" if ece_ok else f"NOT beaten (anchor {ECE_ANCHOR:.2f})"
    print(f"ece: {anchor_txt}, gate pnl: {'ok' if pnl_ok else 'NOT beaten'} -> {verdict}")
    raise SystemExit(0 if verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
