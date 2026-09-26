#!/usr/bin/env python
"""Fit the entry calibrator on barrier labels from replayed decisions.

    .venv/bin/python run_calibration.py --glob "out/backtest-*/decisions.jsonl"

What this fixes versus `run_analysis.py --fit`: the EV gate in System-1
spends P(up-barrier-first) - the probability that entering now, in the
chosen direction, clears its take-profit net of round-trip costs before
anything else. The legacy fit labeled rows by the SIGN of the raw horizon
return, which is a different question on a different horizon. Here the
labels come from the same triple barrier the SFT dataset uses (take 4%,
stop 2%, 60 bps/side by default - the served brackets), the split is
chronological (fit on the past, judged on the future), and the report
includes ECE before/after calibration plus the gate simulation that picks
the recommended min_edge_pct.

Writes out/calibration.json (System-1 picks it up automatically) and
prints the summary. Nothing here changes trading behavior until
rules.min_edge_pct > 0; the recommended value is in the report.

Layering: root CLI over analysis (leaf) + calibration (leaf).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from vouch.analysis.analysis import load_decisions, rank_ic
from vouch.analysis.sft_data import barrier_label
from vouch.calibration import Calibrator

FEATURES = ("conviction", "enter_p", "ls_spread")


def build_rows(rows: list[dict], horizon: int, tp: float, sl: float, fee: float) -> list[dict]:
    """One feature+label row per ENTRY decision with a price trail."""
    prices = [r.get("price") for r in rows]
    out: list[dict] = []
    for i, r in enumerate(rows):
        if not r.get("price") or i + 1 >= len(rows):
            continue
        d = r.get("decision", {})
        if d.get("mode", "entry") != "entry":
            continue
        probs = d.get("probs") or {}
        long_p = float(probs.get("long", 0.0))
        short_p = float(probs.get("short", 0.0))
        p_flat = float(probs.get("flat", 1.0))
        label, fwd = barrier_label(prices, i, horizon, tp, sl, fee)
        out.append(
            {
                "conviction": float(d.get("conviction", 0.0)),
                "enter_p": 1.0 - p_flat,
                "ls_spread": long_p - short_p,
                "label": label,
                "fwd_ret": fwd,
                "i": i,
            }
        )
    return out


def ece(pairs: list[tuple[float, int]], bins: int = 10) -> float:
    """Expected calibration error of P predictions against {0,1} outcomes:
    per bin, the weight of the bin times |mean predicted - mean observed|."""
    if not pairs:
        return 1.0
    sum_p = [0.0] * bins
    sum_y = [0.0] * bins
    count = [0] * bins
    for p, y in pairs:
        b = min(bins - 1, max(0, int(p * bins)))
        sum_p[b] += p
        sum_y[b] += y
        count[b] += 1
    total = len(pairs)
    err = 0.0
    for b in range(bins):
        if count[b]:
            err += (count[b] / total) * abs(sum_p[b] / count[b] - sum_y[b] / count[b])
    return err


def gate_simulation(
    rows: list[dict], cal: Calibrator | None, tp: float, sl: float, fee: float, edges: list[float]
) -> list[dict]:
    """Realized PnL per gated trade at each candidate edge, on ONE pass of the
    validation rows: a row trades when the calibrated EV clears the edge, the
    direction is the spread sign, and the outcome is the barrier label
    (+tp_net win, -(sl+fee) loss, or the raw horizon return minus fees for
    flat rows). First-order simulation; no path dependence between rows."""
    tp_net = max(tp - fee, 0.0)
    loss = sl + fee
    out = []
    for edge in edges:
        n = trades = 0
        pnl = 0.0
        for r in rows:
            p_up = cal.p_up(r["conviction"], r["enter_p"], r["ls_spread"]) if cal else 0.5
            direction = "long" if r["ls_spread"] >= 0 else "short"
            p_win = p_up if direction == "long" else 1.0 - p_up
            ev = p_win * tp - (1 - p_win) * sl - fee
            if ev < edge:
                continue
            n += 1
            if r["label"] == "flat":
                pnl += r["fwd_ret"] - fee
            elif r["label"] == direction:
                pnl += tp_net
                trades += 1
            else:
                pnl -= loss
        out.append(
            {
                "edge": edge,
                "gated_trades": n,
                "barrier_wins": trades,
                "total_pnl": round(pnl, 4),
                "pnl_per_trade": round(pnl / n, 5) if n else 0.0,
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="fit the entry calibrator on barrier labels")
    ap.add_argument(
        "--glob", action="append", required=True, help="decisions.jsonl glob; repeatable"
    )
    ap.add_argument("--horizon", type=int, default=60, help="barrier window, bars")
    ap.add_argument("--tp", type=float, default=0.04, help="take profit pct")
    ap.add_argument("--sl", type=float, default=0.02, help="stop loss pct")
    ap.add_argument("--fee-pct", type=float, default=0.012, help="round-trip fee pct")
    ap.add_argument("--train-frac", type=float, default=0.7, help="chronological train fraction")
    ap.add_argument("--out", type=str, default="out/calibration.json")
    a = ap.parse_args()

    raw = load_decisions(a.glob)
    rows = build_rows(raw, a.horizon, a.tp, a.sl, a.fee_pct)
    if len(rows) < 200:
        raise SystemExit(f"only {len(rows)} entry rows with prices; run a bigger backtest first")
    split = int(len(rows) * a.train_frac)
    train, valid = rows[:split], rows[split:]
    tradeable_train = [
        r | {"y": 1.0 if r["label"] == "long" else 0.0} for r in train if r["label"] != "flat"
    ]
    tradeable_valid = [r for r in valid if r["label"] != "flat"]

    cal = Calibrator.fit_labeled(
        tradeable_train,
        meta={
            "policy": "barrier",
            "horizon": a.horizon,
            "tp_pct": a.tp,
            "sl_pct": a.sl,
            "fee_pct": a.fee_pct,
            "train_rows": len(train),
            "valid_rows": len(valid),
            "gate_breakeven_p_win": round((a.fee_pct + a.sl) / (a.tp + a.sl), 4),
        },
    )
    if cal is None:
        raise SystemExit(
            "not enough tradeable (non-flat) rows to fit; "
            f"got {len(tradeable_train)} in the train split"
        )

    # Validation honesty: ECE of the raw pressure vs the calibrated P, IC of
    # each raw feature on the validation window, and the gate simulation.
    val_pairs_raw = [(r["enter_p"], 1.0 if r["label"] == "long" else 0.0) for r in tradeable_valid]
    val_pairs_cal = [
        (
            cal.p_up(r["conviction"], r["enter_p"], r["ls_spread"]),
            1.0 if r["label"] == "long" else 0.0,
        )
        for r in tradeable_valid
    ]
    ic = {}
    for f in FEATURES:
        ic[f] = round(rank_ic([r[f] for r in valid], [r["fwd_ret"] for r in valid]), 4)
    sim = gate_simulation(
        tradeable_valid,
        cal,
        a.tp,
        a.sl,
        a.fee_pct,
        edges=[0.0, 0.002, 0.004, 0.006, 0.008, 0.010, 0.012, 0.016, 0.020],
    )
    viable = [s for s in sim if s["gated_trades"] >= 30 and s["total_pnl"] > 0]
    recommended = max(viable, key=lambda s: s["total_pnl"]) if viable else None

    cal.meta.update(
        {
            "ece_raw_enter_p": round(ece(val_pairs_raw), 4),
            "ece_calibrated": round(ece(val_pairs_cal), 4),
            "valid_ic": ic,
            "gate_simulation": sim,
            "recommended_min_edge_pct": recommended["edge"] if recommended else None,
        }
    )
    cal.save(Path(a.out))
    print(
        json.dumps(
            {
                "rows": {"total": len(rows), "train": len(train), "valid": len(valid)},
                "tradeable": {"train": len(tradeable_train), "valid": len(tradeable_valid)},
                "ece_raw_enter_p": cal.meta["ece_raw_enter_p"],
                "ece_calibrated": cal.meta["ece_calibrated"],
                "valid_ic": ic,
                "gate_breakeven_p_win": cal.meta["gate_breakeven_p_win"],
                "gate_simulation": sim,
                "recommended_min_edge_pct": cal.meta["recommended_min_edge_pct"],
                "out": a.out,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
