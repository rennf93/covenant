# Kaggle recording sessions (v5 wire-v2 chunks)

Goal: record the 7-month wire-v2 dataset on Kaggle (not the Mac). Two
datasets, one notebook, then a download. Everything else is automatic.

## One-time: create the two datasets

1. Code dataset: kaggle.com -> Datasets -> New Dataset -> drag the folder
   `covenant/kaggle/code-dataset` (from the covenant repo on your Mac) ->
   name it `covenant-v5-code` -> create.
2. Context dataset: same flow with the folder
   `covenant/kaggle/context-dataset` (two cache files, ~104 MB) -> name it
   `covenant-v5-context`.

## Recording

3. kaggle.com -> Code -> New Notebook.
4. Add Input: add BOTH datasets (covenant-v5-code + covenant-v5-context).
5. Settings: Internet ON. Accelerator: start with None (CPU) for the smoke;
   switch to GPU T4 for the full run if the smoke pace is under ~15 rows/s.
6. Paste this single cell and run (smoke first, ~20-40 min):

```python
!pip -q install laya==0.3.20 httpx
!python /kaggle/input/covenant-v5-code/kaggle_record_laya.py --smoke
```

7. Check the output: it should print one chunk recorded with `wire_version:
2` rows and a pace line. Verify: the cell below prints the first row's
context sentence.

```python
import json, glob
f = sorted(glob.glob("/kaggle/working/decisions-v5-smoke/decisions.jsonl"))[0]
r = json.loads(open(f).readline())
print(r["decision"]["wire_version"])
print(r["decision"]["wire"]["state"]["market"])
```

8. If the smoke looks right, run the FULL plan (same notebook or a new one;
   attach the same inputs). If you restarted the session, re-add inputs first:

```python
!python /kaggle/input/covenant-v5-code/kaggle_record_laya.py --resume
```

Chunks already recorded are skipped, so a 12h wall kill costs nothing:
save the version (it auto-saves), open a fresh session attached to that
version, copy /kaggle/working forward, and continue with --resume.

## After the run: get the data home

9. Download /kaggle/working (the notebook's Output tab, or the kernels/output
   API). Each chunk lands in /kaggle/working/backtest-<tag>/decisions.jsonl.
   Give the folder to ZCode - the bake (run_sft_prep), the v5 training
   dataset upload, and the fair test run from there.

## Honest limits

- The stock english head decides during recording (same methodology as the
  v3 campaign); its probabilities ride in each row's meta for the stock-side
  fair test.
- The window is Mar 1 -> Oct 2 2026, split around Coinbase candle holes
  (fail-loud policy; holes are never filled silently).
- Kaggle 12h wall + 30h/week quota apply. The plan skips finished chunks,
  so walls are free.
