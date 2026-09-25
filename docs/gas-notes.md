# Covenant gas notes (analytical)

**Read this first: every number in this document is ANALYTICAL, not measured.**
They are derived by counting storage slots in `contracts/covenant/src/storage.rs`
and applying the public EVM-equivalent unit prices Stylus charges for host
storage calls. No gas was measured on any chain for this document.

Why not measured: the stylus-test mock VM does not meter gas. In
stylus-test 0.10.9 the `MeteringAccess` host functions (`evm_gas_left`,
`evm_ink_left`) simply return a stored counter that nothing ever decrements,
and the call host functions ignore their gas arguments entirely. A
"gas snapshot assertion" in `cargo test` would therefore be theater, so the
suite has none. To get real numbers, deploy to a local Nitro test node
(`cargo stylus deploy` against `http://localhost:8547`) and read the receipt;
that setup is deliberately out of scope for the unit suite.

## 1. Cost model

Stylus prices host storage calls at their EVM equivalents, so the usual
intuitions hold:

| Operation | EVM-equivalent price |
| --- | --- |
| Cold `SLOAD` (first touch of a slot in a tx) | ~2,100 |
| Warm re-read | ~100 |
| `SSTORE` to a new value (zero to nonzero) | ~20,000 |
| `SSTORE` modifying an existing nonzero slot | ~5,000 |
| `LOG` (event) | ~375 + ~375 per topic + ~8/byte data |
| External call base | ~2,600 (cold) |
| `keccak256` | ~30 + ~6 per 32-byte word |

On top of this, Stylus transactions pay a small host-entry overhead for
wasm execution and one-time program activation cost at deploy. Those are
chain-level constants documented by OffchainLabs and change with Arbitrum
pricing; consult the current Stylus docs rather than this file for them.

## 2. Storage slots touched per operation

Slot counts below come straight from the `sol_storage!` layout. A strategy
is 9 fixed slots plus its two strings (each string costs 1 header slot plus
one data slot per 32 bytes). A checkpoint is 7 fixed slots plus
`evidence_uri`. "L" = cold load, "S" = store.

| Operation | Own storage | External calls | Notes |
| --- | --- | --- | --- |
| `registerStrategy` | ~3 L + ~9 S + string chunks (name, metadata_uri) | 1 `transferFrom` (the bond) | token's own accounting adds slots on the token contract |
| `commitEpoch` | ~5 L (owner, status, epoch_count, prev status+committed_at) + ~8 S (epoch_count, 7 fixed checkpoint slots) + evidence chunks | none | evidence_uri length is the main variable cost |
| `finalizeEpoch` | ~7 L + ~1-5 S (status always; equity, cumulative_pnl, hwm, finalized_epochs as they change) | none | cheapest state-changing entrypoint; no token movements by design |
| `challengeEpoch` | ~4 L + ~3 S (status, challenger, stake) | 1 `transferFrom` (the stake) | |
| `resolveChallenge` (upheld) | ~4 L + ~5 S (3 checkpoint slots, bond, strategy status) | 2 `transfer` (stake refund, bond slash) | |
| `resolveChallenge` (dismiss) | ~4 L + ~4 S (3 checkpoint slots, treasury) | none | forfeited stake stays in the contract |
| `resolveChallenge` (forced) | ~4 L + ~3 S | 1 `transfer` (stake refund) | anti-deadlock path, credits nothing to the treasury |
| `withdrawTreasury` | ~2 L + ~1 S | 1 `transfer` | |
| `verifyReceipt` (view) | 1 L (trades_root) + pure hashing | none | no writes, so it costs little and cannot be paused or front-run |
| `pause` / `unpause` / `transferAdmin` / `acceptAdmin` / `setResolver` | 1-2 S each | none | |

## 3. Loop bounds

No contract entrypoint runs an unbounded loop:

- commit / finalize / challenge / resolve / register are O(1) storage
  operations; nothing iterates over epochs, strategies, or receipts.
- `verifyReceipt` is O(depth) keccak hashes, where depth is
  `ceil(log2(leaves))`: at most 6 hashes for the fixture-tested trees and
  ~16 for a million-receipt epoch (see the property test
  `every_proof_verifies_against_the_root` in
  `contracts/merkle-core/tests/properties.rs`, which pins the depth bound).
- The only input-length-scaled cost is string storage: `evidence_uri` (and
  registration strings) each add one slot write per 32 bytes. SDK builders
  keep evidence URIs short; a pathological URI would cost the operator at
  commit time, not anyone else.

## 4. One-time vs per-call

- One-time: deploy transaction, wasm activation ("program cost", paid once
  per code version), and ABI staleness checks by the tooling.
- Per-call: everything in the table above. Performance accounting
  (`finalizeEpoch`) is deliberately the cheap, permissionless path so that
  settling an epoch never depends on the operator or the admin.

## 5. How to get real numbers

1. Run a local Nitro dev node and `cargo stylus deploy` the wasm built by
   `make contract-build`.
2. Drive `commitEpoch`, `finalizeEpoch`, `challengeEpoch`,
   `resolveChallenge` (all three variants), and `verifyReceipt` with `cast`
   or the SDK, and read `gasUsed` from each receipt.
3. Replace this document's table with measured values and delete this
   section 5.
