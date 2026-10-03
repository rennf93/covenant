#!/usr/bin/env python
"""Generate ALL Covenant video narration (13 scenes, ~5 min) - Kokoro af_heart."""

from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro
from kokoro_onnx.config import EspeakConfig

OUT = Path(__file__).parent / "assets"
OUT.mkdir(exist_ok=True)

LINES = {
    "s1": "An agent says it made forty percent this month. Screenshot attached. Proof? None. This is the problem Covenant solves.",
    "s2": "Copy-trading platforms and prop firms price agent performance off screenshots that cost nothing to fake. Right now, at this very hackathon, a hundred and fifteen thousand dollars will reward teams whose numbers nobody can verify.",
    "s3": "Covenant is a non-custodial performance attestation layer for autonomous agents. The agent's results are hashed into Merkle roots, committed on-chain, and challenged by anyone. Built in Rust on Arbitrum Stylus, with U S D G as the bond and unit of account.",
    "s4": "And this is not a prototype. The contract is deployed and source-verified on Arbitrum Sepolia. Address zero x nine two six one three e eight four. A real Stylus activation, thirty-three Rust tests behind it. And it answers reads, right now.",
    "s5": "To trade, an agent registers and posts a ten U S D G bond into escrow. Skin in the game from block one. The strategy is live: vouch dash sol demo, owned by the deployer, bond locked.",
    "s6": "Then the agent commits its first performance epoch on-chain. Equity ten and a half, net flow plus five. The Merkle root of every trade receipt lands with the commit. Transaction hash, real, on Arbiscan.",
    "s7": "And now the honest part: a five-minute challenge window. Anyone can stake five U S D G and dispute the claim. The wait you just saw is real, compressed with a title card. No one trusts the agent. They trust the commitment.",
    "s8": "The window elapsed, unchallenged. Finalize. Equity ten and a half, high-water mark ten and a half, cumulative P and L plus five and a half. And finalize is permissionless: anyone can settle it, not just the agent.",
    "s9": "On top of the covenant runs Vouch, a live trading agent. A calibrated System-1 model scores every tick. Deterministic rails own the money rules: stop, take, cooldown, kill switch. Every decision is logged with its full reasoning.",
    "s10": "And when we tested the intelligence layer, we published the ugly numbers too. Honest calibration at zero point zero nine four, better than stock. And no directional edge found at tested horizons. Published anyway. Because a proof layer is worthless if the thing it proves was cherry-picked.",
    "s11": "For copy-traders, prop firms, and allocators: verify the covenant, not the screenshot. The bond is at stake, the performance is committed, the challenge is open to anyone. Trust moves from promises to settlement.",
    "s12": "Next: order-book and funding inputs, dispute-driven insurance on mainnet, and a real U S D G integration beyond staging. The rails are built. The compass is calibrated. The proof layer is live.",
    "s13": "Covenant. Non-custodial attestation for the agents you're asked to trust. Verified performance, settled on-chain.",
}


def main() -> None:
    k = Kokoro(
        "/tmp/model.onnx",
        "/tmp/af_heart_clean.npz",
        espeak_config=EspeakConfig(data_path="/opt/homebrew/share/espeak-ng-data"),
    )
    style = np.fromfile("/tmp/af_heart.bin", dtype=np.float32).reshape(510, 1, 256)
    total = 0.0
    for name, text in LINES.items():
        audio, sr = k.create(text, voice=style, speed=1.0, lang="en-us")
        out = OUT / f"{name}.wav"
        sf.write(out, audio, sr)
        dur = len(audio) / sr
        total += dur
        print(f"{name}: {dur:.1f}s -> {out}")
    print(f"TOTAL narration: {total:.1f}s")


if __name__ == "__main__":
    main()
