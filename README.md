# Covenant

Onchain proof-of-performance for trading strategies, built for the
Arbitrum Open House Singapore Buildathon.

Strategies commit sequential epoch checkpoints (ending equity in USDG terms,
net external flows, and a Merkle root of canonical trade receipts) to a Stylus
contract on Arbitrum. Anyone can challenge a pending epoch by staking USDG; a
resolver either upholds (epoch invalidated, operator bond slashed) or dismisses
(stake forfeited). The leaderboard ranks strategies by returns computed from
finalized checkpoints only.

See [docs/DESIGN.md](docs/DESIGN.md) for the protocol spec and trust model.

## Layout

| Path | What it is |
| --- | --- |
| `contracts/covenant/` | Stylus contract (Rust). Registry, checkpoints, challenges, USDG escrow. |
| `contracts/merkle-core/` | Pure Rust Merkle/receipt primitives shared by the contract. |
| `sdk/` | TypeScript SDK: receipt canonicalization, Merkle trees, epoch builder, RPC clients. |
| `indexer/` | Event-sourced indexer + JSON API (`/strategies`, `/strategies/:id`, `/health`). |
| `web/` | Next.js leaderboard + trustless receipt verifier (reads the chain from the browser). |
| `vouch/` | The flagship strategy: a two-model trading agent (fast System-1 deciding per tick, local LLM System-2 rewriting its rules between epochs, hard risk rails neither model can touch) that attests every fill onchain as a canonical receipt. Ships with its own dashboard (`python run_ui.py`). |

## Docker

```bash
RPC_URL=https://sepolia-rollup.arbitrum.io/rpc CONTRACT_ADDRESS=0x... \
  docker compose up --build                 # indexer + leaderboard
docker compose --profile agent up            # + the vouch agent, attested shadow mode
```

## Status

- Contract: 11/11 tests green, `wasm32` release build verified, ABI exported to
  `contracts/covenant/abi/ICovenant.sol`. Not yet deployed.
- SDK: 8/8 tests. Merkle semantics are fixture-conformed between TS and Rust.
- Indexer: 5/5 tests.
- Web: `next build` passes.
- Vouch: 10/10 attestation tests; receipts and Merkle roots fixture-conformed
  to the SDK (the same `merkle.json` the Rust contract side validates against).

## Development

```bash
pnpm install            # workspace: sdk, indexer, web
pnpm -r test            # sdk + indexer suites
pnpm --filter @covenant/web run build

# contract tests + WASM build
cd contracts/covenant && cargo test
cargo build --release --target wasm32-unknown-unknown
cargo stylus export-abi
```

## Deploying (staging)

```bash
cd contracts/covenant
cargo stylus deploy \
  --network sepolia \
  --constructor-args <USDG_ADDRESS> <RESOLVER_ADDRESS> <BOND> <STAKE> <WINDOW_SECONDS>
```

USDG: 6 decimals. Arbitrum One `0x004B506865409877C9fA29bfb1ebA929984B9bbC`,
Arbitrum Sepolia `0xFFC95faa3d63Cde504a05B567C600B78C0b41892` (see
[Paxos docs](https://docs.paxos.com)).

Then run the indexer and web app:

```bash
cd indexer
RPC_URL=https://sepolia-rollup.arbitrum.io/rpc \
CONTRACT_ADDRESS=0x… \
CHAIN=arbitrum-sepolia \
START_BLOCK=<deploy block> \
pnpm start

cd web
INDEXER_URL=http://127.0.0.1:8787 \
NEXT_PUBLIC_CONTRACT_ADDRESS=0x… \
NEXT_PUBLIC_RPC_URL=https://sepolia-rollup.arbitrum.io/rpc \
NEXT_PUBLIC_CHAIN=arbitrum-sepolia \
pnpm dev
```
