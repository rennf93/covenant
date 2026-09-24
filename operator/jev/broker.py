"""Paper broker: simulated fills, fees, slippage, equity curve, kill switch.

For shadow mode this is the measuring stick, so the cost model defaults to
RETAIL reality (60 bps per side on Coinbase Advanced for a small account),
not the 4 bps a market maker pays. Run sim/backtests with --fee-bps to
match whatever account you actually have; honest numbers only.

Fill observer hook: if broker.fill_observer is set, it is called once per
booked fill, in the fill-handling path, before the next tick, with a dict
describing the fill (kind, side, price, size_usd, fee, tick, reason).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Defaults for a small retail spot account. Overrides: JEV_FEE_BPS env or
# constructor args. 60 bps per side = 1.2% round trip; at a 2%/4% bracket
# that needs a >53% win rate just to break even. Know your costs.
DEFAULT_FEE_BPS = 60.0
DEFAULT_SLIPPAGE = 0.0005


@dataclass
class Position:
    side: str  # "long" | "short"
    size_usd: float
    entry: float
    open_tick: int = 0
    peak_fav: float = 0.0  # peak favorable move (signed pct) seen while holding


@dataclass
class Broker:
    starting_cash: float = 1000.0
    max_drawdown_pct: float = 0.20  # RAIL: System-2 cannot change this
    fee: float = field(init=False)        # per-side taker fee, fraction
    slippage: float = field(init=False)
    cooldown_ticks: int = 10        # RAIL: applied after a losing close (was hardcoded before)
    cash: float = field(init=False)
    position: Position | None = field(init=False, default=None)
    peak_equity: float = field(init=False)
    halted: bool = field(init=False, default=False)
    halt_reason: str = field(init=False, default="")
    trades: list[dict] = field(init=False, default_factory=list)
    cooldown_until: int = -1  # tick index

    def __post_init__(self) -> None:
        import os

        self.fee = float(os.environ.get("JEV_FEE_BPS", DEFAULT_FEE_BPS)) / 10000.0
        self.slippage = DEFAULT_SLIPPAGE
        self.cash = self.starting_cash
        self.peak_equity = self.starting_cash
        self.fill_observer = None

    def round_trip_cost(self) -> float:
        """Total fraction of notional lost to fees + slippage on open+close."""
        return 2 * (self.fee + self.slippage)

    def equity(self, price: float) -> float:
        eq = self.cash
        if self.position:
            pct = (price - self.position.entry) / self.position.entry
            eq += self.position.size_usd * (pct if self.position.side == "long" else -pct)
        return eq

    def open(self, side: str, size_usd: float, price: float, tick_i: int, reason: str) -> None:
        if self.halted or self.position or size_usd <= 0:
            return
        fill = price * (1 + self.slippage if side == "long" else 1 - self.slippage)
        fee = size_usd * self.fee
        self.cash -= fee
        self.position = Position(side=side, size_usd=size_usd, entry=fill, open_tick=tick_i)
        self.trades.append({"tick": tick_i, "action": "open_" + side, "price": fill, "size_usd": size_usd, "reason": reason})
        if self.fill_observer:
            try:
                self.fill_observer({"kind": "open", "side": side, "price": fill,
                                    "size_usd": size_usd, "fee": fee, "tick": tick_i,
                                    "reason": reason})
            except Exception as e:
                print(f"WARNING: fill observer failed on open: {e}", file=sys.stderr)

    def close(self, price: float, tick_i: int, reason: str) -> None:
        if self.halted or not self.position:
            return
        pos = self.position
        fill = price * (1 - self.slippage if pos.side == "long" else 1 + self.slippage)
        pct = (fill - pos.entry) / pos.entry
        pnl = pos.size_usd * (pct if pos.side == "long" else -pct)
        fee = pos.size_usd * self.fee
        self.cash += pnl - fee
        self.trades.append({"tick": tick_i, "action": "close", "price": fill, "pnl_usd": round(pnl - fee, 4), "reason": reason})
        if self.fill_observer:
            try:
                self.fill_observer({"kind": "close", "side": pos.side, "price": fill,
                                    "size_usd": pos.size_usd, "fee": fee, "tick": tick_i,
                                    "reason": reason, "pnl_usd": pnl - fee})
            except Exception as e:
                print(f"WARNING: fill observer failed on close: {e}", file=sys.stderr)
        if pnl - fee < 0:
            self.cooldown_until = tick_i + self.cooldown_ticks  # RAIL: cooldown after a loss
        self.position = None

    def mark(self, price: float, tick_i: int) -> None:
        """Update peak equity, peak favorable move, and the kill switch."""
        eq = self.equity(price)
        self.peak_equity = max(self.peak_equity, eq)
        dd = 1 - eq / self.peak_equity
        if dd >= self.max_drawdown_pct and not self.halted:
            self.close(price, tick_i, reason="KILL_SWITCH")
            self.halted = True
            self.halt_reason = f"drawdown {dd:.1%} hit the {self.max_drawdown_pct:.0%} kill switch"

    def _fav(self, price: float) -> float:
        pos = self.position
        if not pos:
            return 0.0
        pct = (price - pos.entry) / pos.entry
        return pct if pos.side == "long" else -pct

    def update_trail(self, price: float) -> None:
        if self.position:
            self.position.peak_fav = max(self.position.peak_fav, self._fav(price))

    def stop_check(self, price: float, tick_i: int, rules) -> None:
        """Bracket exits plus the breakeven floor and trailing stop."""
        if not self.position:
            return
        self.update_trail(price)
        pos = self.position
        fav = self._fav(price)
        level = stop_level_for(pos.side, pos.entry, pos.peak_fav, rules)
        label = "STOP" if level < 0 else "TRAIL"
        if fav <= level:
            self.close(price, tick_i, reason=label)
        elif fav >= rules.take_profit_pct:
            self.close(price, tick_i, reason="TAKE")

    def save(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "trades.jsonl").write_text(
            "".join(json.dumps(t) + "\n" for t in self.trades) or ""
        )


def stop_level_for(side: str, entry: float, peak_fav: float, rules) -> float:
    """Favorable-move level at which the position must be closed.

    The stop LEVEL starts at -stop_loss_pct and can only tighten:
    - breakeven floor: once peak favorable move >= breakeven_trigger_pct,
      the stop rises to entry (fav 0);
    - trailing: stop rises to peak_fav - trailing_stop_pct when that is
      higher. Both 0 in rules = classic fixed bracket. Shared by the paper
      broker and the real runner so both book exits identically.
    """
    stop_level = -rules.stop_loss_pct
    if rules.breakeven_trigger_pct > 0 and peak_fav >= rules.breakeven_trigger_pct:
        stop_level = max(stop_level, 0.0)
    if rules.trailing_stop_pct > 0:
        stop_level = max(stop_level, peak_fav - rules.trailing_stop_pct)
    return stop_level
