"""Rules rails tests: the headline safety property of the two-model loop.

System-2 may only propose values inside RAILS, and validate() is the code
boundary that enforces it. Every rail bound, the TP>=SL bracket, the
allowed_actions subset and the sanitize trimming are pinned here. Stdlib
unittest only.
"""

from __future__ import annotations

import os
import unittest

from vouch.config import load_settings
from vouch.engine.rules import ALLOWED_ACTIONS, RAILS, Rules, sanitize, validate


class ValidateRailBoundsTest(unittest.TestCase):
    def test_empty_proposal_is_valid(self):
        ok, msg = validate({})
        self.assertTrue(ok)
        self.assertEqual(msg, "ok")

    def test_every_rail_rejects_out_of_bounds(self):
        for key, (lo, hi) in RAILS.items():
            for bad in (lo - 0.001, hi + 0.001):
                ok, msg = validate({key: bad})
                self.assertFalse(ok, f"{key}={bad} must be rejected")
                self.assertIn("outside rail", msg)

    def test_rail_bounds_are_inclusive(self):
        for key, (lo, hi) in RAILS.items():
            ok, _ = validate({key: lo})
            self.assertTrue(ok, f"{key} at lower bound {lo} must pass")
            ok, _ = validate({key: hi})
            self.assertTrue(ok, f"{key} at upper bound {hi} must pass")

    def test_non_numeric_rail_value_rejected(self):
        ok, msg = validate({"min_conviction": "0.5"})
        self.assertFalse(ok)

    def test_non_dict_rejected(self):
        ok, _ = validate(["min_conviction", 0.5])  # type: ignore[arg-type]
        self.assertFalse(ok)

    def test_unknown_field_rejected(self):
        # The classic S2 overreach: touching a knob the rails do not expose
        # (max_drawdown_pct is the kill switch and is NOT in RAILS).
        ok, msg = validate({"max_drawdown_pct": 0.9})
        self.assertFalse(ok)
        self.assertIn("unknown fields", msg)


class ValidateBracketTest(unittest.TestCase):
    def test_take_profit_below_stop_rejected(self):
        ok, msg = validate({"take_profit_pct": 0.01, "stop_loss_pct": 0.02})
        self.assertFalse(ok)
        self.assertIn("inverted bracket", msg)

    def test_take_profit_equal_to_stop_allowed(self):
        ok, _ = validate({"take_profit_pct": 0.02, "stop_loss_pct": 0.02})
        self.assertTrue(ok)

    def test_take_profit_above_stop_allowed(self):
        ok, _ = validate({"take_profit_pct": 0.04, "stop_loss_pct": 0.02})
        self.assertTrue(ok)

    def test_inverted_bracket_detected_across_rail_bounds(self):
        # TP at its rail floor, SL at its rail ceiling: both individually
        # legal, together inverted, and therefore rejected.
        ok, msg = validate(
            {
                "take_profit_pct": RAILS["take_profit_pct"][0],
                "stop_loss_pct": RAILS["stop_loss_pct"][1],
            }
        )
        self.assertFalse(ok)
        self.assertIn("inverted bracket", msg)


class ValidateAllowedActionsTest(unittest.TestCase):
    def test_subset_with_flat_ok(self):
        for subset in (["flat"], ["long", "flat"], ["short", "flat"], list(ALLOWED_ACTIONS)):
            ok, _ = validate({"allowed_actions": subset})
            self.assertTrue(ok, f"{subset} must be accepted")

    def test_missing_flat_rejected(self):
        ok, msg = validate({"allowed_actions": ["long", "short"]})
        self.assertFalse(ok)
        self.assertIn("flat", msg)

    def test_empty_rejected(self):
        ok, _ = validate({"allowed_actions": []})
        self.assertFalse(ok)

    def test_unknown_action_rejected(self):
        ok, _ = validate({"allowed_actions": ["flat", "yolo"]})
        self.assertFalse(ok)

    def test_not_a_list_rejected(self):
        ok, _ = validate({"allowed_actions": "flat"})
        self.assertFalse(ok)


class SanitizeTest(unittest.TestCase):
    def test_small_proposal_passes_through(self):
        prop = {"min_conviction": 0.2}
        out, msg = sanitize(prop, max_fields=2)
        self.assertEqual(out, prop)
        self.assertIsNone(msg)

    def test_trims_to_lowest_sorted_keys_for_determinism(self):
        # sanitize keeps the alphabetically first max_fields keys, so the
        # trim does not depend on dict insertion order.
        prop = {"min_conviction": 0.2, "cooldown_ticks": 10, "flat_max_p": 0.9}
        out, msg = sanitize(prop, max_fields=2)
        self.assertEqual(out, {"cooldown_ticks": 10, "flat_max_p": 0.9})
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertIn("trimmed", msg)

    def test_max_fields_one_keeps_single_lowest(self):
        out, _ = sanitize({"min_conviction": 0.2, "flat_max_p": 0.9}, max_fields=1)
        self.assertEqual(out, {"flat_max_p": 0.9})


class FromEnvTest(unittest.TestCase):
    def setUp(self):
        load_settings.cache_clear()
        self._old = os.environ.get("VOUCH_RULES")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("VOUCH_RULES", None)
        else:
            os.environ["VOUCH_RULES"] = self._old
        load_settings.cache_clear()

    def test_valid_env_json_applied(self):
        os.environ["VOUCH_RULES"] = '{"min_conviction": 0.3, "allowed_actions": ["long", "flat"]}'
        rules = Rules.from_env()
        self.assertEqual(rules.min_conviction, 0.3)
        self.assertEqual(rules.allowed_actions, ["long", "flat"])

    def test_rail_violating_env_ignored(self):
        os.environ["VOUCH_RULES"] = '{"max_position_pct": 0.99}'
        rules = Rules.from_env()
        self.assertEqual(rules.max_position_pct, 0.15)  # default kept

    def test_invalid_json_ignored(self):
        os.environ["VOUCH_RULES"] = "{not json"
        rules = Rules.from_env()
        self.assertEqual(rules.min_conviction, 0.15)

    def test_one_bad_field_poisons_the_whole_proposal(self):
        # All-or-nothing: a proposal touching an unknown field (the kill
        # switch is not in RAILS) is rejected entirely, so S2 cannot smuggle
        # a legal tweak past alongside an illegal one.
        os.environ["VOUCH_RULES"] = '{"max_drawdown_pct": 0.0, "min_conviction": 0.2}'
        rules = Rules.from_env()
        self.assertEqual(rules.min_conviction, 0.15)  # nothing applied at all
        self.assertNotIn("max_drawdown_pct", rules.to_dict())


if __name__ == "__main__":
    unittest.main()
