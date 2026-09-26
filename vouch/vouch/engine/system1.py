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

If a calibration file exists (see vouch/calibration.py) and
rules.min_edge_pct > 0, entries additionally require calibrated expected
value to clear round-trip costs. No calibration = gate off, behavior as before.

Layering: engine. May use venues and attest; must not import server. The
calibration import is engine -> calibration, which is a leaf module
(pure python, no package imports); the reverse direction (analysis.backtest
importing engine) never crosses an in-flight module init, so this is cycle-free.
"""

from __future__ import annotations

from pathlib import Path

from vouch.calibration import Calibrator
from vouch.config import load_settings
from vouch.engine.broker import Broker
from vouch.engine.market import Tick
from vouch.engine.rules import Rules
from vouch.engine.s1_backends import make_s1_backend
from vouch.protocols import System1Backend


class System1:
    def __init__(
        self, router: System1Backend | None = None, calibration_path: Path | str | None = None
    ) -> None:
        # The provider is chosen by env (dashboard -> VOUCH_S1_*): local laya
        # library, a laya HTTP server, or an OpenAI-compatible endpoint via
        # OpenRouter. All return the same Router.predict shape.
        self.router = router or make_s1_backend()
        path = calibration_path or load_settings().s1.calibration
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
                f"{broker.position.side} ${broker.position.size_usd:.0f} "
                f"at {broker.position.entry:.2f}"
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
                "instructions": "Given `market` and `position`, what should the paper "
                "book do on this tick?",
                "criteria": {
                    "long": "open a long on demonstrated strength",
                    "flat": "hold or stay flat, no clean setup",
                    "short": "open a short on demonstrated weakness",
                },
            },
            "conviction": {
                "type": "score",
                "instructions": "How strong is the setup in `market` right now?",
                "criteria": [
                    "weak chop",
                    "mild lean",
                    "decent setup",
                    "strong setup",
                    "table pounder",
                ],
            },
            "enter_now": {
                "type": "noul",
                "instructions": "Given `position` and `recent_trades`, is opening a NEW "
                "position right now justified?",
                # Explicit criteria: the english checkpoint has a documented
                # label-following failure on bare true/false options (upstream
                # #156), so both sides get a description the model can judge on.
                "criteria": {
                    "false": "stand down: the trade history or open position argues "
                    "against adding risk right now",
                    "true": "nothing in the position or trade history argues against "
                    "opening a new position now",
                },
            },
        }
        res = self.router.predict(state, questions)

        answers = res["answers"]
        probs = answers["action"]["probabilities"]
        p_flat = float(probs.get("flat", 1.0))
        direction = (
            "long" if float(probs.get("long", 0.0)) >= float(probs.get("short", 0.0)) else "short"
        )
        action = answers["action"]["choice"]
        conviction = float(answers["conviction"]["score"]) / 4.0
        enter_p = 1.0 - p_flat  # "pressure to be in a trade", 0..1
        noul = float(answers["enter_now"]["noul"])  # kept for analysis
        # max(option p): the quantity temperature scaling actually fits
        # (answer_confidence). The sibling `confidence` field is normalized
        # entropy and is NOT calibrated; never gate on it.
        action_conf = float(answers["action"].get("answer_confidence", 0.0))

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
            "action_conf": round(action_conf, 3),
            "probs": {k: float(v) for k, v in probs.items()},  # FULL vector, for analysis/SFT
            "final_action": direction if ok else "flat",
            "veto": veto,
            # The exact payload sent to the head. SFT bakes MUST use this, not
            # the compact state - training on a different rendering than
            # serving is the distribution shift that invalidated the first
            # Stage 2 acceptance (2026-09-26).
            "wire": {"state": state, "questions": questions},
        }

    def _decide_exit(self, state: dict, tick: Tick, broker: Broker, rules: Rules) -> dict:
        pos = broker.position
        if pos is None:
            # Defensive: exit mode presumes an open position. A None here
            # means the kill switch closed it between the gate and this
            # call; hold rather than crash the loop.
            return {
                "mode": "exit",
                "raw_action": "hold",
                "direction": "hold",
                "conviction": 0.0,
                "enter_p": None,
                "noul": None,
                "action_conf": 0.0,
                "probs": {},
                "final_action": "hold",
                "veto": "position already closed (kill switch race)",
            }
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
                "criteria": [
                    "keep it",
                    "slight doubt",
                    "thesis fading",
                    "clear reversal",
                    "get out now",
                ],
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
            "action_conf": round(float(answers["exit"].get("answer_confidence", 0.0)), 3),
            "probs": {k: float(v) for k, v in probs.items()},
            "final_action": "exit" if ok else "hold",
            "veto": None
            if ok
            else f"exit pressure {p_exit:.2f} < gate {rules.exit_pressure_min:.2f}",
        }
