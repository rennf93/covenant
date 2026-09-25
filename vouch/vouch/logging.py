"""Library logging for vouch: loggers under the "vouch." namespace.

`get_logger(name)` returns (and, on first call, configures) loggers for
library code that used to print WARNING lines straight to stderr (the
ledger's torn-line notice, the broker's fill-observer failures). Runner
console output (the [HH:MM:SS] prints the dashboard tails) STAYS
print-based by design: those stdout lines are the product.

One handler, one guard: a single stderr StreamHandler on the "vouch" root
logger, level from VOUCH_LOG_LEVEL (default WARNING), propagate left on so
an application that wants to route vouch logs elsewhere still can. The
guard makes repeated get_logger calls idempotent (no duplicate handlers).

Layering: imports config only (leaf modules).
"""

from __future__ import annotations

import logging
import sys

from vouch.config import load_settings

_NAMESPACE = "vouch"
_configured = False


def _configure() -> None:
    global _configured
    if _configured:
        return
    _configured = True
    root = logging.getLogger(_NAMESPACE)
    if root.handlers:  # somebody (an app, a test) already configured us
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(handler)
    level = load_settings().log_level.upper()
    root.setLevel(
        logging.getLevelName(level) if level in logging.getLevelNamesMapping() else logging.WARNING
    )


def get_logger(name: str) -> logging.Logger:
    """Return a logger under the vouch namespace, configured on first use."""
    _configure()
    if name == _NAMESPACE or name.startswith(_NAMESPACE + "."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_NAMESPACE}.{name}")
