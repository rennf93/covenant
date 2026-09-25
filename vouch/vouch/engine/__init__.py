"""vouch.engine: session orchestration, decision head, rails, paper broker.

Re-exports the core trading surface. Layering: engine may use venues and
attest (and calibration, a pure leaf); it must never import
vouch.server. See vouch/__init__.py for the full layering map.
"""

from __future__ import annotations

from vouch.engine.broker import Broker, Position, stop_level_for
from vouch.engine.rules import Rules

__all__ = [
    "Broker",
    "Position",
    "Rules",
    "stop_level_for",
]
