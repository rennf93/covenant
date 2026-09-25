# @covenant/indexer

**Audience:** developers running or extending the indexer/API, and consumers
of the JSON API. This is the component reference: architecture, env vars,
API surface, and tests. **Scope:** `indexer/` at v0.1.0. Where the indexer
sits in the system: [../docs/architecture.md](../docs/architecture.md).

The indexer is event-sourced and read-only. The chain is the source of
truth; this service never writes anything and trusts nothing except decoded
contract logs.

## What it is

Three layers, kept deliberately separate:

1. **Pure reducer** (`indexer/src/state.ts`): folds decoded Covenant events
   into a `CovenantState` of strategies and epochs, and derives leaderboard
   figures from FINALIZED epochs only (equity, cumulative PnL, per-status
   epoch counts, headline `returnWad` = cumulative PnL over the first
   finalized equity in 1e18 fixed point, PnL-relative so deposits do not
   inflate it). Knows nothing about RPC, files, or HTTP, so it is
   exhaustively unit-testable and reusable.
2. **Log transport** (`indexer/src/transport.ts`): the only RPC-aware piece.
   Polls `getLogs` in bounded batches (default 5000 blocks, hard cap 50000),
   decodes against `covenantAbi` from `@covenant/sdk`, folds events through
   the reducer, and checks reducer invariants before accepting a batch.
   Backfills from a start block, then tails the head (poll interval 2s).
3. **JSON API** (`indexer/src/server.ts`): dependency-free `node:http` over
   the reduced state. BigInts serialize to strings; Maps to objects.

`indexer/src/main.ts` wires them together from env. `indexer/demo-server.mjs`
is NOT the indexer: it serves the same API shape from synthetic data flagged
`"demo": true` so the web UI can be reviewed without a deployed contract
(the web app shows a DEMO DATA banner when it sees the flag).

## Env vars

| Var | Required | Default | Meaning |
| --- | --- | --- | --- |
| `RPC_URL` | yes | - | Arbitrum RPC endpoint |
| `CONTRACT_ADDRESS` | yes | - | deployed Covenant proxy |
| `CHAIN` | no | `arbitrum-sepolia` | `arbitrum` or `arbitrum-sepolia` |
| `START_BLOCK` | no | chain head at startup | set this to the deploy block or earlier events are missed |
| `PORT` | no | `8787` | API listen port |

## JSON API

| Endpoint | Returns |
| --- | --- |
| `GET /health` | `{ ok, lastBlock }` (last indexed block, inclusive) |
| `GET /strategies` | leaderboard rows, best `returnWad` first; strategies with no finalized epochs sort last |
| `GET /strategies/:id` | full strategy: row plus all epochs with equity, netFlow, tradesRoot, evidenceUri, status, committedAt, pnl |
| anything else | `404 { error }` |

Status codes match the contract: epoch status 0 pending, 1 finalized,
2 challenged, 3 invalidated; strategy status 0 active, 1 suspended. All
amounts are USDG base units (6 decimals) as decimal strings.

## Run

```bash
cd indexer
pnpm install
RPC_URL=https://sepolia-rollup.arbitrum.io/rpc \
CONTRACT_ADDRESS=0x… \
CHAIN=arbitrum-sepolia \
START_BLOCK=<deploy block> \
pnpm start
```

Or via the repo-root compose file (`docker compose up --build`), which runs
this container on port 8787. Build/typecheck:

```bash
pnpm --filter @covenant/indexer run build
pnpm --filter @covenant/indexer run typecheck
```

## Tests

```bash
pnpm --filter @covenant/indexer test   # 5/5 green
```

The suite (`indexer/test/state.test.ts`) exercises the pure reducer:
strategy registration, epoch commits, challenge/resolve transitions,
finalized-only derivation, and the `invariantsHold` check the transport
runs on every batch.

## How it connects to the others

- Consumes `covenantAbi` from `@covenant/sdk` to decode logs.
- Feeds the web leaderboard (`web/lib/api.ts` reads `/strategies` and
  `/strategies/:id` server-side).
- Deployment details (deploy block gotcha, compose wiring): see
  [../docs/deployment.md](../docs/deployment.md).
