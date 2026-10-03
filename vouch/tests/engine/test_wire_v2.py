"""Wire v2 tests: the market strings are byte-pinned contracts, so both the
v1 (no context) and v2 (context block) renderings are golden-tested, and the
wire_version tag is asserted on every decision path. The token budget is
measured with the laya tokenizer from the local HF cache and skips when it
is unavailable. Stdlib unittest only; the decision head is a stub."""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from vouch.engine.broker import Broker
from vouch.engine.market import Tick
from vouch.engine.rules import Rules
from vouch.engine.session import run_session
from vouch.engine.system1 import System1

# Golden v1: today's market string, byte-for-byte. Never edit this string;
# a diff here means the served prompt drifted under a trained head.
GOLDEN_V1 = (
    "SOL/USDC tick 42. Price 148.23. "
    "1m return +0.12%, 15m -0.34%, 60m +1.21%. "
    "Volume ratio 1.27 vs average. "
    "60m range 146.80-149.10."
)
# Golden v2: the v1 prefix plus the pinned context block (fixed order and
# precision) and the UTC weekday/clock derived from the context ts
# (1780000000 = Thursday 20:26 UTC).
GOLDEN_V2 = GOLDEN_V1 + (
    " Funding 0.0100%/8h, 24h change +0.0150pp. "
    "Open interest $1450M, 1h +0.4%, 24h -3.1%. "
    "BTC 1m +0.03%, 60m -0.42%, 24h +2.10%. "
    "Price +1.2% vs 4h EMA, 30d range position 73%, vol regime normal. "
    "Thursday 20:26 UTC."
)


@dataclass(frozen=True)
class ContextPoint:
    """Test double of the pinned v5 chunk A interface (same fields, same
    order) so these tests run before context_features.py lands."""

    ts: int
    funding_rate_8h: float
    funding_24h_ago: float
    oi_usd_m: float
    oi_chg_1h_pct: float
    oi_chg_24h_pct: float
    btc_ret_1m: float
    btc_ret_60m: float
    btc_ret_24h: float
    ema4h_dist_pct: float
    range_pos_30d: float
    vol_regime: str


class StubBackend:
    """Records served states; answers flat with P(flat)=0.95 so every rail
    vetoes and no sim session ever opens a position."""

    name = "stub"

    def __init__(self) -> None:
        self.states: list[dict] = []

    def predict(self, state: dict, questions: dict) -> dict:
        self.states.append(state)
        answers = {}
        for qname, q in questions.items():
            if q["type"] == "choice":
                keys = list(q["criteria"])
                probs = {k: 0.95 if k == "flat" else 0.05 / (len(keys) - 1) for k in keys}
                answers[qname] = {
                    "choice": "flat" if "flat" in keys else keys[0],
                    "probabilities": probs,
                    "answer_confidence": 0.95,
                }
            elif q["type"] == "score":
                answers[qname] = {"score": 1}
            else:
                answers[qname] = {"noul": 0.2}
        return {"model": "stub", "answers": answers}


def fixture_tick() -> Tick:
    return Tick(
        i=42,
        price=148.234,
        regime="backtest",
        ret_1m=0.0012,
        ret_15m=-0.0034,
        ret_60m=0.0121,
        volume_ratio=1.27,
        high_60m=149.10,
        low_60m=146.80,
    )


def fixture_context() -> ContextPoint:
    return ContextPoint(
        ts=1780000000,  # Thursday 20:26 UTC
        funding_rate_8h=0.0001,
        funding_24h_ago=-0.00005,
        oi_usd_m=1450.2,
        oi_chg_1h_pct=0.42,
        oi_chg_24h_pct=-3.15,
        btc_ret_1m=0.0003,
        btc_ret_60m=-0.0042,
        btc_ret_24h=0.021,
        ema4h_dist_pct=1.25,
        range_pos_30d=0.734,
        vol_regime="normal",
    )


def stubbed_system1() -> tuple[System1, StubBackend]:
    stub = StubBackend()
    # No calibration file: the EV gate must stay off so tests pin wire
    # rendering only, never gate behavior.
    return System1(router=stub, calibration_path="/nonexistent"), stub


def laya_tokenizer():
    """The stock laya tokenizer from the local HF cache, or None when it is
    not installed/cached (the budget test skips): the budget is measured,
    never estimated."""
    try:
        from transformers import AutoTokenizer
    except ImportError:
        return None
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    hub = Path(os.environ.get("HF_HUB_CACHE", hf_home / "hub"))
    hits = sorted(hub.glob("models--convaiinnovations--laya/snapshots/*/tokenizer/tokenizer.json"))
    if not hits:
        return None
    try:
        return AutoTokenizer.from_pretrained(str(hits[-1].parent), local_files_only=True)
    except Exception:  # noqa: BLE001 - a broken cache is a skip, not a failure
        return None


class GoldenWireStringsTest(unittest.TestCase):
    def test_no_context_renders_v1_byte_for_byte(self):
        s1, stub = stubbed_system1()
        d = s1.decide(fixture_tick(), Broker(starting_cash=1000.0), Rules(), [])
        self.assertEqual(stub.states[0]["market"], GOLDEN_V1)
        self.assertEqual(d["wire_version"], 1)
        # the wire payload records the exact served state + questions
        self.assertEqual(d["wire"]["state"], stub.states[0])

    def test_context_renders_pinned_v2_string(self):
        s1, stub = stubbed_system1()
        d = s1.decide(fixture_tick(), Broker(starting_cash=1000.0), Rules(), [], fixture_context())
        self.assertEqual(stub.states[0]["market"], GOLDEN_V2)
        self.assertEqual(d["wire_version"], 2)
        self.assertEqual(d["wire"]["state"], stub.states[0])

    def test_exit_rows_carry_the_wire_version_too(self):
        broker = Broker(starting_cash=1000.0)
        broker.open("long", 100.0, 148.0, 40, reason="test")
        s1, _ = stubbed_system1()
        d1 = s1.decide(fixture_tick(), broker, Rules(), [])
        self.assertEqual(d1["mode"], "exit")
        self.assertEqual(d1["wire_version"], 1)
        s2, _ = stubbed_system1()
        d2 = s2.decide(fixture_tick(), broker, Rules(), [], fixture_context())
        self.assertEqual(d2["wire_version"], 2)

    def test_market_string_token_budget(self):
        tok = laya_tokenizer()
        if tok is None:
            self.skipTest("laya tokenizer not in the local HF cache")
        s1, _ = stubbed_system1()
        d = s1.decide(fixture_tick(), Broker(starting_cash=1000.0), Rules(), [], fixture_context())
        market = d["wire"]["state"]["market"]
        n = len(tok.encode(market, add_special_tokens=False))
        self.assertLessEqual(n, 300, f"wire v2 market string is {n} tokens (budget 300): {market}")


class SimModeRegressionTest(unittest.TestCase):
    def test_sim_session_stays_wire_v1(self):
        # sim mode never has context: every decision must stay v1, v1 golden
        # rendering, or the training corpus silently splits formats
        seen: list[dict] = []

        class Recording(System1):
            def __init__(self):
                System1.__init__(self, router=StubBackend(), calibration_path="/nonexistent")

            def decide(self, tick, broker, rules, recent):
                d = System1.decide(self, tick, broker, rules, recent)
                seen.append(d)
                return d

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch("vouch.engine.session.System1", Recording),
        ):
            run_session(ticks=12, epoch_len=50, seed=7, out=tmp)
        self.assertTrue(seen)
        for d in seen:
            self.assertEqual(d["wire_version"], 1)
            market = d["wire"]["state"]["market"]
            self.assertNotIn(" Funding", market)
            self.assertNotIn("UTC", market)
            self.assertTrue(market.startswith("SOL/USDC tick "))


if __name__ == "__main__":
    unittest.main()
