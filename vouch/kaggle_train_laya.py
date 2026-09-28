"""Kaggle 2xT4 fine-tune of laya-vouch-v3. Paste this WHOLE file into ONE
Kaggle notebook code cell, attach the private dataset that contains
`sft-laya-wire-7mo.jsonl` + `run_train_laya.py`, enable GPU (T4) and
Internet, then Run All.

What it does, in order:
1. Locates the dataset files under /kaggle/input.
2. SELF-TEST (~5 min): a 600-row --amp training slice on the GPU. Fails
   fast and loudly if CUDA, fp16, or the data are wrong, before the long run.
3. MAIN RUN: full fine-tune (2 epochs, effective batch 32 = 16 states x
   accum 2, matching the Mac runs' hyperparams) with fp16 autocast.
   Kaggle kills sessions at 12h: the trainer writes resume.pt into
   /kaggle/working every 200 steps. If this session finds a resume.pt
   from a PRIOR session (attached via Add Input -> Your Work), it copies
   it in and continues from there automatically.
4. Prints the refit temperatures and the exact files to download.

Rerunning after a 12h cutoff: Save Version (Save & Run All is NOT needed
if the session died - use File -> Save Version -> Quick Save so the
/working outputs are kept), start a FRESH session of the same notebook,
Add Input -> Your Work -> that version, Run All. It resumes.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path

WORK = Path("/kaggle/working")
DATA_OUT = WORK / "laya-vouch-v3"


def find_one(pattern: str, what: str) -> Path:
    hits = [p for p in glob.glob(pattern, recursive=True) if os.path.isfile(p)]
    if not hits:
        raise SystemExit(
            f"ERROR: no {what} found under /kaggle/input. Attach your dataset "
            f"(Add Input) with {what} in it and rerun."
        )
    return Path(hits[0])


def find_optional(pattern: str) -> Path | None:
    """Like find_one but None is fine (used for the optional resume checkpoint)."""
    hits = [p for p in glob.glob(pattern, recursive=True) if os.path.isfile(p)]
    return Path(hits[0]) if hits else None


def main() -> None:
    assert os.path.exists("/kaggle/input"), "this script is for Kaggle notebooks only"
    import torch

    print(f"torch {torch.__version__} | cuda available: {torch.cuda.is_available()}")
    if not torch.cuda.is_available():
        raise SystemExit("ERROR: no GPU. Settings -> Accelerator -> GPU T4 x2, then rerun.")
    print(f"GPU: {torch.cuda.get_device_name(0)}")

    data = find_one("/kaggle/input/**/sft-laya-wire-7mo.jsonl", "sft-laya-wire-7mo.jsonl")
    script = find_one("/kaggle/input/**/run_train_laya.py", "run_train_laya.py")
    print(f"data:   {data}")
    print(f"script: {script}")

    env = os.environ.copy()
    env["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
    # persist the HF cache (stock checkpoint download) across sessions, and
    # defragment CUDA allocations (the T4 OOM'd on fragmentation)
    env["HF_HOME"] = str(WORK / "hf")
    env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

    def run(cmd: list[str]) -> None:
        print(f"\n$ {' '.join(str(c) for c in cmd)}", flush=True)
        r = subprocess.run([str(c) for c in cmd], env=env)
        if r.returncode != 0:
            raise SystemExit(f"ERROR: command failed with exit code {r.returncode}")

    # the laya package is not in the Kaggle image; internet is required
    run([sys.executable, "-m", "pip", "install", "-q", "laya==0.3.20"])

    # self-test: proves CUDA + fp16 autocast + data + script before the long run
    run(
        [
            sys.executable,
            script,
            "--data",
            data,
            "--out",
            WORK / "selftest",
            "--epochs",
            "1",
            "--max-rows",
            "600",
            "--batch-states",
            "8",
            "--grad-accum",
            "4",
            "--amp",
            "--save-every",
            "0",
            "--device",
            "cuda",
        ]
    )
    print("\n=== self-test PASSED: cuda + fp16 + data all good; starting the main run ===")

    # auto-resume: a prior session's output may be attached as input
    DATA_OUT.mkdir(parents=True, exist_ok=True)
    prior = find_optional("/kaggle/input/**/resume.pt")
    if prior:
        shutil.copy(prior, DATA_OUT / "resume.pt")
        print(f"resuming: copied {prior} -> {DATA_OUT / 'resume.pt'}")
    else:
        print("no prior resume.pt found - starting fresh")

    # Main run. Attempt 1 at batch 16 (fastest if it fits the T4). On OOM it
    # retries ONCE at the proven batch 8 with --resume: the trainer wrote
    # resume.pt every 200 steps, so at most ~30 min of work is redone. A
    # 12h session kill is handled OUTSIDE (Save Version + re-attach).
    main_base = [
        sys.executable,
        script,
        "--data",
        data,
        "--out",
        DATA_OUT,
        "--epochs",
        "2",
        "--amp",
        "--save-every",
        "200",
        "--resume",
        "--device",
        "cuda",
    ]
    try:
        run(main_base + ["--batch-states", "16", "--grad-accum", "2"])
    except SystemExit as e:
        print(
            f"\n=== main run attempt 1 failed ({e}); retrying at batch 8 with resume ===",
            flush=True,
        )
        run(main_base + ["--batch-states", "8", "--grad-accum", "4"])

    print("\n=== DONE. refit temperatures and files: ===")
    import json

    cfg = json.loads((DATA_OUT / "rl_agent_config.json").read_text())
    print(json.dumps(cfg.get("temperature_by_options", {}), indent=2))
    for p in sorted(DATA_OUT.iterdir()):
        print(f"  {p.name}  {p.stat().st_size / 1e6:.1f} MB")
    print(
        "\nNext: Save Version (Quick Save) to keep /kaggle/working, then download "
        "laya-vouch-v3/ (model.safetensors + rl_agent_config.json + tokenizer/) "
        "to ~/colibri/checkpoints/ on the Mac."
    )


if __name__ == "__main__":
    main()
