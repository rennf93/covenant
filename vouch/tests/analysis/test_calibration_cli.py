"""Calibration CLI helper tests: row building from replayed decisions, ECE
sanity, and the gate simulation economics. Stdlib unittest only."""

from __future__ import annotations

import unittest

from run_calibration import build_rows, ece, gate_simulation
from vouch.calibration import Calibrator


def replay_rows() -> list[dict]:
    # 100 bars: flat for 40, then a clean +5% run (long barriers), then flat.
    prices = [100.0] * 40 + [100.0 + i for i in range(1, 46)] + [145.0] * 15
    rows = []
    for i, p in enumerate(prices):
        rows.append(
            {
                "price": p,
                "ts": f"t{i}",
                "decision": {
                    "mode": "entry",
                    "conviction": 0.5,
                    "enter_p": 0.4,
                    "probs": {"long": 0.45, "flat": 0.2, "short": 0.35},
                },
            }
        )
    return rows


class BuildRowsTest(unittest.TestCase):
    def test_barrier_labels_fire_on_the_run(self):
        rows = build_rows(replay_rows(), horizon=30, tp=0.04, sl=0.02, fee=0.012)
        labels = [r["label"] for r in rows]
        self.assertIn("long", labels)
        # the run starts at bar 40; a 30-bar window can first see it at bar 10
        self.assertEqual(labels[0], "flat")
        longs = [r["i"] for r in rows if r["label"] == "long"]
        self.assertTrue(min(longs) >= 10)  # nothing before the run is visible
        self.assertTrue(max(longs) <= 84)  # nothing after the run ends

    def test_features_come_from_the_decision(self):
        rows = build_rows(replay_rows(), horizon=30, tp=0.04, sl=0.02, fee=0.012)
        r = rows[0]
        self.assertAlmostEqual(r["enter_p"], 0.8)  # 1 - P(flat)
        self.assertAlmostEqual(r["ls_spread"], 0.10)


class EceTest(unittest.TestCase):
    def test_perfect_calibration_scores_zero(self):
        pairs = [(0.0, 0), (1.0, 1)] * 50
        self.assertEqual(ece(pairs), 0.0)

    def test_confident_wrong_scores_high(self):
        pairs = [(0.99, 0)] * 100
        self.assertGreater(ece(pairs), 0.9)


class GateSimulationTest(unittest.TestCase):
    def test_breakeven_is_cost_and_bracket_driven(self):
        # An all-ones calibrator: p_up = 1 everywhere -> every row looks like
        # a sure long. With 4% take / 2% stop / 1.2% fees the win value is
        # tp_net = 2.8% and the loss is sl+fee = 3.2%.
        cal = Calibrator({"conviction": 0.0, "enter_p": 0.0, "ls_spread": 0.0}, 20.0)
        rows = [
            {"label": "long", "fwd_ret": 0.05, "conviction": 1.0, "enter_p": 0.5, "ls_spread": 0.1},
            {
                "label": "short",
                "fwd_ret": -0.05,
                "conviction": 1.0,
                "enter_p": 0.5,
                "ls_spread": 0.1,
            },
            {
                "label": "flat",
                "fwd_ret": 0.001,
                "conviction": 1.0,
                "enter_p": 0.5,
                "ls_spread": 0.1,
            },
        ]
        sim = gate_simulation(rows, cal, tp=0.04, sl=0.02, fee=0.012, edges=[0.0])
        s = sim[0]
        self.assertEqual(s["gated_trades"], 3)
        # long: +0.028, short: -0.032, flat: 0.001 - 0.012
        self.assertAlmostEqual(s["total_pnl"], 0.028 - 0.032 + 0.001 - 0.012, places=6)


if __name__ == "__main__":
    unittest.main()
