# Paste-ready model card for https://huggingface.co/rennf93/covenant-v3
# Paste the block below (everything after this comment block) into README.md
# on the HF edit page you already have open. Set license via the Metadata UI
# to whatever convaiinnovations/laya uses (check its model card first).

---
base_model: convaiinnovations/laya
tags:
- trading
- calibration
- time-series
- fintech
- research
library_name: transformers
---

# covenant-v3

A fine-tuned Laya System-1 decision head for SOL-USD trading: **honestly
calibrated probabilities, no demonstrated directional alpha.** Built as the
System-1 compass of the [Covenant](https://github.com/rennf93/covenant)
project (onchain proof-of-performance for trading strategies, Arbitrum Open
House Singapore buildathon).

## Model

- Base: [convaiinnovations/laya](https://huggingface.co/convaiinnovations/laya)
  (english checkpoint; ModernBERT-style encoder + typed-decision head).
- Fine-tune: full-checkpoint SFT, per-question inverse-sqrt class weighting,
  2 epochs, fp16 on a Kaggle T4, then per-(type,size) temperature refit by NLL
  on a chronological 10% holdout.

## Training data

7 months of SOL-USD 1m "wire-format" engine states (2026-03-01 to 2026-09-26),
299,139 examples, 67,100 tradeable (22.4%). Labels are triple-barrier: 240-bar
(~4h) horizon, +3% take / -1.5% stop, net of ~1.2% round-trip taker fees.
States are exactly the payloads the live engine serves (returns over 1-60m,
volume ratio, range position, position and recent-trade context).

## Honest evaluation (never-trained 20.8-day holdout, Sep 5-26 2026)

| Metric | covenant-v3 | stock laya |
| --- | --- | --- |
| ECE (enter probability, raw served) | **0.0943** | 0.1007 |
| Direction IC (ls_spread) | +0.039 | +0.045 |
| Conviction IC | -0.003 | -0.039 |
| Profitable entry policy at tested costs | none | none |

Acceptance was pre-registered: ship requires better calibration than stock
**and** positive gate-simulated PnL net of fees. The head passes calibration
and fails the PnL criterion, so it does not ship as a directional signal. We
publish the negative result: Covenant's product is truthful verification, and
that includes truthful model reporting. A blind frontier-LLM judge run on the
same 127 holdout moments scored 0.504 (chance), which localizes the gap to the
input features, not the model class. See the repo's `docs/v5-plan.md` for the
follow-up plan (funding, open interest, cross-asset and higher-timeframe
features).

## Intended use

Calibrated pressure/conviction/volume reads as System-1 input to a two-system
loop (calibrated compass + LLM navigator), and as a reference example of an
honestly evaluated trading head. Research use.

## Limitations

- Single asset (SOL-USD), single regime window (Mar-Sep 2026).
- Probabilities are calibrated but not profitable standalone at 3%/1.5%/60bps
  taker costs; breakeven win rate at those costs is 0.60.
- No directional alpha is claimed or demonstrated.
- Not financial advice.
