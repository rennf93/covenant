"""System-1: the laya decision head. One forward pass, typed questions.

This is the "answers one question every 15 seconds" role from the article,
but its every output passes through broker rails (stop, take, trail,
cooldown, kill switch) and rules validated against RAILS. laya proposes;
rails dispose.

Two modes, chosen by whether a position is open:
- ENTRY mode (flat): long/short/flat choice + conviction + enter pressure.
  The tradeable signal is the DIP in P(flat) plus the long/short spread,
  not the raw choice (zero-shot laya is flat-biased; its README says so).
- EXIT mode (position open): hold/exit choice. The bracket rails still
  fire independently; this is laya's chance to leave on deterioration
  before the stop is hit. Gated on rules.exit_pressure_min.

If a calibration file exists (see jev/calibration.py) and
rules.min_edge_pct > 0, entries additionally require calibrated expected
value to clear round-trip costs. No calibration = gate off, behavior as before.
"""

from __future__ import annotations

import os
from pathlib import Path

from .broker import Broker
from .calibration import Calibrator
from .market import Tick
from .rules import Rules
from .s1_backends import make_s1_backend

DEFAULT_CALIBRATION = Path("out/calibration.json")


class System1:
    def __init__(self, router=None, calibration_path: Path | str | None = None):
        # The provider is chosen by env (dashboard -> JEV_S1_*): local laya
        # library, a laya HTTP server, or an OpenAI-compatible endpoint via
        # OpenRouter. All return the same Router.predict shape.
        self.router = router or make_s1_backend()
        path = calibration_path or os.environ.get("JEV_CALIBRATION", DEFAULT_CALIBRATION)
        self.calibration = Calibrator.load(Path(path))

    def decide(self, tick: Tick, broker: Broker, rules: Rules, recent: list[str]) -> dict:
        state = {
            "market": (
                f"SOL/USDC tick {tick.i}. Price {tick.price:.2f}. "
                f"1m return {tick.ret_1m:+.2%}, 15m {tick.ret_15m:+.2%}, 60m {tick.ret_60m:+.2%}. "
                f"Volume ratio {tick.volume_ratio:.2f} vs average. "
                f"60m range {tick.low_60m:.2f}-{tick.high_60m:.2f}."
            ),
            "position": (
                f"{broker.position.side} ${broker.position.size_usd:.0f} at {broker.position.entry:.2f}"
                if broker.position
                else "flat, no open position"
            ),
            "recent_trades": "; ".join(recent[-3:]) if recent else "no trades yet this session",
        }

        if broker.position is not None:
            return self._decide_exit(state, tick, broker, rules)

        questions = {
            "action": {
                "type": "choice",
                "instructions": "Given `market` and `position`, what should the paper book do on this tick?",
                "criteria": {
                    "long": "open a long on demonstrated strength",
                    "flat": "hold or stay flat, no clean setup",
                    "short": "open a short on demonstrated weakness",
                },
            },
            "conviction": {
                "type": "score",
                "instructions": "How strong is the setup in `market` right now?",
                "criteria": ["weak chop", "mild lean", "decent setup", "strong setup", "table pounder"],
            },
            "enter_now": {
                "type": "noul",
                "instructions": "Given `position` and `recent_trades`, is opening a NEW position right now justified?",
            },
        }
        res = self.router.predict(state, questions)

        answers = res["answers"]
        probs = answers["action"]["probabilities"]
        p_flat = float(probs.get("flat", 1.0))
        direction = "long" if float(probs.get("long", 0.0)) >= float(probs.get("short", 0.0)) else "short"
        action = answers["action"]["choice"]
        conviction = float(answers["conviction"]["score"]) / 4.0
        enter_p = 1.0 - p_flat  # "pressure to be in a trade", 0..1
        noul = float(answers["enter_now"]["noul"])  # kept for analysis

        ok = True
        veto = None
        if direction not in rules.allowed_actions:
            ok, veto = False, f"'{direction}' not in allowed_actions"
        if ok and conviction < rules.min_conviction:
            ok, veto = False, f"conviction {conviction:.2f} < min {rules.min_conviction:.2f}"
        if ok and p_flat >= rules.flat_max_p:
            ok, veto = False, f"P(flat) {p_flat:.2f} >= gate {rules.flat_max_p:.2f}"
        if ok and tick.i <= broker.cooldown_until:
            ok, veto = False, "cooldown after a loss"
        if ok and broker.position is not None:
            ok, veto = False, "position already open"
        if ok and self.calibration and rules.min_edge_pct > 0:
            ls_spread = float(probs.get("long", 0.0)) - float(probs.get("short", 0.0))
            p_up = self.calibration.p_up(conviction, enter_p, ls_spread)
            p_win = p_up if direction == "long" else 1.0 - p_up
            cost = broker.round_trip_cost()
            ev = p_win * rules.take_profit_pct - (1 - p_win) * rules.stop_loss_pct - cost
            if ev < rules.min_edge_pct:
                ok, veto = False, f"calibrated EV {ev:.4f} < min edge {rules.min_edge_pct:.4f}"

        return {
            "mode": "entry",
            "raw_action": action,
            "direction": direction,
            "conviction": round(conviction, 3),
            "enter_p": round(enter_p, 3),
            "noul": round(noul, 3),
            "probs": {k: float(v) for k, v in probs.items()},  # FULL vector, for analysis/SFT
            "final_action": direction if ok else "flat",
            "veto": veto,
        }

    def _decide_exit(self, state: dict, tick: Tick, broker: Broker, rules: Rules) -> dict:
        pos = broker.position
        fav = broker._fav(tick.price)
        state = dict(state)
        state["position"] = (
            f"{pos.side} ${pos.size_usd:.0f} at {pos.entry:.2f}, "
            f"unrealized {fav:+.2%}, open since tick {pos.open_tick}"
        )
        questions = {
            "exit": {
                "type": "choice",
                "instructions": (
                    "Given `market` and `position`, should the open position be closed now "
                    "or held? The bracket (stop/take) fires on its own; you are for EARLY exits "
                    "on demonstrated deterioration or an obvious giveback."
                ),
                "criteria": {
                    "hold": "keep the position, thesis intact",
                    "exit": "close now, the move that justified entry is gone or reversing",
                },
            },
            "exit_conviction": {
                "type": "score",
                "instructions": "How strong is the case for closing right now?",
                "criteria": ["keep it", "slight doubt", "thesis fading", "clear reversal", "get out now"],
            },
        }
        res = self.router.predict(state, questions)
        answers = res["answers"]
        probs = answers["exit"]["probabilities"]
        p_exit = float(probs.get("exit", 0.0))
        exit_conviction = float(answers["exit_conviction"]["score"]) / 4.0

        ok = p_exit >= rules.exit_pressure_min
        return {
            "mode": "exit",
            "raw_action": answers["exit"]["choice"],
            "direction": "exit" if ok else "hold",
            "conviction": round(exit_conviction, 3),
            "enter_p": round(1.0 - p_exit, 3),
            "noul": None,
            "probs": {k: float(v) for k, v in probs.items()},
            "final_action": "exit" if ok else "hold",
            "veto": None if ok else f"exit pressure {p_exit:.2f} < gate {rules.exit_pressure_min:.2f}",
        }
