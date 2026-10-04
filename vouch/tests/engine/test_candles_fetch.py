"""fetch_candles completeness tests: the downloader must never hand back a
gapped series silently - a hole in a recording session poisons labels,
calibration, and SFT downstream (stage 2, 2026-09-26). Stdlib unittest."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from vouch.engine.candles import fetch_candles


class FakeResponse:
    def __init__(self, rows: list[list[float]], status_code: int = 200):
        self._rows = rows
        self.status_code = status_code
        self.headers: dict[str, str] = {}

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
        bars = fetch_candles(client, minutes=6, end_ts=end, require_complete=True)
        self.assertEqual(len(bars), 6)

    def test_short_tail_is_filled_when_required(self):
        # venue returns 4 of 6 minutes (trailing dead minutes): the tail is
        # reconstructed flat at the last close, series stays complete
        end = 1_800_000_000 - 1_800_000_000 % 60
        client = FakeClient([chunk_rows(end - 6 * 60, 4)])
        bars = fetch_candles(client, minutes=6, end_ts=end, require_complete=True)
        self.assertEqual(len(bars), 6)
        self.assertEqual([b.volume for b in bars], [3.0, 3.0, 3.0, 3.0, 0.0, 0.0])

    def test_small_hole_is_filled_with_flat_bars(self):
        # 8 real minutes with a 2-minute dead stretch inside: filled as flat
        # zero-volume bars at the previous close, series stays complete
        end = 1_800_000_000 - 1_800_000_000 % 60
        start = end - 8 * 60
        real = chunk_rows(start, 8)
        # drop two minutes in the middle (newest-first list)
        with_hole = [r for r in real if r[0] not in (start + 3 * 60, start + 4 * 60)]
        client = FakeClient([with_hole])
        bars = fetch_candles(client, minutes=8, end_ts=end, require_complete=True)
        self.assertEqual(len(bars), 8)
        self.assertEqual([b.ts for b in bars], [start + k * 60 for k in range(8)])
        dead = [b for b in bars if b.ts in (start + 3 * 60, start + 4 * 60)]
        self.assertTrue(all(b.volume == 0.0 and b.close == 1.5 for b in dead))

    def test_hole_beyond_cap_refuses(self):
        # a 20-minute dead stretch inside a 30-minute request: refuse
        end = 1_800_000_000 - 1_800_000_000 % 60
        start = end - 30 * 60
        real = chunk_rows(start, 30)
        keep = [r for r in real if r[0] < start + 5 * 60 or r[0] >= start + 25 * 60]
        client = FakeClient([keep])
        with self.assertRaises(SystemExit):
            fetch_candles(client, minutes=30, end_ts=end, require_complete=True)

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


class RateLimitRetryTest(unittest.TestCase):
    """429 on a shared egress IP (Kaggle/CI) must back off and retry, not
    kill the recording: the 2026-10-04 full campaign died on its first
    unhandled 429."""

    def test_429_is_backed_off_and_retried(self):
        with patch("vouch.engine.candles.httpx.Client") as client_cls:
            client = client_cls.return_value
            ok = FakeResponse(chunk_rows(1770000000, 10))
            limited = FakeResponse([], status_code=429)
            client.get.side_effect = [limited, limited, ok]
            bars = fetch_candles(client, minutes=10, end_ts=1770000540, require_complete=True)
            self.assertEqual(len(bars), 10)
            self.assertGreaterEqual(client.get.call_count, 3)
