"""CLI tests for run_judge_render.py: --index text output, --all JSONL of
{"ts","doc"} rows, and the loud refusals (no mode selected, out-of-bounds
index, context cache missing the probe minute). Stdlib unittest only; run
from the vouch/ directory.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from run_judge_render import main
from vouch.analysis.judge_doc import load_news, probe_minute_ts, render
from vouch.engine.context_features import ContextSeries


def probe_fixture(ts: str) -> dict:
    return {
        "ts": ts,
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
        "flag_reasons": ["volume"],
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


def event(published: str, title: str, source: str = "CoinDesk") -> dict:
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


def cache_row(ts: int) -> dict:
    """One context cache row in the pinned chunk A format."""
    return {
        "ts": ts,
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


class JudgeRenderCliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        ts = probe_minute_ts(probe_fixture("2026-09-05T22:30:00+00:00"))
        self.probes = [
            probe_fixture("2026-09-05T22:30:00+00:00"),
            probe_fixture("2026-09-05T22:31:00+00:00"),
        ]
        self.probes_path = self.dir / "probes.jsonl"
        self.probes_path.write_text("".join(json.dumps(p) + "\n" for p in self.probes))
        self.cache = self.dir / "ctx.jsonl"
        self.cache.write_text(
            "".join(json.dumps(cache_row(ts + minute * 60)) + "\n" for minute in (-2, -1, 0, 1, 2))
        )
        self.news = self.dir / "news.jsonl"
        self.news.write_text(
            json.dumps(event("2026-09-05T19:00:00+00:00", "Solana ETF inflows hit a record week"))
            + "\n"
        )
        self.series = ContextSeries.load_jsonl(self.cache)

    def run_cli(self, *args) -> None:
        argv = [
            "run_judge_render.py",
            "--probes",
            str(self.probes_path),
            "--context-file",
            str(self.cache),
            "--news-file",
            str(self.news),
            *args,
        ]
        with mock.patch("sys.argv", argv):
            main()

    def test_index_writes_the_document_text(self):
        out = self.dir / "doc.txt"
        self.run_cli("--index", "1", "--out", str(out))
        self.assertEqual(out.read_text(), render(self.probes[1], self.series, load_news(self.news)))

    def test_all_writes_jsonl_of_ts_and_doc(self):
        out = self.dir / "docs.jsonl"
        self.run_cli("--all", "--out", str(out))
        rows = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
        self.assertEqual([r["ts"] for r in rows], [p["ts"] for p in self.probes])
        for row, probe in zip(rows, self.probes, strict=True):
            self.assertEqual(set(row), {"ts", "doc"})
            self.assertEqual(row["doc"], render(probe, self.series, load_news(self.news)))

    def test_mode_is_required(self):
        with self.assertRaises(SystemExit) as ctx:
            self.run_cli("--out", str(self.dir / "x.txt"))
        self.assertEqual(ctx.exception.code, 2)

    def test_index_out_of_bounds_refuses(self):
        with self.assertRaises(SystemExit):
            self.run_cli("--index", "5", "--out", str(self.dir / "x.txt"))

    def test_context_cache_missing_the_probe_minute_refuses(self):
        short = self.dir / "short.jsonl"
        ts = probe_minute_ts(self.probes[0])
        short.write_text(
            "".join(json.dumps(cache_row(ts + minute * 60)) + "\n" for minute in (-120, -119, -118))
        )
        with self.assertRaises(SystemExit):
            self.run_cli(
                "--index", "0", "--context-file", str(short), "--out", str(self.dir / "x.txt")
            )


if __name__ == "__main__":
    unittest.main()
