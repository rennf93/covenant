# Demo artifacts

Everything here comes from the real staging deployment on Arbitrum Sepolia
(2026-10-03). Mock USDG is labeled mock everywhere; the main contract uses
real Paxos USDG constructor args.

| File | What it is |
| --- | --- |
| `demo-commit.json` | The epoch-0 commit package for strategy 2: five canonical SDK receipts built from real SOL-USD prices (fetched live from Coinbase when the script ran), the Merkle root committed on-chain (`0xfd3830...9b7`), and one receipt hash + Merkle proof used for the browser verification shot. |
| `build-commit.mjs` | Rebuilds `demo-commit.json` from live prices (same encode/hashing the contract's `verifyReceipt` enforces). |
| `shot1-register.sh` etc. | The exact commands used on camera: register (10 mock-USDG bond into escrow), commit, finalize, performance read. Wrap `bridge.mjs`. |
| `verify-paste.txt` | The receipt hash + Merkle proof pasted into the web verifier (`/verify`), which checks them against the committed root from the browser, no backend. |
| `capture-take.mjs` / `verify-capture.mjs` | Scripted browser recordings (Playwright) of the live leaderboard, strategy detail page, verifier VERIFIED/REJECTED moments, and Arbiscan pages. |
| `raw-take.webm` / `verify-take.webm` / `slot-raw.webm` | The raw recordings the demo video was cut from. |
| `covenant-demo.mp4` | The assembled 60-second demo (HackQuest "Demo video" slot). |

On-chain trail for strategy 2 (`vouch-sol-demo`):

- register: `0x7c1434d94ef60ff85b9728bc845ce9a44e436e02cbe266f72a8c9be440e30c82`
- commit (epoch 0, this package's root): `0x253957bf13b0613fc0b8e411e0d668f6572b3c87568f882fa6db3c826b279298`
- finalize: `0x5fb8e2b0528bb37052eaad82365bf929804403e9f60d58146cb56098a7abaa84`

Final numbers: equity 10.5, high-water mark 10.5, cumulative PnL +5.5,
1 of 1 epochs finalized. The Oct 1 capture (strategy 1) holds the
original lifecycle: commit `0x5ee0601a7ecd2de871b9f0fd43b078ddf16a746054ca32af5638592532990c85`.
