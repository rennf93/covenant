# HANDOFF: Proven x jev-trading (Arbitrum Open House Singapore Buildathon)

Written 2026-09-25. Read this before touching anything.

**STATUS UPDATE (same day): the fork is DONE and lives in this repo at
`operator/`.** The jev copy was moved INTO the proven repo (per the user; one
submission repo) at `operator/` with the package renamed only in product terms:
run scripts at `operator/run_*.py`, package `operator/jev/`, attestation at
`operator/attest/` (pure-python keccak + canonical receipts + merkle, all
fixture-conformed to the SDK, 10 tests green: `cd operator && python3 -m
unittest discover -s tests`). Dual venue is in `operator/jev/execution.py`
(paper / coinbase / arb-paper; arb-paper is the honest "Arbitrum DEX paper
venue" priced from the live feed, booking venue class ArbitrumDex). Shadow
wiring is done: `run_shadow.py --attest` (or PROVEN_ATTEST=1) records receipts
per fill, closes an epoch at each System-2 boundary (epoch 0 netFlow carries
the seed capital), and writes evidence bundles; chain commits fire only when
PROVEN_RPC_URL/PRIVATE_KEY/CONTRACT_ADDRESS are all set (ledger-only mode
otherwise). The node bridge is `operator/attest/bridge.mjs` (viem resolved via
the sdk package; actions register/commit/finalize/verify). REMAINING: funded
Arbitrum Sepolia key -> deploy (section "Deployment"), register the strategy,
then the onchain demo run. Original jev-trading repo was never touched.

---

## 1. The story (why this exists)

The user is hunting paid code bounties for income. After a systematic sweep of
the bounty market (BountyHub, Opire, Algora, bbradar, HackQuest), almost every
OSS feature-bounty lane turned out to be hostile to AI-authored work:

- fluxerapp/fluxer: strict LLM_USAGE_POLICY (submission closed / account block).
- microG GmsCore ($15k RCS, $2.3k WearOS): community actively attacks AI
  attempts ("AI slop" purges), plus hardware-bound testing.
- Algora/ZIO bounties: 25-36 closed unmerged external PRs per issue, zero
  maintainer reviews. Cold PRs are near-hopeless there.
- Many BountyHub listings are stale (closed GitHub issues, dead repos).

Conclusion: the winnable lane for AI-assisted building is **judged hackathons**
where output is what matters. The user registered for the **Arbitrum Open House
Singapore: Online Buildathon** on HackQuest (registered 2026-09-25; registration
countdown ended ~Oct 2):

- Prize pool: $115,000 = $70k overall ($40k/$20k/$10k) + $15k "Promising
  Products" track ($7k/$5k/$3k) + $30k milestone grants (discretionary).
- Judging criteria (in order): (1) smart contract quality, (2) product-market
  fit, (3) innovation/creativity, (4) real problem solving.
- **Extra consideration for integrating Paxos USDG stablecoin.** We use USDG as
  the unit of account throughout (bonds, stakes, equity, PnL).
- Must deploy on an Arbitrum chain (Arbitrum One / Sepolia / Robinhood Chain).
  At least 1 of 3 overall prizes is reserved for a Robinhood Chain project and
  1 for an Arbitrum chain.
- Three-week build window after registration closes; fully online; AI IDE usage
  is officially encouraged by the event page.

The event page + judging details are visible on the HackQuest page when logged
in: https://www.hackquest.io/hackathons/Arbitrum-Open-House-Singapore-Online-Buildathon
(The user's HackQuest session is/was logged into the ZCode in-app browser.)

## 2. The project: "Proven"

Onchain proof-of-performance for trading strategies. Strategies commit
sequential epoch checkpoints (ending equity in USDG, net external flows, and a
Merkle root of canonical trade receipts) to a Stylus contract on Arbitrum.
Anyone can challenge a pending epoch by staking USDG; a resolver either upholds
(epoch invalidated + operator bond slashed to challenger) or dismisses (stake
forfeited to treasury). Leaderboard ranks by returns computed from finalized
checkpoints only. Non-custodial: strategies keep trading wherever they trade.

Full spec: `docs/DESIGN.md` (read it, especially sections 3, 5, 9).

Why it can win: contract quality is judging criterion #1 and this codebase is
tested to that standard; USDG integration is a named bonus axis and we have it;
the demo strategy is the user's REAL trading bot (most hackathon teams mock
their data); and the trust model is honest (tamper-evident + economically
policed, not pretending to be omniscient).

## 3. What is already built (all committed, all green)

Repo: /Users/renzof/Documents/GitHub/ZZZ/proven (git, 6 commits, branch main).

| Component | Status | Tests |
| --- | --- | --- |
| `contracts/proven/` Stylus contract (Rust) | complete | 11/11 (mock VM), wasm32 release build verified, ABI at `abi/IProven.sol` |
| `contracts/merkle-core/` shared Merkle/receipt primitives | complete | 5/5 + 1 cross-language fixture suite |
| `sdk/` TypeScript SDK (receipts, merkle, epoch builder, RPC clients) | complete | 8/8 |
| `indexer/` event reducer + RPC transport + JSON API | complete | 5/5 |
| `web/` Next.js leaderboard + browser-side receipt verifier | complete | `next build` passes |

All suites run via: `pnpm -r test` (workspace root), and
`cargo test` inside `contracts/proven`.

Contract surface (see `contracts/proven/abi/IProven.sol`):
- `registerStrategy(name, metadataURI)` - pulls USDG bond, assigns sequential id.
- `commitEpoch(strategyId, epochIndex, equity, netFlow, tradesRoot, evidenceURI)`
  - owner-only, strictly sequential epochs, strictly increasing timestamps.
- `finalizeEpoch` - permissionless after the challenge window; applies accounting
  pnl = equity_t - equity_{t-1} - netFlow_t, updates high-water mark.
- `challengeEpoch` - stakes USDG within the window, freezes the checkpoint.
- `resolveChallenge(upheld)` - resolver-only; upheld = stake refunded + bond
  slashed to challenger + strategy suspended; dismissed = stake to treasury,
  checkpoint finalized.
- `verifyReceipt(strategyId, epochIndex, proof[], receipt)` - onchain Merkle check.
- Views: `config`, `getStrategy`, `getPerformance`, `getCheckpoint`, `strategyCount`.

Protocol invariants worth knowing (all enforced/tested):
- Epoch 0's netFlow is the seed capital; cumulative PnL telescopes to
  finalEquity - totalNetFlows (economically correct: deposits are never PnL).
- Empty epochs must not commit a tree (SDK refuses; contract stores bytes32(0)
  and verifyReceipt returns false against zero roots).
- Merkle scheme: sorted-pair keccak256 with duplicate-last padding, proofs are
  position-free. TS and Rust are fixture-conformed
  (`contracts/merkle-core/tests/fixtures/merkle.json`, regenerated by
  `sdk/scripts/gen-fixtures.ts`). If either side drifts, the Rust test fails.
- Existence sentinels: strategy owner == 0 means unregistered; checkpoint
  existence is checked against the strategy's committed epoch_count (NOT
  committed_at, which is legitimately 0 at VM time 0 - this bit us once).

## 4. Technical gotchas (stylus-sdk 0.10.9 / alloy 1.5-1.6) - read before editing

- Storage ints are alloy types, NOT Rust primitives: `uint64` storage fields are
  `U64`, `uint8` are `U8`. Convert at the boundaries (`U64::from(x)`, `.to::<u8>()`,
  or `.as_limbs()[0]` for events, which use plain u64 for uint64 fields).
- sol_storage structs in mappings: no `insert`, no Option. Use
  `map.setter(key)` (mutable accessor) and `map.getter(key)` (read); existence
  via sentinel fields. Nested mappings: bind the inner map first
  (`let m = self.checkpoints.setter(id); let mut cp = m.setter(epoch);`) -
  chaining on a temporary guard hits E0716.
- External token calls: `sol_interface!` methods take (host, context, args...);
  context for mutating calls is `Call::new_mutating(self)`.
- Events: define in `sol!`, emit with `self.vm().log(EventStruct { .. })`
  (LogAccess from prelude).
- sol! error types have no Debug; ProvenError has a manual Debug impl.
- Testing: dev-dependency `stylus-sdk = { features = ["stylus-test"] }` is
  REQUIRED or native tests fail to link. Mock VM: `TestVM::new()`, then
  `vm.set_sender`, `vm.set_block_timestamp`, and `vm.mock_call(target, calldata,
  value, Ok(ret))` where calldata must be byte-identical to what sol_interface
  encodes (tests build it via a mirrored sol! interface + SolCall::abi_encode).
- rust-toolchain.toml pins 1.91.0 + wasm32 target inside contracts/proven;
  merkle-core must stay on alloy-primitives 1.x to match the contract crate.
- pnpm v11: onlyBuiltDependencies moved to pnpm-workspace.yaml (root has it).
  TS packages compile to dist (tsc) and tests run on dist; imports in TS use
  `.js` extensions (NodeNext).

## 5. THE ACTUAL TASK: jev-trading fork + integration

Original repo: /Users/renzof/Documents/GitHub/ZZZ/side projects/jev-trading
**IMPORTANT: do not touch the original. Another session may still be working on
it. COPY it to a new directory (suggest "side projects/<new-name>", pick a good
product name; the platform itself is called "Proven") and work only in the copy.**

What jev-trading is today (README is accurate):
- Two-model loop: laya (fast non-autoregressive model, HTTP server) as System-1
  making per-tick decisions; local qwen3.8-27b via llama.cpp as System-2
  reviewing each epoch and rewriting System-1's rules. Every System-2 proposal
  must pass hard constraint rails in code (position cap, bracket bounds,
  cooldown, 20% drawdown kill switch the rewriter cannot touch).
- Modes: simulation (synthetic), shadow (live prices, paper money), real
  (Coinbase spot, env-gated: JEV_VENUE=coinbase + keys + --confirm-real;
  --max-usd per-order cap enforced in jev/execution.py).
- Layout: run.py / run_shadow.py / run_real.py + jev/{broker, execution,
  market, rules, session, system1, system2}.py. Clean abstractions: the venue
  is already behind an adapter (PaperVenue / make_venue).
- Honest state: no trading edge demonstrated yet (flat). That is FINE for the
  hackathon: Proven's product is truthful verification, not alpha. Pitch:
  "flat PnL proven onchain is exactly what buyers need to see" - the ledger
  exposes bad strategies as reliably as good ones.

### The integration (spec also in docs/DESIGN.md section 9)

1. **Rename + dual venue.** In the copy: generalize the venue adapter so
   Coinbase and an Arbitrum DEX venue are both first-class (venue enum in
   receipts already models this: 0=Coinbase, 1=ArbitrumDex, 2=Other). The
   Arbitrum venue can be implemented against a Uniswap-style router or, for the
   buildathon, against paper fills priced from the same live feeds the shadow
   mode uses - but implement it FULLY (no stubs; if paper, make it an explicit,
   honest "paper venue" mode, not a pretend-live one).
2. **Attestation module** (`attest/` package in the fork, per docs/DESIGN.md 9):
   - `ledger.py`: append-only JSONL of canonical receipts per epoch, written in
     the fill-handling path. Receipt fields exactly per sdk/src/receipt.ts
     (strategyId, epochIndex, venue, venueOrderIdHash=keccak(order id),
     instrumentHash=keccak(instrument string), side, sizeSignedBase,
     priceQuote/feeQuote in 1e8, filledAt). Use the SDK's makeReceipt/
     EpochBuilder via a small python->node bridge OR reimplement the hashing in
     Python and add a fixture test against sdk fixtures (mirroring
     contracts/merkle-core/tests/cross_language.rs). Either is acceptable;
     fixtures MUST be shared.
   - `commit.py`: at epoch close build the tree, convert equity to USDG terms
     (closing mark), call commitEpoch via the SDK operator client
     (sdk/dist/src/client.js ProvenOperator; needs a funded EOA key in env),
     publish evidence bundle (receipts + proofs JSON) to evidence_uri, then
     verify one receipt back onchain as a self-audit.
   - Safety interlocks mirror jev's existing rails: onchain commits allowed in
     shadow mode only when explicitly enabled; real mode requires the human
     flip. NEVER let an autonomous loop trigger real-money trades (this is a
     standing rule in the jev repo).
3. **Wire the demo**: run the fork in shadow mode against live SOL data,
   committing one epoch per the challenge window; keep the indexer running so
   the web leaderboard shows the strategy live.

### Deployment (needs the user, block until they provide)

- Funded Arbitrum Sepolia key (MetaMask export) with Sepolia ETH for gas and
  test USDG (Sepolia USDG: 0xFFC95faa3d63Cde504a05B567C600B78C0b41892; check
  faucet availability - if the Paxos test token is unobtainable, deploying a
  6-decimal mock USDG for staging is acceptable IF clearly labeled and the
  mainnet submission plan uses real USDG; flag it to the user either way).
- Deploy: `cd contracts/proven && cargo stylus deploy --network sepolia
  --constructor-args <USDG> <RESOLVER> <BOND> <STAKE> <WINDOW>` (see README).
- Then: indexer with RPC_URL/CONTRACT_ADDRESS/CHAIN/START_BLOCK, web with
  INDEXER_URL/NEXT_PUBLIC_* (env names in indexer/src/main.ts and web/lib/api.ts).
- End-to-end demo to produce for the submission: register -> commit epochs ->
  (time travel / wait) finalize -> challenge (fail + succeed paths) ->
  browser receipt verification video.

### Submission prep (due when the build window closes)

- Repo hygiene: LICENSE, README with architecture diagram (docs/DESIGN.md has
  the ASCII one), demo video (there is a .video_agent kit in this workspace if
  video tooling is wanted), contract source verification on Arbiscan/Stylus
  (cargo stylus verify).
- HackQuest project submission fields + Project Gallery entry.
- Judging checkboxes to hit explicitly in the README: contract quality
  (tests+verify), PMF story (copy-trading/prop-firm buyers; Hypernova-like
  narrative), innovation (non-custodial attestation vs custodial vaults), real
  problem (screenshot-PnL fraud), USDG integration (unit of account + bond/stake
  escrow), deployed on Arbitrum.

## 6. Standing context

- User's standing instructions: full implementations only, no skeletons/
  shortcuts/workarounds; aimed at prod. They supervise and call the shots; a
  parallel session may be working in other repos - never touch repos outside
  the current task without checking.
- A daily cron automation ("每日新赏金扫描", 10:00 local) sweeps BountyHub and
  Algora for fresh verifiable bounties and reports only verified-new ones.
  Leave it alone unless the user says otherwise.
- User's HackQuest/MetaMask wallet is connected on Arbitrum; they are broke and
  want fast money where legitimate - the daily sweep is the only fast-money
  channel; the buildathon payout is the main prize.
- User identity for git in this repo: configured repo-local as Renn F
  <rennf93@users.noreply.github.com>. GitHub account rennf93; prior hackathon
  entries this fall: roboco (Nebius Global AI Hackathon), robo-fleet (All
  Things Agentic). Never add AI attribution to commits; never use em-dashes.

## 7. Verify-your-start checklist for the next session

1. `cd /Users/renzof/Documents/GitHub/ZZZ/proven && git log --oneline` - expect
   6-7 commits ending at the jev adapter spec.
2. `pnpm install && pnpm -r test` - expect sdk 8/8, indexer 5/5.
3. `cd contracts/proven && cargo test` - expect 11/11.
4. `pnpm --filter @proven/web run build` - expect clean build.
5. Read docs/DESIGN.md sections 3 and 9. Then check whether the other session
   is done with jev-trading (git log there / ask the user) before copying it.
