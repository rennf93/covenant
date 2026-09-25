# Covenant protocol specification (v1)

**Audience:** implementers of anything that speaks the Covenant protocol: the
Rust contract, the TypeScript SDK, the Python agent mirror, indexers, and
verifiers. This is the normative spec; the key words MUST, MUST NOT, SHOULD,
and MAY are to be interpreted as described in RFC 2119. **Scope:** the
canonical receipt, the Merkle scheme, the epoch lifecycle, and the
cross-language conformance rule, as actually implemented at v0.1.0. For the
system overview see [architecture.md](architecture.md); for operating
instructions see [deployment.md](deployment.md).

Every normative statement carries an evidence citation pointing at the code
that enforces it today. If code and spec ever disagree, that is a bug in one
of them: file an issue referencing the citation.

## 1. Canonical trade receipt

A receipt is the canonical, hashable record of one executed fill. Its leaf
hash is `keccak256(abi.encode(...))` over exactly these fields, in exactly
this order:

| # | Field | Type | Semantics |
| --- | --- | --- | --- |
| 1 | `strategyId` | `uint256` | Covenant strategy id assigned by the registry (first strategy is 1). |
| 2 | `epochIndex` | `uint64` | Epoch the fill belongs to; must equal the builder's epoch. |
| 3 | `venue` | `uint8` | Venue class: `0` Coinbase, `1` ArbitrumDex, `2` Other. |
| 4 | `venueOrderIdHash` | `bytes32` | `keccak256(utf8(venue order id))`. The raw id is never published, so CEX account identifiers stay private, yet a disclosed fill can be matched to a venue statement line by the operator. |
| 5 | `instrumentHash` | `bytes32` | `keccak256(utf8(instrument string))`, e.g. `"SOL-USD"` on Coinbase, `"SOL/USDC"` on the Arbitrum DEX. Canonicalization per venue is the adapter's responsibility. |
| 6 | `side` | `uint8` | `0` Buy, `1` Sell, from the strategy's perspective. |
| 7 | `sizeSignedBase` | `int256` | Signed base-asset size in the instrument's base units. Positive = acquired, negative = disposed. |
| 8 | `priceQuote` | `uint256` | Fill price in quote units, 8-decimals fixed point (see 1.2). MUST be > 0. |
| 9 | `feeQuote` | `uint256` | Fee paid, in quote units, 8-decimals fixed point. |
| 10 | `filledAt` | `uint64` | Fill time, unix seconds. |

(evidence: `sdk/src/receipt.ts::receiptParameters`, `sdk/src/receipt.ts::encodeReceipt`,
`vouch/vouch/attest/receipts.py::encode_receipt`,
`contracts/covenant/abi/ICovenant.sol`)

Implementations MUST reject structurally invalid receipts before hashing
(`receiptHash` throws, the Python `encode_receipt` raises). The canonical
encoding is static ABI: because every field is a static type, the encoding is
a bare concatenation of 32-byte big-endian words, which is what makes the
Python mirror feasible without an ABI library.
(evidence: `sdk/src/receipt.ts::receiptHash`,
`vouch/vouch/attest/receipts.py::receipt_hash`)

### 1.1 Sign conventions

| Position | `side` | `sizeSignedBase` |
| --- | --- | --- |
| Bought base asset (including opening a long) | `0` Buy | positive |
| Sold base asset (including closing a long) | `1` Sell | negative |
| Opened a short | `1` Sell | negative (base asset disposed) |
| Closed a short | `0` Buy | positive (base asset acquired) |

The redundant side/size encoding is deliberate and defensive:

- `makeReceipt` derives the sign from `side`, so a caller-supplied
  sign disagreement cannot be constructed through it.
  (evidence: `sdk/src/receipt.ts::makeReceipt`,
  `vouch/vouch/attest/receipts.py::make_receipt`)
- `validateReceipt` rejects any receipt whose side and size sign disagree:
  a Buy MUST carry positive size, a Sell MUST carry negative size.
  (evidence: `sdk/src/receipt.ts::validateReceipt`,
  `sdk/test/sdk.test.ts::receipt hash is stable and side/size sign is enforced`,
  `vouch/vouch/attest/receipts.py::validate_receipt`)

### 1.2 The 8-decimal fixed point rule

`priceQuote` and `feeQuote` are integers in 8-decimals fixed point
(`1e8` units per quote unit). Conversion from human units MUST be exact:

- Adapters SHOULD pass decimal strings; inputs with more than 8 fractional
  digits MUST be rejected (precision would be lost), not rounded.
  (evidence: `sdk/src/receipt.ts::parseFixed8`,
  `vouch/vouch/attest/receipts.py::parse_fixed8`)
- Note the asymmetry: receipt price/fee use 8 decimals, while onchain equity
  and net flow use USDG base units at USDG's 6 decimals. These are different
  scales and MUST NOT be confused.
  (evidence: `sdk/src/client.ts::formatUsdg`,
  `vouch/vouch/attest/receipts.py::usdg_from_usd`)

### 1.3 Hash-of-string fields

`venueOrderIdHash` and `instrumentHash` are `keccak256` of the UTF-8 encoding
of the raw string. Different raw order ids therefore produce different
receipt hashes even when every other field matches.
(evidence: `sdk/src/receipt.ts::makeReceipt` (`hashOf`),
`vouch/vouch/attest/receipts.py::hash_of_string`,
`sdk/test/sdk.test.ts::same receipt data with different raw venue order ids hashes differently`)

### 1.4 Validation invariants

`validateReceipt` MUST reject, and builders MUST refuse to commit, receipts
where any of the following fails:

- `strategyId >= 0`, `epochIndex >= 0`
- `filledAt` fits `uint64`
- `priceQuote`, `feeQuote` fit `uint256`
- `sizeSignedBase` fits `int256` (two's complement in the encoding)
- `side == Buy` implies `sizeSignedBase > 0`; `side == Sell` implies
  `sizeSignedBase < 0`
- `priceQuote > 0` (a zero price is never a real fill)

(evidence: `sdk/src/receipt.ts::validateReceipt`,
`vouch/vouch/attest/receipts.py::validate_receipt`)

## 2. Merkle scheme

Leaves are 32-byte receipt hashes. The tree is a sorted-pair (OpenZeppelin
style) Merkle tree with these normative properties:

1. **Sorted-pair nodes.** An internal node is `keccak256(min ++ max)` where
   the ordering is byte-wise unsigned comparison of the two 32-byte values.
   Proof verification therefore never needs leaf positions.
   (evidence: `sdk/src/merkle.ts::sortedPairHash`,
   `contracts/merkle-core/src/lib.rs::sorted_pair_hash`,
   `vouch/vouch/attest/merkle.py::_sorted_pair_hash`)
2. **Duplicate-last padding, inside the hash input only.** A level with an
   odd node count is padded by duplicating its last node when computing the
   parent hash. The stored level keeps its original count for proof
   generation; on the last odd position a leaf is its own sibling. The
   duplicate exists only inside the hash input.
   (evidence: `sdk/src/merkle.ts::buildMerkleTree`,
   `contracts/merkle-core/src/lib.rs::build_root`,
   `contracts/merkle-core/src/lib.rs::build_proof`,
   `vouch/vouch/attest/merkle.py::build_merkle_tree`)
3. **Position-free proofs.** A proof is the sibling list, leaf to root. A
   verifier MUST NOT need to know the leaf's index.
   (evidence: `sdk/src/merkle.ts::verifyProof`,
   `contracts/merkle-core/src/lib.rs::verify_proof`)
4. **Single-leaf identity.** A one-leaf tree has `root == leaf` and an empty
   proof, which trivially verifies.
   (evidence: `contracts/merkle-core/src/lib.rs::single_leaf_root_equals_leaf_with_empty_proof`,
   `sdk/test/sdk.test.ts::merkle: single leaf root equals leaf with empty proof`)
5. **Empty epoch.** An epoch with zero receipts MUST NOT be tree-committed:
   builders MUST refuse, and the contract stores `bytes32(0)`, against which
   `verifyReceipt` always returns false. Tree construction over an empty
   leaf list MUST error (Rust: panic on the pure primitive, guarded by
   callers).
   (evidence: `contracts/merkle-core/src/lib.rs::build_root`,
   `sdk/src/merkle.ts::buildMerkleTree`,
   `contracts/covenant/src/lib.rs::verify_receipt`,
   `vouch/vouch/attest/commit.py::commit_epoch`)
6. **Duplicate leaves.** Duplicated leaf values are technically verifiable,
   so adapters MUST reject duplicate `venueOrderIdHash` values within an
   epoch (see 3.2 and the EpochBuilder invariant below).

## 3. Epoch lifecycle

### 3.1 Commit

- A commit MUST be sent by the strategy owner, while the strategy is active
  and the contract is unpaused. (evidence:
  `contracts/covenant/src/epochs.rs::commit`)
- `epochIndex` MUST equal the strategy's current epoch count: epochs are
  strictly sequential, first epoch 0, no gaps, no re-commits. (evidence:
  `contracts/covenant/src/epochs.rs::commit`
  (`epoch_index != epoch_count` -> `EpochNotSequential`),
  `contracts/covenant/src/test.rs::commit_epoch_requires_owner_and_sequential_epochs`)
- **Sequential accountability.** The previous checkpoint (`epochIndex - 1`)
  MUST have status `Finalized` before a later epoch may commit (epoch 0 is
  exempt). A Pending or Challenged checkpoint therefore blocks all later
  commits until it is finalized or resolved. This is deliberate: per-epoch
  PnL telescopes across finalized checkpoints, so a disputed epoch MUST NOT
  be buried under newer, undisputed ones. The cost is honest and documented:
  an unresolved challenge stalls the strategy's later epochs until resolution
  or the resolver deadline (3.6) releases it. (evidence:
  `contracts/covenant/src/epochs.rs::commit`
  (`prev_status != CP_FINALIZED` -> `PreviousEpochNotFinalized`),
  `contracts/covenant/src/test.rs::commit_blocks_until_previous_epoch_finalizes`,
  `contracts/covenant/src/test.rs::challenged_epoch_blocks_commits_until_resolved`)
- `committed_at` MUST be strictly increasing: a commit whose block timestamp
  is not greater than the previous epoch's `committed_at` reverts. (evidence:
  `contracts/covenant/src/epochs.rs::commit`
  (`prev_committed_at >= now` -> `EpochNotSequential`))
- `equityUsdg` and `netFlowUsdg` are `int256` USDG base units (6 decimals);
  equity MAY be negative. They are operator-reported and subject to
  challenge. `tradesRoot` is the sorted-pair Merkle root over the epoch's
  canonical receipt hashes; `evidenceURI` points at the offchain evidence
  bundle. (evidence: `sdk/src/epoch.ts::commitPayload`,
  `contracts/covenant/src/epochs.rs::commit`)
- Builders MUST refuse to produce a commit payload for an epoch with zero
  receipts. (evidence: `sdk/src/epoch.ts::EpochBuilder::commitPayload`,
  `vouch/vouch/attest/commit.py::commit_epoch`)

### 3.2 Builder invariants

An epoch accumulator (SDK `EpochBuilder`, agent `EpochLedger`) MUST enforce,
in addition to 1.4:

- `venueOrderIdHash` is unique within the epoch (no double-fills, no
  duplicated leaves);
- a receipt's `epochIndex` matches the accumulator's epoch, and its
  `strategyId` matches the accumulator's strategy;
- receipts are collected with non-decreasing `filledAt` (out-of-order fills
  indicate a sync bug and are refused).

(evidence: `sdk/src/epoch.ts::EpochBuilder::add`,
`vouch/vouch/attest/ledger.py::add_fill`,
`vouch/vouch/attest/ledger.py::_load`,
`sdk/test/sdk.test.ts::epoch builder enforces uniqueness, ordering, and identity`)

The agent ledger is additionally durable: each receipt line is flushed and
fsynced before the fill returns, and on recovery a torn final line is
truncated while a corrupt mid-file line is a hard error. (evidence:
`vouch/vouch/attest/ledger.py` module docstring and `_load`)

### 3.3 Challenge window and finalization

- A freshly committed checkpoint is `Pending` (status 0).
- Any account MAY challenge a pending checkpoint during the window (block
  timestamp `< committed_at + challenge_window`) by staking `challenge_stake`
  USDG. Only one challenger at a time; a challenged checkpoint is `Challenged`
  (status 2), and a second challenge attempt while one is open reverts with
  `EpochNotPending`. (evidence: `contracts/covenant/src/challenge.rs::challenge`,
  `contracts/covenant/src/test.rs::double_challenge_while_challenged_reverts`)
- Challenges MUST reference existing state: an unknown strategy reverts with
  `StrategyNotFound` and an index at or past the strategy's `epoch_count`
  reverts with `EpochUnknown`. (Zero-initialized mapping storage reads as a
  Pending checkpoint at time 0, which is why the existence check is explicit
  rather than implied by the status check.) (evidence:
  `contracts/covenant/src/challenge.rs::challenge`,
  `contracts/covenant/src/test.rs::challenge_unknown_epoch_or_strategy_reverts`)
- Any account MAY finalize a pending checkpoint once the window has elapsed
  (permissionless). Finalizing applies the performance accounting and sets
  status `Finalized` (1). Finalize is NOT gated by the pause: it settles
  accounting only, moves no tokens, and cannot help an attacker, so pausing
  it would gain nothing and could strand performance accounting. Epochs
  finalize strictly in order (`PreviousEpochNotFinalized` otherwise;
  enforced on finalize as defense in depth even though commit refuses to
  create the out-of-order state). (evidence:
  `contracts/covenant/src/epochs.rs::finalize`,
  `contracts/covenant/src/test.rs::finalize_is_not_gated_by_pause`,
  `contracts/covenant/src/test.rs::finalize_enforces_sequential_order`)
- Finalize MUST reject unknown targets the same way challenge does
  (`StrategyNotFound` / `EpochUnknown`); a phantom epoch used to read
  zero-initialized storage as Pending and could be "finalized" by anyone,
  zeroing the strategy's equity. (evidence:
  `contracts/covenant/src/epochs.rs::finalize`,
  `contracts/covenant/src/test.rs::finalize_unknown_epoch_or_strategy_reverts`)

### 3.4 PnL accounting

- Per-epoch PnL: `pnl_t = equity_t - equity_{t-1} - netFlow_t`. For epoch 0
  the previous equity is 0, so `pnl_0 = equity_0 - netFlow_0`; with epoch 0's
  `netFlow` carrying the seed capital, cumulative PnL telescopes to final
  equity minus total deposits. (evidence:
  `contracts/covenant/src/epochs.rs::finalize` (`let pnl = ...`),
  `vouch/README.md` (epoch accounting section))
- The contract tracks cumulative PnL, the high-water mark (updated when
  ending equity exceeds the stored HWM), and the finalized epoch count.
  (evidence: `contracts/covenant/src/epochs.rs::finalize`,
  `contracts/covenant/src/test.rs::finalize_applies_performance_accounting`)
- The indexer derives leaderboard figures from FINALIZED epochs only, and
  computes its headline return as cumulative PnL over the first finalized
  equity in 1e18 fixed point (PnL-relative so deposits do not inflate it).
  (evidence: `indexer/src/state.ts::rederive`)

### 3.5 Resolution economics

The resolver (deploy-time address, changeable by admin) resolves a challenged
epoch:

- **Upheld:** the checkpoint becomes `Invalidated` (3), the strategy is
  `Suspended`, the challenger's stake is refunded, and the operator's
  registration bond is slashed (transferred to the challenger). An upheld
  challenge ends the strategy. (evidence:
  `contracts/covenant/src/challenge.rs::resolve`,
  `contracts/covenant/src/test.rs::challenge_and_upheld_resolution_slashes_operator`)
- **Dismissed:** the checkpoint becomes `Finalized` (1), the challenger's
  stake is forfeited to the contract's treasury (tracked, withdrawable by
  admin). (evidence: `contracts/covenant/src/challenge.rs::resolve`,
  `contracts/covenant/src/test.rs::dismissed_challenge_forfeits_stake_and_finalizes_epoch`)
- Resolving a checkpoint that is not `Challenged` reverts with
  `NotChallenged` (a separate error, not a reuse of the challenge-side
  status error). (evidence: `contracts/covenant/src/challenge.rs::resolve`,
  `contracts/covenant/src/test.rs::resolve_on_non_challenged_epoch_reverts`)

Lifecycle statuses: checkpoint `0` Pending, `1` Finalized, `2` Challenged,
`3` Invalidated; strategy `0` Active, `1` Suspended. (evidence:
`contracts/covenant/src/types.rs` (STATUS_*/CP_* constants),
`sdk/src/client.ts::CheckpointStatus`)

### 3.6 Resolver deadline (anti-deadlock)

An epoch can only be challenged within one `challenge_window` of its commit,
so once `now >= committed_at + 4 * challenge_window` an open challenge is at
least three windows old. From that deadline on:

- resolution becomes PERMISSIONLESS: any account MAY call
  `resolveChallenge`;
- the decision is FORCED-DISMISS regardless of the `upheld` argument: the
  checkpoint becomes `Finalized` (1), the challenger's stake is refunded in
  full, and nothing is credited to the treasury (nobody actually judged the
  challenge spurious, so nobody is punished);
- the emitted `ChallengeResolved` event carries `forced = true` (`resolver`
  is the account that triggered the forced resolution, not a protocol
  resolver);
- the resolver's own authority does NOT expire: the resolver MAY resolve at
  any time, before or after the deadline, with the full upheld/dismiss
  choice.

Implementation notes: the deadline is derived from `committed_at` and the
`challenge_window` parameter as it stands at resolve time (no challenge
timestamp is stored; a parameter change therefore moves open deadlines).
Pause gates the forced path too (see 3.7), so pausing can delay but the
admin cannot secretly divert a forced refund: forced dismissals never touch
the treasury.

(evidence: `contracts/covenant/src/challenge.rs::resolve`,
`contracts/covenant/src/test.rs::permissionless_resolve_before_deadline_reverts`,
`contracts/covenant/src/test.rs::forced_dismiss_after_deadline_refunds_stake`,
`contracts/covenant/src/test.rs::forced_resolution_ignores_the_upheld_argument`,
`contracts/covenant/src/test.rs::resolver_still_resolves_past_deadline_without_forcing`)

### 3.7 Administration, pause, and errors

- **Admin model:** the deployer is the initial admin. Admin handover is
  two-step: `transferAdmin(new)` (current admin only) stores the proposal,
  and `acceptAdmin()` (proposed successor only) completes it. Proposing the
  zero address cancels a pending proposal, so an uncontrolled account can
  never be handed the role by accident. The current admin keeps full
  authority until acceptance. (evidence:
  `contracts/covenant/src/admin.rs::transfer_admin`,
  `contracts/covenant/src/admin.rs::accept_admin`,
  `contracts/covenant/src/test.rs::admin_transfer_requires_two_steps`,
  `contracts/covenant/src/test.rs::admin_transfer_proposal_can_be_cancelled_with_zero_address`)
- **Events:** every state-changing admin action emits an event: `Paused`,
  `Unpaused`, `ResolverSet`, `ParametersSet`, `TreasuryWithdrawn`,
  `AdminTransferProposed`, `AdminTransferAccepted`. (evidence:
  `contracts/covenant/src/admin.rs`, `contracts/covenant/src/types.rs`)
- **Pause coverage:** pause gates `registerStrategy`, `commitEpoch`,
  `challengeEpoch`, and `resolveChallenge` (including the forced
  permissionless path). `finalizeEpoch` is deliberately pause-exempt (3.3):
  it only settles accounting after the window and moves no tokens. (evidence:
  `contracts/covenant/src/epochs.rs::commit`,
  `contracts/covenant/src/challenge.rs::challenge`,
  `contracts/covenant/src/challenge.rs::resolve`,
  `contracts/covenant/src/test.rs::paused_contract_rejects_challenge_and_resolve`)
- **Parameters:** `setParameters` applies to future activity only; bonds and
  stakes already escrowed are untouched, and a checkpoint's resolver
  deadline uses the window parameter in force at resolve time. (evidence:
  `contracts/covenant/src/admin.rs::set_parameters`,
  `contracts/covenant/src/test.rs::set_parameters_applies_only_to_new_activity`)
- **Errors:** withdrawal beyond the tracked treasury reverts with
  `TreasuryOverdraw` (not a generic zero-amount error); accepting a transfer
  without being the proposed successor reverts with `NotPendingAdmin`.
  (evidence: `contracts/covenant/src/admin.rs::withdraw_treasury`,
  `contracts/covenant/src/types.rs`)

### 3.8 Onchain receipt verification

`verifyReceipt(strategyId, epochIndex, proof, receiptHash)` is a pure view:
it MUST return false for unknown epochs and for zero roots (empty epochs),
and otherwise verify the proof against the committed root. (evidence:
`contracts/covenant/src/verify.rs::verify_receipt`,
`contracts/covenant/src/test.rs::verify_receipt_checks_merkle_proofs`)

## 4. Cross-language conformance rule

The protocol has three independent implementations that MUST agree on every
byte:

| Language | Receipt encoding | Merkle scheme |
| --- | --- | --- |
| TypeScript (SDK) | `sdk/src/receipt.ts` | `sdk/src/merkle.ts` |
| Rust (contract) | primitives in `contracts/merkle-core/src/lib.rs` | `contracts/merkle-core/src/lib.rs` |
| Python (agent) | `vouch/vouch/attest/receipts.py` | `vouch/vouch/attest/merkle.py` |

Conformance rule:

1. Merkle semantics are pinned by a shared fixture file,
   `contracts/merkle-core/tests/fixtures/merkle.json`, generated from the
   TypeScript SDK by `sdk/scripts/gen-fixtures.ts`. The Rust crate MUST
   verify it (`contracts/merkle-core/tests/cross_language.rs`), and the
   Python agent MUST replay the same file. Implementations MUST NOT change
   the fixtures by hand; they are generated artifacts.
   (evidence: `contracts/merkle-core/tests/cross_language.rs::typescript_fixtures_verify`,
   `vouch/tests/attest/test_attest.py::MerkleFixtureTest`)
2. The Python receipt encoder is additionally pinned by
   `vouch/tests/attest/fixtures/receipts.json`, vectors generated against
   the SDK distribution. (evidence:
   `vouch/tests/attest/test_attest.py::ReceiptFixtureTest`)
3. **Any change to the receipt encoding, the hash derivation, or the Merkle
   semantics is a protocol change.** It MUST be mirrored in all three
   implementations and the fixtures MUST be regenerated in the same change,
   with all conformance suites green. (evidence: the module docstrings of
   `sdk/src/receipt.ts`, `contracts/merkle-core/src/lib.rs`,
   `vouch/vouch/attest/receipts.py`, all of which say exactly this)

## 5. Conformance

Each rule above is enforced by one or more suites. Current counts, all green
as of 2026-09-25:

| Suite | Command | Count | Enforces |
| --- | --- | --- | --- |
| Contract unit tests (mock VM) | `cd contracts/covenant && cargo test` | 28/28 | Section 3: sequential commits AND ordered finalization, strict timestamps, permissionless pause-exempt finalize + accounting, phantom-epoch rejections, resolver deadline (forced dismiss, refund, event flag), challenge economics, window expiry rejection, pause coverage, two-step admin handover, treasury overdraw, proof verification, event topics, admin guards |
| merkle-core (incl. cross-language fixture suite and property tests) | `cd contracts/merkle-core && cargo test` | 9/9 | Sections 2 and 4: Rust Merkle primitives against TS-generated vectors, single-leaf identity, empty-input refusal, sorted-pair commutativity, plus fixed-seed property tests over random tree sizes 1..=64 (root recomputation, every proof, depth bound) |
| SDK | `pnpm --filter @covenant/sdk test` | 8/8 | Sections 1 and 2: exact fixed point, sign enforcement, hash-of-string fields, tree/proof behavior incl. property tests, EpochBuilder invariants |
| Indexer | `pnpm --filter @covenant/indexer test` | 5/5 | Section 3.4/leaderboard: event reducer behavior (status transitions, finalized-only derivation, invariants hold) |
| vouch attestation | `cd vouch && python3 -m unittest discover -s tests` | 10/10 | Sections 1, 2, 4: Python keccak/receipt/merkle fixture vectors, validation refusals, ledger invariants and crash recovery, ledger-only commit, USDG conversion |

CI runs all five (`.github/workflows/ci.yml`). A conformance suite failure is
by definition a protocol regression and blocks merge.
