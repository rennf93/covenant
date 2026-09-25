# Covenant: Onchain Proof-of-Performance for Trading Strategies

## 1. Problem

Automated trading strategies (bots) are marketed with screenshots and self-reported
PnL. Buyers of copy-trading signals, prop-firm candidates, and capital allocators
have no tamper-evident way to verify a strategy's real history. Offchain leaderboards
are trust-me databases; onchain vaults (dHEDGE, Enzyme) solve it by taking custody,
which changes the product and excludes CEX-native strategies.

## 2. Product

Covenant is a **non-custodial attestation layer**: strategies commit cryptographic
evidence of their trading history onchain, performance is computed from committed
checkpoints, and anyone can verify a disclosed trade belongs to that history.
Strategies can trade anywhere (CEX or DEX); the ledger is on Arbitrum.

- **Strategy Registry**: operators register a strategy, post a performance bond in USDG.
- **Epoch Checkpoints**: on a fixed cadence (e.g. daily), the operator commits
  (epochIndex, ending equity in USDG terms, net flows, Merkle root of that epoch's
  trade receipts). Commits are sequential and tamper-evident: any retroactive edit
  breaks the chain.
- **Trade Receipts**: canonical, hashable records of executed fills (venue, order id,
  instrument, side, size, price, fee, timestamp). Only the Merkle root goes onchain.
  Receipts are disclosed per-trade and verifiable against the committed root.
- **Optimistic Verification**: each epoch commit has a challenge window. Anyone may
  challenge by staking USDG. A resolver (v1: platform multisig, later: decentralized
  court) either upholds the challenge (epoch invalidated, operator bond slashed to
  challenger) or dismisses it (challenger stake forfeited). This makes false history
  economically irrational instead of impossible, which is the honest trust model for
  offchain fills.
- **Performance Accounting**: PnL per epoch = equity_t - equity_{t-1} - deposits_t
  + withdrawals_t. Returns, drawdown, and streaks are derived onchain where cheap,
  offchain by the indexer for the leaderboard.
- **USDG Settlement**: bonds, stakes, and equity are denominated in USDG (Paxos),
  providing the stable unit of account and the hackathon's extra-consideration axis.

## 3. Trust model (stated honestly)

Self-reported fills cannot be covenant against a CEX without venue cooperation.
Covenant therefore guarantees *consistency and commitment*, not absolute truth:
1. History is **append-only**: an operator cannot quietly rewrite the past.
2. Disclosures are **verifiable**: any shown trade receipt either validates against
   the committed root or the strategy is provably lying.
3. Falsehood is **expensive**: bonds and challenger stakes make fraud a negative-EV
   attack under a functioning resolver.
4. DEX-native strategies can go further (route fills through a settlement adapter),
   which upgrades their attestations from self-reported to venue-verified. This is
   the roadmap to "verified" vs "attested" tiers on the leaderboard.

## 4. Architecture

```
┌────────────┐   commits+receipts   ┌──────────────────────┐
│ Strategy    │ ───────────────────▶ │ Stylus Contract       │
│ vouch       │   (vouch adapter or    │ (Arbitrum One/Sepolia)│
│ (vouch agent)   │    any bot SDK)      │ USDG bonds/stakes     │
└────────────┘                       └──────────┬───────────┘
        ▲                                       │ events + state
        │ challenges (anyone)                   ▼
┌───────┴─────────┐                   ┌──────────────────────┐
│ Challengers      │                   │ Indexer (TS)         │
│ (stake USDG)     │                   │ → Postgres/JSON API  │
└──────────────────┘                   └──────────┬───────────┘
                                       ┌──────────▼───────────┐
                                       │ Leaderboard (Next.js)│
                                       │ + public REST API     │
                                       └──────────────────────┘
```

Components (all in this monorepo):
- `contracts/` Stylus contract (Rust). Registry, checkpoints, Merkle verification,
  challenge escrow, slashing. Unit-tested in Rust; deployment via cargo-stylus.
- `sdk/` TypeScript SDK: typed client for operators (commit epochs, publish receipts)
  and readers (fetch verified performance). Used by the vouch adapter and the indexer.
- `web/` Next.js leaderboard: onchain reads via viem, strategy pages with receipt
  verification UI (drop a receipt + proof, see it verify), public REST API routes.
- `docs/` this spec, threat model, deployment runbooks.

## 5. Contract spec (v1)

Types: amounts are i256/i128 USDG base units; ids are uint256.

- `registerStrategy(string name, string metadataURI)` payable-in-USDG bond
  → emits StrategyRegistered(id, owner, bond)
- `commitEpoch(uint256 strategyId, uint64 epochIndex, int256 equityUsdg,
   int256 netFlowUsdg, bytes32 tradesRoot, string evidenceURI)`
  - only owner; epochIndex == last+1; monotonic timestamps
  - stores checkpoint; status = Pending until challenge window (configurable, e.g. 48h)
    elapses; then Finalized
  - emits EpochCommitted
- `challengeEpoch(uint256 strategyId, uint64 epochIndex, string reason)`
  - stakes USDG; emits EpochChallenged; freezes epoch status = Challenged
- `resolveChallenge(uint256 strategyId, uint64 epochIndex, bool upheld)`
  - only resolver; upheld → checkpoint invalidated + strategy suspended + bond
    slashed to challenger; dismissed → challenger stake forfeited to treasury
- `verifyReceipt(uint256 strategyId, uint64 epochIndex, bytes32[] proof,
   bytes32 receiptHash)` → bool (pure verification against committed root)
- `getStrategy(id)`, `getCheckpoint(strategyId, epochIndex)`, `getEpochCount(id)`,
  `getPerformance(id)` (cumulative PnL, high-water mark, finalized epochs count)
- Admin: `setChallengeWindow`, `setResolver`, `pause`, bond parameter.
- Security: CEI ordering on all token movements, pull-pattern for resolver payouts
  where practical, no unbounded loops, i256 overflow-checked conversions, Pausable.

## 6. Receipt canonical format

```
receipt = keccak256(abi.encode(
  strategyId, epochIndex, venue (u8: 0=Coinbase, 1=ArbitrumDEX, 2=Other),
  venueOrderIdHash (keccak of venue order id, avoids leaking CEX ids),
  instrumentHash (keccak of e.g. "SOL-USD" or "SOL/USDC@arbitrum"),
  side (u8: 0=buy, 1=sell), sizeSignedBase (i256, instrument decimals),
  priceQuote (u256, 1e8), feeQuote (u256, 1e8), filledAt (u64 unix)
))
```

## 7. Operator SDK flow (vouch adapter implements this)

1. register + bond (one-time, env-gated like vouch's real mode)
2. per fill: append canonical receipt to local epoch log (SQLite/JSONL)
3. at epoch close: build Merkle tree, `commitEpoch`
4. receipts published alongside (IPFS/HTTP evidenceURI + optional onchain root)

## 8. Milestones (three-week buildathon window)

- W1: contract complete + unit tests + local deploy on Arbitrum Sepolia;
  SDK complete; indexer MVP-but-real (RPC-driven, event-sourced).
- W2: vouch agent ("new name") with dual venue adapter (Coinbase | Arbitrum DEX) +
  attestation module wired to SDK; leaderboard with live strategy.
- W3: receipt verification UI, hardening, demo video, docs, USDG integration polish,
  deploy to Arbitrum One if fees permit, submission.

## 9. vouch adapter spec (implemented in vouch/attest)

Once the vouch port lands (renamed, dual venue), the attestation module is:

- `attest/ledger.py` - append-only JSONL of canonical receipts per epoch
  (venue, venue_order_id, instrument, side, size, price, fee, filled_at).
  Written in the same transaction context as fill handling; crash-safe.
- `attest/commit.py` - at epoch close: build EpochBuilder, compute equity in
  USDG terms (venue balances converted at the epoch's closing mark), call
  commitEpoch via the SDK operator client, publish the evidence bundle
  (receipts + proofs) to evidence_uri, verify one receipt back onchain as a
  self-audit.
- Venue normalization: Coinbase fills (SOL-USD, 8dp) and Arbitrum DEX fills
  (SOL/USDC, onchain decimals) both map to canonical receipts with
  venueOrderIdHash = keccak(venue order id), instrumentHash = keccak(instrument).
- Real mode interlock: attestation writes are allowed in shadow AND real mode,
  but the USDG onchain commit only fires in real mode or when explicitly
  enabled, mirroring vouch's existing safety rails.
