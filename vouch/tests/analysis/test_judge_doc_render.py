"""Judge document tests: the re-judging instrument is a pinned contract.

The golden document is asserted byte-for-byte; the MARKET STATE line is
pinned against run_judge_validate.render_document (v1 bytes + the wire-v2
context sentence), the question wording against the pre-registered
instrument, and the news window/cap/refusal policies against the spec.
Fixtures are small and literal: a 5-row context cache in the pinned chunk A
JSONL format and news events in the pinned C1 schema (no vouch.news import).

Stdlib unittest only; run from the vouch/ directory.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from run_judge_validate import render_document
from vouch.analysis.judge_doc import (
    EMPTY_NEWS_LINE,
    QUESTION_CRITERIA,
    QUESTION_INSTRUCTIONS,
    context_sentence,
    news_lines,
    probe_minute_ts,
    render,
)
from vouch.engine.context_features import ContextSeries

PROBE_TS = "2026-09-05T22:30:00+00:00"  # a Saturday 22:30 UTC minute

# The exact expected document for the fixtures below. Never edit the string;
# a diff here means the served instrument drifted under the re-judging.
GOLDEN = (
    "MARKET STATE\n"
    "SOL/USDC. Price 200.00. 1m return +0.01%, 15m -0.24%, 60m +0.38%. "
    "Volume ratio 3.30 vs average. "
    "60m range 199.50-200.40 (position in range 0.20). "
    "Funding 0.0100%/8h, 24h change +0.0050pp. "
    "Open interest $1450M, 1h +0.4%, 24h -3.1%. "
    "BTC 1m +0.03%, 60m -0.42%, 24h +2.10%. "
    "Price +1.2% vs 4h EMA, 30d range position 73%, vol regime normal. "
    "Saturday 22:30 UTC.\n"
    "last 15 one-minute closes: 199.50 200.10 200.00\n"
    "\n"
    "COMPASS READS\n"
    "The calibrated compass flagged this moment (volume, conviction). "
    "Its readings: upward pressure 0.05, downward pressure 0.06, "
    "no-direction pressure 0.89, conviction 2/4, entry pressure 0.11, "
    "direction spread -0.02. The compass does NOT pick direction; "
    "it only reports how notable the moment is.\n"
    "\n"
    "NEWS DIGEST\n"
    "- 3.5h ago [CoinDesk] Solana ETF inflows hit a record week\n"
    "- 20.0h ago [Decrypt] Solana validators approve a new fee market\n"
    "\n"
    "QUESTION\n"
    "A trading compass flagged this moment as notable. Decide exactly one "
    "thing: should the book open a directional position NOW? Judge the setup "
    "on its merits; when uncertain or conditions look choppy, choose no_trade.\n"
    "- long: open a long on demonstrated strength\n"
    "- short: open a short on demonstrated weakness\n"
    "- no_trade: no clean directional setup, stand aside\n"
)


def probe_fixture() -> dict:
    """One probe row in the run_probe_build.py schema (trimmed path_15)."""
    return {
        "ts": PROBE_TS,
        "price": 200.0,
        "state": {
            "ret_1m": 0.0001,
            "ret_15m": -0.0024,
            "ret_60m": 0.0038,
            "volume_ratio": 3.3,
            "range_pos_60m": 0.2,
            "in_position": False,
        },
        "laya": {
            "probs": {"long": 0.0467, "flat": 0.8892, "short": 0.0641},
            "conviction": 2,
            "enter_p": 0.1108,
            "ls_spread": -0.0174,
            "action_conf": 0.889,
        },
        "flag_reasons": ["volume", "conviction"],
        "label": "flat",
        "fwd_ret": 0.0073,
        "tp_net": 0.018,
        "context": {
            "window_low": 199.5,
            "window_high": 200.4,
            "window_ret": 0.0025,
            "path_15": [199.5, 200.1, 200.0],
        },
    }


def write_context_cache(path: Path, ts: int, minutes: tuple[int, ...] = (-2, -1, 0, 1, 2)) -> Path:
    """The pinned chunk A cache format: one JSON row per minute on the 1m
    grid (load_jsonl refuses larger holes, so rows stay contiguous)."""
    rows = []
    for minute in minutes:
        rows.append(
            {
                "ts": ts + minute * 60,
                "funding_rate_8h": 0.0001,
                "funding_24h_ago": 0.00005,
                "oi_usd_m": 1450.2,
                "oi_chg_1h_pct": 0.42,
                "oi_chg_24h_pct": -3.15,
                "btc_ret_1m": 0.0003,
                "btc_ret_60m": -0.0042,
                "btc_ret_24h": 0.021,
                "ema4h_dist_pct": 1.25,
                "range_pos_30d": 0.734,
                "vol_regime": "normal",
            }
        )
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def event(published: str, title: str, source: str = "CoinDesk") -> dict:
    """One news row in the pinned C1 schema."""
    return {
        "id": "deadbeefdeadbeef",
        "title": title,
        "url": "https://example.com/x",
        "source": source,
        "published_at": published,
        "currencies": ["SOL"],
        "kind": "news",
        "via": "rss",
    }


def golden_news() -> list[dict]:
    return [
        event("2026-09-05T19:00:00+00:00", "Solana ETF inflows hit a record week"),
        event("2026-09-04T20:00:00+00:00", "26.5h before the probe, out of window"),
        event("2026-09-05T02:30:00+00:00", "Solana validators approve a new fee market", "Decrypt"),
    ]


class GoldenDocumentTest(unittest.TestCase):
    def setUp(self):
        self.probe = probe_fixture()
        self.ts = probe_minute_ts(self.probe)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.series = ContextSeries.load_jsonl(
            write_context_cache(Path(tmp.name) / "ctx.jsonl", self.ts)
        )

    def render(self, news_rows=None) -> str:
        return render(self.probe, self.series, golden_news() if news_rows is None else news_rows)

    def test_full_document_matches_the_golden_bytes(self):
        self.assertEqual(self.render(), GOLDEN)

    def test_market_state_line_is_v1_bytes_plus_the_pinned_v2_sentence(self):
        point = self.series.at(self.ts)
        assert point is not None
        expected = render_document(self.probe)["state"]["market"] + context_sentence(point)
        self.assertEqual(self.render().splitlines()[1], expected)

    def test_compass_reads_are_unchanged_from_render_document(self):
        self.assertEqual(
            self.render().splitlines()[5],
            render_document(self.probe)["state"]["compass_reading"],
        )

    def test_question_is_the_pre_registered_instrument_at_the_end(self):
        doc = self.render()
        entry = render_document(self.probe)["questions"]["entry"]
        self.assertEqual(QUESTION_INSTRUCTIONS, entry["instructions"])
        self.assertEqual(QUESTION_CRITERIA, entry["criteria"])
        self.assertIn("QUESTION\n", doc)
        self.assertGreater(doc.index("NEWS DIGEST"), doc.index("COMPASS READS"))
        self.assertTrue(doc.endswith(f"- no_trade: {QUESTION_CRITERIA['no_trade']}\n"))


class EmptyNewsWindowTest(unittest.TestCase):
    def setUp(self):
        self.probe = probe_fixture()
        self.ts = probe_minute_ts(self.probe)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.series = ContextSeries.load_jsonl(
            write_context_cache(Path(tmp.name) / "ctx.jsonl", self.ts)
        )

    def test_no_news_rows_render_the_pinned_literal_line(self):
        doc = render(self.probe, self.series, [])
        self.assertIn(f"NEWS DIGEST\n{EMPTY_NEWS_LINE}\n", doc)

    def test_only_out_of_window_rows_render_it_too(self):
        rows = [
            event("2026-09-04T20:00:00+00:00", "26.5h before the probe"),
            event("2026-09-05T23:30:00+00:00", "an hour after the probe"),
        ]
        doc = render(self.probe, self.series, rows)
        self.assertIn(f"NEWS DIGEST\n{EMPTY_NEWS_LINE}\n", doc)


class MissingContextTest(unittest.TestCase):
    def setUp(self):
        self.probe = probe_fixture()
        self.ts = probe_minute_ts(self.probe)

    def render_with_cache(self, minutes: tuple[int, ...], probe: dict | None = None) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cache = write_context_cache(Path(tmp.name) / "ctx.jsonl", self.ts, minutes)
        render(self.probe if probe is None else probe, ContextSeries.load_jsonl(cache), [])

    def test_cache_ending_before_the_probe_minute_refuses(self):
        with self.assertRaises(SystemExit):
            self.render_with_cache((-120, -119, -118))

    def test_probe_without_a_ts_refuses(self):
        probe = {k: v for k, v in self.probe.items() if k != "ts"}
        with self.assertRaises(SystemExit):
            self.render_with_cache((-2, -1, 0, 1, 2), probe)

    def test_unparseable_probe_ts_refuses(self):
        probe = {**self.probe, "ts": "not-a-date"}
        with self.assertRaises(SystemExit):
            self.render_with_cache((-2, -1, 0, 1, 2), probe)


class NewsWindowTest(unittest.TestCase):
    def setUp(self):
        self.ts = int(datetime(2026, 9, 5, 22, 30, tzinfo=UTC).timestamp())

    def lines_for(self, *ages: timedelta) -> list[str]:
        base = datetime.fromtimestamp(self.ts, tz=UTC)
        rows = [event((base - age).isoformat(), f"t{i}") for i, age in enumerate(ages)]
        return news_lines(rows, self.ts)

    def test_window_is_inclusive_at_both_ends(self):
        lines = self.lines_for(timedelta(0), timedelta(hours=24))
        self.assertEqual(lines, ["- 0.0h ago [CoinDesk] t0", "- 24.0h ago [CoinDesk] t1"])

    def test_events_outside_the_window_are_excluded(self):
        lines = self.lines_for(timedelta(seconds=-60), timedelta(hours=24, seconds=1))
        self.assertEqual(lines, [])

    def test_newest_first(self):
        lines = self.lines_for(timedelta(hours=10), timedelta(hours=2), timedelta(hours=20))
        ages = [line.split("h ago")[0].lstrip("- ") for line in lines]
        self.assertEqual(ages, ["2.0", "10.0", "20.0"])


class NewsCapTest(unittest.TestCase):
    def test_cap_at_twenty_keeps_the_newest(self):
        ts = int(datetime(2026, 9, 5, 22, 30, tzinfo=UTC).timestamp())
        base = datetime.fromtimestamp(ts, tz=UTC)
        rows = [event((base - timedelta(hours=h)).isoformat(), f"title {h}") for h in range(1, 26)]
        lines = news_lines(rows, ts)
        self.assertEqual(len(lines), 20)
        self.assertEqual(lines[0], "- 1.0h ago [CoinDesk] title 1")
        self.assertEqual(lines[-1], "- 20.0h ago [CoinDesk] title 20")
        self.assertFalse(any("title 21" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
