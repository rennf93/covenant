# FILMING RUNBOOK: Covenant live demo (Oct 3)

Everything below is prepared and running. Read this once, then record.
Target: one continuous take is fine; you will trim to 12-20s for the video slot.

## Before you hit record

1. Terminal: open a fresh tab, font zoomed (cmd +), cd:
   `cd /Users/renzof/Documents/GitHub/ZZZ/covenant`
2. Browser tabs, in this order:
   - http://localhost:3000  (the leaderboard; strategy "vouch-sol-demo" must be visible - it is indexed live from the chain)
   - https://sepolia.arbiscan.io/address/0x92613e84e3473f3886172947ecdff3627978fd18  (the verified main contract)
   - http://localhost:3000/verify  (the receipt verifier)
3. MetaMask: network "Arbitrum Sepolia" (RPC https://arbitrum-sepolia.publicnode.com, chain 412346), account 0x7c3C...C7d5 (the deployer, already imported).
4. Screen recorder: 1920x1080 (QuickTime > File > New Screen Recording, or OBS). 30fps.

## The take (in order)

### Shot 1: Register (the bond)
Paste and run:

```bash
export RPC=https://arbitrum-sepolia.publicnode.com
export COV=0x1de6ccb02f29308851a9f59c09845c6d348d16a4
export PK=$(grep DEPLOYER_PRIVATE_KEY ~/.config/covenant-deploy/deployer.env | cut -d= -f2)
echo '{"action":"register","name":"vouch-sol-live","metadataUri":"https://github.com/rennf93/covenant"}' \
  | COVENANT_RPC_URL=$RPC COVENANT_CONTRACT_ADDRESS=$COV COVENANT_PRIVATE_KEY=$PK \
  node vouch/vouch/attest/bridge.mjs
```

Expected output: `{"hash":"0x...","strategyId":"2","chainId":421614}`.
This pulls the 10 mock-USDG bond into escrow on camera. Click through to the
tx on Arbiscan (or paste the hash into arbiscan.sepolia.io search).

### Shot 2: Commit epoch 0 (the root lands)

```bash
echo '{"action":"commit","strategyId":"2","epochIndex":"0","equity":"10500000","netFlow":"5000000","tradesRoot":"ROOT_FROM_BELOW","evidenceUri":"https://github.com/rennf93/covenant/tree/main/submission-video/demo"}' \
  | COVENANT_RPC_URL=$RPC COVENANT_CONTRACT_ADDRESS=$COV COVENANT_PRIVATE_KEY=$PK \
  node vouch/vouch/attest/bridge.mjs
```

IMPORTANT: replace ROOT_FROM_BELOW with the tradesRoot value from
`submission-video/demo/demo-commit.json` (built from five real receipts at
real SOL prices; the JSON also holds the leaf hashes and the verify proof).
The receipts are canonical SDK receipts - the same encode/hashing the
contract's onchain verifier enforces.

### Shot 3: The leaderboard (live, indexed from chain events)
Switch to the localhost:3000 tab. Within ~1 min of the commit,
"vouch-sol-live" appears with epoch 0 pending and the challenge countdown.
The 5-minute window is real - let it run while you shoot other angles.

### Shot 4: The receipt verifier
On the /verify tab: paste `receiptHash` and `proof` from
`demo-commit.json` (verifyOne). It verifies against the committed root from
the browser, direct to the RPC. Then flip one character of the proof and
watch it fail. This is the "no indexer, no trust" moment - linger here.

### Shot 5: Finalize (permissionless) after the 5-minute window
When the countdown ends:

```bash
echo '{"action":"finalize","strategyId":"2","epochIndex":"0"}' \
  | COVENANT_RPC_URL=$RPC COVENANT_CONTRACT_ADDRESS=$COV COVENANT_PRIVATE_KEY=$PK \
  node vouch/vouch/attest/bridge.mjs
```

Then the read that proves accounting:

```bash
echo '{"action":"getPerformance","strategyId":"2"}' \
  | COVENANT_RPC_URL=$RPC COVENANT_CONTRACT_ADDRESS=$COV node vouch/vouch/attest/bridge.mjs
```

Expected: equity 10500000 (10.5), highWaterMark 10500000, cumulativePnl 5500000 (+5.5),
epochCount 1, finalizedEpochs 1.

### Shot 6: Arbiscan close-up
- Main contract page (verified badge): 0x92613e84e3473f3886172947ecdff3627978fd18
- The demo instance: https://sepolia.arbiscan.io/address/0x1de6ccb02f29308851a9f59c09845c6d348d16a4
- The commit tx from Shot 2 (show EpochCommitted event decoded).
- Mock USDG token page: 0x8428da91ee8d2963182aa440816d7349882e64ce
  (the bond transfer, labeled mock on screen per the honesty rule).

## After filming

Hand the raw recording to ZCode (drop it in submission-video/, any format,
QuickTime .mov is fine). It gets trimmed into the reserved 12-20s slot and
the final video is re-rendered with the intro/outro around it.

## Honesty rules (from the storyboard - non-negotiable)

- Mock USDG must be labeled mock whenever shown.
- The 5-minute wait is real; in the final cut it is compressed with a title card.
- Every address on screen comes from this runbook.
