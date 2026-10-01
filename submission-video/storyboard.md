# Covenant Submission Video Storyboard: "Covenant: Proof for Agents"

Target: 150 seconds (2:30). Landscape 1920x1080, 30fps.
Narration: Kokoro TTS, voice af_heart, dry first-person builder voice.
Law: every number, address, and log line on screen comes from today's capture. Where the demo has a real wait, the video says so.

## Scene structure and narration

### S1 Hook — 0:00-0:09 (9s)
VISUAL: Black. Terminal cursor. Type-on: "an agent says it made 40% this month." Pause. Red strike: "prove it."
NARRATION: "An agent says it made forty percent this month. Screenshot attached. Proof? None. This is the problem Covenant solves."
(from the hyperframe intro, reused)

### S2 What Covenant is — 0:09-0:24 (15s)
VISUAL: Architecture card: agent → performance epochs → Merkle root → on-chain covenant. Three pills: Arbitrum Stylus (Rust), USDG escrow, non-custodial.
NARRATION: "Covenant is a non-custodial performance layer for autonomous agents. The agent's results are hashed into Merkle roots, committed on-chain, and challenged by anyone. Built in Rust on Arbitrum Stylus, with USDG as the bond and unit of account."

### S3 The contract is live — 0:24-0:42 (18s)
VISUAL: Terminal: cargo stylus deploy (compressed by title card "the deploy is real"), then Arbiscan view of contract 0x92613e84... "verified" badge.
NARRATION: "The contract is deployed and source-verified on Arbitrum Sepolia. Not a mock, not a local node: a real Stylus activation, thirty-three Rust tests behind it."

### S4 The agent trades — 0:42-1:05 (23s)
VISUAL: Split: left, the vouch agent terminal running a live session (rails enforcing, laya compass scoring); right, decisions.jsonl tail with one decision highlighted (probs, veto reason, action).
NARRATION: "On top of the covenant runs Vouch, a live trading agent. A calibrated System-1 model scores every tick. Deterministic rails own the money rules - stop, take, cooldown, kill switch. Every decision is logged with its full reasoning."

### S5 Performance committed — 1:05-1:28 (23s)
VISUAL: Terminal: epoch end → receipts hashed → Merkle root printed → root committed on-chain (tx hash + Arbiscan confirm). Then the challenge window countdown: "anyone can dispute."
NARRATION: "At every epoch end, the receipts are hashed into a Merkle tree and the root lands on-chain. From that moment, anyone can challenge the claim by staking. No one trusts the agent: they trust the commitment."

### S6 The honest evaluation — 1:28-1:50 (22s)
VISUAL: Card montage: holdout ECE 0.094 vs stock 0.101; IC table; "no directional edge found at tested horizons"; the probe-set validator counting.
NARRATION: "And when we tested the intelligence layer, we published the ugly numbers too: honest calibration, no directional edge at tested horizons. Because a proof layer is worthless if the thing it proves was cherry-picked."

### S7 Outro — 1:50-2:02 (12s)
VISUAL: End card: wordmark, "verified performance, settled on-chain", contract address, network, repo. USDG + Arbitrum marks.
NARRATION: "Covenant. Non-custodial attestation for the agents you're asked to trust. Verified performance, settled on-chain."

## Audio
Music: one cinematic bed, ducked under narration, small swell at S5 commit and S6 honesty beat.

## Honesty rules
- Contract address: only 0x92613e84e3473f3886172947ecdff3627978fd18, from today's deploy.
- Every log line: from today's capture.
- Real waits labeled real and compressed with title cards, never sped silently.
- "Mock USDG" labeled as mock wherever the test token appears on screen.
- No "AI-powered" wording anywhere.
