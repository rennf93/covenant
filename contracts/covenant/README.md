# contracts/covenant

The Covenant Stylus contract: strategy registry, sequential epoch checkpoints
with optimistic challenges, USDG escrow (bonds, challenger stakes,
treasury), and onchain Merkle receipt verification.

- `src/lib.rs` - the single `#[public]` ABI impl; each concern lives in its
  own module (`epochs.rs` commit/finalize, `challenge.rs` stake/resolve/
  resolver deadline, `registry.rs` registration, `admin.rs` parameters,
  pause, treasury, admin handover, `verify.rs` Merkle verification,
  `views.rs` read-only accessors)
- `abi/ICovenant.sol` - exported ABI (`cargo stylus export-abi`); the viem
  mirror lives in `sdk/src/abi.ts`
- `../merkle-core/` - pure Rust Merkle/receipt primitives shared by tests;
  `src/lib.rs::verify_receipt` verifies proofs against committed roots (the
  sorted-pair scheme must stay byte-identical with `sdk/src/merkle.ts` and
  `vouch/attest/merkle.py`, enforced by the shared fixture at
  `../merkle-core/tests/fixtures/merkle.json`)

## Protocol surfaces worth knowing

- Epochs commit and finalize strictly in order: a pending or challenged
  checkpoint blocks later commits (`PreviousEpochNotFinalized`) until it
  settles.
- Resolver deadline: if the resolver does not decide within three challenge
  windows of the challenge (deadline `committed_at + 4 * challenge_window`),
  anyone may resolve and the decision is forced-dismiss with the stake
  refunded (`ChallengeResolved` carries `forced = true`).
- Admin handover is two-step: `transferAdmin(new)` proposes, `acceptAdmin()`
  accepts; `pendingAdmin()` reads the proposal. Every admin action emits an
  event.
- Pause gates register/commit/challenge/resolve. `finalizeEpoch` stays
  permissionless and pause-exempt: it settles accounting only, moves no
  tokens.

## Tests and build

```bash
cargo test                                        # 28 unit tests (mock VM)
cargo build --release --target wasm32-unknown-unknown
cargo stylus export-abi
```

The toolchain is pinned in `rust-toolchain.toml` (1.91.0 + wasm32 target).

## Deploy runbook (Arbitrum Sepolia staging)

Prereqs: `cargo stylus` installed (`cargo install --git
https://github.com/OffchainLabs/stylus-targets cargo-stylus` ... see
https://docs.arbitrum.io/stylus/quickstart), a funded EOA key exported as
`PRIVATE_KEY`, and an RPC endpoint.

Constructor args, in order:

| Arg | Meaning | Suggested staging value |
| --- | --- | --- |
| `USDG` | USDG token address (6 decimals) | `0xFFC95faa3d63Cde504a05B567C600B78C0b41892` (Arbitrum Sepolia; Arbitrum One: `0x004B506865409877C9fA29bfb1ebA929984B9bbC`) |
| `RESOLVER` | address allowed to resolve challenges | your operator EOA for staging |
| `BOND` | strategy registration bond, USDG base units (6 dp) | `10_000000` (10 USDG) |
| `STAKE` | challenger stake per challenge, USDG base units | `5_000000` (5 USDG) |
| `WINDOW_SECONDS` | challenge window before an epoch finalizes | `300` (5 min for the demo; production wants hours) |

```bash
cd contracts/covenant
cargo stylus deploy --network sepolia \
  --constructor-args <USDG> <RESOLVER> <BOND> <STAKE> <WINDOW_SECONDS>
```

Then verify the source on Arbiscan/Stylus:

```bash
cargo stylus verify --network sepolia
```

If the Paxos test USDG faucet is unavailable, a 6-decimal mock USDG is
acceptable for staging IF clearly labeled as mock in the demo materials;
the production submission plan uses real USDG.

## After deploy

1. Register the strategy (pulls the bond): the vouch agent does this via
   `attest/bridge.mjs` action `register`, or the SDK's `CovenantOperator`.
2. Point the indexer at it (repo root `docker-compose.yml` or by hand):

```bash
cd indexer
RPC_URL=https://sepolia-rollup.arbitrum.io/rpc \
CONTRACT_ADDRESS=0x… \
CHAIN=arbitrum-sepolia \
START_BLOCK=<deploy block> \
pnpm start
```

3. Run an attested vouch session (`vouch/README.md` has the env trio) and
   watch the leaderboard (`web/`).
