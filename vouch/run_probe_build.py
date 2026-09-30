#!/usr/bin/env python
"""Build the judge probe set: labeled, compass-flagged moments from a replay.

Every row is one moment the v3 compass flagged as notable (its pressure,
direction spread, or conviction crossed a threshold), carried forward with:
- the SETUP a judge sees: state features + the compass's own calibrated reads
- the OUTCOME: triple-barrier label over the next `--horizon` bars (the same
  served brackets the EV gate spends), computed from the replay's price trail
- price context for rendering judge documents (recent path summary)

Flags are any-of the three compass signals, with a minimum gap between
probes so autocorrelated neighbors don't stack. The probe set is the
calibration instrument for judge layers (laya-multilingual document reader,
LLM): judges answer "is a directional entry justified?" per probe and are
scored against the label BEFORE any live use.

    .venv/bin/python run_probe_build.py \
        --replay out/backtest-calft-v4-merged/decisions.jsonl \
        --out out/probe-set-v1.jsonl

Layering: root CLI over analysis (leaf) + engine types.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from vouch.analysis.sft_data import barrier_label


def build_probes(
    rows: list[dict],
    horizon: int,
    tp_pct: float,
    sl_pct: float,
    fee_pct: float,
    enter_p_min: float,
    spread_min: float,
    conviction_min: int,
    volume_min: float,
    gap_minutes: int,
) -> tuple[list[dict], dict]:
    paired = [(r, float(r["price"])) for r in rows if r.get("price") is not None]
    prices = [p for _, p in paired]
    probes: list[dict] = []
    stats = {"seen": len(rows), "flagged": 0, "labeled": 0, "cooldown_skips": 0}
    last_flag_min = -(10**9)
    tp_net = max(tp_pct - fee_pct, 0.0)
    for i, (r, price) in enumerate(paired):
        if i + 1 >= len(prices):
            continue
        d = r.get("decision") or {}
        if d.get("mode", "entry") != "entry":
            continue
        probs = d.get("probs") or {}
        long_p = float(probs.get("long", 0.0))
        flat_p = float(probs.get("flat", 1.0))
        short_p = float(probs.get("short", 0.0))
        enter_p = 1.0 - flat_p
        spread = long_p - short_p
        conviction = int(d.get("conviction", 0))
        st = r.get("state") or {}
        vol = float(st.get("volume_ratio", 1.0))
        minute = i  # bars are 1m; index IS the minute index of the trail
        if minute - last_flag_min < gap_minutes:
            stats["cooldown_skips"] += 1
            continue
        reasons = []
        if enter_p >= enter_p_min:
            reasons.append("pressure")
        if abs(spread) >= spread_min:
            reasons.append("spread")
        if conviction >= conviction_min:
            reasons.append("conviction")
        if vol >= volume_min:
            reasons.append("volume")
        if not reasons:
            continue
        label, fwd = barrier_label(prices, i, horizon, tp_pct, sl_pct, fee_pct)
        stats["flagged"] += 1
        last_flag_min = minute
        # price context for rendering judge documents: the 60-bar path summary
        w0 = max(0, i - 59)
        window = prices[w0 : i + 1]
        probes.append(
            {
                "ts": r.get("ts"),
                "price": price,
                "state": st,
                "laya": {
                    "probs": {"long": long_p, "flat": flat_p, "short": short_p},
                    "conviction": conviction,
                    "enter_p": round(enter_p, 4),
                    "ls_spread": round(spread, 4),
                    "action_conf": d.get("action_conf"),
                },
                "flag_reasons": reasons,
                "label": label,
                "fwd_ret": round(fwd, 6),
                "tp_net": round(tp_net, 6),
                "context": {
                    "window_low": round(min(window), 4),
                    "window_high": round(max(window), 4),
                    "window_ret": round(window[-1] / window[0] - 1, 6),
                    "path_15": [round(p, 4) for p in prices[max(0, i - 14) : i + 1]],
                },
            }
        )
    stats["labeled"] = len(probes)
    return probes, stats


def main() -> None:
    ap = argparse.ArgumentParser(description="build the judge probe set from a v3 replay")
    ap.add_argument(
        "--replay",
        default="out/backtest-calft-v4-merged/decisions.jsonl",
        help="v3 replay decisions.jsonl (the compass's reads)",
    )
    ap.add_argument("--out", default="out/probe-set-v1.jsonl")
    ap.add_argument("--horizon", type=int, default=240, help="barrier window, bars")
    ap.add_argument("--tp", type=float, default=0.03)
    ap.add_argument("--sl", type=float, default=0.015)
    ap.add_argument("--fee-pct", type=float, default=0.012)
    ap.add_argument("--enter-p-min", type=float, default=0.6, help="flag: pressure above")
    ap.add_argument("--spread-min", type=float, default=0.4, help="flag: |long-short| above")
    ap.add_argument("--conviction-min", type=int, default=2, help="flag: conviction at/above")
    ap.add_argument("--volume-min", type=float, default=2.0, help="flag: volume ratio above")
    ap.add_argument(
        "--gap-minutes",
        type=int,
        default=60,
        help="minimum minutes between probes (cooldown against autocorrelation)",
    )
    a = ap.parse_args()

    rows = [json.loads(line) for line in Path(a.replay).read_text().splitlines() if line.strip()]
    probes, stats = build_probes(
        rows,
        a.horizon,
        a.tp,
        a.sl,
        a.fee_pct,
        a.enter_p_min,
        a.spread_min,
        a.conviction_min,
        a.volume_min,
        a.gap_minutes,
    )
    out = Path(a.out)
    with out.open("w") as f:
        for p in probes:
            f.write(json.dumps(p) + "\n")
    from collections import Counter

    labels = Counter(p["label"] for p in probes)
    reasons = Counter(r for p in probes for r in p["flag_reasons"])
    print(
        json.dumps(
            {
                "out": a.out,
                **stats,
                "labels": dict(labels),
                "flag_reasons": dict(reasons),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
