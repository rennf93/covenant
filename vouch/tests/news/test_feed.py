"""News feed parsing tests against REAL captured fixtures.

coindesk.rss, cointelegraph.rss and decrypt.rss were curled from the pinned
default feeds on 2026-10-03; cryptoslate.atom is CryptoSlate's Atom
rendition of the same wire (none of the four default URLs serve Atom
directly, /feed/atom/ does). Fixtures are trimmed to a few items: bulky
description/content/media elements are dropped, title/link/pubDate are
verbatim. Stdlib unittest."""

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

from vouch.news.feed import NewsEvent, event_id, event_time, parse_feed, tag_currencies

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "news"


def _fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class RssParsingTest(unittest.TestCase):
    """RSS 2.0 fixtures captured from the pinned default feeds."""

    def setUp(self):
        self.events = parse_feed("CoinDesk", _fixture_bytes("coindesk.rss"))

    def test_items_map_to_the_pinned_schema(self):
        self.assertEqual(len(self.events), 4)
        for ev in self.events:
            self.assertEqual(ev.kind, "news")
            self.assertEqual(ev.via, "rss")
            self.assertEqual(ev.source, "CoinDesk")
            self.assertEqual(ev.id, event_id(ev.url))
            self.assertTrue(ev.url.startswith("https://www.coindesk.com/"))
            self.assertTrue(ev.published_at.endswith("+00:00"))

    def test_rfc822_dates_become_iso8601_utc(self):
        ev = next(e for e in self.events if "bank-group-sues" in e.url)
        # real pubDate: Fri, 02 Oct 2026 21:23:20 +0000
        self.assertEqual(ev.published_at, "2026-10-02T21:23:20+00:00")

    def test_non_utc_rfc822_offsets_convert_to_utc(self):
        rss = (
            b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
            b"<item><title>Ethereum fees drop</title><link>https://example.com/eth-fees</link>"
            b"<pubDate>Fri, 02 Oct 2026 16:00:00 -0500</pubDate></item>"
            b"</channel></rss>"
        )
        events = parse_feed("synthetic", rss)
        self.assertEqual(events[0].published_at, "2026-10-02T21:00:00+00:00")

    def test_currency_tags_on_real_items(self):
        by_url = {ev.url: ev.currencies for ev in self.events}
        blast = next(v for k, v in by_url.items() if "ethereum-layer-2-blast" in k)
        live = next(v for k, v in by_url.items() if "live-updates-bitcoin" in k)
        banks = next(v for k, v in by_url.items() if "bank-group-sues" in k)
        self.assertEqual(blast, ("ETH",))
        self.assertEqual(live, ("BTC",))
        self.assertEqual(banks, ("CRYPTO",))

    def test_cdata_title_and_link_parse(self):
        # cointelegraph wraps link in CDATA with tracking params: the URL is
        # taken verbatim, the content address hashes whatever the feed ships
        events = parse_feed("Cointelegraph", _fixture_bytes("cointelegraph.rss"))
        self.assertEqual(len(events), 4)
        bitcoin = next(e for e in events if "bitcoin-briefly-taps-87k" in e.url)
        self.assertEqual(bitcoin.currencies, ("BTC",))
        self.assertIn("utm_source=rss_feed", bitcoin.url)


class AtomParsingTest(unittest.TestCase):
    """Atom fixture: CryptoSlate's /feed/atom/ rendition (real capture)."""

    def setUp(self):
        self.events = parse_feed("CryptoSlate", _fixture_bytes("cryptoslate.atom"))

    def test_entries_use_the_alternate_link_and_iso_dates(self):
        self.assertEqual(len(self.events), 3)
        bitcoin = next(e for e in self.events if "sell-wall" in e.url)
        self.assertEqual(bitcoin.published_at, "2026-10-02T20:30:33+00:00")
        self.assertEqual(bitcoin.currencies, ("BTC",))

    def test_missing_published_falls_back_to_updated(self):
        atom = (
            b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">'
            b"<title>t</title>"
            b"<entry><title>Solana outage postmortem</title>"
            b'<link rel="alternate" href="https://example.com/sol-pm"/>'
            b"<updated>2026-10-01T00:30:00Z</updated></entry>"
            b"<entry><title>Ethereum upgrade ships</title>"
            b'<link rel="alternate" href="https://example.com/eth-up"/>'
            b"<published>2026-10-01T02:00:00+02:00</published></entry>"
            b"</feed>"
        )
        events = parse_feed("synthetic", atom)
        by_url = {e.url: e.published_at for e in events}
        self.assertEqual(by_url["https://example.com/sol-pm"], "2026-10-01T00:30:00+00:00")
        # +02:00 offset normalizes to UTC like every other producer
        self.assertEqual(by_url["https://example.com/eth-up"], "2026-10-01T00:00:00+00:00")


class MalformedItemToleranceTest(unittest.TestCase):
    def test_bad_date_skips_the_item_not_the_fetch(self):
        raw = _fixture_bytes("decrypt.rss").replace(
            b"Fri, 02 Oct 2026 21:16:04 +0000", b"not a date, really"
        )
        events = parse_feed("Decrypt", raw)
        self.assertEqual(len(events), 3)  # 4 real items, the broken one gone
        self.assertFalse(any("california-subpoena" in e.url for e in events))

    def test_item_without_a_link_is_skipped(self):
        rss = (
            b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
            b"<item><title>Bitcoin rallies</title>"
            b"<pubDate>Fri, 02 Oct 2026 10:00:00 +0000</pubDate></item>"
            b"<item><title>Solana fee market heats up</title>"
            b"<link>https://example.com/sol-fees</link>"
            b"<pubDate>Fri, 02 Oct 2026 11:00:00 +0000</pubDate></item>"
            b"</channel></rss>"
        )
        events = parse_feed("synthetic", rss)
        self.assertEqual([e.url for e in events], ["https://example.com/sol-fees"])

    def test_unsupported_document_refuses_loudly(self):
        with self.assertRaises(ValueError):
            parse_feed("bad", b"<html><body>not a feed</body></html>")


class TagCurrenciesTest(unittest.TestCase):
    def test_pinned_rule_table(self):
        self.assertEqual(tag_currencies("Solana ETF delayed", "https://x.com/a"), ("SOL",))
        self.assertEqual(tag_currencies("BTC treasury", "https://x.com/a"), ("BTC",))
        self.assertEqual(tag_currencies("Ethereum L2 shuts", "https://x.com/a"), ("ETH",))
        self.assertEqual(tag_currencies("Regulator sues exchange", "https://x.com/a"), ("CRYPTO",))

    def test_multiple_matches_keep_the_pinned_order(self):
        self.assertEqual(
            tag_currencies("Bitcoin and Solana rally", "https://x.com/a"), ("SOL", "BTC")
        )
        self.assertEqual(tag_currencies("BTC vs ETH", "https://x.com/a"), ("BTC", "ETH"))

    def test_url_counts_even_when_the_title_is_quiet(self):
        self.assertEqual(tag_currencies("Quiet title", "https://x.com/solana-post"), ("SOL",))
        self.assertEqual(tag_currencies("Quiet title", "https://x.com/bitcoin-etf"), ("BTC",))


class EventHelpersTest(unittest.TestCase):
    def test_event_id_is_sha256_of_url_truncated_to_16(self):
        url = "https://example.com/a"
        self.assertEqual(event_id(url), hashlib.sha256(url.encode()).hexdigest()[:16])

    def test_row_roundtrip_ignores_forward_added_keys(self):
        row = {
            "id": "0123456789abcdef",
            "title": "T",
            "url": "https://example.com/t",
            "source": "S",
            "published_at": "2026-10-03T12:00:00+00:00",
            "currencies": ["SOL", "BTC"],
            "kind": "news",
            "via": "rss",
            "votes": {"positive": 3},  # a future schema addition
        }
        ev = NewsEvent.from_row(row)
        self.assertEqual(ev.currencies, ("SOL", "BTC"))
        self.assertEqual(event_time(ev).hour, 12)
        self.assertNotIn("votes", ev.to_row())

    def test_from_row_requires_the_pinned_fields(self):
        with self.assertRaises(KeyError):
            NewsEvent.from_row({"id": "x", "url": "https://example.com/x"})


if __name__ == "__main__":
    unittest.main()
