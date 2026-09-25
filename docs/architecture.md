# Covenant architecture

**Audience:** a new contributor on day one. This is the system overview: what
the components are, how data moves between them, and why the trust model is
what it is. The byte-level rules of the protocol live in
[protocol.md](protocol.md); how to run everything lives in
[deployment.md](deployment.md). **Scope:** the monorepo as built for the
Arbitrum Open House Singapore Buildathon (v0.1.0).

## What Covenant is, in one paragraph

Covenant is a non-custodial proof-of-performance layer for trading strategies.
A strategy operator commits sequential epoch checkpoints onchain (ending
equity in USDG terms, net external flows, and a Merkle root over that epoch's
canonical trade receipts) to a Stylus contract on Arbitrum. Anyone can
challenge a pending epoch by staking USDG; a resolver either upholds (epoch
invalidated, operator bond slashed) or dismisses (stake forfeited). The
leaderboard ranks strategies using finalized checkpoints only. Every trade
receipt is disclosed offchain and can be verified by anyone against the
committed root, from the browser.

## Component diagram

```
                     fills (CEX or DEX, anywhere)
                              |
                    ┌─────────▼──────────┐
                    │  vouch agent       │  two-model trading loop:
                    │  (vouch/)          │  System-1 per tick, System-2
                    │  canonical receipts│  rewrites rules between epochs
                    └─────────┬──────────┘
              append-only     |            commitEpoch(equity, netFlow,
              receipt ledger  |            tradesRoot, evidenceURI)
              + evidence      |            (also: any bot using the SDK)
              bundles         ▼
┌──────────────┐    ┌────────────────────────────┐   challengeEpoch + stake
│ Challengers  │--->│  Stylus contract (Rust)    │<------------------┐
│ (anyone,     │    │  contracts/covenant/       │                   │
│  stake USDG) │    │  registry, checkpoints,    │        ┌──────────┴─────────┐
└──────────────┘    │  USDG escrow, slashing     │        │ resolver           │
                    └─────────┬──────────────────┘        │ (uphold | dismiss) │
                              │ events                    └────────────────────┘
                              ▼
                    ┌────────────────────────────┐
                    │  indexer (TypeScript)      │  getLogs -> pure event
                    │  indexer/                  │  reducer -> JSON API
                    └─────────┬──────────────────┘
                              │ GET /strategies
                              ▼
                    ┌────────────────────────────┐
                    │  web leaderboard (Next.js) │  rankings, epoch detail,
                    │  web/                      │  trustless receipt verifier
                    └────────────────────────────┘  (reads the chain from
                                                     the browser via viem)
```

The SDK (`sdk/`) is the shared language: the agent's Node bridge, the indexer,
and the web verifier all import `@covenant/sdk` (directly or by building on
its ABI) instead of hand-rolling receipt or Merkle code.

## Data flow: fill to leaderboard

1. **Fill.** The agent books a fill at a venue (Coinbase spot, the Arbitrum
   DEX paper venue, or the simulated paper venue). Booking uses the venue's
   executed price and fee, not the requested ones
   (`vouch/vouch/venues/`).
2. **Receipt.** The fill becomes a canonical receipt: the byte-exact Python
   mirror of `sdk/src/receipt.ts`, with sign derived from position direction
   (`vouch/vouch/attest/receipts.py`). Venue class travels in the receipt.
3. **Ledger.** The receipt is appended to a per-epoch JSONL ledger, flushed
   and fsynced before the fill returns; duplicates and backwards timestamps
   are refused (`vouch/vouch/attest/ledger.py`).
4. **Epoch commit.** At the epoch boundary (the same boundary where System-2
   rewrites rules, and on every exit path including the kill switch), an
   evidence bundle (receipts, hashes, Merkle proofs, equity, net flow,
   trades root) is written to disk BEFORE any chain call, then
   `commitEpoch` is sent through the Node bridge
   (`vouch/vouch/attest/commit.py`, `vouch/vouch/attest/bridge.mjs`). The agent
   self-audits the first receipt onchain after committing.
5. **Chain state.** The contract stores the checkpoint (pending), applies
   performance accounting only at finalize, and escrows bonds and stakes
   (`contracts/covenant/src/lib.rs`).
6. **Events.** `StrategyRegistered`, `EpochCommitted`, `EpochFinalized`,
   `EpochChallenged`, `ChallengeResolved`, `StrategyStatusChanged` (declared
   in `contracts/covenant/src/lib.rs`).
7. **Indexer.** A log transport polls `getLogs` in bounded batches, decodes
   events against the ABI, and folds them into a pure reducer's state; a
   dependency-free JSON API serves `/health`, `/strategies`,
   `/strategies/:id` (`indexer/src/transport.ts`, `indexer/src/state.ts`,
   `indexer/src/server.ts`).
8. **Leaderboard.** The Next.js site fetches the indexer API server-side
   (`web/lib/api.ts`) and ranks by total return over finalized epochs;
   the strategy pages show epoch history and the verify page recomputes
   Merkle proofs against the chain from the browser (`web/app/verify/`).

## Trust model (stated honestly)

Self-reported fills cannot be proven against a CEX without venue cooperation.
Covenant therefore guarantees **consistency and commitment**, not absolute
truth:

1. **History is append-only.** Checkpoints are strictly sequential
   (epochIndex must equal the current epoch count, committedAt strictly
   increasing), so an operator cannot quietly rewrite the past
   (`contracts/covenant/src/lib.rs::commit_epoch`).
2. **Disclosures are verifiable.** Any shown trade receipt either validates
   against the committed Merkle root or the strategy is provably lying
   (`contracts/covenant/src/lib.rs::verify_receipt`). The web verifier does
   this check from the browser, not through a trusting API.
3. **Falsehood is expensive.** A registration bond and challenger stakes
   make fraud a negative-EV attack under a functioning resolver: an upheld
   challenge slashes the bond to the challenger; a dismissed challenge
   forfeits the stake to the treasury
   (`contracts/covenant/src/lib.rs::resolve_challenge`). This is the honest
   trust model for offchain fills: expensive, not impossible.
4. **Attested vs verified is an upgrade path, not a lie of omission.** The
   venue class travels inside every receipt, so a proof can never blur which
   market produced a fill. DEX-native strategies can later route fills
   through a settlement adapter, upgrading their attestations from
   self-reported to venue-verified. Today everything is "attested"; the
   roadmap is a "verified" tier on the leaderboard.

## Component responsibilities

| Component | Path | Owns | Does NOT own |
| --- | --- | --- | --- |
| Stylus contract | `contracts/covenant/` | Registry, sequential checkpoints, challenge escrow, bond slashing, onchain Merkle verification, performance accounting | Receipt canonicalization (consumes the root; `merkle-core` holds the primitives) |
| merkle-core | `contracts/merkle-core/` | Pure Rust receipt-hash and sorted-pair Merkle primitives shared by the contract; the cross-language fixture test | Chain state, storage |
| SDK | `sdk/` | Canonical receipt encoding, Merkle trees, `EpochBuilder`, typed read/operator clients, the Covenant ABI | Signature/key management (callers bring a viem wallet) |
| Indexer | `indexer/` | Event sourcing into a queryable model, the JSON API | Being the source of truth (the chain is); writes of any kind |
| Web | `web/` | Leaderboard UI, strategy pages, browser-side trustless receipt verification | Server-side trust: contract reads happen client-side via viem |
| vouch agent | `vouch/` | Trading loop, rails, venue adapters, attestation (receipts, ledger, evidence bundles, commits) | Protocol semantics (it mirrors the SDK byte-for-byte and is fixture-tested against it) |
| docs | `docs/` | This tree: architecture, normative protocol spec, runbook | Everything else |

## Deployment topology

`docker-compose.yml` at the repo root defines three services:

| Service | Image build | Port | Notes |
| --- | --- | --- | --- |
| `indexer` | `indexer/Dockerfile` (node:24-alpine, builds the SDK first) | 8787 | Requires `RPC_URL` and `CONTRACT_ADDRESS`; `CHAIN` defaults to `arbitrum-sepolia` |
| `web` | `web/Dockerfile` (node:24-alpine, builds SDK + web) | 3000 | Talks to the indexer at `http://indexer:8787` |
| `vouch` | `vouch/Dockerfile` (python:3.12-slim, profile `agent`) | none | Attested shadow mode only (`COVENANT_ATTEST=1`); real-money mode is deliberately impossible in the container. Writes to the `vouch-out` volume |

Without a deployed contract there is nothing to index; `indexer/demo-server.mjs`
serves the same JSON API from synthetic data (flagged `"demo": true`, which the
web app renders as a DEMO DATA banner) purely so the UI can be reviewed.

## Where things live

```
contracts/covenant/         Stylus contract (Rust): src/lib.rs, src/test.rs,
                            exported ABI at abi/ICovenant.sol
contracts/merkle-core/      Pure Rust Merkle primitives; cross-language
                            fixture test in tests/cross_language.rs, shared
                            fixtures in tests/fixtures/merkle.json
sdk/                        TypeScript SDK: receipt.ts, merkle.ts, epoch.ts,
                            client.ts, abi.ts; fixtures generator scripts/
indexer/                    src/state.ts (pure reducer), src/transport.ts
                            (log transport), src/server.ts (JSON API),
                            src/main.ts (entry), demo-server.mjs
web/                        app/page.tsx (leaderboard),
                            app/strategies/[id]/ (epoch detail),
                            app/verify/ (receipt verifier), lib/api.ts
vouch/                      vouch/vouch/ (package: config, venues/, engine/,
                            analysis/, attest/, server/), run_*.py CLIs,
                            tests/{attest,venues,engine}/
.github/workflows/ci.yml    TS + contract + vouch CI gates
docs/                       architecture.md (this file), protocol.md,
                            deployment.md
```

## How the pieces are tested together

Cross-language conformance is the backbone: the TypeScript SDK generates
`contracts/merkle-core/tests/fixtures/merkle.json`, which the Rust crate
verifies (`contracts/merkle-core/tests/cross_language.rs`) and the Python
agent replays (`vouch/tests/`, pointing at the same file), plus a
receipt-level fixture set for the Python mirror
(`vouch/tests/fixtures/receipts.json`). The exact rules and the suites that
enforce them are listed in [protocol.md](protocol.md), Conformance.
Current counts: contract 11/11, merkle-core 6/6 (including the
cross-language fixture suite), SDK 8/8, indexer 5/5, vouch 10/10.
