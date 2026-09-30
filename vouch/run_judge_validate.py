#!/usr/bin/env python
"""Validate laya-multilingual as a document-reading entry judge on probes.

Renders each probe's setup as a compact market document (state features +
the compass's own calibrated reads + the recent price path), appends the
entry decision question at the END (the position the upstream long-context
bench validates), and runs laya-multilingual with max_len=8192.

Scored against the probe labels: accuracy on tradeable probes (long/short),
per-class breakdown, probability spread (saturation check: a judge stuck at
one answer is useless no matter its accuracy), and latency per read.

    .venv/bin/python run_judge_validate.py --probes out/probe-set-v1.jsonl \
        --limit 40

Layering: root CLI; reads probe files built by run_probe_build.py.
"""

from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path


def render_document(p: dict) -> dict:
    """The market document + the entry question (question goes LAST - the
    position the upstream long-context bench validates)."""
    st = p["state"]
    laya = p["laya"]
    path = " ".join(f"{x:.2f}" for x in p["context"]["path_15"])
    state = {
        "market": (
            f"SOL/USDC. Price {p['price']:.2f}. "
            f"1m return {st.get('ret_1m', 0):+.2%}, 15m {st.get('ret_15m', 0):+.2%}, "
            f"60m {st.get('ret_60m', 0):+.2%}. "
            f"Volume ratio {st.get('volume_ratio', 1.0):.2f} vs average. "
            f"60m range {p['context']['window_low']:.2f}-{p['context']['window_high']:.2f} "
            f"(position in range {st.get('range_pos_60m', 0.5):.2f})."
        ),
        "recent_path": f"last 15 one-minute closes: {path}",
        "compass_reading": (
            f"The calibrated compass flagged this moment ({', '.join(p['flag_reasons'])}). "
            f"Its readings: upward pressure {laya['probs']['long']:.2f}, downward pressure "
            f"{laya['probs']['short']:.2f}, no-direction pressure {laya['probs']['flat']:.2f}, "
            f"conviction {laya['conviction']}/4, entry pressure {laya['enter_p']:.2f}, "
            f"direction spread {laya['ls_spread']:+.2f}. The compass does NOT pick direction; "
            "it only reports how notable the moment is."
        ),
    }
    question = {
        "entry": {
            "type": "choice",
            "instructions": (
                "A trading compass flagged this moment as notable. Decide exactly one "
                "thing: should the book open a directional position NOW? Judge the setup "
                "on its merits; when uncertain or conditions look choppy, choose no_trade."
            ),
            "criteria": {
                "long": "open a long on demonstrated strength",
                "short": "open a short on demonstrated weakness",
                "no_trade": "no clean directional setup, stand aside",
            },
        }
    }
    return {"state": state, "questions": question}


def main() -> None:
    ap = argparse.ArgumentParser(description="validate laya-multilingual as entry judge")
    ap.add_argument("--probes", default="out/probe-set-v1.jsonl")
    ap.add_argument("--limit", type=int, default=40, help="probes to read (stratified)")
    ap.add_argument("--max-len", type=int, default=8192)
    ap.add_argument("--out", default="out/judge-validate-v1.json")
    a = ap.parse_args()

    import laya

    probes = [json.loads(line) for line in Path(a.probes).read_text().splitlines() if line.strip()]
    tradeable = [p for p in probes if p["label"] != "flat"]
    flat = [p for p in probes if p["label"] == "flat"]
    random.seed(7)
    half = a.limit // 2
    sample = random.sample(tradeable, min(half, len(tradeable))) + random.sample(
        flat, min(a.limit - half, len(flat))
    )
    print(
        f"probes: {len(sample)} ({sum(1 for p in sample if p['label'] != 'flat')} tradeable, "
        f"{sum(1 for p in sample if p['label'] == 'flat')} flat)"
    )

    agent = laya.load("convaiinnovations/laya", subfolder="multilingual", device="mps")
    agent.cfg["max_len"] = a.max_len
    agent.cfg["head_max_len"] = 512

    results = []
    for i, p in enumerate(sample):
        doc = render_document(p)
        t0 = time.perf_counter()
        r = agent.predict(doc["state"], doc["questions"], max_len=a.max_len)
        lat = time.perf_counter() - t0
        ans = r["answers"]["entry"]
        choice = ans["choice"]
        ok = choice == p["label"]
        results.append(
            {
                "ts": p["ts"],
                "gold": p["label"],
                "choice": choice,
                "correct": ok,
                "flag_reasons": p["flag_reasons"],
                "probs": ans.get("probabilities"),
                "latency_s": round(lat, 3),
                "input_tokens": r.get("usage", {}).get("input_tokens"),
            }
        )
        print(
            f"[{i + 1}/{len(sample)}] gold={p['label']:5} judge={choice:8} "
            f"{'OK' if ok else 'MISS'} {lat:.2f}s",
            flush=True,
        )

    tradeable_res = [r for r, p in zip(results, sample, strict=False) if p["label"] != "flat"]
    from collections import Counter

    summary = {
        "total": len(results),
        "accuracy_all": round(sum(r["correct"] for r in results) / max(1, len(results)), 3),
        "accuracy_tradeable": round(
            sum(r["correct"] for r in tradeable_res) / max(1, len(tradeable_res)), 3
        ),
        "choice_distribution": dict(Counter(r["choice"] for r in results)),
        "gold_distribution": dict(Counter(p["label"] for p in sample)),
        "latency_mean_s": round(sum(r["latency_s"] for r in results) / max(1, len(results)), 3),
        "max_input_tokens": max((r.get("input_tokens") or 0) for r in results),
    }
    print(json.dumps(summary, indent=2))
    Path(a.out).write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    print(f"wrote {a.out}")


if __name__ == "__main__":
    main()
