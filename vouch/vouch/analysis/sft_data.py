"""SFT dataset builder: turn logged decisions into fine-tuning pairs for laya.

Reads decisions.jsonl rows (which carry the market `state`, laya's raw
`probs`, and price), derives the CORRECT typed answer from what price
actually did after the decision, and emits JSONL in the shape the laya
Router consumes:

    {"state": {...}, "questions": {...}, "answers": {...}}

Two label policies:

- "barrier" (default, matches the strategy's actual economics): triple
  barrier over the forward window. Long wins if +take_profit (net of
  round-trip costs) is touched before -stop_loss; short wins mirrored;
  neither within `horizon` bars -> flat. The served brackets (stop 2%,
  take 4%, 60 bps/side fees) are the defaults, so the labels train exactly
  the decision the EV gate spends. This supersedes the old fixed-horizon
  threshold, which calibrated one horizon and traded another.
- "fixed": the legacy policy, fwd_ret >= +thr -> long, <= -thr -> short,
  else flat, over `horizon` decisions. Kept for comparability.

Answers cover the FULL served ENTRY question trio, not just `action`:
laya is asked action + conviction + enter_now at serve time, so it should
be trained on all three (a head trained on one question but served three
answers questions it was never optimized for). Ground truth only exists
for direction, so conviction/enter_now labels are DERIVED POLICY from the
same outcome (documented in barrier_label):

    barrier hit, move >= 2x net take  -> conviction 4 ("table pounder")
    barrier hit, move >= 1.5x         -> conviction 3
    barrier hit                       -> conviction 2
    flat, |fwd_ret| <= sl/2 (chop)    -> conviction 0
    flat otherwise                    -> conviction 1
    enter_now = (label != flat)

Usage:
    python run_sft_prep.py --glob "out/backtest-*/decisions.jsonl" \
        --label-policy barrier --out out/sft-laya.jsonl

Layering: analysis (imports analysis.load_decisions only; a leaf).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from vouch.analysis.analysis import load_decisions

ENTRY_QUESTIONS = {
    "action": {
        "type": "choice",
        "instructions": "Given `market` and `position`, what should the paper "
        "book do on this tick?",
        "criteria": {
            "long": "open a long on demonstrated strength",
            "flat": "hold or stay flat, no clean setup",
            "short": "open a short on demonstrated weakness",
        },
    },
    "conviction": {
        "type": "score",
        "instructions": "How strong is the setup in `market` right now?",
        "criteria": [
            "weak chop",
            "mild lean",
            "decent setup",
            "strong setup",
            "table pounder",
        ],
    },
    "enter_now": {
        "type": "noul",
        "instructions": "Given `position` and `recent_trades`, is opening a NEW "
        "position right now justified?",
        "criteria": {
            "false": "stand down: the trade history or open position argues "
            "against adding risk right now",
            "true": "nothing in the position or trade history argues against "
            "opening a new position now",
        },
    },
}


def barrier_label(
    prices: list[float],
    i: int,
    horizon: int,
    tp_pct: float,
    sl_pct: float,
    fee_pct: float,
) -> tuple[str, float]:
    """Triple barrier from index i (inclusive of entry price prices[i]).

    The barriers that can be FIRST touched are the two net-take barriers:
    up at entry*(1 + tp_net), down at entry*(1 - tp_net), where
    tp_net = tp_pct - fee_pct (a win must clear round-trip costs). The stop
    distance (sl_pct + fee_pct) is wider than the take distance, so a path
    to the stop always crosses the opposite take barrier first - stops have
    no first-touch case of their own; they show up as "neither take hit
    within the horizon" -> flat, which is the correct label for a bracketed
    trade that went nowhere clean. sl_pct shapes the conviction policy, not
    the barrier. Returns (label, forward return at horizon end or at the
    last bar).
    """
    entry = prices[i]
    if entry <= 0:
        return "flat", 0.0
    tp_net = max(tp_pct - fee_pct, 0.0)
    if tp_net <= 0.0:
        return "flat", 0.0
    up_take = entry * (1.0 + tp_net)
    dn_take = entry * (1.0 - tp_net)
    end = min(i + horizon, len(prices) - 1)
    for j in range(i + 1, end + 1):
        p = prices[j]
        if p >= up_take:
            return "long", prices[end] / entry - 1
        if p <= dn_take:
            return "short", prices[end] / entry - 1
    return "flat", prices[end] / entry - 1


def _conviction_for(label: str, fwd_ret: float, tp_net: float, sl_pct: float) -> int:
    """Derived conviction level (0-4) from the same outcome that labeled the
    row. Policy, not ground truth: documented in the module docstring."""
    if label == "flat":
        return 0 if abs(fwd_ret) <= sl_pct / 2 else 1
    move = fwd_ret if label == "long" else -fwd_ret
    if move >= 2.0 * tp_net:
        return 4
    if move >= 1.5 * tp_net:
        return 3
    return 2


def build_dataset(
    rows: list[dict],
    horizon: int,
    threshold: float,
    policy: str = "barrier",
    tp_pct: float = 0.04,
    sl_pct: float = 0.02,
    fee_pct: float = 0.012,
) -> list[dict]:
    # Pair rows and prices up front so barrier indices always align with the
    # price series even if a priceless row is dropped.
    paired = [(r, float(r["price"])) for r in rows if r.get("price") is not None]
    prices = [p for _, p in paired]
    tp_net = max(tp_pct - fee_pct, 0.0)
    out: list[dict] = []
    for i, (r, price) in enumerate(paired):
        if i + 1 >= len(prices):
            continue
        d = r.get("decision", {})
        if d.get("mode", "entry") != "entry" or not r.get("state"):
            continue
        if policy == "barrier":
            label, fwd = barrier_label(prices, i, horizon, tp_pct, sl_pct, fee_pct)
            conviction = _conviction_for(label, fwd, tp_net, sl_pct)
            label_meta = {
                "policy": "barrier",
                "tp_pct": tp_pct,
                "sl_pct": sl_pct,
                "fee_pct": fee_pct,
                "tp_net": round(tp_net, 6),
            }
        else:
            j = i + horizon
            if j >= len(prices):
                continue
            fwd = prices[j] / price - 1
            if fwd >= threshold:
                label = "long"
            elif fwd <= -threshold:
                label = "short"
            else:
                label = "flat"
            conviction = 2 if label != "flat" else (0 if abs(fwd) <= threshold / 2 else 1)
            label_meta = {
                "policy": "fixed",
                "horizon": horizon,
                "threshold": threshold,
            }
        answers = {
            "action": {"choice": label},
            "conviction": {"score": conviction},
            "enter_now": {"noul": 1.0 if label != "flat" else 0.0},
        }
        out.append(
            {
                "state": r["state"],
                "questions": ENTRY_QUESTIONS,
                "answers": answers,
                "meta": {
                    "ts": r.get("ts"),
                    "fwd_ret": round(fwd, 6),
                    **label_meta,
                    "source_probs": d.get("probs"),
                },
            }
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="build laya SFT data from decision logs")
    ap.add_argument(
        "--glob", action="append", required=True, help="decisions.jsonl glob; repeatable"
    )
    ap.add_argument("--horizon", type=int, default=60, help="barrier window, bars")
    ap.add_argument(
        "--threshold",
        type=float,
        default=0.004,
        help="fixed-policy only: absolute forward return that counts as a real move",
    )
    ap.add_argument(
        "--label-policy",
        choices=["barrier", "fixed"],
        default="barrier",
        help="barrier = triple barrier over the served brackets (default); "
        "fixed = legacy threshold over the horizon",
    )
    ap.add_argument("--tp", type=float, default=0.04, help="take profit pct (barrier)")
    ap.add_argument("--sl", type=float, default=0.02, help="stop loss pct (barrier)")
    ap.add_argument("--fee-pct", type=float, default=0.012, help="round-trip fee pct (barrier)")
    ap.add_argument("--out", type=str, default="out/sft-laya.jsonl")
    a = ap.parse_args()

    rows = load_decisions(a.glob)
    ds = build_dataset(
        rows,
        a.horizon,
        a.threshold,
        policy=a.label_policy,
        tp_pct=a.tp,
        sl_pct=a.sl,
        fee_pct=a.fee_pct,
    )
    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(json.dumps(x) + "\n" for x in ds))
    from collections import Counter

    labels = Counter(x["answers"]["action"]["choice"] for x in ds)
    convictions = Counter(x["answers"]["conviction"]["score"] for x in ds)
    print(
        json.dumps(
            {
                "rows_in": len(rows),
                "pairs_out": len(ds),
                "labels": dict(labels),
                "convictions": dict(sorted(convictions.items())),
                "policy": a.label_policy,
                "out": str(out_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
