"""vouch: a self-rewriting, attested paper-to-live trading loop.

LAYOUT (import layering, enforced by convention and checked by grep):

    vouch.config / exceptions / logging / protocols   base seams
    vouch.venues    LEAF: execution venue adapters (paper, coinbase, arb-paper)
    vouch.attest    LEAF: covenant receipts, ledger, evidence, chain commits
    vouch.analysis  LEAF except backtest.py, which replays the live path
                    and sits ABOVE engine (one direction only)
    vouch.engine    may use venues + attest (+ analysis.calibration, a pure leaf)
    vouch.server    may use everything (the dashboard)
    run_*.py        entry points, project root

engine must not import server; venues and attest must not import engine or
server. Import order in THIS file is deliberate: base seams, then venues
(pulls attest), then engine, then the analysis backtest re-export, so no
module is ever partially initialized on any import path.
"""

from __future__ import annotations

from vouch.analysis.analysis import add_forward_returns, load_decisions, rank_ic, report
from vouch.analysis.backtest import run_backtest
from vouch.analysis.calibration import Calibrator
from vouch.analysis.sft_data import build_dataset
from vouch.attest import AttestationConfig, Attestor, EpochLedger, merkle, receipts
from vouch.config import Settings, load_settings
from vouch.engine import Broker, Rules, stop_level_for
from vouch.engine.session import run_session
from vouch.exceptions import (
    BackendError,
    CommitError,
    LedgerError,
    S2Error,
    VenueError,
    VouchError,
)
from vouch.logging import get_logger
from vouch.protocols import FillObserver, SourcingVenue, System1Backend, Venue
from vouch.venues import (
    VENUE_INFO,
    VENUES,
    ArbPaperVenue,
    CoinbaseVenue,
    PaperVenue,
    available_venues,
    make_venue,
)

__version__ = "0.1.0"

__all__ = [
    "ArbPaperVenue",
    "AttestationConfig",
    "Attestor",
    "BackendError",
    "Broker",
    "Calibrator",
    "CoinbaseVenue",
    "CommitError",
    "EpochLedger",
    "FillObserver",
    "LedgerError",
    "PaperVenue",
    "Rules",
    "S2Error",
    "Settings",
    "SourcingVenue",
    "System1Backend",
    "VENUE_INFO",
    "VENUES",
    "Venue",
    "VenueError",
    "VouchError",
    "add_forward_returns",
    "available_venues",
    "build_dataset",
    "get_logger",
    "load_decisions",
    "load_settings",
    "make_venue",
    "merkle",
    "rank_ic",
    "receipts",
    "report",
    "run_backtest",
    "run_session",
    "stop_level_for",
]
