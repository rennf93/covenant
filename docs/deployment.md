# Covenant deployment and operations runbook

**Audience:** whoever is deploying or demoing the stack (typically the
operator running the vouch agent). This is the operate runbook: prereqs,
deploy, indexer, web, agent env, the end-to-end demo script, and
troubleshooting. **Scope:** the commands that actually work against this
repository at v0.1.0. Protocol rules live in [protocol.md](protocol.md);
component internals live in [architecture.md](architecture.md) and the
per-component READMEs.

## 0. Prerequisites

- **Toolchain:** Node 24 + pnpm (workspace: `sdk`, `indexer`, `web`),
  Python 3.12 for the agent, Rust 1.91.0 with the `wasm32-unknown-unknown`
  target (pinned in `contracts/covenant/rust-toolchain.toml`).
- **cargo stylus:** install per the Arbitrum docs quickstart
  (https://docs.arbitrum.io/stylus/quickstart), e.g.:
  `cargo install --git https://github.com/OffchainLabs/stylus-targets cargo-stylus`
- **Keys and RPC:**
  - a funded EOA private key exported as `PRIVATE_KEY` for deploying
    (deploy gas + the strategy registration bond later),
  - an Arbitrum RPC endpoint. The public endpoint
    `https://sepolia-rollup.arbitrum.io/rpc` works for Arbitrum Sepolia.
- **USDG:** 6 decimals on every chain. Arbitrum One
  `0x004B506865409877C9fA29bfb1ebA929984B9bbC`, Arbitrum Sepolia
  `0xFFC95faa3d63Cde504a05B567C600B78C0b41892` (also exported as
  `USDG` in `sdk/src/abi.ts`). See the Paxos docs for the faucet.

## 1. Deploy the contract

Constructor args, in order (`contracts/covenant/src/lib.rs::constructor`):

| Arg | Type | Meaning | Suggested staging value |
| --- | --- | --- | --- |
| `usdg` | address | USDG token (6 decimals) | `0xFFC95faa3d63Cde504a05B567C600B78C0b41892` (Sepolia) |
| `resolver` | address | may resolve challenges | your operator EOA for staging |
| `bond_amount` | uint256 | registration bond, USDG base units (6 dp) | `10000000` (10 USDG) |
| `challenge_stake` | uint256 | challenger stake per challenge | `5000000` (5 USDG) |
| `challenge_window` | uint64 | seconds before an epoch may finalize | `300` (5 min demo; production wants hours) |

Zero values for any of these revert at construction.

```bash
cd contracts/covenant
cargo test                                        # 28/28 before you deploy
cargo stylus deploy --network sepolia \
  --constructor-args <USDG> <RESOLVER> <BOND> <STAKE> <WINDOW_SECONDS>
cargo stylus verify --network sepolia             # source verification
```

Note the deployed contract address and the deploy block number (the indexer
needs both). Mock USDG labeling rule: if the Paxos test faucet is
unavailable and you stage against a 6-decimal mock USDG, the demo materials
MUST label it as mock; the submission plan uses real USDG (see
`contracts/covenant/README.md`).

## 2. Register the strategy

Either action fires `registerStrategy(name, metadataUri)` and escrows the
bond from the caller:

- the vouch agent's Node bridge, action `register`
  (`vouch/vouch/attest/bridge.mjs`), or
- the SDK's `CovenantOperator.registerStrategy` (approve the USDG bond to
  the contract first; `sdk/src/client.ts`).

The strategy id is assigned sequentially starting at 1; put it in
`COVENANT_STRATEGY_ID` for the agent.

## 3. Run the indexer

Env (parsed in `indexer/src/main.ts::env`):

| Var | Required | Meaning |
| --- | --- | --- |
| `RPC_URL` | yes | Arbitrum RPC endpoint |
| `CONTRACT_ADDRESS` | yes | deployed Covenant contract |
| `CHAIN` | no | `arbitrum-sepolia` (default) or `arbitrum` |
| `START_BLOCK` | no | deploy block; defaults to the persisted cursor, then the chain head AT STARTUP |
| `PORT` | no | default 8787 |
| `STATE_FILE` | no | atomic JSON snapshot written after every batch and resumed on start (default `./covenant-state.json`) |

By hand:

```bash
cd indexer
pnpm install
RPC_URL=https://sepolia-rollup.arbitrum.io/rpc \
CONTRACT_ADDRESS=0x… \
CHAIN=arbitrum-sepolia \
START_BLOCK=<deploy block> \
pnpm start
```

Via compose (repo root, together with the web app):

```bash
RPC_URL=https://sepolia-rollup.arbitrum.io/rpc CONTRACT_ADDRESS=0x… \
  docker compose up --build
```

The API serves `GET /health`, `GET /strategies`
(`?window=7d|30d|all`, `?limit`, `?offset`), `GET /strategies/:id`, and
`GET /stream` (Server-Sent Events: full snapshot on connect and after every
indexed batch) in `indexer/src/server.ts`. Every USDG amount in a response is
a base-unit integer string (6 decimals); `committedAt`/`createdAt` are ISO.
Sanity check: `curl localhost:8787/health` returns the last indexed block.

## 4. Run the web leaderboard

```bash
cd web
INDEXER_URL=http://127.0.0.1:8787 \
NEXT_PUBLIC_CONTRACT_ADDRESS=0x… \
NEXT_PUBLIC_RPC_URL=https://sepolia-rollup.arbitrum.io/rpc \
NEXT_PUBLIC_CHAIN=arbitrum-sepolia \
pnpm dev                       # http://localhost:3000
```

`INDEXER_URL` is the server-side data source (`web/lib/api.ts`); the three
`NEXT_PUBLIC_*` vars are baked into the client bundle and are used by the
receipt verifier, which reads the chain from the browser
(`web/app/verify/verify-client.tsx`). All three are required for the verify
page to function.

## 5. Run the attested agent

Agent env (full list in `vouch/.env.example`; config is read in
`vouch/vouch/attest/commit.py::AttestationConfig.from_env`):

| Var | Meaning |
| --- | --- |
| `COVENANT_ATTEST=1` | enables the attestation path (receipts + evidence always; chain writes only when connected) |
| `COVENANT_RPC_URL` | onchain mode: Arbitrum RPC |
| `COVENANT_PRIVATE_KEY` | onchain mode: funded operator EOA key |
| `COVENANT_CONTRACT_ADDRESS` | onchain mode: deployed Covenant contract |
| `COVENANT_CHAIN` | default `arbitrum-sepolia` |
| `COVENANT_STRATEGY_ID` / `COVENANT_STRATEGY_NAME` | the registered strategy |

The three connection vars (`COVENANT_RPC_URL`, `COVENANT_PRIVATE_KEY`,
`COVENANT_CONTRACT_ADDRESS`) are the onchain switch: all three present =
chain writes fire; any missing = ledger-only mode (evidence bundles written,
nothing touches the chain).

Attested shadow session (live prices, paper money):

```bash
cd vouch
COVENANT_ATTEST=1 \
COVENANT_RPC_URL=https://sepolia-rollup.arbitrum.io/rpc \
COVENANT_PRIVATE_KEY=0x… \
COVENANT_CONTRACT_ADDRESS=0x… \
.venv/bin/python run_shadow.py --attest --minutes 60
```

Everything at once: `docker compose --profile agent up` from the repo root
(attested shadow mode; real-money mode is deliberately impossible in the
container).

## 6. The demo script

The full arc, end to end:

1. **Deploy** (section 1) and **register** (section 2).
2. **Attested shadow run** (section 5): the agent trades live SOL prices
   with paper money, writes fsynced receipt ledgers and evidence bundles per
   epoch, and commits epochs onchain when connected. Watch
   `COVENANT_EVIDENCE_DIR` (default `evidence/`) fill up.
3. **Finalize**: once each epoch's challenge window (5 min in staging)
   elapses, anyone may finalize
   (`CovenantOperator.finalizeEpoch` or the bridge `finalize` action). The
   contract applies PnL accounting; the leaderboard picks it up. Turnkey:
   `pnpm --filter @covenant/sdk run finalize` polls every strategy, lists the
   checkpoints whose window has elapsed, and finalizes them (DRY-RUN by
   default, `--execute` to send):

   | Var | Required | Meaning |
   | --- | --- | --- |
   | `RPC_URL` | yes | Arbitrum RPC endpoint |
   | `CONTRACT_ADDRESS` | yes | deployed Covenant proxy |
   | `PRIVATE_KEY` | yes (with `--execute`) | funded EOA key sending the finalize txs |
   | `CHAIN` | no | `arbitrum-sepolia` (default) or `arbitrum` |
4. **Challenge paths**, to show the economics: `challengeEpoch` a pending
   epoch with a second account (stake is escrowed, status flips to
   Challenged), then resolve both ways with the resolver:
   - dismiss: stake forfeited to the treasury, epoch finalizes;
   - uphold: epoch invalidated, strategy suspended, bond slashed to the
     challenger.
   If the resolver never shows up, the contract self-heals: after
   `committed_at + 4 * challenge_window` (20 minutes at the 5-minute staging
   window) anyone may force a dismissal, which refunds the stake instead of
   forfeiting it (`ChallengeResolved` with `forced = true`).
   The indexer and web UI reflect each transition from events alone.
5. **Browser verify**: open the web verify page, paste a receipt and its
   Merkle proof from the evidence bundle, and watch it verify against the
   committed root straight from your browser RPC (no indexer involved). Then
   flip a byte and watch it fail.
6. **Close the demo honestly**: the agent's performance is essentially flat
   so far, and that is the point. Covenant proves whatever happened,
   including nothing.

## 7. Troubleshooting

- **Unfunded key.** Deploy, register, and every commit need gas and (for
  register) the USDG bond from the same EOA. A key with Arbitrum ETH but no
  USDG fails at `registerStrategy` with a token transfer failure; a key with
  USDG but no ETH fails at the RPC. Fund both.
- **Mock USDG.** If you staged against a mock 6-decimal USDG, label it as
  mock in every demo artifact. Do not present mock-USDG numbers as real
  USDG; the contract README's rule is a hard one.
- **Indexer start block.** `START_BLOCK` defaults to the chain head at
  startup (`indexer/src/main.ts::main`). If you start the indexer after
  registering/committing, those events never get indexed. Pass the deploy
  block. Symptom: `/strategies` returns an empty leaderboard while the
  chain clearly has activity.
- **Bridge module resolution.** The agent's Node bridge
  (`vouch/vouch/attest/bridge.mjs`) resolves `viem` through the sdk package
  (the require is anchored relative to the bridge file's location) and
  imports the built ABI from `sdk/dist/src/abi.js`. If it fails to start,
  run `pnpm install` at the repo root and
  `pnpm --filter @covenant/sdk run build`; the bridge needs `node` on PATH.
- **Commit fails after evidence was written.** By design: the evidence
  bundle is written before the chain call, so a failed commit can be retried
  without rebuilding anything (`vouch/vouch/attest/commit.py`). Do not
  delete the evidence directory to "fix" a failed commit.
- **Self-audit failure.** If the agent's own first receipt does not verify
  onchain after a commit, the agent raises rather than staying silent. That
  is a protocol emergency: check that the contract address points at the
  deployed Covenant contract and that the SDK/agent fixtures are in sync
  ([protocol.md](protocol.md), section 4).
- **Web verify page does nothing.** It needs all three `NEXT_PUBLIC_*` vars
  at build/dev time; it talks to the RPC directly, not to the indexer
  (`web/app/verify/verify-client.tsx`).
- **Demo UI without a chain.** `node indexer/demo-server.mjs` (port 8788)
  serves the same API from synthetic data flagged `"demo": true`; the web
  app shows a DEMO DATA banner. It is never the real indexer.
