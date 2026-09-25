# Vouch

The flagship strategy on the Covenant leaderboard: a two-model trading loop
where **laya** (fast, non-autoregressive decision engine) is System-1 making
per-tick trade decisions, and a local **qwen3.8-27b** llama.cpp server is
System-2, reviewing each epoch and rewriting System-1's tunable rules. Every
System-2 proposal must survive hard constraint rails enforced in code
(position cap, bracket bounds, cooldown, a 20% drawdown kill switch the
rewriter cannot touch). Every fill is attested to Covenant as a canonical
onchain receipt.

Runs seven ways:

```bash
# one-time: a venv with the runtime deps (laya is the System-1 engine)
python3.12 -m venv .venv
.venv/bin/python -m pip install laya httpx fastapi uvicorn cryptography pyjwt
# optional: enables the dashboard/shadow live trade websocket (everything
# falls back to polling without it)
.venv/bin/python -m pip install websockets

# 1. Simulation: synthetic market, fastest loop, deterministic
.venv/bin/python run.py --ticks 300 --epoch 50 --seed 7

# 2. Shadow: LIVE prices, PAPER money. The measuring stick.
.venv/bin/python run_shadow.py --minutes 480
#    (streams real trades over the public websocket by default; falls back
#     to spot polling; builds true 1m bars with real volume; --resume picks
#     a killed session back up from out/live-<seed>/state.json)

# 3. Backtest: months of REAL 1m candles through the exact live path, in minutes
.venv/bin/python run_backtest.py --minutes 4320 --epoch-len 120 --tag sept3d

# 4. Analysis: does laya predict anything? IC of every output vs forward returns
.venv/bin/python run_analysis.py \
    --glob "out/backtest-*/decisions.jsonl" --horizon 15 --fit

# 5. REAL: actual orders. Requires the env flip + keys, refuses otherwise.
VOUCH_VENUE=coinbase VOUCH_CB_KEY_NAME=... VOUCH_CB_PRIVATE_KEY=... \
  .venv/bin/python run_real.py --max-usd 10 --confirm-real

# 6. Register the strategy onchain (once; prints COVENANT_STRATEGY_ID)
.venv/bin/python run_register.py --name vouch-sol --metadata-uri ipfs://bafy...

# 7. Dashboard: local web UI to watch runs, edit settings, start/stop/restart
.venv/bin/python run_ui.py     # http://127.0.0.1:8787
```

Or run the whole stack with docker from the repo root:
`docker compose --profile agent up` (attested shadow mode; real-money mode
is deliberately impossible in the container).

## The dashboard (run_ui.py)

A local-only web UI (FastAPI, binds 127.0.0.1:8787) that lets you:

- **Watch**: active runs with live console output, every past session's
  summary/decisions/trades/System-2 rewrites, return percentages, kill-switch state.
- **Configure**: strategy rules (validated against RAILS server-side, so a
  value outside a rail is refused), fee bps, product, and per-mode run
  defaults. Saved settings are injected into spawned runners via
  `VOUCH_RULES` / `VOUCH_FEE_BPS` env, so they apply to real loops.
- **Control**: launch sim/shadow/backtest runs, stop and restart them.
- **Venues**: pick the venue per run from a registry (`vouch/vouch/venues/`).
  Three ship built in: paper (simulated fills), coinbase (live spot), and
  `arb-paper` (an honest Arbitrum DEX paper venue: live SOL/USDC pricing,
  simulated AMM taker fills). Adding a venue means implementing
  get_price + market_order + get_fill and one registry entry, and it then
  appears in the UI automatically.

Safety posture: the UI refuses to start real-money runs unless the SERVER
was started with `VOUCH_UI_ALLOW_REAL=1` AND the request confirms AND the
Coinbase keys are actually configured. A dashboard click is not a human
running `run_real.py` by hand; that gate stays. The API reads run logs and
spawns processes, so do not expose the port beyond localhost.

Run lifecycle vs server restarts: every runner is spawned with
`start_new_session=True`, i.e. in its own process group. A UI server
restart (or crash) therefore does NOT kill or orphan-hang running agents -
they keep running detached, still writing their console logs, and can be
re-adopted by watching `out/<run_id>/` (or resumed with `--resume`). The
in-memory RUNS registry is lost on restart, which is acceptable: Stop is
the only thing that ever sends a signal, and it goes to the whole process
group (`os.killpg`), so a stopped run leaves no grandchildren behind.

## System-1 providers (choose in the dashboard)

The decision head is pluggable; all three return the same typed answers,
so rails, logging, analysis, calibration, and SFT data collection are
provider-agnostic. Set in the dashboard Settings (or by hand via env):

- `local`: laya checkpoint in-process. Original mode, no network, slow
  cold start (~15s model load). Needs laya installed in the runner venv.
- `server`: a laya HTTP server (`laya.serve`) over `/v1/systemone`.
  Set the URL (e.g. `http://127.0.0.1:9989`) and, if the server was
  started with `LAYA_API_KEY`, the same value as the bearer key. A cold
  server answers `/health` instantly but the first predict can be slow;
  warm predicts are ~0.2s. This is the recommended mode: it decouples
  the bot from model loading entirely.
- `openrouter`: any OpenAI-compatible chat endpoint with an API key
  (default endpoint is OpenRouter; `VOUCH_S1_BASE_URL` overrides). The
  typed questions are serialized into one prompt and the JSON reply is
  parsed back into the same answers shape with neutral defaults for
  missing fields. Model id is required (e.g. `openai/gpt-4o-mini`).

Env equivalents: `VOUCH_S1_PROVIDER`, `VOUCH_S1_URL`, `VOUCH_S1_API_KEY`,
`VOUCH_S1_MODEL`, `VOUCH_S1_BASE_URL`. API keys entered in the dashboard are
stored in `out/ui/config.json` (plaintext, gitignored, localhost-only
server): do not point the dashboard at an untrusted network.

System-2 backend: `VOUCH_S2_BASE_URL=http://127.0.0.1:9990/v1`,
`VOUCH_S2_MODEL=qwen3.8-27b` (any OpenAI-compatible endpoint; see
.env.example, also editable in the dashboard Settings). Thinking is
disabled for the rewrite call; it turns a truncated 600-token think
block into a 14-second clean JSON answer. If the LLM is unreachable the
module latches the failure once and falls back to a built-in heuristic
rewriter, so a session never stalls or dies when the big model is down.

## Shadow-to-live flip

`run_real.py` refuses to run without `VOUCH_VENUE=coinbase` AND
`--confirm-real`. The venue adapter enforces a per-order exposure cap
(`--max-usd`, default 10) inside `vouch/vouch/venues/` - in code, not in
prompts. Real mode is long-only (spot), single position, started BY A
HUMAN by hand. It must never be launched by an autonomous loop.
Real mode books entry/exit from the venue's executed FILLS (price + fee
pulled back from Coinbase), so logged PnL is what the venue charged.

## The measurement loop (use it before trusting anything)

Every mode logs `decisions.jsonl` with laya's FULL probability vector,
the market state, and the veto reason per tick. Three tools consume it:

- **run_analysis.py** computes the Spearman IC of conviction, enter
  pressure, and the long/short spread against forward returns at H
  decisions. Rule of thumb: |IC| < 0.02 over a few thousand samples is
  noise. With `--fit` it also fits a logistic calibration (features ->
  P(up move)) and writes `out/calibration.json`.
- **run_sft_prep.py** turns the same logs into an SFT dataset for laya
  (labels derived from what price actually did next). This is the input
  to the planned fine-tune of the System-1 checkpoint.
- When `out/calibration.json` exists and `rules.min_edge_pct > 0`,
  System-1 gates every entry on calibrated expected value clearing
  round-trip costs, instead of a hand-tuned probability cutoff.

## Costs are the honest kind

The default fee model is 60 bps PER SIDE (small-account Coinbase
Advanced retail reality), not the 4 bps a market maker pays. Override
per run with `VOUCH_FEE_BPS` (all paper modes) or `--fee-bps` (backtest).
The `arb-paper` venue prices its AMM taker cost separately
(`VOUCH_ARB_FEE_BPS`, default 30: pool fee + price impact for a
retail-size swap). At 1.2% round trip on coinbase-paper, a 2%/4% bracket
needs a >53% win rate to break even; treat any backtest that ignores
this as fiction.

## What System-1 can do

- ENTRY mode (flat): long/short/flat choice + conviction + enter
  pressure; the tradeable signal is the DIP in P(flat) plus the
  long/short spread, not the raw argmax (zero-shot laya is flat-biased).
- EXIT mode (position open): hold/exit choice, gated by
  `rules.exit_pressure_min`. The bracket fires independently; laya exits
  early on demonstrated deterioration before the stop is hit.
- Rails: stop loss, take profit, breakeven floor (after
  `breakeven_trigger_pct` of favorable move the stop rises to entry),
  trailing stop (`trailing_stop_pct` giveback from peak), cooldown after
  a losing close (`cooldown_ticks`, actually wired to the rules now),
  20% drawdown kill switch. The trailing/breakeven/exit-gate knobs are
  System-2-adjustable, inside RAILS.

## What System-2 can do

Reviews a rolling window of recent epochs (not just the last one),
refuses to rewrite anything until at least 3 closed trades are in the
window, and is limited to at most 2 field changes per epoch so every
rewrite stays attributable. `s2-history.jsonl` keeps the full
applied/rejected record for later attribution analysis.

## Honest performance notes (2026-09-24)

- laya System-1: ~0.3-0.5 s per decision warm on the Mac (M-series),
  first call after model load ~15 s. HTTP server steady-state similar.
- qwen3.8-27b System-2 (Mac, llama.cpp, thinking off): ~14 s per rewrite,
  ~7 tok/s thinking-on (do not use thinking-on for this task).
- Trading so far: essentially flat. Sim 300 ticks: +0.08% on $1000, one
  closed trade (a stop). Shadow sessions on live SOL: +-0.03%, very few
  entries. NO edge demonstrated yet; the base laya checkpoint is
  near-chance zero-shot on typed decisions (its own README says so).
  The realistic near-term value is the loop, the rails, and the data
  collection, not the returns. Fine-tuning laya is the planned upgrade,
  and run_sft_prep.py now produces its training data.
- The decision on what to build next should be made by the IC numbers
  from run_analysis.py, not by feel.

## Covenant attestation (vouch/vouch/attest)

The `vouch.attest` package turns every booked fill into a canonical Covenant
receipt (the byte-exact Python mirror of `sdk/src/receipt.ts`: static ABI
encoding, keccak256 leaf hash, sign derived from position direction so a
bought base asset is positive and a sold one negative, including short
positions). Receipts land in a per-epoch append-only JSONL ledger
(`receipts-epoch-NNNN.jsonl` in the run's out dir) that is crash-safe:
each line is flushed and fsynced before the fill returns, duplicates and
backwards timestamps are refused, and a torn final line after a crash is
truncated on recovery.

Real mode writes the same receipts from the SAME bookkeeping seam: every
actual venue fill (side from the position direction, the real order id as
the venueOrderIdHash source, the venue's executed price and fee) becomes a
receipt exactly like a shadow fill. Attestation never places, sizes, or
loosens an order - the `--confirm-real` / `VOUCH_VENUE` interlock is
untouched.

Closing an epoch (at the same boundary where System-2 rewrites the rules,
and on every exit path - the kill switch included - so fsynced receipts
are never silently dropped) writes a self-contained evidence bundle
BEFORE any chain call, then optionally commits `commitEpoch(equity, netFlow,
tradesRoot, evidenceUri)` through a small Node bridge
(`vouch/vouch/attest/bridge.mjs`, viem) against the Stylus proxy, and
self-audits the first receipt onchain.

### Continuity: commits never poison the epoch index (vouch/vouch/attest/onchain.py)

Every commit outcome is recorded durably in `out/<run_id>/onchain.json`
(`{last_committed_epoch, retries, last_error, committed_at, finalized}`),
and the local epoch index can never advance past the last epoch that
actually committed onchain:

- A failed chain commit keeps the epoch open locally: receipts keep
  accumulating in the same ledger and the commit is retried at the next
  epoch boundary (the Merkle root is recomputed, so growth is fine). The
  failure is logged loudly; nothing is dropped and nothing stalls trading.
- On startup in onchain mode the agent queries the contract's epoch count
  for the strategy (bridge view op `getPerformance`) and resyncs:
  a fresh local ledger fast-forwards to the chain's index; a local ledger
  with history that diverges from the chain (epochs exist onchain that the
  ledger lacks, or the chain is behind the local record) is FROZEN with a
  loud error explaining the mismatch - no more commits until a human
  resolves it. Evidence bundles keep being written; trading continues.
- `--resume` restores session state (open position, peak equity, cooldown,
  epoch window, epoch index) from `out/<run_id>/state.json`, saved at each
  epoch boundary and on exit. Without `--resume` a runner starts fresh,
  exactly as before.

### Registration and finalization

Strategies must be registered once before committing (the id is
bond-keyed):

```bash
COVENANT_ATTEST=1 COVENANT_RPC_URL=... COVENANT_PRIVATE_KEY=... \
  COVENANT_CONTRACT_ADDRESS=... .venv/bin/python run_register.py \
  --name vouch-sol --metadata-uri ipfs://bafy...
# prints: export COVENANT_STRATEGY_ID=<id>
```

`--name`/`--metadata-uri` default to `COVENANT_STRATEGY_NAME` /
`COVENANT_STRATEGY_METADATA_URI`. Refuses to run without the full env trio:
registration is a bond-collateralizing chain write.

Finalization (the piece that makes onchain PnL actually accrue) is
permissionless, so the agent does not wait for an external finalizer: at
every epoch boundary, before committing, the loop finalizes any of its own
earlier checkpoints whose challenge window (read live via the bridge
`getConfig` op) has fully elapsed, and never finalizes the same epoch twice
(the set is persisted in `onchain.json`). `sdk/scripts/finalize-due.ts`
remains the catch-all sweeper for anything left over.

### Evidence bundles v2: signed, per-run, publishable

Each closed epoch writes the bundle to TWO places: `out/<run_id>/evidence/
epoch-N.json` (run-local audit copy) and `<evidence_dir>/<run_id>/
epoch-N.json` (the stable URI committed onchain, `evidence/<run_id>/
epoch-N.json` by default). Runs can no longer overwrite each other's
evidence - the shared, unsigned `evidence/epoch-N.json` of v1 is gone.

Bundle schema (v2, the web verifier must implement exactly this):

- Receipts, hashes and Merkle proofs are UNCHANGED (conformance-pinned to
  the SDK fixtures).
- `equityUsdg` / `netFlowUsdg` are integer base-unit STRINGS at 6 decimals
  with a `-` prefix allowed (`"100000000"` = 100 USDG).
- New fields: `signer` (0x address), `chainId` (int), `contract` (0x
  address), `signature` (0x, 65-byte rsv EIP-712 signature), `signedAt`
  (unix seconds).
- EIP-712 typed data: domain `{name: "Covenant Evidence", version: "1",
  chainId, verifyingContract}`; message
  `{protocol: "covenant-v1", strategyId, epochIndex, committedAt,
  equityUsdg (int256), netFlowUsdg (int256), tradesRoot (bytes32)}`.
- Optional `witness` annotation (additive, present only when
  `COVENANT_PYTH_PRICE_ID` is set): `{source: "pyth", priceId (0x + 32
  bytes), price (integer string at pyth scale), expo (int), confidence
  (integer string), publishTime (unix int)}` - the Pyth network price for
  the instrument at commit time, human price = price * 10^expo. Example:

  ```json
  "witness": {
    "source": "pyth",
    "priceId": "0xef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b121",
    "price": "14732000000",
    "expo": -8,
    "confidence": "120000",
    "publishTime": 1758800000
  }
  ```

What is signed, honestly: the signature covers exactly the typed message
above (who, which epoch, what equity/flow, which trades root) - it is made
by the operator key through the bridge (`viem signTypedData`; no new Python
crypto deps) and is verifiable by anyone against the committed `tradesRoot`
and the onchain receipts. What is NOT signed or proven: the equity/netFlow
VALUES themselves are self-attested paper-accounting (in shadow they are
paper money by definition), and in ledger-only mode bundles carry the
deterministic unsigned fallback (zero signer, 65 zero bytes) which every
verifier must reject as unsigned. The `witness` object, when present, is
also UNSIGNED: it is a neutral price cross-check (Pyth via Hermes, 3s
timeout) embedded outside the EIP-712 message so the signed schema stays
byte-identical with or without it - sanity-check material, not proof. A
fetch failure or missing feed id simply omits the field and never fails a
commit; verify any witness price against Pyth's historical API.

Optional publishing: set `COVENANT_IPFS_API` (kubo RPC base URL, e.g.
`http://127.0.0.1:5001`) and the bundle is pinned via `/api/v0/add` (stdlib
multipart POST) and the commit references `ipfs://<cid>` instead of the
relative path. A publishing failure is logged and never fails a commit.

### Modes

- **ledger-only** (default): receipts recorded, evidence bundles written
  (unsigned), nothing touches the chain. This is what you get with
  `--attest` or `COVENANT_ATTEST=1` but without the full connection env trio.
- **onchain**: `COVENANT_ATTEST=1` plus `COVENANT_RPC_URL`,
  `COVENANT_PRIVATE_KEY`, `COVENANT_CONTRACT_ADDRESS`. Same code path, the
  chain writes just turn on: resync at startup, signed evidence, commit,
  self-audit, auto-finalize.

The bridge needs `node` on PATH and viem resolvable from `covenant/sdk`
(sibling of the `vouch/` project dir, `pnpm --filter @covenant/sdk build`).
The docker image ships node; ledger-only mode works out of the box, and
onchain mode additionally needs the sdk tree mounted next to `/app/vouch`.

Venue honesty carries into the receipts: the `arb-paper` venue books
ArbitrumDex-class receipts while the fills themselves remain paper (live
SOL/USDC prices, simulated AMM fills, no router); the default paper venue
books `Other`. The venue class travels in the receipt so a proof can
never blur which market produced the fill.

Attestation is opt-in: run with `--attest` or set `COVENANT_ATTEST=1`. Env
vars (see `.env.example`):

```
COVENANT_ATTEST=1
COVENANT_RPC_URL=https://sepolia-rollup.arbitrum.io/rpc
COVENANT_PRIVATE_KEY=<funded operator EOA key>
COVENANT_CONTRACT_ADDRESS=<Covenant Stylus proxy address>
COVENANT_CHAIN=arbitrum-sepolia
COVENANT_STRATEGY_ID=0
COVENANT_STRATEGY_NAME=vouch-sol
COVENANT_EVIDENCE_DIR=evidence
COVENANT_IPFS_API=            # optional; kubo RPC base URL
# Optional neutral price witness: Pyth feed id for the traded instrument.
# When set, each evidence bundle gains an unsigned "witness" annotation with
# the Pyth price at commit time (SOL/USD example id below).
COVENANT_PYTH_PRICE_ID=
COVENANT_PYTH_HERMES_URL=https://hermes.pyth.network
```

Example: an attested shadow session (ledger-only unless the connection
trio above is exported):

```bash
COVENANT_ATTEST=1 .venv/bin/python run_shadow.py --attest --minutes 60
```

Epoch accounting: shadow mode has paper money and no deposits, so the
epoch 0 `netFlow` carries the seed capital (the starting cash) and every
later epoch is 0. Real mode books the actual deposit the same way. That
makes cumulative PnL telescope to finalEquity minus deposits, which is the
invariant the protocol audits.

Conformance: the Python receipt encoder and Merkle tree are validated
against fixtures generated from the TypeScript SDK by
`tests/attest/test_attest.py` (`python3 -m unittest discover -s tests`); the
Merkle fixtures are the same cross-language vectors shared with
`contracts/merkle-core`, so python, TS SDK and Rust contract agree on
every byte. The EIP-712 typed-data shape is pinned byte-for-byte in
`tests/attest/test_evidence_v2.py`.
