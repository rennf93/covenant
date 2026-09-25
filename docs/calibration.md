# Calibrating the System-1 decision head

How vouch talks to laya, what is calibrated and what is not, and the path
from the stock checkpoints to a trading-trained one. Everything here is
measurable; nothing needs faith.

## The honest starting point

The laya engine is a bidirectional encoder (ModernBERT-class) + a decision
head that answers typed questions with softmax(logits / T) over per-
(question type, option count) fitted temperatures. It is deterministic,
not generative: no system prompt, no sampling.

The checkpoints shipped with the package were never trained on markets.
Auto-routing serves the base `english` checkpoint to vouch (verified live:
a strong momentum state scores long 0.076 / flat 0.835 / short 0.089 -
that distribution is a temperature artifact, not a market opinion; the
checkpoint's published zero-shot accuracy on typed decisions is 36% vs a
46% majority class). Consequences, in order of importance:

1. No threshold, gate, or prompt tweak can extract edge from probabilities
   that do not yet encode trading information. Calibrating the stock head
   makes it honestly, confidently useless. The path to profitability runs
   through fine-tuning on labeled market states (Stage 2 below).
2. What CAN be fixed in the talk layer today: provenance (know which
   checkpoint answered), label semantics (train on the decision you
   actually trade), horizon matching, and a calibrated entry gate with a
   measured expected edge. That is Stage 1, and it is shipped in this repo.

## What Stage 1 changed (in this repo)

- Checkpoint pin: `VOUCH_S1_CHECKPOINT` (empty = old auto-route behavior).
  The server backend sends `model` when pinned, logs the first reply's
  `routing.model`, and warns on any mismatch. Read this as the provenance
  of every probability you were ever shown.
- `answer_confidence` (max option probability - the quantity temperature
  scaling actually fits) is now logged on every decision as
  `action_conf`. The sibling entropy `confidence` is not calibrated;
  never gate on it.
- `enter_now` (noul) carries explicit criteria text: the english
  checkpoint has a documented label-following failure on bare
  true/false options.
- OpenRouter parity: missing/invalid choice answers now default to
  `flat`, never `long`, and parsed answers carry `answer_confidence`.
- Triple-barrier labels (default): an SFT/calibration row is long if
  price touches +take_profit net of round-trip fees before -take_profit,
  within the horizon; flat otherwise. This is exactly the decision the
  entry EV gate spends - the legacy ±0.4% 15-tick label calibrated one
  horizon and traded another.
- `run_calibration.py`: chronological train/validation split, logistic
  calibration on barrier labels over tradeable rows, ECE before/after,
  per-feature IC on the validation window, a gate simulation that picks
  the recommended `min_edge_pct`, and the break-even p_win implied by the
  bracket economics: (fees + stop) / (take + stop) = 0.533 at the served
  defaults (4% take / 2% stop / 60 bps per side). Any calibrated p_win
  below 0.533 cannot clear costs; a head that cannot exceed it in
  validation should not trade real size.
- The gate itself stays off (`min_edge_pct = 0`) until a calibration file
  exists AND you set an edge. The recommended value comes from the gate
  simulation on data the calibrator never saw.

## The loop

```bash
# 1. baseline truth: replay history through the exact live path.
#    --data-harvest force-closes positions after entry so every tick stays a
#    flat-state ENTRY decision. Without it the replay sits in one position
#    for days (verified: 136 entry decisions in 8800 ticks, exit pressure
#    never above ~0.40 vs the 0.85 gate) and starves the dataset. Harvest
#    mode PnL is meaningless; plain runs are for strategy evaluation.
VOUCH_S1_PROVIDER=server VOUCH_S1_URL=http://127.0.0.1:9989 \
  python run_backtest.py --minutes 43200 --no-s2 --data-harvest --tag calbase30d

# 2. measure the head as-is (IC, and with --fit the legacy calibration)
python run_analysis.py --glob "out/backtest-*/decisions.jsonl" --horizon 60

# 3. fit + validate the entry calibrator on barrier labels
python run_calibration.py --glob "out/backtest-calbase30d/decisions.jsonl" \
  --horizon 60 --tp 0.04 --sl 0.02 --fee-pct 0.012

# 4. bake the fine-tuning dataset (same labels as the calibrator)
python run_sft_prep.py --glob "out/backtest-calbase30d/decisions.jsonl" \
  --label-policy barrier --horizon 60 --out out/sft-laya-barrier.jsonl
```

Stage 1 result so far (30d SOL-USD replay, 2026-09-25): see
`out/calibration.json` and the commit message; the numbers are stamped
there so they cannot silently drift. If the validation ECE of the
calibrated map is not materially better than raw, and the gate
simulation does not find a positive edge with >= 30 trades, the honest
conclusion is "no tradeable signal yet" - which is exactly what Stage 2
exists to fix.

## Stage 2: fine-tune the decision head (touches the model)

The laya package ships the training path upstream (model card /
`research` notebooks: dataset -> train -> fit per-(type, size)
temperatures -> push). The venv copy is inference-only; run training from
the upstream repo with the colibri venv. Shape of the work:

1. Feed `out/sft-laya-barrier.jsonl` (rows of `{state, questions,
   answers}` in the exact served wire format, including all three ENTRY
   questions and the noul criteria text) into the upstream SFT trainer.
2. Refit per-(type, option-count) temperatures on held-out states -
   upstream measured ECE 0.466 -> 0.081 doing this on `english`; skip it
   and the probabilities stay smooth lies.
3. Serve the result as a NAMED checkpoint (do not overwrite the stock
   ones) and pin it: `VOUCH_S1_CHECKPOINT=<name>`. Confirm via the
   provenance log line that it, not `english`, is answering.
4. Re-run steps 1-3 of the loop against the fine-tuned head and compare:
   validation ECE, IC, gate simulation. Ship it to the live loop only if
   the validation edge survives; otherwise say so and keep the old head.

Rules that keep the data honest:

- Never mix OpenRouter decisions into calibration/SFT sets: it is a
  sampled chat model with clamped defaults - a different data-generating
  process. Use `local`/`server` rows only.
- Question wording and criteria order are positional inputs; changing
  either changes the model's answers. Byte-pin them (the trio lives in
  `vouch/analysis/sft_data.py` and must match `engine/system1.py`).
- Labels, gates, and costs must share one horizon and one fee model.
  The defaults in `run_calibration.py` / `run_sft_prep.py` match the
  served brackets; if you change the brackets in the UI, re-run both.
