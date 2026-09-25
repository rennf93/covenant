#!/usr/bin/env python
"""Build laya SFT data from decision logs. See vouch/analysis/sft_data.py.

    .venv/bin/python run_sft_prep.py --glob "out/backtest-*/decisions.jsonl" \
        --horizon 15 --threshold 0.004 --out out/sft-laya.jsonl
"""

from vouch.analysis.sft_data import main

if __name__ == "__main__":
    main()
