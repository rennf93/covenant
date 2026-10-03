"""Context cache tests: endpoint parsers against real-data fixtures, at()
staleness, build determinism, gap refusals, funding forward-fill, warm-up
marking. Fixtures under tests/fixtures/context/ are trimmed REAL endpoint
data fetched live 2026-10-03 (Binance fapi funding, data.binance.vision
daily metrics CSVs, Coinbase SOL/BTC 1m candles; window 2026-09-28 00:00 to
2026-09-30 00:00 UTC). Stdlib unittest."""

from __future__ import annotations

import csv
import json
import math
import tempfile
import unittest
from pathlib import Path

from vouch.engine.candles import Bar
from vouch.engine.context_features import (
    EMA4H_SPAN,
    FOUR_HOURS,
    HOUR,
    RANGE_LOOKBACK,
    WARMUP_LOOKBACK_DAYS,
    ContextPoint,
    ContextSeries,
    FundingEntry,
    OiPoint,
    build_context_points,
    context_row,
    fetch_funding,
    funding_rate_24h_ago,
    funding_rate_at,
    parse_funding,
    parse_oi_metrics_csv,
    vol_regime_for,
    write_jsonl,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "context"
WIN_START = 1790553600  # 2026-09-28 00:00 UTC
WIN_END = 1790726400  # 2026-09-30 00:00 UTC
BASE = 1_800_000_000  # synthetic rig origin, aligned to the minute and 4h


def rig_prices(i: int) -> float:
    """Deterministic synthetic price path (drift + two sine components)."""
    return 100.0 + 0.01 * i + 5.0 * math.sin(i * 0.05) + 2.0 * math.sin(i * 0.011)


def synth_bars(start_ts: int, minutes: int) -> list[Bar]:
    bars = []
    for i in range(minutes):
        close = rig_prices(i)
        prev = rig_prices(i - 1) if i else close
        bars.append(
            Bar(
                ts=start_ts + i * 60,
                open=prev,
                high=max(prev, close) + 0.5,
                low=min(prev, close) - 0.5,
                close=close,
                volume=1.0,
            )
        )
    return bars


def synth_oi(start_ts: int, minutes: int) -> list[OiPoint]:
    """5m grid OI in USD notional, complete over the bar span."""
    rows = []
    for i in range(0, minutes, 5):
        rows.append(OiPoint(ts=start_ts + i * 60, oi_usd=1e9 + 1e7 * i))
    return rows


def synth_funding(start_ts: int, minutes: int) -> list[FundingEntry]:
    entries = []
    rate = 0.0001
    for i in range(0, minutes, 480):  # every 8h
        entries.append(FundingEntry(ts=start_ts + i * 60, rate=rate))
        rate += 0.00005
    return entries


def load_fixture_bars(name: str) -> list[Bar]:
    bars = []
    with (FIXTURES / name).open() as f:
        for row in csv.DictReader(f):
            bars.append(
                Bar(
                    ts=int(row["ts"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
    return bars


def load_fixture_funding():
    return parse_funding(json.loads((FIXTURES / "funding-solusdt.json").read_text()))


def load_fixture_oi() -> list[OiPoint]:
    rows: list[OiPoint] = []
    for day in ("2026-09-28", "2026-09-29"):
        rows.extend(parse_oi_metrics_csv((FIXTURES / f"oi-solusdt-{day}.csv").read_text()))
    return rows


def build_full_rig(history_minutes: int, window_minutes: int):
    start = BASE - history_minutes * 60
    return (
        synth_bars(start, history_minutes + window_minutes),
        synth_bars(start, history_minutes + window_minutes),
        synth_funding(start, history_minutes + window_minutes),
        synth_oi(start, history_minutes + window_minutes),
        BASE,
        BASE + window_minutes * 60,
    )


class FundingParserTest(unittest.TestCase):
    def test_real_fixture_shape(self):
        entries = load_fixture_funding()
        self.assertEqual(len(entries), 21)
        # fundingTime ms floored to seconds; first entry 2026-09-26 00:00 UTC
        self.assertEqual(entries[0].ts, 1790380800)
        stamps = [e.ts for e in entries]
        self.assertEqual(stamps, sorted(stamps))
        self.assertEqual(len(set(stamps)), len(stamps))
        self.assertTrue(all(abs(e.rate) < 0.01 for e in entries))

    def test_ms_jitter_floors_to_the_boundary(self):
        # live entries run up to +4ms past the 8h boundary (verified 2026-10-03)
        entries = parse_funding([{"fundingTime": 1790380800004, "fundingRate": "0.00010000"}])
        self.assertEqual(entries[0].ts, 1790380800)
        self.assertEqual(entries[0].rate, 0.0001)


class FundingForwardFillTest(unittest.TestCase):
    def setUp(self):
        t0 = 1_000_000
        self.entries = [
            FundingEntry(t0, 0.0001),
            FundingEntry(t0 + 8 * HOUR, 0.0002),
            FundingEntry(t0 + 16 * HOUR, 0.0003),
        ]

    def test_ffill_across_the_8h_boundary(self):
        t0 = self.entries[0].ts
        # the minute BEFORE the boundary still carries the old rate
        self.assertEqual(funding_rate_at(self.entries, t0 + 8 * HOUR - 60), 0.0001)
        # at the boundary itself the new rate applies (fundingTime <= ts)
        self.assertEqual(funding_rate_at(self.entries, t0 + 8 * HOUR), 0.0002)
        self.assertEqual(funding_rate_at(self.entries, t0 + 8 * HOUR + 3600), 0.0002)

    def test_before_first_entry_is_none(self):
        self.assertIsNone(funding_rate_at(self.entries, self.entries[0].ts - 60))

    def test_24h_ago_picks_never_interpolated_nearest(self):
        t0 = self.entries[0].ts
        # 24h before t0+16h is t0 exactly: the t0 entry itself
        self.assertEqual(funding_rate_24h_ago(self.entries, t0 + 16 * HOUR), 0.0001)

    def test_24h_ago_tie_earlier_entry_wins(self):
        t0 = self.entries[0].ts
        # target t0+12h (query ts t0+36h) sits exactly 4h from the t0+8h and
        # t0+16h entries: the tie resolves to the earlier entry
        self.assertEqual(funding_rate_24h_ago(self.entries, t0 + 36 * HOUR), 0.0002)

    def test_24h_ago_falls_back_to_closest_available(self):
        t0 = self.entries[0].ts
        # target t0-8h predates the data: closest is the first entry
        self.assertEqual(funding_rate_24h_ago(self.entries, t0 + 8 * HOUR), 0.0001)


class OiMetricsParserTest(unittest.TestCase):
    def test_real_daily_file_full_grid_sorted(self):
        rows = parse_oi_metrics_csv((FIXTURES / "oi-solusdt-2026-09-28.csv").read_text())
        self.assertEqual(len(rows), 288)
        self.assertEqual(rows[0].ts, WIN_START)
        # real files arrive in arbitrary order; parser must sort
        for prev, cur in zip(rows, rows[1:], strict=False):
            self.assertEqual(cur.ts - prev.ts, 300)
        self.assertTrue(all(r.oi_usd > 5e8 for r in rows))

    def test_two_days_stitch_without_gap(self):
        rows = load_fixture_oi()
        self.assertEqual(len(rows), 576)
        self.assertEqual(rows[0].ts, WIN_START)
        self.assertEqual(rows[-1].ts, WIN_END - 300)
        for prev, cur in zip(rows, rows[1:], strict=False):
            self.assertEqual(cur.ts - prev.ts, 300)


class OiGapRefusalTest(unittest.TestCase):
    def build_with_oi(self, oi: list[OiPoint]):
        bars = synth_bars(BASE, 240)
        funding = synth_funding(BASE, 240)
        return build_context_points(bars, synth_bars(BASE, 240), funding, oi, BASE, BASE + 120 * 60)

    def test_hole_over_three_missing_rows_refuses(self):
        # drop 6 consecutive rows after BASE+3600: a 2100s step, 6 missing rows
        oi = [p for p in synth_oi(BASE, 240) if not (BASE + 3600 < p.ts <= BASE + 3600 + 6 * 300)]
        with self.assertRaises(SystemExit):
            self.build_with_oi(oi)

    def test_three_missing_rows_tolerated(self):
        # exactly 3 missing rows (a 1200s step) carries last and builds
        oi = [p for p in synth_oi(BASE, 240) if not (BASE + 3600 < p.ts <= BASE + 3600 + 3 * 300)]
        built = self.build_with_oi(oi)
        self.assertEqual(len(built), 120)


class ContextSeriesTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)

    def point(self, ts: int) -> ContextPoint:
        return ContextPoint(
            ts=ts,
            funding_rate_8h=0.0001,
            funding_24h_ago=0.0001,
            oi_usd_m=1000.0,
            oi_chg_1h_pct=0.1,
            oi_chg_24h_pct=1.0,
            btc_ret_1m=0.001,
            btc_ret_60m=0.01,
            btc_ret_24h=0.02,
            ema4h_dist_pct=1.5,
            range_pos_30d=0.5,
            vol_regime="normal",
        )

    def write(self, points: list[ContextPoint], warmup: bool = False) -> Path:
        path = Path(self._tmpdir.name) / "cache.jsonl"
        write_jsonl([(p, warmup) for p in points], path)
        return path

    def write_raw(self, lines: list[str]) -> Path:
        path = Path(self._tmpdir.name) / "raw.jsonl"
        path.write_text("\n".join(lines) + "\n")
        return path

    def test_at_returns_row_for_the_queried_minute(self):
        base = BASE
        path = self.write([self.point(base + i * 60) for i in range(5)])
        series = ContextSeries.load_jsonl(path)
        self.assertEqual(series.at(base).ts, base)
        # mid-minute queries map down to the row's minute
        self.assertEqual(series.at(base + 30).ts, base)
        self.assertEqual(series.at(base + 4 * 60 + 59).ts, base + 4 * 60)

    def test_at_returns_none_when_stale(self):
        base = BASE
        path = self.write([self.point(base), self.point(base + 60)])
        series = ContextSeries.load_jsonl(path)
        self.assertIsNone(series.at(base + 120))  # minute not in the cache
        self.assertIsNone(series.at(base - 60))

    def test_hole_over_15_minutes_refuses(self):
        base = BASE
        rows = [self.point(base + i * 60) for i in range(3)]
        rows += [self.point(base + i * 60) for i in range(19, 21)]  # 16 missing minutes
        path = self.write(rows)
        with self.assertRaises(SystemExit):
            ContextSeries.load_jsonl(path)

    def test_hole_of_exactly_15_minutes_loads(self):
        base = BASE
        rows = [self.point(base + i * 60) for i in range(3)]
        rows += [self.point(base + 18 * 60)]  # rows at +0,+1,+2 then +18: 15 missing
        path = self.write(rows)
        series = ContextSeries.load_jsonl(path)
        self.assertIsNone(series.at(base + 10 * 60))
        self.assertEqual(series.at(base + 18 * 60).ts, base + 18 * 60)

    def test_unsorted_or_duplicate_rows_refuse(self):
        base = BASE
        path = self.write([self.point(base + 60), self.point(base)])
        with self.assertRaises(SystemExit):
            ContextSeries.load_jsonl(path)
        path = self.write([self.point(base), self.point(base)])
        with self.assertRaises(SystemExit):
            ContextSeries.load_jsonl(path)

    def test_malformed_line_refuses(self):
        path = self.write_raw([json.dumps(context_row(self.point(BASE)))[:40]])
        with self.assertRaises(SystemExit):
            ContextSeries.load_jsonl(path)

    def test_warmup_field_round_trips_as_false(self):
        base = BASE
        path = self.write([self.point(base)])
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertNotIn("warmup", rows[0])
        path = self.write([self.point(base)], warmup=True)
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(rows[0]["warmup"], True)


class BuildDeterminismTest(unittest.TestCase):
    def test_rebuild_is_byte_identical(self):
        rig = build_full_rig(6 * 1440, 120)  # 6d history (ema warmup + margin), 2h window
        built_one = build_context_points(*rig)
        built_two = build_context_points(*rig)
        self.assertEqual(built_one, built_two)
        with tempfile.TemporaryDirectory() as tmp:
            one, two = Path(tmp) / "a.jsonl", Path(tmp) / "b.jsonl"
            write_jsonl(built_one, one)
            write_jsonl(built_two, two)
            self.assertEqual(one.read_bytes(), two.read_bytes())
            # rows sorted by ts, one per minute, pinned key order (a trailing
            # "warmup" mark is appended only on warm-up rows)
            rows = [json.loads(line) for line in one.read_text().splitlines()]
            self.assertEqual([r["ts"] for r in rows], [rig[4] + i * 60 for i in range(120)])
            self.assertEqual(
                [k for k in rows[0] if k != "warmup"],
                [
                    "ts",
                    "funding_rate_8h",
                    "funding_24h_ago",
                    "oi_usd_m",
                    "oi_chg_1h_pct",
                    "oi_chg_24h_pct",
                    "btc_ret_1m",
                    "btc_ret_60m",
                    "btc_ret_24h",
                    "ema4h_dist_pct",
                    "range_pos_30d",
                    "vol_regime",
                ],
            )


class DerivedMathTest(unittest.TestCase):
    """Cross-checks of the derived fields against slow naive references
    written straight from the pre-registered definitions."""

    def setUp(self):
        self.history, self.window = 6 * 1440, 240
        self.sol = synth_bars(BASE - self.history * 60, self.history + self.window)
        built = build_context_points(
            self.sol,
            synth_bars(BASE - self.history * 60, self.history + self.window),
            synth_funding(BASE - self.history * 60, self.history + self.window),
            synth_oi(BASE - self.history * 60, self.history + self.window),
            BASE,
            BASE + self.window * 60,
        )
        self.built = built
        self.first_close = self.sol[0].close

    def naive_ema4h(self, ts: int) -> float:
        closes = []
        last_key = ts // FOUR_HOURS - 1  # last completed 4h bucket
        first_key = (BASE - self.history * 60) // FOUR_HOURS
        for key in range(first_key, last_key + 1):
            closes.append(
                rig_prices((key * FOUR_HOURS + FOUR_HOURS - 60 - (BASE - self.history * 60)) // 60)
            )
        alpha = 2.0 / (EMA4H_SPAN + 1)
        ema = sum(closes[:EMA4H_SPAN]) / EMA4H_SPAN
        for close in closes[EMA4H_SPAN:]:
            ema = alpha * close + (1 - alpha) * ema
        price = rig_prices((ts - (BASE - self.history * 60)) // 60)
        return (price / ema - 1) * 100

    def naive_range_pos(self, ts: int) -> float:
        window = [b for b in self.sol if ts - RANGE_LOOKBACK <= b.ts < ts]
        high = max(b.high for b in window)
        low = min(b.low for b in window)
        price = rig_prices((ts - (BASE - self.history * 60)) // 60)
        return min(max((price - low) / max(high - low, 1e-9), 0.0), 1.0)

    def naive_vol_std(self, ts: int) -> float:
        closes = []
        cur_hour = ts // HOUR
        start_hour = (BASE - self.history * 60) // HOUR
        for hour in range(start_hour, cur_hour):
            close_ts = hour * HOUR + HOUR - 60
            closes.append(rig_prices((close_ts - (BASE - self.history * 60)) // 60))
        rets = [(c / p - 1) * 100 for p, c in zip(closes, closes[1:], strict=False)][-24:]
        mean = sum(rets) / len(rets)
        return math.sqrt(sum((r - mean) ** 2 for r in rets) / len(rets))

    def test_sampled_rows_match_naive_references(self):
        for point, _ in self.built[::37]:
            with self.subTest(ts=point.ts):
                self.assertAlmostEqual(point.ema4h_dist_pct, self.naive_ema4h(point.ts), places=8)
                self.assertAlmostEqual(
                    point.range_pos_30d, self.naive_range_pos(point.ts), places=8
                )
                self.assertEqual(point.vol_regime, vol_regime_for(self.naive_vol_std(point.ts)))

    def test_oi_and_btc_fields_off_the_grid(self):
        point, _ = self.built[-1]
        ts = point.ts
        # synthetic OI is 1e9 + 1e7 per minute on the 5m grid: recompute the
        # forward-filled now/past rows the way the spec defines the change
        base_m = (ts - (BASE - self.history * 60)) // 60
        now_m = base_m - base_m % 5
        past_m = (base_m - 60) - (base_m - 60) % 5
        expected = ((1e9 + 1e7 * now_m) / (1e9 + 1e7 * past_m) - 1) * 100
        self.assertAlmostEqual(point.oi_chg_1h_pct, expected, places=8)
        # btc rig == sol rig: returns are the synthetic path's returns
        price = rig_prices(base_m)
        past = rig_prices(base_m - 1)
        self.assertAlmostEqual(point.btc_ret_1m, price / past - 1, places=10)


class WarmupMarkingTest(unittest.TestCase):
    def test_truncated_lookback_marks_all_rows_warmup(self):
        history, window = 2 * 1440, 240  # 2d history: every pinned 30d lookback is short
        built = build_context_points(*build_full_rig(history, window))
        self.assertTrue(all(warm for _, warm in built))
        for point, _ in built:
            self.assertTrue(math.isfinite(point.ema4h_dist_pct))
            self.assertTrue(0.0 <= point.range_pos_30d <= 1.0)
            self.assertIn(point.vol_regime, ("quiet", "normal", "elevated", "extreme"))

    def test_warmup_values_come_from_whatever_history_exists(self):
        history, window = 2 * 1440, 240
        built = build_context_points(*build_full_rig(history, window))
        # ema has only 12 completed 4h bars: the SMA of all of them seeds it
        point, _ = built[-1]
        ts = point.ts
        closes = []
        first_key = (BASE - history * 60) // FOUR_HOURS
        for key in range(first_key, ts // FOUR_HOURS - 1 + 1):  # completed buckets only
            closes.append(
                rig_prices((key * FOUR_HOURS + FOUR_HOURS - 60 - (BASE - history * 60)) // 60)
            )
        self.assertLess(len(closes), EMA4H_SPAN)
        seed = sum(closes) / len(closes)
        price = rig_prices((ts - (BASE - history * 60)) // 60)
        self.assertAlmostEqual(point.ema4h_dist_pct, (price / seed - 1) * 100, places=8)

    def test_full_lookback_clears_warmup(self):
        # 31d history + 1d window: range_pos_30d, ema4h, vol all fully covered
        history, window = (WARMUP_LOOKBACK_DAYS + 1) * 1440, 1440
        built = build_context_points(*build_full_rig(history, window))
        self.assertEqual(len(built), window)
        self.assertTrue(all(not warm for _, warm in built))


class RealFixtureIntegrationTest(unittest.TestCase):
    """Full build over the real 2-day fixture window (SOL + BTC candles,
    funding, OI), then the cache round trip."""

    def setUp(self):
        self.sol = load_fixture_bars("candles-sol-1m.csv")
        self.btc = load_fixture_bars("candles-btc-1m.csv")
        self.funding = load_fixture_funding()
        self.oi = load_fixture_oi()

    def build(self):
        return build_context_points(self.sol, self.btc, self.funding, self.oi, WIN_START, WIN_END)

    def test_window_rows_and_round_trip(self):
        built = self.build()
        self.assertEqual(len(built), 2880)
        with tempfile.TemporaryDirectory() as tmp:
            one, two = Path(tmp) / "one.jsonl", Path(tmp) / "two.jsonl"
            write_jsonl(built, one)
            write_jsonl(self.build(), two)
            # determinism on real data: byte-identical rebuild
            self.assertEqual(one.read_bytes(), two.read_bytes())
            self.assertEqual(len(one.read_text().splitlines()), 2880)
            series = ContextSeries.load_jsonl(one)
            point = series.at(WIN_START + 3600 + 30)
            self.assertIsNotNone(point)
            self.assertEqual(point.ts, WIN_START + 3600)
            self.assertIsNone(series.at(WIN_END))  # past the cached window
            self.assertIsNone(series.at(WIN_START - 60))

    def test_truncated_30d_history_marks_warmup(self):
        # fixtures carry only 2 days: every row's range_pos_30d lookback is short
        self.assertTrue(all(warm for _, warm in self.build()))

    def test_values_spot_check_against_raw_fixtures(self):
        built = dict((p.ts, p) for p, _ in self.build())
        ts = WIN_START + 13 * HOUR + 7 * 60
        point = built[ts]
        # funding ffill: latest entry with ts <= query
        entries = [e for e in self.funding if e.ts <= ts]
        self.assertEqual(point.funding_rate_8h, entries[-1].rate)
        # oi ffill: latest 5m row at or before the minute; USD millions
        oi_rows = [o for o in self.oi if o.ts <= ts]
        self.assertAlmostEqual(point.oi_usd_m, oi_rows[-1].oi_usd / 1e6, places=8)
        # 1h change off the row 12 5m periods back (forward-filled)
        past = [o for o in self.oi if o.ts <= ts - HOUR][-1]
        self.assertAlmostEqual(
            point.oi_chg_1h_pct, (oi_rows[-1].oi_usd / past.oi_usd - 1) * 100, places=8
        )
        # btc 1m return off the fixture closes
        closes = {b.ts: b.close for b in self.btc}
        self.assertAlmostEqual(point.btc_ret_1m, closes[ts] / closes[ts - 60] - 1, places=10)
        self.assertIn(point.vol_regime, ("quiet", "normal", "elevated", "extreme"))


class FetchFundingPaginationTest(unittest.TestCase):
    def test_pages_are_followed_until_short_page(self):
        class FakeResponse:
            def __init__(self, rows):
                self._rows = rows

            def raise_for_status(self):
                return None

            def json(self):
                return self._rows

        class FakeClient:
            def __init__(self):
                self.calls = []

            def get(self, url, params=None, timeout=10):
                self.calls.append(params)
                start = params["startTime"] // 1000
                if len(self.calls) == 1:
                    rows = [  # full page: 1000 entries 8h apart
                        {"fundingTime": (start + i * 480) * 1000, "fundingRate": "0.0001"}
                        for i in range(1000)
                    ]
                else:
                    rows = [{"fundingTime": (start + 480) * 1000, "fundingRate": "0.0002"}]
                return FakeResponse(rows)

        client = FakeClient()
        entries = fetch_funding(client, symbol="SOLUSDT", start_ts=1_000_000, end_ts=1_500_000)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[1]["startTime"], (1_000_000 + 999 * 480) * 1000 + 1)
        self.assertEqual(entries[0].ts, 1_000_000)
        self.assertEqual(entries[-1].ts, 1_000_000 + 1000 * 480)
        self.assertTrue(all(e.ts <= 1_500_000 for e in entries))


if __name__ == "__main__":
    unittest.main()
