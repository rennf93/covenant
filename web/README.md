# @covenant/web

**Audience:** developers working on the leaderboard site, and anyone who
wants to know what the UI does and does not trust. **Scope:** `web/` at
v0.1.0. System context: [../docs/architecture.md](../docs/architecture.md).

## What it is

A Next.js (App Router) leaderboard for Covenant strategies plus a trustless
receipt verifier. Two data paths, kept separate on purpose:

- **Indexer API (server-side):** rankings and epoch history are read from
  the indexer's JSON API (`web/lib/api.ts`, 15s revalidation). Fast, but
  only as trustworthy as the chain the indexer mirrors.
- **Chain (client-side):** the verify page talks to the RPC directly via
  viem and re-verifies a receipt's Merkle proof against the committed
  `tradesRoot` in your browser. No indexer, no API, no trust: if the proof
  does not check out onchain, the UI shows it failing.

## Pages

| Route | What it shows |
| --- | --- |
| `/` | leaderboard: strategy rankings with derived performance (equity, cumulative PnL, return %, epoch status counts), best return first; a DEMO DATA banner when the indexer reports `"demo": true` |
| `/strategies/[id]` | full strategy detail: epochs with equity, netFlow, PnL, tradesRoot, evidence URI, lifecycle status |
| `/verify` | the trustless receipt verifier: paste a receipt hash and its Merkle proof from an evidence bundle, verify against the chain from the browser |

Shared formatting/pill helpers live in `web/lib/api.ts`
(`formatUsdg` for 6-decimal USDG base units, `formatReturnWad` for 1e18
fixed point returns, status pills).

## Env vars

| Var | Where | Required | Meaning |
| --- | --- | --- | --- |
| `INDEXER_URL` | server | no (default `http://127.0.0.1:8787`) | indexer JSON API base URL |
| `NEXT_PUBLIC_CONTRACT_ADDRESS` | client bundle | yes for `/verify` | deployed Covenant proxy |
| `NEXT_PUBLIC_RPC_URL` | client bundle | yes for `/verify` | Arbitrum RPC the browser will call |
| `NEXT_PUBLIC_CHAIN` | client bundle | no (default `arbitrum-sepolia`) | chain for browser reads |

The three `NEXT_PUBLIC_*` vars are baked in at build time; the verify page
refuses to run without them (`web/app/verify/verify-client.tsx`).

## Run

```bash
pnpm install                                # repo root
cd web
INDEXER_URL=http://127.0.0.1:8787 \
NEXT_PUBLIC_CONTRACT_ADDRESS=0x… \
NEXT_PUBLIC_RPC_URL=https://sepolia-rollup.arbitrum.io/rpc \
NEXT_PUBLIC_CHAIN=arbitrum-sepolia \
pnpm dev                                    # http://localhost:3000
```

Production build (the CI gate):

```bash
pnpm --filter @covenant/web run build
```

In compose (`repo root`), the web container talks to the indexer container
at `http://indexer:8787` and serves on port 3000.

## Tests

There is no JS unit suite for the web app; the quality gate is
`next build` passing (run in CI, `.github/workflows/ci.yml`) plus the
verification path being end-to-end testable in the browser against a
deployed contract (see [../docs/deployment.md](../docs/deployment.md), the
demo script).

## How it connects to the others

- Reads the indexer (`../indexer/`) for rankings and epoch history.
- Imports `@covenant/sdk` (the ABI) for browser-side contract reads.
- Evidence bundles produced by the agent (`vouch/vouch/attest/commit.py`)
  are the intended input format for the verify page: receipts, hashes,
  and Merkle proofs per epoch.
