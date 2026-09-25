"""Paper broker tests: the fill-observer seam and the kill switch. Stdlib
unittest only, no network, no LLM (the broker is pure simulation).
"""

from __future__ import annotations

import unittest

from vouch.config import load_settings
from vouch.engine.broker import Broker


def _fresh_broker(**kwargs) -> Broker:
    """Broker reads VOUCH_FEE_BPS via the cached Settings; drop any cached
    snapshot from a previous test so the default fee model applies."""
    load_settings.cache_clear()
    return Broker(starting_cash=1000.0, max_drawdown_pct=0.20, **kwargs)


class FillObserverTest(unittest.TestCase):
    def test_observer_receives_open_and_close(self):
        broker = _fresh_broker()
        events: list[dict] = []
        broker.fill_observer = events.append

        broker.open(side="long", size_usd=100.0, price=100.0, tick_i=3, reason="conv")
        self.assertEqual(len(events), 1)
        opened = events[0]
        self.assertEqual(opened["kind"], "open")
        self.assertEqual(opened["side"], "long")
        self.assertEqual(opened["tick"], 3)
        self.assertEqual(opened["reason"], "conv")
        self.assertAlmostEqual(opened["size_usd"], 100.0)
        self.assertAlmostEqual(opened["price"], 100.0 * 1.0005)  # buy slippage
        self.assertTrue(opened["fee"] > 0)

        broker.close(price=110.0, tick_i=7, reason="TAKE")
        self.assertEqual(len(events), 2)
        closed = events[1]
        self.assertEqual(closed["kind"], "close")
        self.assertEqual(closed["side"], "long")
        self.assertEqual(closed["tick"], 7)
        self.assertAlmostEqual(closed["price"], 110.0 * (1 - 0.0005))  # sell slippage
        self.assertIn("pnl_usd", closed)

    def test_observer_exception_does_not_break_trading(self):
        broker = _fresh_broker()

        def exploding(_fill: dict) -> None:
            raise RuntimeError("observer on fire")

        broker.fill_observer = exploding
        broker.open(side="long", size_usd=100.0, price=100.0, tick_i=0, reason="conv")
        self.assertIsNotNone(broker.position)  # the open still booked
        broker.close(price=110.0, tick_i=1, reason="TAKE")
        self.assertIsNone(broker.position)  # the close still booked
        self.assertEqual(len(broker.trades), 2)

    def test_no_observer_is_fine(self):
        broker = _fresh_broker()
        broker.open(side="short", size_usd=50.0, price=100.0, tick_i=0, reason="conv")
        broker.close(price=90.0, tick_i=1, reason="TAKE")
        self.assertEqual(len(broker.trades), 2)


class KillSwitchTest(unittest.TestCase):
    def test_drawdown_halt_closes_and_stops_trading(self):
        broker = Broker(starting_cash=1000.0, max_drawdown_pct=0.05)
        broker.open(side="long", size_usd=500.0, price=100.0, tick_i=0, reason="conv")
        self.assertFalse(broker.halted)

        # A ~20% adverse move blows through the 5% kill switch.
        broker.mark(price=80.0, tick_i=1)
        self.assertTrue(broker.halted)
        self.assertIn("kill switch", broker.halt_reason)
        self.assertIsNone(broker.position)  # force-closed on the mark
        self.assertEqual(broker.trades[-1]["reason"], "KILL_SWITCH")

        # Halted broker refuses new positions.
        broker.open(side="long", size_usd=100.0, price=80.0, tick_i=2, reason="conv")
        self.assertIsNone(broker.position)

    def test_no_halt_when_in_drawdown_below_limit(self):
        broker = Broker(starting_cash=1000.0, max_drawdown_pct=0.20)
        broker.open(side="long", size_usd=100.0, price=100.0, tick_i=0, reason="conv")
        broker.mark(price=95.0, tick_i=1)
        self.assertFalse(broker.halted)
        self.assertIsNotNone(broker.position)


if __name__ == "__main__":
    unittest.main()
