#!/usr/bin/env python
"""Generate all Covenant video narration with Kokoro TTS (af_heart voice).

Same voice as the reference guard-core video. Outputs s1.wav..s7.wav into
assets/. Requires: /tmp/model.onnx + the converted voice npz (see shell
history), espeak-ng data at /opt/homebrew/share/espeak-ng-data.
"""

from pathlib import Path

import numpy as np
import soundfile as sf
from kokoro_onnx import Kokoro
from kokoro_onnx.config import EspeakConfig

OUT = Path(__file__).parent / "assets"
OUT.mkdir(exist_ok=True)

LINES = {
    "s1": "An agent says it made forty percent this month. Screenshot attached. Proof? None. This is the problem Covenant solves.",
    "s2": "Covenant is a non-custodial performance layer for autonomous agents. The agent's results are hashed into Merkle roots, committed on-chain, and challenged by anyone. Built in Rust on Arbitrum Stylus, with U S D G as the bond and unit of account.",
    "s3": "The contract is deployed and source-verified on Arbitrum Sepolia. Not a mock, not a local node: a real Stylus activation, thirty-three Rust tests behind it.",
    "s4": "On top of the covenant runs Vouch, a live trading agent. A calibrated System-1 model scores every tick. Deterministic rails own the money rules: stop, take, cooldown, kill switch. Every decision is logged with its full reasoning.",
    "s5": "At every epoch end, the receipts are hashed into a Merkle tree and the root lands on-chain. From that moment, anyone can challenge the claim by staking. No one trusts the agent. They trust the commitment.",
    "s6": "And when we tested the intelligence layer, we published the ugly numbers too. Honest calibration, no directional edge at tested horizons. Because a proof layer is worthless, if the thing it proves was cherry-picked.",
    "s7": "Covenant. Non-custodial attestation for the agents you're asked to trust. Verified performance, settled on-chain.",
}


def main() -> None:
    k = Kokoro(
        "/tmp/model.onnx",
        "/tmp/af_heart_clean.npz",
        espeak_config=EspeakConfig(data_path="/opt/homebrew/share/espeak-ng-data"),
    )
    style = np.fromfile("/tmp/af_heart.bin", dtype=np.float32).reshape(510, 1, 256)
    for name, text in LINES.items():
        audio, sr = k.create(text, voice=style, speed=1.0, lang="en-us")
        out = OUT / f"{name}.wav"
        sf.write(out, audio, sr)
        print(f"{name}: {len(audio) / sr:.1f}s -> {out}")


if __name__ == "__main__":
    main()
