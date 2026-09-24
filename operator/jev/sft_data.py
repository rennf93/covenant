"""SFT dataset builder: turn logged decisions into fine-tuning pairs for laya.

Reads decisions.jsonl rows (which carry the market `state`, laya's raw
`probs`, and price), derives the CORRECT typed answer from what price
actually did over the next H decisions, and emits JSONL in the shape the
laya Router consumes:

    {"state": {...}, "questions": {...}, "answers": {"action": {"choice": ...}}}

Label policy over the forward horizon H:
    fwd_ret >= +thr  -> "long"     (strength worth acting on)
    fwd_ret <= -thr  -> "short"    (weakness worth acting on)
    otherwise        -> "flat"     (chop: the right answer was to sit out)

This is the bridge from "collecting data" to "fine-tuning laya", the
planned upgrade the README calls out. Only ENTRY-mode rows are emitted.

Usage:
    python run_sft_prep.py --glob "out/backtest-*/decisions.jsonl" \
        --horizon 15 --threshold 0.004 --out out/sft-laya.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analysis import load_decisions

ENTRY_QUESTIONS = {
    "action": {
        "type": "choice",
        "instructions": "Given `market` and `position`, what should the paper book do on this tick?",
        "criteria": {
            "long": "open a long on demonstrated strength",
            "flat": "hold or stay flat, no clean setup",
            "short": "open a short on demonstrated weakness",
        },
    },
}


def build_dataset(rows: list[dict], horizon: int, threshold: float) -> list[dict]:
    out: list[dict] = []
    for i, r in enumerate(rows):
        j = i + horizon
        if j >= len(rows) or not r.get("price") or not rows[j].get("price"):
            continue
        d = r.get("decision", {})
        if d.get("mode", "entry") != "entry" or not r.get("state"):
            continue
        fwd = rows[j]["price"] / r["price"] - 1
        if fwd >= threshold:
            label = "long"
        elif fwd <= -threshold:
            label = "short"
        else:
            label = "flat"
        out.append({
            "state": r["state"],
            "questions": ENTRY_QUESTIONS,
            "answers": {"action": {"choice": label}},
            "meta": {"ts": r.get("ts"), "fwd_ret": round(fwd, 6), "horizon": horizon,
                     "threshold": threshold, "source_probs": d.get("probs")},
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="build laya SFT data from decision logs")
    ap.add_argument("--glob", action="append", required=True,
                    help="decisions.jsonl glob; repeatable")
    ap.add_argument("--horizon", type=int, default=15)
    ap.add_argument("--threshold", type=float, default=0.004,
                    help="absolute forward return that counts as a real move")
    ap.add_argument("--out", type=str, default="out/sft-laya.jsonl")
    a = ap.parse_args()

    rows = load_decisions(a.glob)
    ds = build_dataset(rows, a.horizon, a.threshold)
    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(json.dumps(x) + "\n" for x in ds))
    from collections import Counter
    labels = Counter(x["answers"]["action"]["choice"] for x in ds)
    print(json.dumps({"rows_in": len(rows), "pairs_out": len(ds), "labels": dict(labels),
                      "out": str(out_path)}, indent=2))


if __name__ == "__main__":
    main()
