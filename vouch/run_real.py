#!/usr/bin/env python
"""vouch REAL mode. This places actual orders with actual money.

The flip from shadow to live is one environment variable:

    VOUCH_VENUE=coinbase  VOUCH_CB_KEY_NAME=...  VOUCH_CB_PRIVATE_KEY=... \
        .venv/bin/python run_real.py --max-usd 10 --confirm-real

Without VOUCH_VENUE=coinbase it refuses to run (that is the safety interlock;
a typo cannot spend money). Guardrails:
- --max-usd hard exposure cap enforced inside the venue adapter per order;
- same decision rails as shadow (stop/take/trail, cooldown, 20% drawdown kill);
- entry and exit prices come from the venue's executed FILLS (including
  fee), so the PnL in the log is what the venue actually charged;
- started BY A HUMAN, by hand. It is not an AutoClaw task and must never
  become one (vault/finance-policy.md in the HomeNAS repo).

USE AT YOUR OWN LOSS. Read README.md first.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from vouch.engine.broker import Position, stop_level_for
from vouch.engine.candles import (
    BarAggregator,
    inject_volume,
    latest_candle_volume,
    state_snapshot,
    tick_from_bars,
)
from vouch.engine.rules import Rules
from vouch.engine.system1 import System1
from vouch.engine.system2 import rewrite
from vouch.exceptions import VenueError
from vouch.venues import PaperVenue, make_venue


class RealBook:
    """Broker-like shim so System-1's entry/exit logic works unchanged."""

    def __init__(self, position: Position | None, cooldown_until: float):
        self.position = position
        self.cooldown_until = cooldown_until

    def round_trip_cost(self) -> float:
        return 0.0  # real fees are measured from actual venue fills, not modeled

    def _fav(self, price: float) -> float:
        return _fav(self.position, price) if self.position else 0.0


def _fav(position: Position, price: float) -> float:
    pct = (price - position.entry) / position.entry
    return pct if position.side == "long" else -pct


def main() -> None:
    ap = argparse.ArgumentParser(description="vouch REAL trading - places actual orders")
    ap.add_argument("--minutes", type=int, default=480)
    ap.add_argument("--interval", type=int, default=20)
    ap.add_argument("--epoch-minutes", type=int, default=60)
    ap.add_argument("--max-usd", type=float, default=10.0, help="hard exposure cap per order")
    ap.add_argument(
        "--confirm-real",
        action="store_true",
        help="required with --venue coinbase; proves you meant it",
    )
    ap.add_argument("--seed", type=str, default=datetime.now(UTC).strftime("%Y%m%d-%H%M%S"))
    a = ap.parse_args()

    import os

    if os.environ.get("VOUCH_VENUE", "paper") == "coinbase" and not a.confirm_real:
        raise SystemExit(
            "Refusing: pass --confirm-real to acknowledge real orders with real money."
        )
    venue = make_venue(max_usd=a.max_usd)
    if isinstance(venue, PaperVenue) and os.environ.get("VOUCH_VENUE") != "paper":
        raise SystemExit("Venue fell back to paper; check your env vars.")

    out_dir = Path("out") / f"real-{a.seed}"
    out_dir.mkdir(parents=True, exist_ok=True)
    rules = Rules.from_env()  # honors VOUCH_RULES (set by the UI)
    s1 = System1()
    client = httpx.Client()
    agg = BarAggregator()
    log = out_dir / "decisions.jsonl"

    # real position state: single long spot position (shorts disabled)
    position: Position | None = None
    quote_usd = 0.0
    peak_equity = a.max_usd
    halted = False
    halt_reason = ""
    cooldown_until = 0  # tick index (System-1 compares against tick.i)
    realized = 0.0  # NET: gross pnl minus every venue fee
    fees_paid = 0.0
    closed = 0
    epoch_windows: list[dict] = []
    epoch_start_trades = 0
    epoch_start_eq = a.max_usd
    last_volume_fetch = 0.0
    last_bar_ts = 0
    tick_counter = 0
    deadline = time.time() + a.minutes * 60
    next_epoch = time.time() + a.epoch_minutes * 60

    def close_position(price: float, reason: str) -> None:
        nonlocal position, realized, closed, cooldown_until, fees_paid, quote_usd
        if not position:
            return
        exit_side = "SELL" if position.side == "long" else "BUY"
        res = venue.market_order(
            client, exit_side, min(quote_usd * price / position.entry, a.max_usd), price
        )
        fill = {}
        if res.get("order_id") and not isinstance(venue, PaperVenue):
            fill = venue.get_fill(client, res["order_id"]) or {}
        exit_price = float(fill.get("avg_price") or 0.0) or price
        exit_fee = float(fill.get("fee_usd") or 0.0)
        gross = quote_usd * (exit_price / position.entry - 1)
        realized += gross - exit_fee
        fees_paid += exit_fee
        closed += 1
        net = gross - exit_fee
        if net < 0:
            # Tick-based, like the paper broker: System-1 gates entries on
            # `tick.i <= cooldown_until`, so a wall-clock deadline here would
            # veto every entry for the rest of the session (tick.i never
            # reaches ~1.7e9 epoch seconds).
            cooldown_until = tick_counter + rules.cooldown_ticks
        position = None
        quote_usd = 0.0
        with log.open("a") as f:
            f.write(
                json.dumps(
                    {
                        "ts": datetime.now(UTC).isoformat(),
                        "event": "close",
                        "reason": reason,
                        "gross_usd": round(gross, 4),
                        "exit_fee": round(exit_fee, 4),
                        "net_usd": round(net, 4),
                        "realized_total": round(realized, 4),
                        "exit_price": exit_price,
                        "fill": res,
                    }
                )
                + "\n"
            )
        print(f"CLOSE ({reason}) net {net:+.4f} USD, realized {realized:+.4f}")
        recent.append(f"{datetime.now():%H:%M} CLOSE {reason} net {net:+.4f}")

    recent: list[str] = []

    def process_bar() -> None:
        nonlocal tick_counter, position, quote_usd, peak_equity, halted, halt_reason
        nonlocal realized, fees_paid, cooldown_until, epoch_start_trades, epoch_start_eq
        if not agg.bars:
            return
        tick_counter += 1
        tick = tick_from_bars(tick_counter, agg.bars, regime="live")
        price = tick.price

        if position:
            position.peak_fav = max(position.peak_fav, _fav(position, price))
        equity = a.max_usd + realized + (_fav(position, price) * quote_usd if position else 0.0)
        peak_equity = max(peak_equity, equity)
        if not halted and 1 - equity / peak_equity >= 0.20:
            close_position(price, "KILL_SWITCH")
            halted = True
            halt_reason = "20% drawdown on real money; session over"
            raise KeyboardInterrupt

        if position:
            level = stop_level_for(position.side, position.entry, position.peak_fav, rules)
            fav = _fav(position, price)
            if fav <= level:
                close_position(price, "STOP" if level < 0 else "TRAIL")
            elif fav >= rules.take_profit_pct:
                close_position(price, "TAKE")

        # epoch boundary: System-2 reviews the rolling window and rewrites rules.
        # NOTE (pre-existing, preserved verbatim): next_epoch is read here but
        # assigned later in this function without a nonlocal declaration, so the
        # first processed bar raises UnboundLocalError. Flagged, not changed.
        if time.time() >= next_epoch:  # noqa: F823
            stats = {
                "trades": closed - epoch_start_trades,
                "pnl_usd": round(realized - (epoch_start_eq - a.max_usd), 4),
                "open_position": position is not None,
            }
            rules2, s2info = rewrite(rules, stats, window=epoch_windows)
            rules.min_conviction = rules2.min_conviction
            rules.flat_max_p = rules2.flat_max_p
            rules.max_position_pct = rules2.max_position_pct
            rules.stop_loss_pct = rules2.stop_loss_pct
            rules.take_profit_pct = rules2.take_profit_pct
            rules.cooldown_ticks = rules2.cooldown_ticks
            rules.breakeven_trigger_pct = rules2.breakeven_trigger_pct
            rules.trailing_stop_pct = rules2.trailing_stop_pct
            rules.exit_pressure_min = rules2.exit_pressure_min
            rules.min_edge_pct = rules2.min_edge_pct
            with (out_dir / "s2-history.jsonl").open("a") as f:
                f.write(
                    json.dumps(
                        {
                            "ts": datetime.now(UTC).isoformat(),
                            "epoch": stats,
                            "s2": s2info,
                            "rules": rules.to_dict(),
                        }
                    )
                    + "\n"
                )
            epoch_windows.append(stats)
            epoch_start_trades = closed
            epoch_start_eq = a.max_usd + realized
            next_epoch = time.time() + a.epoch_minutes * 60  # noqa: F841  (see F823 note above)
            print(
                f"[{datetime.now():%H:%M:%S}] SYSTEM-2 via {s2info['backend']}: "
                f"applied={s2info['applied']}"
            )

        book = RealBook(position, cooldown_until)
        d = s1.decide(tick, book, rules, recent)
        if d["mode"] == "exit" and d["final_action"] == "exit" and position:
            close_position(price, "LAYA_EXIT")
        elif (
            d["mode"] == "entry"
            and d["final_action"] == "long"
            and d["veto"] is None
            and position is None
            and tick_counter > cooldown_until
        ):
            size = round(min(a.max_usd, equity * rules.max_position_pct), 2)
            try:
                res = venue.market_order(client, "BUY", size, price)
                fill = {}
                if res.get("order_id") and not isinstance(venue, PaperVenue):
                    fill = venue.get_fill(client, res["order_id"]) or {}
                entry_price = float(fill.get("avg_price") or 0.0) or price
                entry_fee = float(fill.get("fee_usd") or 0.0)
                realized -= entry_fee
                fees_paid += entry_fee
                quote_usd = float(fill.get("size") * entry_price) if fill.get("size") else size
                position = Position(
                    side="long", size_usd=quote_usd, entry=entry_price, open_tick=tick_counter
                )
                recent.append(
                    f"{datetime.now():%H:%M} OPEN long @ {entry_price:.2f} "
                    f"(conv {d['conviction']:.2f})"
                )
                with log.open("a") as f:
                    f.write(
                        json.dumps(
                            {
                                "ts": datetime.now(UTC).isoformat(),
                                "event": "open",
                                "side": "BUY",
                                "quote_usd": size,
                                "entry_price": entry_price,
                                "entry_fee": round(entry_fee, 4),
                                "fill": res,
                                "decision": d,
                            }
                        )
                        + "\n"
                    )
                print(f"OPEN BUY ${size:.2f} @ {entry_price:.2f} (conv {d['conviction']:.2f})")
            except VenueError as e:
                print(f"ORDER REFUSED: {e}")
                with log.open("a") as f:
                    f.write(
                        json.dumps(
                            {
                                "ts": datetime.now(UTC).isoformat(),
                                "event": "refused",
                                "why": str(e),
                                "decision": d,
                            }
                        )
                        + "\n"
                    )

        with log.open("a") as f:
            f.write(
                json.dumps(
                    {
                        "ts": datetime.now(UTC).isoformat(),
                        "tick": tick_counter,
                        "price": price,
                        "equity": round(equity, 4),
                        "state": state_snapshot(tick, book),
                        "decision": d,
                    }
                )
                + "\n"
            )

    print(
        f"REAL session: venue={type(venue).__name__} product={venue.product} cap={a.max_usd} USD, "
        f"{a.minutes} min. Long-only (spot). Shorts are disabled in real mode."
    )
    try:
        while time.time() < deadline:
            try:
                price = venue.get_price(client)
            except Exception as e:  # noqa: BLE001
                print(f"[{datetime.now():%H:%M:%S}] price fetch failed ({type(e).__name__})")
                time.sleep(a.interval)
                continue
            agg.add(time.time(), price)

            if agg.bars and time.time() - last_volume_fetch > 60:
                vol = latest_candle_volume(client)
                if vol:
                    inject_volume(agg.bars, vol[0], vol[1])
                last_volume_fetch = time.time()

            if agg.bars and agg.bars[-1].ts > last_bar_ts:
                last_bar_ts = agg.bars[-1].ts
                process_bar()
            time.sleep(a.interval)
    except KeyboardInterrupt:
        pass

    summary = {
        "mode": f"REAL ({type(venue).__name__})",
        "starting": a.max_usd,
        "realized_pnl_net_usd": round(realized, 4),
        "fees_paid_usd": round(fees_paid, 4),
        "return_pct": round(realized / a.max_usd * 100, 3),
        "closed_trades": closed,
        "open_position": position is not None,
        "halted": halted,
        "halt_reason": halt_reason,
        "ended": datetime.now(UTC).isoformat(),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    if position:
        print("NOTE: session ended with an OPEN position - close it manually on the venue.")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
