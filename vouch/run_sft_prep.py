#!/usr/bin/env python
"""Build laya SFT data from decision logs. See vouch/analysis/sft_data.py.

    .venv/bin/python run_sft_prep.py --glob "out/backtest-*/decisions.jsonl" \
        --label-policy barrier --horizon 60 --out out/sft-laya-barrier.jsonl

Default policy is "barrier": triple-barrier labels over the served
brackets (take 4%, stop 2%, 60 bps/side), so the head is trained on
exactly the decision the EV gate spends. --label-policy fixed keeps the
legacy 15-tick threshold behavior for comparison.
"""

from vouch.analysis.sft_data import main

if __name__ == "__main__":
    main()
