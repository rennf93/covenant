"""News store parsing tests for the judge doc (pinned C1 JSONL schema).

The store is consumed through the schema, not the vouch.news module. Feeds
are inherently gappy and the store tolerates bad items on write, so the
consumer skips unparseable lines/rows instead of refusing (docs/
v5-phase1-spec.md, chunk C). Stdlib unittest only; run from vouch/.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from vouch.analysis.judge_doc import load_news, news_lines, probe_minute_ts


def event(**overrides) -> dict:
    row = {
        "id": "deadbeefdeadbeef",
        "title": "Solana ETF inflows hit a record week",
        "url": "https://example.com/x",
        "source": "CoinDesk",
        "published_at": "2026-09-05T21:30:00+00:00",  # 1h before the probe
        "currencies": ["SOL"],
        "kind": "news",
        "via": "rss",
    }
    row.update(overrides)
    return row


PROBE = {"ts": "2026-09-05T22:30:00+00:00"}
TS = probe_minute_ts(PROBE)


class LoadNewsTest(unittest.TestCase):
    def test_reads_the_pinned_schema_rows(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Path(tmp.name) / "events.jsonl"
        store.write_text(json.dumps(event()) + "\n" + json.dumps(event(id="ff")) + "\n")
        rows = load_news(store)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["published_at"], "2026-09-05T21:30:00+00:00")

    def test_skips_unparseable_lines_not_fatal(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Path(tmp.name) / "events.jsonl"
        store.write_text(
            json.dumps(event())
            + "\nnot json\n"
            + json.dumps(["not", "a", "dict"])
            + "\n\n"
            + json.dumps(event(id="ff"))
            + "\n"
        )
        self.assertEqual([r["id"] for r in load_news(store)], ["deadbeefdeadbeef", "ff"])


class PublishedAtParsingTest(unittest.TestCase):
    def lines(self, *rows: dict) -> list[str]:
        return news_lines(list(rows), TS)

    def test_z_suffix_and_offsets_parse(self):
        lines = self.lines(
            event(published_at="2026-09-05T21:30:00Z", title="zulu"),
            event(published_at="2026-09-05T23:30:00+02:00", title="offset"),
        )
        self.assertEqual(lines, ["- 1.0h ago [CoinDesk] zulu", "- 1.0h ago [CoinDesk] offset"])

    def test_naive_timestamps_are_read_as_utc(self):
        lines = self.lines(event(published_at="2026-09-05T21:30:00"))
        self.assertEqual(lines, ["- 1.0h ago [CoinDesk] Solana ETF inflows hit a record week"])

    def test_bad_rows_are_skipped_not_fatal(self):
        lines = self.lines(
            event(published_at="not-a-date"),
            event(published_at=None),
            {"title": "no published_at", "source": "CoinDesk"},
            event(title=None),
            event(),
        )
        self.assertEqual(lines, ["- 1.0h ago [CoinDesk] Solana ETF inflows hit a record week"])

    def test_titles_render_on_one_line(self):
        lines = self.lines(event(title="  Solana   ETF\tapprovals\ncontinue  "))
        self.assertEqual(lines, ["- 1.0h ago [CoinDesk] Solana ETF approvals continue"])

    def test_equal_timestamps_keep_store_order(self):
        rows = [event(title="first"), event(title="second")]
        self.assertEqual(
            [line.rsplit(" ", 1)[-1] for line in self.lines(*rows)], ["first", "second"]
        )


if __name__ == "__main__":
    unittest.main()
