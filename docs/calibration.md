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

Stage 1 baseline result (30d SOL-USD 1m replay, 2026-08-27 to 2026-09-25,
43,140 harvest ticks, measured 2026-09-26):

- **The stock head has no tradeable signal.** Validation IC of the raw
  outputs against forward returns: conviction -0.060, enter_p -0.027,
  ls_spread +0.006 - noise, two of three slightly inverse.
- **The fitted calibrator overfits and must not be used.** Only 300
  tradeable (non-flat) train rows exist, and validation ECE got WORSE
  after fitting: 0.160 raw -> 0.376 calibrated. `recommended_min_edge_pct`
  is null and the gate stays OFF (rules.min_edge_pct = 0); the gate
  simulation lost money at every edge setting.
- **Label economics, now measured:** with 60 bps/side fees the net take
  barrier sits at +/-2.8% within 60 bars (1 hour), and only ~1% of hours
  move that far: 42,694 flat / 258 long / 187 short. The honest answer to
  "should I trade this tick" is no 99% of the time. Consequences for
  Stage 2: the fine-tune needs class weighting or flat subsampling, and
  the tradeable-row count scales with horizon and the fee model (maker
  fees or longer horizons widen the barrier set - strategy decisions, not
  calibration decisions).
- This is the required baseline, not a failure: it quantifies that the
  stock head's probabilities carry no edge, so Stage 2 (fine-tune on
  these labeled states + temperature refit) is the only remaining path,
  and its acceptance test is now precise - beat ECE 0.16 and produce
  positive gate-simulation PnL on the held-out window, or it does not
  ship.

## Stage 2: fine-tune the decision head (touches the model)

Upstream facts (verified 2026-09-26; installed laya 0.3.20 = latest PyPI):

- The fine-tune loop exists and is concrete:
  `notebooks/laya_finetune_typed_decisions_2xT4_kaggle.ipynb` (dataset build
  -> RLCD training -> temperature fitting -> eval -> Hub push), ~4-5h on
  Kaggle's free 2xT4 for 4 epochs over ~30k questions; plus
  `docs/finetune_browser_agent.md`, a worked single-domain fine-tune on one
  16GB GPU - the closest analog to a trading-head specialisation. Our
  harvest replay at 30 days yields ~40k states: the right ballpark.
- Temperatures are refit per (question type, option-count bucket) on
  held-out data inside that loop. This step is not optional: shipped
  checkpoints are over-confident (english ECE 0.466 -> 0.081 after refit),
  and the english choice:3-5 bucket ships T=1.760, the smoothing that
  squashes vouch's entry probabilities toward uniform today. The
  multilingual checkpoint ships with NO fitted temperatures at all.
- Checkpoint budgets: english 512-token context (~320 left for state after
  options), typed-decisions 1024, multilingual up to 8192 (measured
  degradation past ~4k tokens in the long-context bench). Our trading
  states are small (~80 tokens); typed-decisions has the headroom if states
  grow.
- The model card's workaround for the noul label-following failure (#156)
  is stronger than our criteria-text patch: use a two-option CHOICE with
  neutral keys instead of noul. Do not switch mid-collection (it changes
  the wire contract and would split the dataset); adopt it for the
  fine-tuned head's question trio, pinned byte-identically at serve time.

Baseline from the hummin session (their coding-agent gate, same engine,
PR #5, finished 2026-09-26; their findings, our baseline):

- The checkpoint saturates around P 0.5-0.9 with identical scores across
  rubric rewrites. ROOT CAUSE FOUND: gate reads routed to the english
  checkpoint (512-token context) while their rubric + command payload was
  ~700 tokens - the rubric tail was silently truncated, so prompt edits
  scored identically to 4 decimals. Check state size against the context
  budget before trusting ANY score comparison.
- Multilingual with the full rubric saturates to P 0.87-0.96 (cannot
  discriminate); a compressed rubric INVERTED behavior (benign 0.92,
  destructive 0.27). The checkpoint does not reliably condition on
  instructions in this domain. Conclusion: prompt engineering has a
  ceiling; precision must come from deterministic structure.
- Their architecture, now shipped in PR #5: deterministic per-segment
  classifiers own the enumerable cases (read-only / additive-write fast
  paths, canonical-destructive hard paths), laya scores only the gray
  zone at a measured threshold (0.7), every verdict is audit-logged, and
  137 tests pin the fast paths.

Vouch mapping: rails = the deterministic layer; decisions.jsonl = the
audit trail; the calibrated gate + measured thresholds are Stage 1. The
truncation warning is checked, not assumed: a typical vouch ENTRY prompt
measures 140 tokens against english's 512 budget (272 with a 3x-bloated
state), so the rubric-truncation failure cannot bite at current state
sizes. typed-decisions doubles the headroom (1024) if states grow. If a
state format change moves scores for no apparent reason, check tokens
first.

Shape of the work:

1. Feed `out/sft-laya-barrier.jsonl` (rows of `{state, questions,
   answers}` in the exact served wire format, including all three ENTRY
   questions and the noul criteria text) into the upstream RLCD trainer.
2. Refit per-(type, option-count) temperatures on held-out states -
   upstream measured ECE 0.466 -> 0.081 on `english`; skip it and the
   probabilities stay smooth lies.
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
