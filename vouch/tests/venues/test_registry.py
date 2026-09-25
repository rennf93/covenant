"""Venue registry tests: selection by env, receipt identity attrs, fill round
trip. Stdlib unittest only, no network: the venue adapter's price fetch is
never exercised (and ArbPaperVenue's market_order/get_fill never touch the
client at all; PaperVenue.get_price is stubbed where a price is needed).
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from vouch.attest.receipts import VENUE_ARBITRUM_DEX, VENUE_COINBASE, VENUE_OTHER
from vouch.config import load_settings
from vouch.exceptions import VenueError
from vouch.venues import (
    VENUES,
    ArbPaperVenue,
    CoinbaseVenue,
    PaperVenue,
    available_venues,
    make_venue,
)


def _with_env(**env: str):
    """Patch env vars AND drop the cached Settings snapshot, since the venue
    registry reads configuration through load_settings()."""

    def decorator(fn):
        def wrapper(*args, **kwargs):
            with mock.patch.dict(os.environ, env, clear=False):
                load_settings.cache_clear()
                try:
                    return fn(*args, **kwargs)
                finally:
                    load_settings.cache_clear()

        return wrapper

    return decorator


class RegistrySelectionTest(unittest.TestCase):
    @_with_env()
    def test_default_is_paper(self):
        self.assertIsInstance(make_venue(), PaperVenue)

    @_with_env(VOUCH_VENUE="arb-paper")
    def test_arb_paper_selected_by_env(self):
        self.assertIsInstance(make_venue(), ArbPaperVenue)

    @_with_env(VOUCH_VENUE="coinbase", VOUCH_CB_KEY_NAME="org/key", VOUCH_CB_PRIVATE_KEY="ZmFrZQ==")
    def test_coinbase_selected_by_env_when_keys_set(self):
        self.assertIsInstance(make_venue(), CoinbaseVenue)

    @_with_env(VOUCH_VENUE="coinbase")
    def test_coinbase_without_keys_refuses(self):
        with self.assertRaises(VenueError):
            make_venue()

    @_with_env(VOUCH_VENUE="nonsense")
    def test_unknown_venue_falls_back_to_paper(self):
        self.assertIsInstance(make_venue(), PaperVenue)

    def test_registry_lists_three_venues(self):
        self.assertEqual(set(VENUES), {"paper", "coinbase", "arb-paper"})


class ReceiptIdentityTest(unittest.TestCase):
    """venue_id / receipt_venue / instrument are the receipt identity: the
    venue class travels into the canonical Covenant receipt."""

    def test_paper_identity(self):
        v = PaperVenue()
        self.assertEqual(v.venue_id, "paper")
        self.assertEqual(v.receipt_venue, VENUE_OTHER)
        self.assertEqual(v.instrument, "SOL-USD")

    def test_arb_paper_identity(self):
        v = ArbPaperVenue()
        self.assertEqual(v.venue_id, "arb-paper")
        self.assertEqual(v.receipt_venue, VENUE_ARBITRUM_DEX)
        self.assertEqual(v.instrument, "SOL/USDC")

    def test_coinbase_identity(self):
        with mock.patch.dict(
            os.environ, {"VOUCH_CB_KEY_NAME": "org/key", "VOUCH_CB_PRIVATE_KEY": "ZmFrZQ=="}
        ):
            load_settings.cache_clear()
            try:
                v = CoinbaseVenue("SOL-USD")
            finally:
                load_settings.cache_clear()
        self.assertEqual(v.venue_id, "coinbase")
        self.assertEqual(v.receipt_venue, VENUE_COINBASE)
        self.assertEqual(v.instrument, "SOL-USD")


class ArbPaperFillRoundTripTest(unittest.TestCase):
    """market_order books a fill record; get_fill returns it. No network:
    the paper path never uses the httpx client."""

    def test_fill_record_round_trip(self):
        venue = ArbPaperVenue()
        res = venue.market_order(client=None, side="BUY", quote_usd=10.0, price=150.0)
        self.assertEqual(res["venue"], "arb-paper")
        order_id = res["order_id"]
        self.assertTrue(order_id.startswith("arbpaper-"))
        fill = venue.get_fill(None, order_id)  # type: ignore[arg-type]
        self.assertAlmostEqual(fill["avg_price"], res["fill_price"])
        self.assertAlmostEqual(fill["fee_usd"], res["fee_usd"])
        self.assertAlmostEqual(fill["size"], round(10.0 / res["fill_price"], 6))

    def test_unknown_order_id_returns_empty(self):
        venue = ArbPaperVenue()
        self.assertEqual(venue.get_fill(None, "nope"), {})  # type: ignore[arg-type]


class AvailableVenuesTest(unittest.TestCase):
    @_with_env()
    def test_coinbase_reports_missing_env(self):
        listed = {v["id"]: v for v in available_venues()}
        self.assertEqual(set(listed), {"paper", "coinbase", "arb-paper"})
        self.assertTrue(listed["paper"]["configured"])
        self.assertFalse(listed["coinbase"]["configured"])
        self.assertEqual(
            listed["coinbase"]["missing_env"], ["VOUCH_CB_KEY_NAME", "VOUCH_CB_PRIVATE_KEY"]
        )

    @_with_env(VOUCH_CB_KEY_NAME="org/key", VOUCH_CB_PRIVATE_KEY="ZmFrZQ==")
    def test_coinbase_reports_configured(self):
        listed = {v["id"]: v for v in available_venues()}
        self.assertTrue(listed["coinbase"]["configured"])
        self.assertEqual(listed["coinbase"]["missing_env"], [])


if __name__ == "__main__":
    unittest.main()
