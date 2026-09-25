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

Attestation is bookkeeping, not trading: with COVENANT_ATTEST=1 every actual
venue fill becomes a canonical receipt (side from the position direction,
the real order id as the venueOrderIdHash source, executed price and fee),
exactly like shadow's paper fills. It never places, sizes, or loosens an
order.

USE AT YOUR OWN LOSS. Read README.md first.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

from vouch.attest import AttestationConfig, Attestor, receipts
from vouch.config import load_settings
from vouch.engine.broker import Position, stop_level_for
from vouch.engine.candles import (
    BarAggregator,
    inject_volume,
    latest_candle_volume,
    state_snapshot,
    tick_from_bars,
)
from vouch.engine.rules import Rules
from vouch.engine.session import load_state, save_state
from vouch.engine.system1 import System1
from vouch.engine.system2 import rewrite
from vouch.exceptions import CommitError, VenueError
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
    ap.add_argument(
        "--resume",
        action="store_true",
        help="restore session state from out/real-<seed>/state.json (open position, "
        "epoch index, pending epoch commit) instead of starting fresh",
    )
    a = ap.parse_args()

    if os.environ.get("VOUCH_VENUE", "paper") == "coinbase" and not a.confirm_real:
        raise SystemExit(
            "Refusing: pass --confirm-real to acknowledge real orders with real money."
        )
    venue = make_venue(max_usd=a.max_usd)
    if isinstance(venue, PaperVenue) and os.environ.get("VOUCH_VENUE") != "paper":
        raise SystemExit("Venue fell back to paper; check your env vars.")

    out_dir = Path("out") / f"real-{a.seed}"
    state = load_state(out_dir) if a.resume else None
    if a.resume and state is None:
        print(f"no restorable state under {out_dir}; starting a fresh session")
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
    recent: list[str] = []
    deadline = time.time() + a.minutes * 60
    next_epoch = time.time() + a.epoch_minutes * 60

    # --resume: restore session state. Order placement semantics are NOT
    # restored from state alone: the venue, cap and opt-in flags still have
    # to be provided by the human relaunching the run.
    if state is not None:
        try:
            if state.get("position"):
                position = Position(**state["position"])
                quote_usd = float(state.get("quote_usd", 0.0))
            peak_equity = float(state.get("peak_equity", a.max_usd))
            halted = bool(state.get("halted", False))
            halt_reason = str(state.get("halt_reason", ""))
            cooldown_until = int(state.get("cooldown_until", 0))
            realized = float(state.get("realized", 0.0))
            fees_paid = float(state.get("fees_paid", 0.0))
            closed = int(state.get("closed", 0))
            epoch_start_trades = int(state.get("epoch_start_trades", 0))
            epoch_start_eq = float(state.get("epoch_start_eq", a.max_usd))
            tick_counter = int(state.get("tick_counter", 0))
            if state.get("next_epoch"):
                next_epoch = float(state["next_epoch"])
            epoch_windows = list(state.get("epoch_windows", []))
            recent = list(state.get("recent", []))
            rules = Rules.from_dict(state.get("rules", rules.to_dict()))
            print(
                f"resumed session state: tick {tick_counter}, realized {realized:+.4f}, "
                f"epoch index {state.get('epoch_index', 0)}"
            )
        except (KeyError, TypeError, ValueError) as e:
            print(f"WARNING: could not restore state ({e}); continuing fresh")

    # Covenant attestation: env opt-in (COVENANT_ATTEST=1), ledger-only unless
    # the full connection trio is set. Attestation NEVER places orders.
    cfg = AttestationConfig.from_env()
    attestor = None
    ledger = None
    epoch_index = int(state.get("epoch_index", 0)) if state is not None else 0
    strategy_name = ""
    if cfg.enabled:
        attest_settings = load_settings().attest
        strategy_id = attest_settings.strategy_id
        strategy_name = attest_settings.strategy_name
        attestor = Attestor(
            cfg,
            run_dir=out_dir,
            strategy_id=strategy_id,
            venue=venue.receipt_venue,
            instrument=venue.instrument,
        )
        ledger = attestor.start_epoch(epoch_index)
        mode = "onchain" if cfg.connected else "ledger-only"
        print(
            f"[{datetime.now():%H:%M:%S}] attestation {mode}: venue={venue.venue_id} "
            f"instrument={venue.instrument}"
        )
        if cfg.connected:
            decision = attestor.resync_with_chain(
                next_local_epoch=epoch_index,
                pending_receipt_epoch=epoch_index if len(ledger) else None,
                has_local_history=state is not None or attestor.tracker.last_committed_epoch >= 0,
            )
            if decision["action"] == "fast_forward":
                epoch_index = int(decision["next_epoch"])
                ledger = attestor.start_epoch(epoch_index)
                print(f"[{datetime.now():%H:%M:%S}] resync: {decision['message']}")
            elif decision["action"] == "refuse":
                print(f"WARNING: {decision['message']}", file=sys.stderr)

    def record_fill(
        *, kind: str, side: str, order_id: str, size_base: float, price: float, fee: float
    ) -> None:
        """One actual venue fill -> one canonical receipt. Side follows the
        POSITION DIRECTION, not open/close (opening a long is a BUY of the
        base asset, closing it is a SELL) - the same mapping shadow uses.
        Bookkeeping only: a receipt failure never blocks trading."""
        if ledger is None:
            return
        is_buy = (kind == "open") == (side == "long")
        try:
            ledger.add_fill(
                venue_order_id=order_id,
                side=receipts.SIDE_BUY if is_buy else receipts.SIDE_SELL,
                size_base=f"{size_base:.8f}",
                price=f"{price:.8f}",
                fee=f"{fee:.8f}",
                filled_at=int(time.time()),
            )
        except Exception as e:  # noqa: BLE001 - attestation must not break trading
            print(f"WARNING: receipt not recorded for {kind} fill {order_id}: {e}", file=sys.stderr)

    def mark_equity(price: float) -> float:
        return a.max_usd + realized + (_fav(position, price) * quote_usd if position else 0.0)

    def persist_session(price: float) -> None:
        save_state(
            out_dir,
            {
                "mode": "real",
                "saved_at": datetime.now(UTC).isoformat(),
                "position": asdict(position) if position else None,
                "quote_usd": quote_usd,
                "peak_equity": peak_equity,
                "halted": halted,
                "halt_reason": halt_reason,
                "cooldown_until": cooldown_until,
                "realized": realized,
                "fees_paid": fees_paid,
                "closed": closed,
                "epoch_start_trades": epoch_start_trades,
                "epoch_start_eq": epoch_start_eq,
                "tick_counter": tick_counter,
                "next_epoch": next_epoch,
                "epoch_index": epoch_index,
                "epoch_windows": epoch_windows,
                "recent": recent,
                "rules": rules.to_dict(),
                "equity_mark_usd": round(mark_equity(price), 4),
            },
        )

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
        entry_quote = quote_usd
        position_side = position.side
        base_sold = float(fill.get("size")) if fill.get("size") else entry_quote / position.entry
        position = None
        quote_usd = 0.0
        record_fill(
            kind="close",
            side=position_side,
            order_id=str(res.get("order_id") or f"{a.seed}-close-{tick_counter}"),
            size_base=base_sold,
            price=exit_price,
            fee=exit_fee,
        )
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

    def close_epoch_boundary(price: float) -> None:
        """Commit the in-flight epoch; advance the index ONLY on a closed
        epoch. A failed chain commit keeps the epoch open (receipts keep
        landing in the same ledger) and retries at the next boundary, so the
        local index can never run ahead of what committed onchain."""
        nonlocal ledger, epoch_index
        if attestor is None or ledger is None:
            return
        attestor.auto_finalize()
        # Epoch 0's netFlow carries the seed capital (the real deposit), so
        # cumulative PnL telescopes to finalEquity minus deposits.
        try:
            res = attestor.commit_epoch(
                ledger,
                equity_usd=mark_equity(price),
                net_flow_usd=a.max_usd if epoch_index == 0 else 0,
                strategy_name=strategy_name,
            )
        except CommitError as e:
            print(
                f"[{datetime.now():%H:%M:%S}] attestation epoch {epoch_index} commit FAILED, "
                f"epoch stays open and will retry at the next boundary: {e}"
            )
            return
        how = (
            f"tx {res.get('hash') or 'already committed'}"
            if res["committed"]
            else f"ledger-only ({res['receipts']} receipts)"
        )
        print(f"[{datetime.now():%H:%M:%S}] attestation epoch {epoch_index} closed: {how}")
        epoch_index += 1
        ledger = attestor.start_epoch(epoch_index)

    def process_bar() -> None:
        nonlocal tick_counter, position, quote_usd, peak_equity, halted, halt_reason
        nonlocal realized, fees_paid, cooldown_until, epoch_start_trades, epoch_start_eq
        nonlocal next_epoch
        if not agg.bars:
            return
        tick_counter += 1
        tick = tick_from_bars(tick_counter, agg.bars, regime="live")
        price = tick.price

        if position:
            position.peak_fav = max(position.peak_fav, _fav(position, price))
        equity = mark_equity(price)
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
        if time.time() >= next_epoch:
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
            next_epoch = time.time() + a.epoch_minutes * 60
            print(
                f"[{datetime.now():%H:%M:%S}] SYSTEM-2 via {s2info['backend']}: "
                f"applied={s2info['applied']}"
            )
            close_epoch_boundary(price)
            persist_session(price)

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
                base_bought = (
                    float(fill.get("size")) if fill.get("size") else quote_usd / entry_price
                )
                record_fill(
                    kind="open",
                    side="long",
                    order_id=str(res.get("order_id") or f"{a.seed}-open-{tick_counter}"),
                    size_base=base_bought,
                    price=entry_price,
                    fee=entry_fee,
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
        while time.time() < deadline and not halted:
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
    finally:
        # Shutdown commit of the in-flight epoch, same guarantee as shadow:
        # every exit path (kill switch, Ctrl-C, deadline) closes what fsynced.
        price_last = list(agg.bars)[-1].close if agg.bars else a.max_usd
        if ledger is not None and attestor is not None:
            try:
                attestor.auto_finalize()
                res = attestor.commit_epoch(
                    ledger,
                    equity_usd=mark_equity(price_last),
                    net_flow_usd=a.max_usd if epoch_index == 0 else 0,
                    strategy_name=strategy_name,
                )
                how = (
                    f"tx {res.get('hash') or 'already committed'}"
                    if res["committed"]
                    else f"ledger-only ({res['receipts']} receipts)"
                )
                print(
                    f"[{datetime.now():%H:%M:%S}] attestation epoch {epoch_index} "
                    f"closed at shutdown: {how}"
                )
                epoch_index += 1
            except CommitError as e:
                print(
                    f"WARNING: shutdown commit of epoch {epoch_index} failed; the epoch stays "
                    f"pending in out/real-{a.seed}/ - rerun with --resume --seed {a.seed} to "
                    f"retry it. ({e})",
                    file=sys.stderr,
                )
            except Exception as e:  # noqa: BLE001 - never mask the run summary
                print(
                    f"WARNING: shutdown commit of epoch {epoch_index} failed: {e}", file=sys.stderr
                )
        persist_session(price_last)

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
        "epoch_index": epoch_index,
        "ended": datetime.now(UTC).isoformat(),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    if position:
        print("NOTE: session ended with an OPEN position - close it manually on the venue.")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
