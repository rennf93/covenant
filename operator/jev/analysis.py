"""Analysis: does laya predict anything? Measure before trading.

Reads decisions.jsonl files (from live, shadow, or backtest runs), computes
forward returns over H decisions, and reports the rank information
coefficient (Spearman) of each laya output against those forward returns,
plus win-rate-style diagnostics by signal bucket. Also fits the
calibration model (jev/calibration.py) and writes out/calibration.json.

Interpretation: |IC| < 0.02 on a few thousand samples is noise. If every
feature sits near zero, no gate tuning will make this profitable; go
fine-tune laya or change the feature set.

Usage:
    python run_analysis.py --glob "out/backtest-*/decisions.jsonl" --horizon 15 --fit
"""

from __future__ import annotations

import glob as globmod
import json
from pathlib import Path

from .calibration import Calibrator


def load_decisions(patterns: list[str]) -> list[dict]:
    rows: list[dict] = []
    for pattern in patterns:
        for path in sorted(globmod.glob(pattern)):
            for line in Path(path).read_text().splitlines():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    rows.sort(key=lambda r: r.get("ts", ""))
    return rows


def add_forward_returns(rows: list[dict], horizon: int) -> None:
    for i, r in enumerate(rows):
        j = i + horizon
        if j < len(rows) and r.get("price") and rows[j].get("price"):
            r["fwd_ret"] = rows[j]["price"] / r["price"] - 1
        else:
            r["fwd_ret"] = None


def _rank(xs: list[float]) -> list[float]:
    idx = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    k = 0
    while k < len(idx):
        ties = [k]
        while ties[-1] + 1 < len(idx) and xs[idx[ties[-1] + 1]] == xs[idx[k]]:
            ties.append(ties[-1] + 1)
        avg = sum(ties) / len(ties)  # average rank for ties
        for t in ties:
            ranks[idx[t]] = avg
        k = ties[-1] + 1
    return ranks


def rank_ic(xs: list[float], ys: list[float]) -> float:
    """Spearman rank correlation; 0.0 when there is nothing to correlate."""
    if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
        return 0.0
    rx, ry = _rank(xs), _rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    if vx == 0 or vy == 0:
        return 0.0
    return cov / (vx * vy)


FEATURES = {
    "conviction": lambda d: d.get("conviction"),
    "enter_p": lambda d: d.get("enter_p"),
    "ls_spread": lambda d: (
        (d.get("probs", {}).get("long", 0.0) - d.get("probs", {}).get("short", 0.0))
        if d.get("probs") else None
    ),
    "p_flat": lambda d: d.get("probs", {}).get("flat") if d.get("probs") else None,
}


def report(rows: list[dict], horizon: int = 15) -> dict:
    usable = [r for r in rows if r.get("fwd_ret") is not None and r.get("decision", {}).get("mode", "entry") == "entry"]
    out: dict = {
        "decisions": len(rows),
        "entry_mode_with_forward": len(usable),
        "horizon": horizon,
        "features": {},
        "buckets": {},
    }
    for name, get in FEATURES.items():
        xs, ys = [], []
        for r in usable:
            v = get(r["decision"])
            if v is None:
                continue
            xs.append(float(v))
            ys.append(r["fwd_ret"])
        out["features"][name] = {
            "n": len(xs),
            "ic": round(rank_ic(xs, ys), 4) if xs else None,
        }
        # bucket diagnostics: top-vs-bottom tercile mean forward return
        if len(xs) >= 30:
            order = sorted(range(len(xs)), key=lambda i: xs[i])
            third = len(order) // 3
            lo = [ys[i] for i in order[:third]]
            hi = [ys[i] for i in order[-third:]]
            out["buckets"][name] = {
                "bottom_tercile_mean_fwd": round(sum(lo) / len(lo), 6),
                "top_tercile_mean_fwd": round(sum(hi) / len(hi), 6),
            }

    # calibration fit over the same rows
    fit_rows = []
    for r in usable:
        d = r["decision"]
        ls = (d.get("probs", {}).get("long", 0.0) - d.get("probs", {}).get("short", 0.0)) if d.get("probs") else 0.0
        fit_rows.append({"conviction": d.get("conviction", 0.0), "enter_p": d.get("enter_p", 0.0),
                         "ls_spread": ls, "fwd_ret": r["fwd_ret"]})
    cal = Calibrator.fit(fit_rows, horizon=horizon)
    out["calibration"] = (cal.meta if cal else None)
    return out, cal
