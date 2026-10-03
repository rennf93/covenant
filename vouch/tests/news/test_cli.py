"""run_news_fetch CLI tests: feed spec parsing, per-feed counts, dead-feed
tolerance, and the loud zero-reachable exit. Stdlib unittest, no network."""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import run_news_fetch
from run_news_fetch import main, parse_feed_spec, resolve_feeds
from vouch.news.feed import DEFAULT_FEEDS, make_event


def stamp(hour: int) -> datetime:
    return datetime(2026, 10, 3, hour, tzinfo=UTC)


class FeedSpecTest(unittest.TestCase):
    def test_name_url_pairs_and_bare_urls_mix(self):
        feeds = parse_feed_spec("CoinDesk| https://a/feed , https://b.example/feed ")
        self.assertEqual(
            feeds, [("CoinDesk", "https://a/feed"), ("b.example", "https://b.example/feed")]
        )

    def test_bare_url_strips_www(self):
        self.assertEqual(
            parse_feed_spec("https://www.coindesk.com/rss/"),
            [("coindesk.com", "https://www.coindesk.com/rss/")],
        )

    def test_flag_beats_env_beats_defaults(self):
        with patch.dict(os.environ, {"VOUCH_NEWS_FEEDS": "Env|https://env/feed"}):
            self.assertEqual(resolve_feeds("A|https://a"), [("A", "https://a")])
            self.assertEqual(resolve_feeds(None), [("Env", "https://env/feed")])
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_feeds(None), list(DEFAULT_FEEDS))

    def test_defaults_are_the_pinned_four(self):
        self.assertEqual(
            [name for name, _ in DEFAULT_FEEDS],
            ["CoinDesk", "Cointelegraph", "Decrypt", "CryptoSlate"],
        )


class CliRunTest(unittest.TestCase):
    def _run(self, store: Path, feeds: str, side_effect, extra: list[str]) -> str:
        argv = ["run_news_fetch.py", "--feeds", feeds, "--store", str(store), *extra]
        out = io.StringIO()
        with (
            patch.object(sys, "argv", argv),
            patch.object(run_news_fetch, "fetch_feed", side_effect=side_effect),
            contextlib.redirect_stdout(out),
        ):
            main()
        return out.getvalue()

    def test_happy_path_prints_per_feed_counts_and_merges(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "events.jsonl"

            def fake(client, name, url):
                if name == "A":
                    return [
                        make_event("A", "Solana ETF approved", "https://a/sol-etf", stamp(10)),
                        make_event("A", "Quiet macro day", "https://a/macro", stamp(9)),
                    ]
                return [make_event("B", "Solana ETF approved", "https://a/sol-etf", stamp(11))]

            out = self._run(store, "A|https://a/feed,B|https://b/feed", fake, [])
            self.assertIn("A: fetched 2, new 2, deduped 0", out)
            # same story via feed B: content address matches, deduped
            self.assertIn("B: fetched 1, new 0, deduped 1", out)
            rows = [json.loads(line) for line in store.read_text().splitlines()]
            self.assertEqual(len(rows), 2)
            self.assertIn('"store_events": 2', out)

    def test_rerun_dedupes_everything_and_keeps_the_file_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "events.jsonl"

            def fake(client, name, url):
                return [make_event("A", "Solana ETF approved", "https://a/sol-etf", stamp(10))]

            self._run(store, "A|https://a/feed", fake, [])
            first = store.read_bytes()
            out = self._run(store, "A|https://a/feed", fake, [])
            self.assertIn("A: fetched 1, new 0, deduped 1", out)
            self.assertEqual(store.read_bytes(), first)

    def test_limit_per_feed_caps_feed_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "events.jsonl"

            def fake(client, name, url):
                return [
                    make_event("A", f"Story {i}", f"https://a/s{i}", stamp(10 - i))
                    for i in range(5)
                ]

            out = self._run(store, "A|https://a/feed", fake, ["--limit-per-feed", "2"])
            self.assertIn("A: fetched 2, new 2, deduped 0", out)
            self.assertEqual(len(store.read_text().splitlines()), 2)

    def test_dead_feed_warns_and_the_run_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "events.jsonl"

            def fake(client, name, url):
                if name == "Dead":
                    raise RuntimeError("HTTP 503")
                return [make_event("Alive", "Ethereum fee drop", "https://z/eth", stamp(8))]

            out = self._run(store, "Dead|https://dead/feed,Alive|https://z/feed", fake, [])
            self.assertIn("WARNING Dead (https://dead/feed) failed: HTTP 503", out)
            self.assertIn("Alive: fetched 1, new 1, deduped 0", out)
            self.assertEqual(len(store.read_text().splitlines()), 1)

    def test_zero_reachable_feeds_warns_loudly_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "events.jsonl"

            def fake(client, name, url):
                raise OSError("connection refused")

            out = self._run(store, "A|https://a/feed,B|https://b/feed", fake, [])
            self.assertIn("WARNING all 2 feeds unreachable", out)
            self.assertIn("exit 0", out)

    def test_no_feeds_configured_refuses(self):
        argv = ["run_news_fetch.py", "--feeds", " , ", "--store", "out/x.jsonl"]
        with (
            patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            main()


if __name__ == "__main__":
    unittest.main()
