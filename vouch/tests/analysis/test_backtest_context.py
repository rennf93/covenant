"""Backtest context wiring tests: --context-file must refuse a cache that
does not span the replay window (a partial cache would silently record a
mixed v1/v2 replay), and stay fully out of the way when absent. Chunk A's
ContextSeries is faked at the import seam; only the pinned load_jsonl/at()
surface is exercised. Stdlib unittest only."""

from __future__ import annotations

import sys
import types
import unittest
from unittest import mock

from vouch.analysis.backtest import _load_context_series


class _CoveredSeries:
    loaded_from: str | None = None

    @classmethod
    def load_jsonl(cls, path):
        obj = cls()
        obj.loaded_from = path
        return obj

    def at(self, ts):
        return {"ts": ts}


class _HoleySeries(_CoveredSeries):
    def at(self, ts):
        return None  # stale everywhere: coverage never spans the window


def _fake_chunk_a(series_cls):
    mod = types.ModuleType("vouch.engine.context_features")
    mod.ContextSeries = series_cls  # type: ignore[attr-defined]
    return mod


class ContextCoverageTest(unittest.TestCase):
    def _patch_chunk_a(self, series_cls):
        fake = _fake_chunk_a(series_cls)
        return mock.patch.dict(sys.modules, {"vouch.engine.context_features": fake})

    def test_no_file_is_none_without_touching_chunk_a(self):
        self.assertIsNone(_load_context_series(None, 1000, 2000))

    def test_covering_file_is_loaded_and_returned(self):
        with self._patch_chunk_a(_CoveredSeries):
            series = _load_context_series("out/context-sol.jsonl", 1000, 2000)
        self.assertIsInstance(series, _CoveredSeries)
        self.assertEqual(series.loaded_from, "out/context-sol.jsonl")

    def test_refuses_when_the_window_edges_have_no_row(self):
        with (
            self._patch_chunk_a(_HoleySeries),
            self.assertRaises(SystemExit) as ctx,
        ):
            _load_context_series("out/context-sol.jsonl", 1000, 2000)
        self.assertIn("does not cover the replay window", str(ctx.exception))

    def test_refuses_when_chunk_a_is_not_built(self):
        real = sys.modules.pop("vouch.engine.context_features", None)
        try:
            with (
                mock.patch.dict(sys.modules, {"vouch.engine.context_features": None}),
                self.assertRaises(SystemExit) as ctx,
            ):
                _load_context_series("out/context-sol.jsonl", 1000, 2000)
            self.assertIn("chunk A", str(ctx.exception))
        finally:
            if real is not None:
                sys.modules["vouch.engine.context_features"] = real


if __name__ == "__main__":
    unittest.main()
