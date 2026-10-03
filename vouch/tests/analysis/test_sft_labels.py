"""Barrier label tests: the ground truth both the SFT dataset and the entry
calibrator are built on. The label must answer exactly the question the EV
gate spends: did the net take barrier (take profit minus round-trip costs)
get touched first, and in which direction? Stdlib unittest only."""

from __future__ import annotations

import unittest

from vouch.analysis.sft_data import ENTRY_QUESTIONS, _conviction_for, barrier_label, build_dataset

TP, SL, FEE = 0.04, 0.02, 0.012
TP_NET = TP - FEE  # 0.028


def series(moves: list[float], entry: float = 100.0) -> list[float]:
    return [entry * (1.0 + m) for m in moves]


class BarrierLabelTest(unittest.TestCase):
    def test_up_take_touched_first_is_long(self):
        # rises 2.8% net-take on bar 3
        path = series([0.0, 0.01, 0.02, TP_NET, TP_NET + 0.01])
        label, fwd = barrier_label(path, 0, horizon=10, tp_pct=TP, sl_pct=SL, fee_pct=FEE)
        self.assertEqual(label, "long")
        self.assertGreater(fwd, 0.0)

    def test_down_take_touched_first_is_short(self):
        path = series([0.0, -0.01, -0.02, -TP_NET, -TP_NET - 0.01])
        label, _ = barrier_label(path, 0, horizon=10, tp_pct=TP, sl_pct=SL, fee_pct=FEE)
        self.assertEqual(label, "short")

    def test_move_that_never_clears_fees_is_flat(self):
        # +2.0% is a real-looking move but under the 2.8% net take barrier
        path = series([0.0, 0.01, 0.02, 0.015, 0.01])
        label, _ = barrier_label(path, 0, horizon=10, tp_pct=TP, sl_pct=SL, fee_pct=FEE)
        self.assertEqual(label, "flat")

    def test_horizon_cut_keeps_flat(self):
        # take barrier hit on the bar AFTER the horizon -> flat
        path = series([0.0, 0.01, 0.02, TP_NET])
        label, _ = barrier_label(path, 0, horizon=2, tp_pct=TP, sl_pct=SL, fee_pct=FEE)
        self.assertEqual(label, "flat")

    def test_fee_move_makes_the_label_harder_than_gross(self):
        # +2.5% clears a fee-free 2% barrier but not the 2.8% net barrier
        path = series([0.0, 0.012, 0.025])
        self.assertEqual(
            "flat", barrier_label(path, 0, horizon=5, tp_pct=TP, sl_pct=SL, fee_pct=FEE)[0]
        )
        # the same path clears a gross 2% barrier when fees are zero
        self.assertEqual(
            "long", barrier_label(path, 0, horizon=5, tp_pct=0.02, sl_pct=SL, fee_pct=0.0)[0]
        )

    def test_nonpositive_entry_is_flat(self):
        label, fwd = barrier_label([0.0, 0.0], 0, horizon=5, tp_pct=TP, sl_pct=SL, fee_pct=FEE)
        self.assertEqual((label, fwd), ("flat", 0.0))

    def test_stop_never_labels_directly(self):
        # a 3.2% drop (through where a long stop would sit) must label short
        # via the down take barrier, which is crossed first
        path = series([0.0, -0.02, -(SL + FEE)])
        label, _ = barrier_label(path, 0, horizon=5, tp_pct=TP, sl_pct=SL, fee_pct=FEE)
        self.assertEqual(label, "short")


class ConvictionPolicyTest(unittest.TestCase):
    def test_barrier_hits_scale_with_excursion(self):
        tp_net = TP - FEE
        self.assertEqual(_conviction_for("long", 2 * tp_net, tp_net, SL), 4)
        self.assertEqual(_conviction_for("long", 1.5 * tp_net, tp_net, SL), 3)
        self.assertEqual(_conviction_for("long", tp_net, tp_net, SL), 2)
        self.assertEqual(_conviction_for("short", -tp_net, tp_net, SL), 2)

    def test_flat_rows_split_chop_from_lean(self):
        self.assertEqual(_conviction_for("flat", 0.005, TP - FEE, SL), 0)  # quiet chop
        self.assertEqual(_conviction_for("flat", 0.015, TP - FEE, SL), 1)  # moved, no barrier


class BuildDatasetTrioTest(unittest.TestCase):
    @staticmethod
    def _entry_row(i: int, price: float) -> dict:
        return {
            "price": price,
            "ts": f"t{i}",
            # the compact state: NOT what the bake may train on
            "state": {"market": f"x{i}"},
            "decision": {
                "mode": "entry",
                "probs": {"long": 0.4, "flat": 0.3, "short": 0.3},
                # the byte-exact payload the engine sent the head
                "wire": {"state": {"market": f"wire {i}"}, "questions": ENTRY_QUESTIONS},
            },
        }

    def test_rows_carry_the_full_served_question_trio(self):
        # a price path with one net-take up move inside the horizon
        prices = [100.0, 100.5, 101.0, 101.5, 102.0, 103.0]
        rows = [self._entry_row(i, p) for i, p in enumerate(prices)]
        ds = build_dataset(
            rows, horizon=4, threshold=0.004, policy="barrier", tp_pct=TP, sl_pct=SL, fee_pct=FEE
        )
        self.assertTrue(ds)
        for pair in ds:
            self.assertEqual(set(pair["answers"].keys()), {"action", "conviction", "enter_now"})
            self.assertEqual(pair["questions"], ENTRY_QUESTIONS)
            # byte-pin rule: the trained state is the wire payload, not the
            # compact row state
            self.assertTrue(pair["state"].get("market", "").startswith("wire "))
            self.assertIn(pair["answers"]["action"]["choice"], ("long", "flat", "short"))
            self.assertTrue(0 <= pair["answers"]["conviction"]["score"] <= 4)
            self.assertIn(pair["answers"]["enter_now"]["noul"], (0.0, 1.0))

    def test_rows_without_wire_payload_are_skipped(self):
        # pre-wire replays cannot be trained on without a train/serve
        # format mismatch (stage 2, 2026-09-26) - they must not silently
        # fall back to the compact state
        prices = [100.0, 100.5, 101.0, 101.5, 102.0, 103.0]
        rows = [self._entry_row(i, p) for i, p in enumerate(prices)]
        for r in rows[:3]:
            del r["decision"]["wire"]
        ds = build_dataset(
            rows, horizon=4, threshold=0.004, policy="barrier", tp_pct=TP, sl_pct=SL, fee_pct=FEE
        )
        # 3 wire-less rows skipped, and the last row always drops (no future
        # bar to label against)
        self.assertEqual(len(ds), len(prices) - 3 - 1)
        for pair in ds:
            self.assertTrue(pair["state"].get("market", "").startswith("wire"))

    def test_exit_rows_are_skipped(self):
        rows = [
            {"price": 100.0, "ts": "t0", "decision": {"mode": "exit", "probs": {}}},
            {"price": 100.5, "ts": "t1", "decision": {"mode": "exit", "probs": {}}},
        ]
        self.assertEqual(build_dataset(rows, horizon=2, threshold=0.004), [])


class WireVersionMixTest(unittest.TestCase):
    """v5 wire versioning: a bake must be single-format (the gold-bug
    lesson). decision.wire_version travels on every wire row; pre-v5 wire
    rows predate the field and are v1 by definition."""

    @staticmethod
    def _row(i: int, price: float, wire_version: int | None) -> dict:
        d = {
            "mode": "entry",
            "probs": {"long": 0.4, "flat": 0.3, "short": 0.3},
            "wire": {"state": {"market": f"wire {i}"}, "questions": ENTRY_QUESTIONS},
        }
        if wire_version is not None:
            d["wire_version"] = wire_version
        return {"price": price, "ts": f"t{i}", "state": {"market": f"x{i}"}, "decision": d}

    def test_mixed_versions_are_refused_loudly(self):
        prices = [100.0, 100.5, 101.0, 101.5, 102.0]
        rows = [
            self._row(0, prices[0], 1),
            self._row(1, prices[1], 1),
            self._row(2, prices[2], 2),
            self._row(3, prices[3], 2),
        ]
        with self.assertRaises(SystemExit) as ctx:
            build_dataset(rows, horizon=2, threshold=0.004)
        self.assertIn("mix wire versions [1, 2]", str(ctx.exception))

    def test_single_version_bakes_and_records_it(self):
        prices = [100.0, 100.5, 101.0, 101.5, 102.0]
        rows = [self._row(i, p, 2) for i, p in enumerate(prices)]
        ds = build_dataset(rows, horizon=2, threshold=0.004)
        self.assertTrue(ds)
        for pair in ds:
            self.assertEqual(pair["meta"]["wire_version"], 2)

    def test_pre_v5_wire_rows_default_to_v1(self):
        prices = [100.0, 100.5, 101.0, 101.5, 102.0]
        rows = [self._row(i, p, None) for i, p in enumerate(prices)]
        ds = build_dataset(rows, horizon=2, threshold=0.004)
        self.assertTrue(ds)
        for pair in ds:
            self.assertEqual(pair["meta"]["wire_version"], 1)


if __name__ == "__main__":
    unittest.main()
