# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Hardening (contracts/covenant)

- **Fixed (critical):** `finalizeEpoch` accepted phantom epochs. An
  uncommitted epoch read zero-initialized storage as `Pending` with
  `committed_at = 0`, so anyone could "finalize" a nonexistent epoch and
  apply `pnl = 0 - equity`, zeroing the strategy. `finalizeEpoch` and
  `challengeEpoch` now verify existence explicitly (`StrategyNotFound` /
  `EpochUnknown`); a challenge of a phantom epoch inside the first window
  was also possible before and is now rejected.
- **Fixed:** sequential accountability. `commitEpoch` now requires the
  previous checkpoint to be `Finalized` (`PreviousEpochNotFinalized`
  otherwise, epoch 0 exempt), and `finalizeEpoch` enforces the same order as
  defense in depth. Consequence, documented in `docs/protocol.md`: a pending
  or challenged epoch blocks later commits until it resolves, so disputed
  epochs can no longer be buried under newer ones.
- **Added:** resolver deadline (anti-deadlock). Once
  `now >= committed_at + 4 * challenge_window`, resolution becomes
  permissionless and forced-dismiss: the stake is refunded to the challenger,
  the checkpoint finalizes, nothing goes to the treasury, and the
  `ChallengeResolved` event carries a new `forced = true` field. The
  resolver keeps full authority at any time.
- **Added:** two-step admin transfer (`transferAdmin` / `acceptAdmin`, plus
  a `pendingAdmin()` view; proposing the zero address cancels). Every admin
  action now emits an event (`Paused`, `Unpaused`, `ResolverSet`,
  `ParametersSet`, `TreasuryWithdrawn`, `AdminTransferProposed`,
  `AdminTransferAccepted`).
- **Changed:** pause now gates `challengeEpoch` and `resolveChallenge` (in
  addition to registration). `finalizeEpoch` is deliberately pause-exempt
  and stays permissionless: it only settles accounting after the window and
  moves no tokens.
- **Changed:** checks-effects-interactions ordering in `registerStrategy`
  and `challengeEpoch` (state effects now precede the USDG transfer,
  matching `resolveChallenge`).
- **Changed (error hygiene):** `withdrawTreasury` overdraw gets its own
  `TreasuryOverdraw` error; resolving a non-challenged checkpoint gets
  `NotChallenged` instead of reusing `AlreadyChallenged`; wrong `acceptAdmin`
  caller gets `NotPendingAdmin`.
- Tests: 28 contract unit tests (was 11), covering the phantom-epoch and
  out-of-order regressions, double challenge, non-resolver resolve, unknown
  strategy/epoch, paused challenge/resolve, pause-exempt finalize, treasury
  happy/overdraw paths, parameter scoping, the admin handover flow, and all
  resolver-deadline paths. A stale test comment claiming out-of-order
  finalize was impossible is corrected.

### Added (contracts/merkle-core)

- Fixed-seed proptest suite (`tests/properties.rs`) over random tree sizes
  1..=64: root recomputation is deterministic, every proof verifies, proofs
  are exactly `ceil(log2(n))` siblings, and foreign leaves fail. The RNG
  seed is fixed so CI runs are reproducible.

### Added (docs)

- `docs/gas-notes.md`: an analytical gas breakdown (storage slots per
  operation, loop bounds, one-time vs per-call costs). The stylus-test mock
  VM does not meter gas (its metering host functions never charge), so all
  numbers there are analytical, explicitly not measured.

### Changed (ABI)

- Regenerated `contracts/covenant/abi/ICovenant.sol` via
  `cargo stylus export-abi` and mirrored exactly into `sdk/src/abi.ts`.
  Surface delta: new functions `transferAdmin(address)`, `acceptAdmin()`,
  `pendingAdmin() view returns (address)`; `ChallengeResolved` gains a
  non-indexed `forced` bool (event signature changed); new errors
  `NotPendingAdmin`, `PreviousEpochNotFinalized`, `NotChallenged`,
  `TreasuryOverdraw`. New events `Paused`, `Unpaused`, `ResolverSet`,
  `ParametersSet`, `TreasuryWithdrawn`, `AdminTransferProposed`,
  `AdminTransferAccepted`; `sdk/src/abi.ts` now also carries the error
  entries. Consumers decoding `ChallengeResolved` (indexer) must be updated
  for the new field.

### Changed (CI)

- Contract job: `cargo fmt --check` and `cargo clippy --all-targets -- -D
  warnings` on both crates, an ABI-sync step (re-export and diff against the
  committed `abi/ICovenant.sol`), and a Merkle fixture freshness check in
  the TS job (regenerate via `sdk/scripts/gen-fixtures.ts`, diff against the
  committed fixtures). Makefile gains a `contract-lint` target wired into
  `check`.

## [0.1.0] - 2026-09-25

First complete build: the protocol, the stack, the flagship agent, and the
submission. Hackathon scope (Arbitrum Open House Singapore Buildathon); the
contract is tested and ABI-verified but not yet deployed to a public network
at this tag.

### Added

- Stylus contract (`contracts/covenant/`): strategy registry, sequential
  epoch checkpoints, optimistic challenges with USDG escrow, bond slashing,
  onchain Merkle receipt verification, performance accounting on finalize;
  11/11 unit tests, `wasm32` release build verified, ABI exported to
  `contracts/covenant/abi/ICovenant.sol`.
- `contracts/merkle-core/`: pure Rust receipt-hash and sorted-pair Merkle
  primitives shared by the contract; 6/6 tests including the cross-language
  fixture suite over the shared `merkle.json` vectors.
- TypeScript SDK (`sdk/`): canonical receipt encoding, exact 8-decimal fixed
  point parsing, sorted-pair Merkle trees, `EpochBuilder` with protocol
  invariants, `CovenantReader`/`CovenantOperator` viem clients, Covenant ABI
  and USDG addresses; 8/8 tests. Cross-language fixtures generated by
  `sdk/scripts/gen-fixtures.ts`.
- Indexer (`indexer/`): pure event reducer, bounded-batch RPC log transport,
  dependency-free JSON API (`/health`, `/strategies`, `/strategies/:id`);
  5/5 tests. Synthetic demo server (`indexer/demo-server.mjs`) flagged
  `demo: true` for UI review without a deployed contract.
- Web leaderboard (`web/`): Next.js rankings, strategy/epoch detail pages,
  and a trustless receipt verifier that re-verifies Merkle proofs against
  the chain from the browser; `next build` green.
- vouch agent (`vouch/`): the flagship strategy, ported and renamed. Two-model
  loop (System-1 per-tick decisions, local LLM System-2 rewriting rules
  between epochs under hard constraint rails), dual venue adapter (Coinbase
  spot, Arbitrum DEX paper) plus the simulated paper venue, local dashboard,
  and the Covenant attestation module: fsynced per-epoch receipt ledgers,
  evidence bundles written before any chain call, `commitEpoch` through a
  Node bridge, onchain self-audit; 10/10 attestation tests, fixture-conformed
  to the SDK and the Rust merkle-core.
- Docker compose stack for indexer + leaderboard + agent (attested shadow
  profile; real-money mode deliberately impossible in the container).
- CI (`.github/workflows/ci.yml`): TS suites + web build, Rust toolchain
  with merkle-core and contract tests plus the wasm32 release build, vouch
  ruff/mypy/attestation gates.
- Documentation tree (`docs/`): architecture overview, normative protocol
  spec with evidence citations, deployment runbook; per-component READMEs;
  community files and GitHub templates.

### Changed

- Renamed the platform from Proven to Covenant throughout the codebase,
  clients (`ProvenReader`/`ProvenOperator` became
  `CovenantReader`/`CovenantOperator`), and docs; the agent got its own
  product name in the same pass.
- Restructured the repo into a pnpm workspace (`sdk`, `indexer`, `web`) with
  the Rust contracts and the Python agent alongside.
- Split the original design spec into `docs/architecture.md` and the
  normative `docs/protocol.md` and retired `docs/DESIGN.md`.

### Fixed

- Receipt sign convention on the short side: a sold/open-short receipt now
  correctly carries negative `sizeSignedBase` with `side = Sell`, including
  short positions, with side/size agreement enforced in both the TS and
  Python validators.
- Epoch close paths: the kill switch and every other exit path now close the
  open epoch, so fsynced receipts are never silently dropped without an
  evidence bundle.
- QA fixes from live paper sessions (dashboard, venue adapters, feed
  fallbacks) after the first full attested run.
