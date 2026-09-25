# Covenant

Onchain proof-of-performance for trading strategies, built for the
Arbitrum Open House Singapore Buildathon.

Trading strategies are marketed with screenshots and self-reported PnL.
Covenant is a non-custodial alternative: strategies commit sequential epoch
checkpoints (ending equity in USDG terms, net external flows, and a Merkle
root of canonical trade receipts) to a Stylus contract on Arbitrum. Anyone
can challenge a pending epoch by staking USDG; a resolver either upholds
(epoch invalidated, operator bond slashed) or dismisses (stake forfeited).
Every disclosed trade receipt is verifiable by anyone against the committed
root, from the browser. The leaderboard ranks strategies by returns computed
from finalized checkpoints only. This guarantees consistency and commitment,
not absolute truth; the trust model is stated honestly in
[docs/architecture.md](docs/architecture.md).

The flagship strategy is **vouch** (`vouch/`), a two-model trading agent
(fast System-1 deciding per tick, a local LLM System-2 rewriting its rules
between epochs, hard risk rails neither model can touch) that attests every
fill onchain as a canonical receipt. Its honest performance note: no edge
demonstrated yet; the value shown today is the attestation loop, not the
returns (`vouch/README.md`).

## Architecture at a glance

```
 fills (CEX/DEX)   commitEpoch + challenges/stakes
      |                        |
┌─────▼──────┐        ┌───────▼─────────────┐
│ vouch agent │------->│ Stylus contract     │<-- resolver (uphold|dismiss)
│ receipts +  │        │ (Arbitrum, USDG     │
│ evidence    │        │  escrow, slashing)  │
└────────────┘        └───────┬─────────────┘
                              │ events
                       ┌──────▼──────┐
                       │ indexer (TS)│  getLogs -> reducer -> JSON API
                       └──────┬──────┘
                       ┌──────▼───────┐
                       │ web (Next.js)│  leaderboard + trustless
                       └──────────────┘  browser receipt verifier
```

## Layout

| Path | What it is |
| --- | --- |
| `contracts/covenant/` | Stylus contract (Rust). Registry, checkpoints, challenges, USDG escrow. |
| `contracts/merkle-core/` | Pure Rust Merkle/receipt primitives shared by the contract. |
| `sdk/` | TypeScript SDK: receipt canonicalization, Merkle trees, epoch builder, RPC clients. |
| `indexer/` | Event-sourced indexer + JSON API (`/strategies`, `/strategies/:id`, `/health`). |
| `web/` | Next.js leaderboard + trustless receipt verifier (reads the chain from the browser). |
| `vouch/` | The flagship strategy: two-model trading agent that attests every fill onchain. Ships with its own dashboard (`python run_ui.py`). |
| `docs/` | Architecture, normative protocol spec, deployment runbook. |

## Quickstart

Development (workspace: `sdk`, `indexer`, `web`):

```bash
pnpm install            # workspace: sdk, indexer, web
pnpm -r test            # sdk + indexer suites
pnpm --filter @covenant/web run build

# contract tests + WASM build
cd contracts/covenant && cargo test
cargo build --release --target wasm32-unknown-unknown
cargo stylus export-abi
```

Deploy the contract and run the indexer/web/agent against it: the full
runbook (constructor args, env tables, the end-to-end demo script,
troubleshooting) is [docs/deployment.md](docs/deployment.md). In short:

```bash
# 1. deploy (see docs/deployment.md for the constructor args table)
cd contracts/covenant
cargo stylus deploy --network sepolia \
  --constructor-args <USDG> <RESOLVER> <BOND> <STAKE> <WINDOW_SECONDS>
cargo stylus verify --network sepolia

# 2. indexer (set START_BLOCK to the deploy block)
cd indexer
RPC_URL=https://sepolia-rollup.arbitrum.io/rpc CONTRACT_ADDRESS=0x… \
CHAIN=arbitrum-sepolia START_BLOCK=<deploy block> pnpm start

# 2.5 settle pending epochs whose challenge window passed (dry-run first)
pnpm --filter @covenant/sdk run finalize          # add -- --execute to send

# 3. leaderboard
cd web
INDEXER_URL=http://127.0.0.1:8787 \
NEXT_PUBLIC_CONTRACT_ADDRESS=0x… \
NEXT_PUBLIC_RPC_URL=https://sepolia-rollup.arbitrum.io/rpc \
NEXT_PUBLIC_CHAIN=arbitrum-sepolia pnpm dev

# 4. attested shadow run (ledger-only unless the COVENANT_* connection
#    trio is exported; see vouch/README.md and docs/deployment.md)
cd vouch && COVENANT_ATTEST=1 .venv/bin/python run_shadow.py --attest
```

USDG: 6 decimals. Arbitrum One `0x004B506865409877C9fA29bfb1ebA929984B9bbC`,
Arbitrum Sepolia `0xFFC95faa3d63Cde504a05B567C600B78C0b41892` (see
[Paxos docs](https://docs.paxos.com)).

## Docker

```bash
RPC_URL=https://sepolia-rollup.arbitrum.io/rpc CONTRACT_ADDRESS=0x… \
  docker compose up --build                 # indexer + leaderboard
docker compose --profile agent up            # + the vouch agent, attested shadow mode
```

## Documentation

- [docs/architecture.md](docs/architecture.md) - system overview, data flow,
  trust model, component responsibilities. Start here on day one.
- [docs/protocol.md](docs/protocol.md) - the normative protocol spec
  (canonical receipt, Merkle scheme, epoch lifecycle, cross-language
  conformance), with evidence citations into the code.
- [docs/deployment.md](docs/deployment.md) - operate runbook: deploy,
  indexer, web, agent, the demo script, troubleshooting.
- Per-component references: [sdk/README.md](sdk/README.md),
  [indexer/README.md](indexer/README.md), [web/README.md](web/README.md),
  [vouch/README.md](vouch/README.md),
  [contracts/covenant/README.md](contracts/covenant/README.md).
- Contributing and security: [CONTRIBUTING.md](CONTRIBUTING.md),
  [SECURITY.md](SECURITY.md), [CHANGELOG.md](CHANGELOG.md).

## Status

- Contract: 28/28 tests green (full lifecycle, regressions for the phantom-finalize
  and ordering bugs, admin flows, resolver deadline), clippy `-D warnings` + fmt in
  CI, fixed-seed property tests on merkle-core, wasm32 release build verified, ABI
  exported to `contracts/covenant/abi/ICovenant.sol` with a CI sync check. Not yet
  deployed.
- merkle-core: 6/6 tests plus the cross-language fixture suite, with a fixture
  freshness check in CI.
- SDK: 19/19 tests. Operator/challenger/resolver/admin clients with receipt
  waiting, USDG approve helpers, custom-error decoding, and `pnpm finalize`
  (`sdk/scripts/finalize-due.ts`, dry-run by default) so pending epochs settle and
  onchain PnL accrues.
- Indexer: 32/32 tests. Block timestamps, RPC retry, reorg rollback, atomic
  state persistence with resume, 7d/30d/all windows, pagination, SSE `/stream`.
- Web: `next build` passes. Leaderboard with time-window tabs and a LIVE pill
  (SSE), per-epoch challenge flow from the browser (window.ethereum + viem, no
  wallet SDK), resolver controls, and an evidence drawer that walks Merkle proofs
  and verifies the EIP-712 bundle signature against the onchain owner.
- Vouch: 97/97 tests (ruff, near-strict mypy, import-linter clean). Real venue
  fills become receipts; commits retry without breaking epoch sequence; register
  (`run_register.py`) and auto-finalize wired; evidence bundles v2 carry an
  EIP-712 operator signature (typed over strategy, epoch, equity, net flow,
  trades root) and optionally publish to IPFS (`COVENANT_IPFS_API`). Honest
  limits: committed equity is still operator-reported; the signature proves who
  said it and when, not that it is true - the challenge game exists for that.

## Judging criteria mapping

- **Contract quality.** 28/28 mock-VM tests covering the full lifecycle plus
  regressions for audited bugs (phantom-epoch finalize, out-of-order
  finalization), sequential accountability (an unsettled epoch blocks later
  commits), a resolver deadline so challenged stakes cannot freeze forever,
  two-step admin transfer with events on every admin action, full pause
  coverage, checks-effects-interactions ordering, property tests on
  merkle-core, exported ABI with a CI sync check, clippy `-D warnings` and
  rustfmt gates, and `cargo stylus verify` in the runbook.
- **Product-market fit.** Buyers of copy-trading signals, prop-firm
  candidates, and capital allocators who currently must trust screenshots.
  Non-custodial, so strategies can stay CEX-native. The challenge game is
  participatory: anyone can stake a challenge from the browser in two
  clicks, and a finalizer settles epochs so the onchain leaderboard accrues
  real, finalized returns.
- **Innovation.** Attestation instead of custody: the operator keeps their
  keys and their venue; the chain holds sequential commitments, an economic
  challenge game, and position-free Merkle proofs over trade receipts.
  Evidence bundles v2 are EIP-712 signed by the operator and carry an
  optional neutral Pyth price witness, so a reader can verify who published
  what, when, and cross-check it against an independent feed.
  Custodial vaults (dHEDGE, Enzyme) solve verification by changing the
  product; Covenant does not take custody.
- **Real problem.** Screenshot-PnL fraud: offchain leaderboards are trust-me
  databases. Here history is append-only, disclosures are verifiable, and
  falsehood is expensive (bond slashed on an upheld challenge).
- **USDG.** The unit of account end to end: bonds, challenger stakes,
  treasury, and all committed equity/net-flow values are USDG base units.
- **Deployed on Arbitrum.** Stylus contract targeting Arbitrum One and
  Arbitrum Sepolia; the indexer, leaderboard, and agent defaults are
  Arbitrum-first.
