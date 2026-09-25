"""Analysis harness: IC measurement, calibration fitting, SFT data, backtest.

Layering: analysis is a LEAF package except for backtest.py, which replays
the exact live path and sits above engine (see backtest.py's docstring).
calibration.py is a pure leaf that engine (System-1) also imports.

backtest is re-exported LAZILY via module __getattr__: an eager import here
would make `import vouch.engine.system1` -> (analysis package init) ->
backtest -> `from vouch.engine.system1 import System1` hit a partially
initialized module. Importing vouch.analysis.backtest directly always works.
"""

from __future__ import annotations

from typing import Any

from vouch.analysis.analysis import (
    FEATURES,
    add_forward_returns,
    load_decisions,
    rank_ic,
    report,
)
from vouch.analysis.sft_data import ENTRY_QUESTIONS, build_dataset
from vouch.calibration import Calibrator

__all__ = [
    "Calibrator",
    "ENTRY_QUESTIONS",
    "FEATURES",
    "add_forward_returns",
    "build_dataset",
    "load_decisions",
    "rank_ic",
    "report",
    "run_backtest",
]


def __getattr__(name: str) -> Any:  # lazy: backtest sits above engine, see docstring
    if name == "run_backtest":
        from vouch.analysis.backtest import run_backtest

        return run_backtest
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
