"""News store tests: merge-on-rerun, content-addressed dedupe, sort order,
pinned row shape, malformed-line tolerance. Stdlib unittest."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from vouch.news.feed import NewsEvent, event_id, tag_currencies
from vouch.news.store import NewsStore


def ev(url: str, published_at: str, title: str = "A title", source: str = "Feed") -> NewsEvent:
    return NewsEvent(
        id=event_id(url),
        title=title,
        url=url,
        source=source,
        published_at=published_at,
        currencies=tag_currencies(title, url),
        kind="news",
        via="rss",
    )


class MergeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store = NewsStore(Path(self._tmp.name) / "events.jsonl")

    def test_merge_sorts_by_published_at(self):
        self.store.merge(
            [
                ev("https://a/3", "2026-10-03T12:00:00+00:00"),
                ev("https://a/1", "2026-10-01T00:00:00+00:00"),
                ev("https://a/2", "2026-10-02T06:30:00+00:00"),
            ]
        )
        stamps = [e.published_at for e in self.store.load()]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(stamps[0], "2026-10-01T00:00:00+00:00")

    def test_same_story_via_two_feeds_dedupes_by_id(self):
        # identical URL from two sources: one content address, one row, and
        # the first writer keeps the row (reruns stay stable)
        first = ev("https://x/story", "2026-10-02T10:00:00+00:00", source="FeedA")
        second = ev("https://x/story", "2026-10-02T10:05:00+00:00", source="FeedB")
        self.assertEqual(first.id, second.id)
        self.store.merge([first])
        self.store.merge([second])
        rows = self.store.load()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].source, "FeedA")

    def test_rerun_is_idempotent_byte_for_byte(self):
        events = [
            ev("https://a/1", "2026-10-01T00:00:00+00:00"),
            ev("https://b/2", "2026-10-02T00:00:00+00:00"),
        ]
        self.store.merge(events)
        first = self.store.path.read_bytes()
        total = self.store.merge(events)
        self.assertEqual(total, 2)
        self.assertEqual(self.store.path.read_bytes(), first)

    def test_merge_keeps_existing_rows_and_adds_new_ones(self):
        self.store.merge([ev("https://a/1", "2026-10-01T00:00:00+00:00")])
        self.store.merge([ev("https://b/2", "2026-10-02T00:00:00+00:00")])
        rows = self.store.load()
        self.assertEqual([r.url for r in rows], ["https://a/1", "https://b/2"])

    def test_no_grid_refusal_gaps_are_fine(self):
        # news feeds are gappy by design: rows 5 days apart coexist
        self.store.merge(
            [
                ev("https://a/1", "2026-10-01T00:00:00+00:00"),
                ev("https://a/2", "2026-10-06T00:00:00+00:00"),
            ]
        )
        self.assertEqual(len(self.store.load()), 2)


class RowShapeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "events.jsonl"
        self.store = NewsStore(self.path)
        self.store.merge(
            [ev("https://x/sol-etf", "2026-10-03T09:00:00+00:00", "Solana ETF approved")]
        )

    def test_rows_use_the_pinned_field_order(self):
        row = json.loads(self.path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(
            list(row.keys()),
            ["id", "title", "url", "source", "published_at", "currencies", "kind", "via"],
        )
        self.assertEqual(row["currencies"], ["SOL"])
        self.assertEqual(row["kind"], "news")
        self.assertEqual(row["via"], "rss")

    def test_roundtrip_preserves_the_event(self):
        original = self.store.load()[0]
        again = NewsEvent.from_row(json.loads(json.dumps(original.to_row())))
        self.assertEqual(again, original)


class ToleranceTest(unittest.TestCase):
    def test_malformed_lines_are_skipped_with_a_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            good = ev("https://a/1", "2026-10-01T00:00:00+00:00")
            lines = [
                json.dumps(good.to_row()),
                "not json at all",
                json.dumps({"id": "x"}),  # missing pinned fields
                "",
            ]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rows = NewsStore(path).load()
            self.assertEqual([r.url for r in rows], ["https://a/1"])
            self.assertIn("malformed line", out.getvalue())

    def test_missing_store_loads_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(NewsStore(Path(tmp) / "nope.jsonl").load(), [])


if __name__ == "__main__":
    unittest.main()
