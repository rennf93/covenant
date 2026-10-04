#!/usr/bin/env python
"""Kaggle runner: record wire-v2 training chunks (v5) on a Kaggle notebook.

Runs the vouch data-harvest replay against the IN-PROCESS stock laya head
(VOUCH_S1_PROVIDER=local; checkpoint downloads from HF), so no S1 server is
needed. Chunks are anchored to the CONTEXT CACHE's last timestamp, not to
run time: days-back per chunk is computed as (now - chunk_end)/86400 at run
time, so the recorded window matches the cache no matter when the notebook
runs.

Inputs (auto-discovered under /kaggle/input, or pass explicitly):
  - the code dataset: contains run_backtest.py + the vouch/ package
  - the context dataset: contains the context cache (.jsonl)

Outputs: /kaggle/working/decisions-<tag>/decisions.jsonl per chunk, plus
record-manifest.json. A chunk whose decisions.jsonl already exists is
skipped (resume: re-Run All after a 12h wall without losing chunks).

Usage in a notebook cell:
    !python kaggle_record_laya.py --smoke          # 2-day validation first
    !python kaggle_record_laya.py                  # full plan (chunk-days 27)
    !python kaggle_record_laya.py --resume         # skip finished chunks
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CHUNK_DAYS = 27
SMOKE_DAYS = 2
TOTAL_DAYS = 215  # Mar 1 -> Oct 2 2026; the cache defines the exact end


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def discover(name_hint: str, marker: str) -> Path:
    """Find the dir under /kaggle/input containing `marker`, at any depth
    (Kaggle may nest the uploaded folder inside the dataset)."""
    root = Path("/kaggle/input")
    if root.exists():
        hits = sorted(p.parent for p in root.glob(f"**/{marker}"))
        if hits:
            return hits[0]
    raise SystemExit(f"could not find {name_hint}: nothing under /kaggle/input contains {marker}")


def cache_span(cache: Path) -> tuple[int, int]:
    """(first_ts, last_ts) of a context cache; rows are sorted by ts."""
    with open(cache) as f:
        first = int(json.loads(f.readline())["ts"])
    with open(cache, "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - 4096))
        tail = f.read().decode().strip().splitlines()
    return first, int(json.loads(tail[-1])["ts"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code-root", default=None, help="dir containing run_backtest.py + vouch/")
    ap.add_argument(
        "--context-dir",
        default=None,
        help="dir of context cache .jsonl segment files; each chunk uses the "
        "one cache that fully covers its window (segments are split around "
        "Coinbase candle holes)",
    )
    ap.add_argument("--context", default=None, help="single-cache form of --context-dir")
    ap.add_argument("--out", default="/kaggle/working")
    ap.add_argument("--chunk-days", type=int, default=CHUNK_DAYS)
    ap.add_argument("--total-days", type=int, default=TOTAL_DAYS)
    ap.add_argument("--smoke", action="store_true", help="one 2-day chunk, fast validation")
    ap.add_argument("--resume", action="store_true", help="skip chunks whose output exists")
    ap.add_argument(
        "--cpu-warn-rows", type=float, default=6.0, help="pace alert threshold (rows/s)"
    )
    a = ap.parse_args()

    code_root = Path(a.code_root) if a.code_root else discover("code dataset", "run_backtest.py")
    ctx_dir = Path(a.context_dir) if a.context_dir else discover("context dataset", ".jsonl")
    caches = sorted(ctx_dir.glob("*.jsonl")) if ctx_dir.is_dir() else [Path(a.context)]
    if not caches or (len(caches) == 1 and not caches[0].exists()):
        raise SystemExit(f"no context caches found under {ctx_dir}")
    spans = {c: cache_span(c) for c in caches}
    out_root = Path(a.out)
    out_root.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(code_root))
    env = dict(os.environ)
    env["PYTHONPATH"] = str(code_root)
    env.setdefault("VOUCH_S1_PROVIDER", "local")
    env.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    env.setdefault("TOKENIZERS_PARALLELISM", "false")
    run_py = code_root / "run_backtest.py"
    if not run_py.exists():
        raise SystemExit(f"{run_py} missing")

    def pick_cache(ws: int, we: int) -> Path:
        for c, (lo, hi) in spans.items():
            if lo <= ws and we <= hi:
                return c
        raise SystemExit(
            f"no single context cache covers window [{ws}, {we}]; "
            f"available: {[(c.name, s) for c, s in spans.items()]}"
        )

    log(f"code={code_root} caches={[(c.name, s) for c, s in spans.items()]}")

    plan: list[tuple[str, int, int]] = []
    if a.smoke:
        # 2 days ending just inside the newest segment
        hi = max(hi for _, hi in spans.values())
        plan.append(("v5-smoke", hi - 3 * 86400, hi - 1 * 86400))
    else:
        # derive the plan from the cache segments themselves: each segment is
        # split into <=chunk-days blocks, so every chunk is covered by exactly
        # one cache (segments are already split around candle holes)
        for c, (lo, hi) in sorted(spans.items(), key=lambda kv: kv[1][0], reverse=True):
            cur_end = hi
            while cur_end - lo > a.chunk_days * 86400:
                seg_start = cur_end - a.chunk_days * 86400
                plan.append((f"v5-{c.stem}-{cur_end % 100000}", seg_start, cur_end))
                cur_end = seg_start
            if cur_end - lo >= 2 * 86400:
                plan.append((f"v5-{c.stem}-{cur_end % 100000}", lo, cur_end))
    log(f"plan: {[(t, (e - s) // 86400) for t, s, e in plan]}")

    # A chunk counts as done only if the MANIFEST says so (written after the
    # replay exits cleanly). decisions.jsonl existing is NOT proof: a 12h
    # wall can kill a chunk mid-write, and resuming onto that partial file
    # would silently drop the unrecorded days.
    done_tags: set[str] = set()
    manifest_path = out_root / "record-manifest.json"
    if a.resume and manifest_path.exists():
        try:
            for m in json.loads(manifest_path.read_text()):
                if m.get("status") == "recorded" and m.get("tag"):
                    done_tags.add(str(m["tag"]))
        except ValueError:
            log(f"record-manifest.json unreadable; starting the plan fresh")

    manifest: list[dict[str, object]] = []
    for tag, ws, we in plan:
        chunk_out = out_root / f"backtest-{tag}"
        decisions = chunk_out / "decisions.jsonl"
        if tag in done_tags:
            log(f"{tag}: recorded (per manifest), skipping")
            manifest.append({"tag": tag, "status": "skipped"})
            continue
        if a.resume and decisions.exists():
            # leftover from a wall kill mid-chunk: partial rows, not a
            # recording. Delete before re-recording (the replay writer
            # appends, so keeping it would duplicate rows).
            import shutil

            shutil.rmtree(chunk_out)
            log(f"{tag}: removed partial output from an interrupted run")
        days = (we - ws) / 86400
        context = pick_cache(ws, we)
        # days-back is relative to NOW at run time; the absolute window is
        # [ws, we], anchored to the cache segments, not to run time.
        days_back = max((time.time() - we) / 86400.0, 0.0)
        minutes = int(round((we - ws) / 60.0))
        log(
            f"{tag}: recording {days:.2f}d [{ws}, {we}] "
            f"via {context.name} (days_back={days_back:.4f})"
        )
        t0 = time.time()
        cmd = [
            sys.executable,
            str(run_py),
            "--minutes",
            str(minutes),
            "--days-back",
            f"{days_back:.6f}",
            "--context-file",
            str(context),
            "--data-harvest",
            "--no-s2",
            "--tag",
            tag,
            "--out-root",
            str(out_root),
        ]
        r = subprocess.run(cmd, env=env, cwd=str(code_root))
        if r.returncode != 0 or not decisions.exists():
            log(
                f"{tag}: FAILED (rc={r.returncode}) - stopping the plan; "
                "fix and re-run with --resume"
            )
            manifest.append({"tag": tag, "status": "failed"})
            (out_root / "record-manifest.json").write_text(json.dumps(manifest, indent=1))
            raise SystemExit(1)
        with open(decisions, "rb") as f:
            rows = sum(1 for _ in f)
        pace = rows / max(time.time() - t0, 1)
        log(f"{tag}: {rows} rows in {(time.time() - t0) / 60:.1f} min = {pace:.1f} rows/s")
        if pace < a.cpu_warn_rows:
            log(
                f"WARNING: pace {pace:.1f} rows/s - at this speed the remaining "
                f"chunks may not fit one 12h session; consider enabling GPU (T4) "
                f"for the laya head, or smaller --chunk-days"
            )
        manifest.append({"tag": tag, "rows": rows, "status": "recorded"})
        (out_root / "record-manifest.json").write_text(json.dumps(manifest, indent=1))

    recorded = [m for m in manifest if m["status"] == "recorded"]
    total = sum(int(m["rows"]) for m in recorded if isinstance(m["rows"], int))
    log(f"ALL DONE: {total} rows across {len(recorded)} chunks -> {out_root}")


if __name__ == "__main__":
    main()
