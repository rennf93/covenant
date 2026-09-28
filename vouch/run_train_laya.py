#!/usr/bin/env python
"""Fine-tune the laya decision head on barrier-labeled trading states.

    .venv/bin/python run_train_laya.py \
        --data out/sft-laya-barrier.jsonl \
        --out ~/colibri/checkpoints/laya-vouch-v1 \
        --epochs 2

Trains the FULL stock checkpoint (encoder + decision head) with class-
weighted cross-entropy on the SFT rows (the served ENTRY question trio),
then refits per-(question type, option-count) temperatures on a
chronological HOLDOUT split that training never saw, and saves everything
into --out as a complete local checkpoint directory (model.safetensors +
tokenizer/ + rl_agent_config.json). A resume checkpoint (resume.pt) is
written every --save-every optimizer steps; after an interruption, rerun
with --resume to continue from it.

Isolation: the stock checkpoint is only ever READ (via the HF cache); all
writes go to --out. Serve the result with run_serve_vouch_laya.py on its
own port; :9989 keeps serving stock.

Device: auto (mps on this Mac). fp32 training - MPS fp16 autocast is an
inference optimization and unstable for training. Acceptance test before
shipping any of this (docs/calibration.md): holdout ECE < 0.16 AND
positive gate-simulation PnL vs the stock head on identical states.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import time
from pathlib import Path

DEFAULT_OUT = str(Path.home() / "colibri" / "checkpoints" / "laya-vouch-v1")


def gold_index(qid: str, internal_q: dict, answers: dict) -> tuple[int, str]:
    """Gold option index for one question from its SFT answer + the internal
    (normalized) question. Index is positional over the option list."""
    a = answers.get(qid)
    if a is None:
        raise ValueError(f"row missing an answer for question {qid!r}")
    qtype = internal_q["t"]
    crit = internal_q["crit"]
    if qtype == "choice":
        keys = list(crit.keys())
        return keys.index(a["choice"]), a["choice"]
    if qtype == "score":
        level = int(a["score"])
        if not 0 <= level < len(crit):
            raise ValueError(f"score {level} out of range for {qid!r}")
        return level, str(level)
    # noul: option order is [false, true]; answer noul >= 0.5 means true
    return (1 if float(a["noul"]) >= 0.5 else 0), ("true" if float(a["noul"]) >= 0.5 else "false")


def class_weights(labels: list[int], power: float = 0.5) -> list[float]:
    """Inverse-frequency weighting softened by `power` (0 = no weighting,
    1 = full inverse frequency). Normalized to mean 1 so the loss scale
    stays comparable across datasets. Computed PER QUESTION: action is
    99% flat, conviction is 91% level-0, enter_now is 99% false - each
    question's imbalance is its own."""
    counts: dict[int, int] = {}
    for lab in labels:
        counts[lab] = counts.get(lab, 0) + 1
    raw = [((len(labels) / counts[lab]) ** power) for lab in labels]
    mean = sum(raw) / len(raw)
    return [r / mean for r in raw]


def fit_temperature(logits: list[list[float]], golds: list[int], lo: float, hi: float) -> float:
    """Maximum-likelihood temperature for one (qtype, option-count) bucket:
    minimize NLL of softmax(logits / T) against the gold index. Golden-
    section search over [lo, hi]; the NLL is unimodal in T for a fixed set
    of logits (standard result for the temperature-scaling family)."""
    import math

    def nll(t: float) -> float:
        total = 0.0
        for row, gold in zip(logits, golds, strict=True):
            zs = [z / t for z in row]
            m = max(zs)
            total += -(zs[gold] - m) + math.log(sum(math.exp(z - m) for z in zs))
        return total / len(logits)

    gr = (math.sqrt(5.0) - 1.0) / 2.0
    a, b = lo, hi
    c, d = b - gr * (b - a), a + gr * (b - a)
    fc, fd = nll(c), nll(d)
    for _ in range(40):
        if fc < fd:
            b, d, fd = d, c, fc
            c = b - gr * (b - a)
            fc = nll(c)
        else:
            a, c, fc = c, d, fd
            d = a + gr * (b - a)
            fd = nll(d)
    return (a + b) / 2.0


def main() -> None:
    ap = argparse.ArgumentParser(description="fine-tune laya on barrier-labeled trading states")
    ap.add_argument("--data", type=str, default="out/sft-laya-barrier.jsonl")
    ap.add_argument("--base", type=str, default="convaiinnovations/laya")
    ap.add_argument(
        "--base-subfolder",
        type=str,
        default="",
        help="checkpoint subfolder in the repo; EMPTY = repo root, which IS the "
        "english checkpoint. Use multilingual / typed-decisions for those.",
    )
    ap.add_argument("--out", type=str, default=DEFAULT_OUT)
    ap.add_argument("--epochs", type=int, default=2)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--batch-states", type=int, default=8, help="states per forward pass")
    ap.add_argument("--grad-accum", type=int, default=4)
    ap.add_argument("--holdout-frac", type=float, default=0.1)
    ap.add_argument("--weight-power", type=float, default=0.5)
    ap.add_argument("--max-rows", type=int, default=0, help="cap rows (smoke tests)")
    ap.add_argument(
        "--save-every",
        type=int,
        default=400,
        help="save a mid-run resume checkpoint every N optimizer steps (0 = never)",
    )
    ap.add_argument("--resume", action="store_true", help="continue from out/resume.pt if present")
    ap.add_argument(
        "--amp",
        action="store_true",
        help="fp16 mixed precision on CUDA (T4-class GPUs have fp16, not bf16, "
        "tensor cores); ignored on mps/cpu - fp16 autocast is unstable there",
    )
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--device", type=str, default=None, help="force mps/cuda/cpu")
    a = ap.parse_args()

    import torch
    from laya.agent import Agent
    from laya.common import QTYPES, collate_items, temp_bucket
    from safetensors.torch import save_file

    random.seed(a.seed)
    torch.manual_seed(a.seed)

    rows = [json.loads(line) for line in Path(a.data).read_text().splitlines() if line.strip()]
    if a.max_rows:
        rows = rows[: a.max_rows]
    if len(rows) < 500:
        raise SystemExit(f"only {len(rows)} rows in {a.data}; bake a bigger dataset first")
    split = int(len(rows) * (1.0 - a.holdout_frac))
    train_rows, holdout_rows = rows[:split], rows[split:]
    print(
        f"rows: {len(rows)} total | train {len(train_rows)} | holdout {len(holdout_rows)}",
        flush=True,
    )

    device = (
        torch.device(a.device)
        if a.device
        else torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    )
    print(f"device: {device}", flush=True)
    agent = Agent(a.base, device=str(device), subfolder=a.base_subfolder or None)
    qids = list(rows[0]["questions"].keys())
    internal = {qid: agent._to_internal(rows[0]["questions"][qid]) for qid in qids}
    for qid in qids:
        agent._check_question(qid, rows[0]["questions"][qid])

    # Pre-encode once: every row becomes len(qids) option-scored items with a
    # gold index and a PER-QUESTION class weight (each question's label
    # imbalance is its own: action 99% flat, conviction 91% level-0, ...).
    per_q_labels: dict[str, list[int]] = {qid: [] for qid in qids}
    examples: list[dict] = []
    t0 = time.time()
    for i, row in enumerate(train_rows):
        items = agent._encode_state(row["state"], qids, internal)
        for qid, it in zip(qids, items, strict=True):
            gi, _ = gold_index(qid, internal[qid], row["answers"])
            it["gold"] = gi
            per_q_labels[qid].append(gi)
        examples.append(
            {"items": items, "golds": dict.fromkeys(qids, 0), "weights": {qid: 1.0 for qid in qids}}
        )
        if i % 10000 == 0:
            print(f"  encoded {i}/{len(train_rows)} ({time.time() - t0:.0f}s)", flush=True)
    print(f"encoded in {time.time() - t0:.0f}s", flush=True)

    for qid in qids:
        weights = class_weights(per_q_labels[qid], power=a.weight_power)
        for ex, w in zip(examples, weights, strict=True):
            ex["weights"][qid] = w
        counts: dict[int, int] = {}
        for lab in per_q_labels[qid]:
            counts[lab] = counts.get(lab, 0) + 1
        print(f"  {qid}: labels {dict(sorted(counts.items()))}", flush=True)

    model = agent.model
    model.train()
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=0.01)
    total_steps = max(1, (len(examples) // a.batch_states) * a.epochs // a.grad_accum)
    warmup = max(1, int(total_steps * 0.03))

    def lr_at(step: int) -> float:
        if step < warmup:
            return a.lr * step / warmup
        t = (step - warmup) / max(1, total_steps - warmup)
        return a.lr * (0.1 + 0.9 * 0.5 * (1 + math.cos(math.pi * min(1.0, t))))

    pad = agent.tok.pad_token_id
    out_dir = Path(a.out).expanduser()
    resume_path = out_dir / "resume.pt"
    is_mps = device.type == "mps"
    # fp16 autocast on CUDA (T4s have fp16 tensor cores, no bf16): forward in
    # fp16, losses/backward in fp32 with a GradScaler. Ignored on mps/cpu,
    # where fp16 autocast is unstable for training.
    use_amp = bool(a.amp) and device.type == "cuda"
    scaler = torch.amp.GradScaler("cuda", enabled=use_amp)
    step = 0
    resume_state = None
    if a.resume and resume_path.exists():
        resume_state = torch.load(resume_path, map_location="cpu", weights_only=False)
        model.load_state_dict(resume_state["model"])
        opt.load_state_dict(resume_state["opt"])
        step = resume_state["step"]
        # keep only the schedule fields: holding the loaded tensors would pin
        # a second full copy of weights + optimizer state (measured +5GB)
        resume_state = {
            "epoch": resume_state["epoch"],
            "pos": resume_state["pos"],
            "order": resume_state["order"],
        }
        print(
            f"resumed from {resume_path}: epoch {resume_state['epoch']} "
            f"pos {resume_state['pos']} step {step}",
            flush=True,
        )

    def save_resume(epoch: int, pos: int, order: list[int]) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        torch.save(
            {
                "model": model.state_dict(),
                "opt": opt.state_dict(),
                "step": step,
                "epoch": epoch,
                "pos": pos,
                "order": order,
            },
            resume_path,
        )

    for epoch in range(1, a.epochs + 1):
        order = list(range(len(examples)))
        random.shuffle(order)
        begin = 0
        if resume_state is not None:
            if resume_state["epoch"] == epoch:
                order = resume_state["order"]
                begin = resume_state["pos"]
            elif epoch > resume_state["epoch"]:
                begin = 0
        t0 = time.time()
        running = 0.0
        win_batches = 0
        seen = 0
        opt.zero_grad()
        for start in range(begin, len(order), a.batch_states):
            batch = [examples[i] for i in order[start : start + a.batch_states]]
            groups = [ex["items"] for ex in batch]
            b = collate_items(groups, pad)
            with torch.autocast("cuda", dtype=torch.float16, enabled=use_amp):
                logits, _ = model(
                    b["input_ids"].to(device),
                    b["attention_mask"].to(device),
                    b["marker_pos"].to(device),
                    b["marker_mask"].to(device),
                    b["qtype"].to(device),
                )
            logits = logits.float()  # logsumexp/CE in fp32 even under autocast
            wsum = 0.0
            loss = logits.new_zeros(())
            row = 0
            for ex in batch:
                for j, qid in enumerate(qids):
                    k = len(ex["items"][j]["markers"])
                    gold = ex["golds"][qid]
                    w = ex["weights"][qid]
                    z = logits[row, :k]
                    ce = -(z[gold] - torch.logsumexp(z, 0))
                    loss = loss + w * ce
                    wsum += w
                    row += 1
            scaler.scale(loss / wsum / a.grad_accum).backward()
            running += loss.item() / wsum
            win_batches += 1
            seen += len(batch)
            if (start // a.batch_states) % a.grad_accum == a.grad_accum - 1:
                step += 1
                for g in opt.param_groups:
                    g["lr"] = lr_at(step)
                scaler.unscale_(opt)
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                scaler.step(opt)
                scaler.update()
                opt.zero_grad()
                if a.save_every and step % a.save_every == 0:
                    save_resume(epoch, start + a.batch_states, order)
            # Variable-length batches fragment the MPS allocator pool and it
            # never returns freed blocks to the OS: measured growth to 29GB
            # and swap death in 3h without this flush (2026-09-26).
            if win_batches >= 100:
                if is_mps:
                    torch.mps.empty_cache()
                avg = running / win_batches
                mem = torch.mps.current_allocated_memory() / 2**20 if is_mps else 0.0
                print(
                    f"epoch {epoch} rows {seen}/{len(examples)} loss {avg:.4f} "
                    f"lr {lr_at(step):.2e} mps {mem:.0f}MB ({time.time() - t0:.0f}s)",
                    flush=True,
                )
                running = 0.0
                win_batches = 0
        print(f"epoch {epoch} done in {time.time() - t0:.0f}s", flush=True)
        model.eval()
        model.train()

    # --- temperature refit on the holdout (training never saw these) -------
    print("refitting temperatures on the holdout...", flush=True)
    model.eval()
    buckets: dict[str, dict] = {}
    with torch.no_grad():
        for start in range(0, len(holdout_rows), a.batch_states):
            part = holdout_rows[start : start + a.batch_states]
            groups, metas = [], []
            for row in part:
                items = agent._encode_state(row["state"], qids, internal)
                for qid, it in zip(qids, items, strict=True):
                    gi, _ = gold_index(qid, internal[qid], row["answers"])
                    it["gold"] = gi
                groups.append(items)
                metas.append(items)
            b = collate_items(groups, pad)
            logits, _ = model(
                b["input_ids"].to(device),
                b["attention_mask"].to(device),
                b["marker_pos"].to(device),
                b["marker_mask"].to(device),
                b["qtype"].to(device),
            )
            row = 0
            for items in metas:
                for it in items:
                    k = len(it["markers"])
                    bucket = temp_bucket(it["qtype"], k)
                    acc = buckets.setdefault(bucket, {"logits": [], "golds": []})
                    acc["logits"].append([float(x) for x in logits[row, :k]])
                    acc["golds"].append(it["gold"])
                    row += 1

    refit = {}
    for bucket, acc in sorted(buckets.items()):
        qtype = QTYPES[bucket.split(":")[0]]
        t = fit_temperature(acc["logits"], acc["golds"], lo=0.05, hi=10.0)
        t = max(0.5, min(5.0, t))
        refit.setdefault("temperature", [1.0, 1.0, 1.0])[qtype] = t
        refit.setdefault("temperature_by_options", {})[bucket] = t
        print(f"  {bucket}: T = {t:.4f} ({len(acc['logits'])} holdout rows)", flush=True)

    # --- save a complete checkpoint directory ------------------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    sd = {k: v.detach().contiguous().cpu() for k, v in model.state_dict().items()}
    save_file(sd, str(out_dir / "model.safetensors"))
    tok_src = _find_tokenizer_dir(a.base, a.base_subfolder)
    tok_dst = out_dir / "tokenizer"
    if tok_dst.exists():
        shutil.rmtree(tok_dst)
    shutil.copytree(tok_src, tok_dst)
    cfg = dict(agent.cfg)
    cfg.update(refit)
    cfg["vouch"] = {
        "trained_from": f"{a.base}/{a.base_subfolder}",
        "data": a.data,
        "train_rows": len(train_rows),
        "holdout_rows": len(holdout_rows),
        "epochs": a.epochs,
        "lr": a.lr,
        "date": time.strftime("%Y-%m-%d"),
    }
    (out_dir / "rl_agent_config.json").write_text(json.dumps(cfg, indent=2))
    resume_path.unlink(missing_ok=True)
    print(f"saved checkpoint to {out_dir}", flush=True)
    print(
        "next: serve it with run_serve_vouch_laya.py --checkpoint "
        f"{out_dir} --port 9988, then re-run the calibration loop against :9988",
        flush=True,
    )


def _find_tokenizer_dir(base: str, subfolder: str) -> Path:
    """The stock checkpoint's tokenizer dir, from the resolved HF snapshot
    (or straight off a local base path when fine-tuning from one)."""
    local = Path(base)
    if local.is_dir():
        p = local / (subfolder or "") / "tokenizer"
        if p.is_dir():
            p = local / "tokenizer"
        if p.is_dir():
            return p
    from huggingface_hub import snapshot_download

    prefix = f"{subfolder}/" if subfolder else ""
    snap = snapshot_download(base, allow_patterns=[prefix + "tokenizer/*"])
    p = Path(snap) / (subfolder or "") / "tokenizer"
    if not p.is_dir():
        raise SystemExit(f"tokenizer dir not found under {snap}")
    return p


if __name__ == "__main__":
    main()
