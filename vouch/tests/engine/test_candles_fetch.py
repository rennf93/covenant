"""fetch_candles completeness tests: the downloader must never hand back a
gapped series silently - a hole in a recording session poisons labels,
calibration, and SFT downstream (stage 2, 2026-09-26). Stdlib unittest."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from vouch.engine.candles import fetch_candles


class FakeResponse:
    def __init__(self, rows: list[list[float]]):
        self._rows = rows

    def raise_for_status(self) -> None:
        return None

    def json(self) -> list[list[float]]:
        return self._rows


class FakeClient:
    """Returns queued responses in order; the last one repeats forever."""

    def __init__(self, queues: list[list[list[float]]]):
        self.queues = list(queues)
        self.calls = 0

    def get(self, url: str, params: dict | None = None, timeout: int = 10) -> FakeResponse:
        idx = min(self.calls, len(self.queues) - 1)
        self.calls += 1
        return FakeResponse(self.queues[idx])


def chunk_rows(start_ts: int, count: int) -> list[list[float]]:
    """Coinbase row shape [time, low, high, open, close, volume], newest first."""
    tss = [start_ts + i * 60 for i in range(count)]
    return [[float(ts), 1.0, 2.0, 1.0, 1.5, 3.0] for ts in reversed(tss)]


class FetchCandlesCompletenessTest(unittest.TestCase):
    def test_complete_series_passes(self):
        # 6 minutes requested, 6 rows returned: fine with require_complete
        end = 1_800_000_000 - 1_800_000_000 % 60
        client = FakeClient([chunk_rows(end - 6 * 60, 6)])
        bars = fetch_candles(
            client, minutes=6, end_ts=end, require_complete=True
        )
        self.assertEqual(len(bars), 6)

    def test_missing_minutes_raise_when_required(self):
        # venue returns only 4 of 6 minutes: refuse the series
        end = 1_800_000_000 - 1_800_000_000 % 60
        client = FakeClient([chunk_rows(end - 6 * 60, 4)])
        with self.assertRaises(SystemExit):
            fetch_candles(client, minutes=6, end_ts=end, require_complete=True)

    def test_missing_minutes_tolerated_when_not_required(self):
        end = 1_800_000_000 - 1_800_000_000 % 60
        client = FakeClient([chunk_rows(end - 6 * 60, 4)])
        bars = fetch_candles(client, minutes=6, end_ts=end, require_complete=False)
        self.assertEqual(len(bars), 4)

    def test_short_chunk_is_retried_then_accepted(self):
        # first response short (throttle), retry returns the full chunk
        end = 1_800_000_000 - 1_800_000_000 % 60
        rows = chunk_rows(end - 6 * 60, 6)
        client = FakeClient([rows[:3], rows])
        with patch("vouch.engine.candles.time.sleep"):
            bars = fetch_candles(client, minutes=6, end_ts=end, require_complete=True)
        self.assertEqual(len(bars), 6)
        self.assertEqual(client.calls, 2)

    def test_dedupes_overlapping_rows(self):
        end = 1_800_000_000 - 1_800_000_000 % 60
        rows = chunk_rows(end - 6 * 60, 6)
        client = FakeClient([rows + rows])
        bars = fetch_candles(client, minutes=6, end_ts=end, require_complete=True)
        self.assertEqual(len(bars), 6)


if __name__ == "__main__":
    unittest.main()
