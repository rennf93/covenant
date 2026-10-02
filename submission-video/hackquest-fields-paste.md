# HackQuest Fields — Copy-Paste Ready

## Intro (200 chars max)

Non-custodial performance attestation for autonomous agents. Merkle-committed epochs, public challenge windows, USDG bonds. Live on Arbitrum Sepolia.

## Sector (pick 2)
- DeFi
- AI

## Tech Tags (pick up to 8)
- Rust
- Web3
- Python
- Node

## MVP Link
https://github.com/rennf93/covenant

## Project Link
https://github.com/rennf93/covenant

## Description

Covenant is a non-custodial performance attestation layer for autonomous trading agents, built in Rust on Arbitrum Stylus.

**The problem:** AI trading agents market themselves with screenshots and self-reported PnL that cost nothing to fabricate. Copy-traders, prop firms, and allocators have no way to verify claims before committing capital.

**How Covenant works:** The agent posts a USDG bond, then commits performance epochs on-chain. Every epoch's trade receipts are hashed into a Merkle tree and the root lands on Arbitrum. Each epoch sits in a public challenge window where anyone can stake USDG and dispute the claim. Dismissed challenges close the epoch; upheld challenges slash the bond and end the strategy. No oracle committee, no curator, no custody.

**What's deployed:** The Covenant contract is live on Arbitrum Sepolia — deployed, source-verified via reproducible Stylus build, and tested with 33 Rust tests. A full lifecycle was executed on-chain: strategy registered with 10 USDG bond escrowed, epoch 0 committed (equity 10.5 USDG, net flow +5), 5-minute challenge window elapsed unchallenged, epoch finalized. Equity, high-water mark, and cumulative PnL updated permissionlessly.

**The agent on top:** Vouch is a live SOL trading agent with a calibrated System-1 model scoring every market tick, deterministic risk rails (stop, take, cooldown, kill switch), and a full audit log. Every decision is committed as an on-chain epoch the community can verify or dispute.

**USDG integration:** USDG is the unit of account (equity, PnL, HWM denominated in 6-dp USDG), the performance bond (10 USDG escrowed at registration), and the challenger stake (5 USDG per dispute).

## Progress During Hackathon

- Deployed Covenant Stylus contract on Arbitrum Sepolia: 0x92613e84e3473f3886172947ecdff3627978fd18
- Source-verified via reproducible cargo stylus build ("Verification successful")
- Built mock-USDG test token (0x8428da91ee8d2963182aa440816d7349882e64ce) for staging bond/stake flows
- Deployed demo Covenant instance (0x018e24d3e2c537bcfa0194a8cfd566536c588925) on mock token
- Executed full on-chain lifecycle: register strategy (10 USDG bond escrowed) → commit epoch 0 (equity 10.5, net flow +5) → 5-minute challenge window elapsed → finalize (permissionless)
- Built Vouch trading agent: calibrated System-1 compass, deterministic rails, full audit log
- Ran rigorous model evaluation: ECE 0.094 vs stock 0.101; published negative result on directional edge
- Recorded 7 months of SOL-USD wire-format training data (299k rows, 67k tradeable)
- Fine-tuned laya v3 model on Kaggle T4 (fp16, 2 epochs)

## Fundraising Status

Bootstrapped. No external funding. Seeking Arbitrum grant to extend the proof layer to mainnet with a live USDG integration.

## Deployment Details

**Ecosystem Deployed:** Arbitrum

**Testnet/Mainnet:** Testnet

**Contract address & deployed link:**
```
Covenant (verified): 0x92613e84e3473f3886172947ecdff3627978fd18
https://sepolia.arbiscan.io/address/0x92613e84e3473f3886172947ecdff3627978fd18

Mock USDG (test token): 0x8428da91ee8d2963182aa440816d7349882e64ce
Demo Covenant (mock instance): 0x018e24d3e2c537bcfa0194a8cfd566536c588925

Deploy tx: 0xba9d0debff4618f160383882338db28f82a1c00d394023472cf147a97e7d03b2
Verification: cargo stylus verify — "Verification successful"
```

## Images (4 × 1280x720 — I'll generate these)
1. Architecture diagram (agent → Merkle root → covenant → challenge)
2. Arbiscan contract page (verified badge)
3. Terminal: deploy + verify output
4. Vouch agent: compass + rails + audit log cards
