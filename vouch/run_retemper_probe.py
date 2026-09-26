#!/usr/bin/env python
"""Re-temper the saved fine-tuned checkpoint's holdout logits offline.

Stage 2 acceptance FAILED with temperatures clamped at 5.0 (memorization
signature). This probe asks whether ANY post-hoc temperature - including
ones the laya package's [0.5, 5] clamp forbids, and a two-parameter refit
that scales the flat logit separately from the long/short pair - produces
an honest operating point WITHOUT retraining.

Two facts make this cheap and safe:
- The gate-sim p_up = long/(long+short) is INVARIANT to a common
  temperature, so a plain T sweep can only move the ECE half. The 2-param
  refit is what can move direction.
- The holdout rows of out/sft-laya-barrier.jsonl carry the barrier label
  (action gold) and fwd_ret, so the whole eval runs offline in minutes on
  the same never-trained window the acceptance harness used.

Verification gate: at the checkpoint's served temperatures the offline
numbers must reproduce the acceptance verdict before any sweep result is
trusted. The expected values below were measured on laya-vouch-v1
(2026-09-26, ECE 0.2961 / gate +0.184); RE-MEASURE THEM after any retrain.
NOTE: the probe must run against a checkpoint whose TRAINING data used the
engine wire format - vouch-v1 trained on the compact state rendering, so
its offline-vs-served verification CANNOT match and the probe output is
meaningless for it (that mismatch is how the format bug was found).

    .venv/bin/python run_retemper_probe.py
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

DATA = "out/sft-laya-barrier.jsonl"
CKPT = str(Path.home() / "colibri" / "checkpoints" / "laya-vouch-v1")
HOLDOUT_FRAC = 0.1
TP, SL, FEE = 0.04, 0.02, 0.012
EDGES = [0.0, 0.002, 0.004, 0.006, 0.008, 0.010, 0.012, 0.016, 0.020]
BATCH = 24

# served config: choice:3-5 / noul:2 / score:3-5 -> per-type temps
SERVED_T = {"choice": 5.0, "noul": 4.945675457326308, "score": 5.0}
QTYPE_BY_NAME = {"action": "choice", "conviction": "score", "enter_now": "noul"}


def ece(pairs: list[tuple[float, int]], bins: int = 10) -> float:
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


def softmax(
    zs: list[float], t: float, flat_bias: float = 0.0, ls_scale: float = 1.0
) -> list[float]:
    """Softmax with optional 2-param refit: ls_scale multiplies the long/short
    logits, flat_bias shifts the flat logit (positive = flatter-enter)."""
    adj = []
    for i, z in enumerate(zs):
        if i == 1:  # flat sits at index 1 in the served option order
            adj.append(z + flat_bias)
        else:
            adj.append(z * ls_scale)
    m = max(adj)
    ex = [pow(2.718281828459045, (a - m) / t) for a in adj]
    s = sum(ex)
    return [e / s for e in ex]


def main() -> None:
    ap = argparse.ArgumentParser(description="re-temper probe on the holdout window")
    ap.add_argument("--checkpoint", default=CKPT)
    ap.add_argument("--data", default=DATA)
    a = ap.parse_args()

    import torch
    from laya.agent import Agent

    from run_train_laya import gold_index

    rows = [
        json.loads(line) for line in Path(a.data).read_text().splitlines() if line.strip()
    ]
    split = int(len(rows) * (1.0 - HOLDOUT_FRAC))
    holdout = rows[split:]
    print(f"holdout rows: {len(holdout)}", flush=True)

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    agent = Agent(a.checkpoint, device=str(device))
    qids = list(holdout[0]["questions"].keys())
    internal = {qid: agent._to_internal(holdout[0]["questions"][qid]) for qid in qids}
    # the money question: 3 options, option 1 = flat
    action_qid = next(
        q
        for q in qids
        if len(agent._encode_state(holdout[0]["state"], [q], internal)[0]["markers"]) == 3
    )
    print(f"action qid: {action_qid!r}", flush=True)

    # forward the whole holdout once; keep the 3 raw action logits per row
    logits_all: list[list[float]] = []
    t0 = time.time()
    with torch.no_grad():
        for start in range(0, len(holdout), BATCH):
            part = holdout[start : start + BATCH]
            groups = [agent._encode_state(r["state"], [action_qid], internal) for r in part]
            pad = agent.tok.pad_token_id
            from laya.common import collate_items

            b = collate_items(groups, pad)
            logits, _ = agent.model(
                b["input_ids"].to(device),
                b["attention_mask"].to(device),
                b["marker_pos"].to(device),
                b["marker_mask"].to(device),
                b["qtype"].to(device),
            )
            for i in range(len(part)):
                logits_all.append([float(x) for x in logits[i, :3]])
    print(f"forwarded in {time.time() - t0:.0f}s", flush=True)

    # eval rows: barrier label from the action gold, fwd_ret from meta
    eval_rows = []
    for r, zs in zip(holdout, logits_all, strict=True):
        gi, _ = gold_index(action_qid, internal[action_qid], r["answers"])
        label = {0: "long", 1: "flat", 2: "short"}[gi]
        eval_rows.append({"zs": zs, "label": label, "fwd_ret": r["meta"]["fwd_ret"]})
    tradeable = [r for r in eval_rows if r["label"] != "flat"]
    print(f"tradeable rows: {len(tradeable)}", flush=True)

    def evaluate(t: float, flat_bias: float = 0.0, ls_scale: float = 1.0) -> dict:
        pairs = []
        sim_rows = []
        for r in eval_rows:
            ps = softmax(r["zs"], t, flat_bias, ls_scale)
            long_p, flat_p, short_p = ps
            enter_p = 1.0 - flat_p
            if r["label"] != "flat":
                pairs.append((enter_p, 1.0 if r["label"] == "long" else 0.0))
            sim_rows.append(
                {
                    "enter_p": enter_p,
                    "ls_spread": long_p - short_p,
                    "label": r["label"],
                    "fwd_ret": r["fwd_ret"],
                    "conviction": 0.0,
                }
            )
        e = ece(pairs)
        # gate sim on tradeable rows only, mirroring run_holdout_eval
        from run_calibration import gate_simulation

        sim = gate_simulation(
            [r for r in sim_rows if r["label"] != "flat"], None, TP, SL, FEE, EDGES
        )
        best = max(sim, key=lambda s: s["total_pnl"])
        return {
            "ece": round(e, 4),
            "best_edge": best["edge"],
            "best_trades": best["gated_trades"],
            "best_pnl": best["total_pnl"],
        }

    print("\n--- verification at served temps (expect ECE ~0.2961, pnl ~+0.184)", flush=True)
    ver = evaluate(SERVED_T["choice"])
    print(json.dumps(ver), flush=True)

    print("\n--- common-temperature sweep (flat_bias=0, ls_scale=1)", flush=True)
    print("T     ECE     best_edge  trades  pnl", flush=True)
    for t in [1.0, 1.76, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0]:
        r = evaluate(t)
        print(
            f"{t:<5} {r['ece']:<7} {r['best_edge']:<10} {r['best_trades']:<7} {r['best_pnl']}",
            flush=True,
        )

    print("\n--- 2-param refit grid: ls_scale x flat_bias at T=5", flush=True)
    print("ls_scale  flat_bias  ECE     best_edge  trades  pnl", flush=True)
    results = []
    for ls in [0.5, 1.0, 1.5, 2.0, 3.0]:
        for fb in [-1.0, -0.5, 0.0, 0.5, 1.0, 2.0]:
            r = evaluate(5.0, fb, ls)
            results.append((r, ls, fb))
            print(
                f"{ls:<9} {fb:<10} {r['ece']:<7} {r['best_edge']:<11} "
                f"{r['best_trades']:<7} {r['best_pnl']}",
                flush=True,
            )

    best = min(results, key=lambda x: x[0]["ece"])
    print(f"\nbest ECE: {best[0]} at ls_scale={best[1]} flat_bias={best[2]}", flush=True)
    best_pnl = max(results, key=lambda x: x[0]["best_pnl"])
    print(f"best pnl: {best_pnl[0]} at ls_scale={best_pnl[1]} flat_bias={best_pnl[2]}", flush=True)
    print(
        "\nstock bar on this window: ECE 0.3229 / +0.564 @ edge 0.008; "
        "T-invariance means a common-T sweep cannot move pnl - only the 2-param refit can.",
        flush=True,
    )


if __name__ == "__main__":
    main()
