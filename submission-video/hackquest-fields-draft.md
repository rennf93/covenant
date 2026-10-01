# HackQuest Submission Fields — Covenant (draft, paste-ready)

## Project name
Covenant — Non-custodial Performance Attestation for Autonomous Agents

## One-line pitch
Agents claim performance. Covenant makes them prove it on-chain: Merkle-committed epochs, a 5-minute public challenge window, USDG bonds at stake.

## Description
Covenant is a non-custodial performance attestation layer for autonomous trading agents, built in Rust on Arbitrum Stylus.

Agents post a USDG bond, then commit performance epochs on-chain: every epoch's receipts are hashed into a Merkle tree and the root lands on Arbitrum Sepolia. Each epoch sits in a 5-minute challenge window during which anyone can dispute the claim by staking USDG. If the challenge is upheld, the strategy ends and the bond is slashed. If it elapses unchallenged, the epoch finalizes and the agent's equity, high-water mark, and cumulative PnL update permissionlessly — no curator, no custody.

A live trading agent (Vouch) runs on top: a calibrated System-1 model scores every market tick, deterministic rails enforce the money rules (stop, take, cooldown, kill switch), and every decision is logged with its full reasoning. Performance flows from the agent to the chain without a human in the loop.

**Deployed (Arbitrum Sepolia):** 0x92613e84e3473f3886172947ecdff3627978fd18 (source-verified)
**Live read:** equity 10.5 USDG-mock, 1 finalized epoch, bond 10 USDG escrowed

## Judging criteria mapping

### Smart contract quality
- 33 Rust tests green (unit + cross-language + property suites, `cargo test`)
- Source verified on-chain via reproducible Stylus build (`cargo stylus verify`, "Verification successful")
- Checks-effects-interactions ordering; strict epoch sequencing; permissionless finalize is pause-exempt (settles accounting, moves no tokens)

### Product-market fit
- Buyers: prop firms evaluating remote traders, copy-trading platforms vetting signal sellers, funds allocating to AI agents — anyone who today relies on screenshots and API self-reports
- Covenant replaces trust with settlement: the bond is escrowed before trading, the performance is committed during trading, and disputes are permissionless after trading

### Innovation and creativity
- Non-custodial: the protocol never holds keys or positions, only attestations and bonds
- The agent attests its own performance — and makes that dangerous on purpose: false claims are slashable by anyone, no oracle committee required
- Written in Rust on Stylus (WASM), not Solidity — cheaper WASM-level execution for hashing-heavy epochs

### Real problem solving
- The "screenshot-PnL fraud" problem: agents market themselves with images that cost nothing to fabricate
- Covenant's answer: the performance record is born on-chain, committed at epoch boundaries, and disputable by the crowd — the fraud moves from "fake a screenshot" to "post a bond and survive public scrutiny"

## USDG integration
- Unit of account: equity, PnL, high-water mark all denominated in USDG base units (6 dp)
- Performance bond: 10 USDG escrowed at strategy registration
- Challenger stake: 5 USDG per dispute, slashed on dismissal
- Deployed with Paxos USDG (0xFFC95faa3d63Cde504a05B567C600B78C0b41892) on the verification instance; demo flow uses a clearly-labeled mock token on the same 6-dp interface

## Technical
- Stack: Arbitrum Stylus (Rust → WASM), viem bridge, Python attestor, React dashboard
- Repo: https://github.com/rennf93/covenant
- Verified contract: https://sepolia.arbiscan.io/address/0x92613e84e3473f3886172947ecdff3627978fd18
