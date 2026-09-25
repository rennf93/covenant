"""Live shadow mode: REAL market prices, PAPER money.

Polls a public no-auth price API (Coinbase spot) for SOL-USD - or, better,
streams real trades over the public websocket - builds TRUE 1-minute bars
(correct 1m/15m/60m windows, real volume via the public candles endpoint),
feeds the same laya System-1 head, and books every decision on the paper
broker with the same rails (stop/take/trail, cooldown, 20% drawdown kill
switch) and the same retail fee model as a real account. No keys, no
orders, no exchange connection.

Every tick is logged with laya's full probability vector, so shadow runs
feed the analysis harness (IC) and the SFT dataset builder directly.

Run:
    .venv/bin/python run_shadow.py --minutes 60
    .venv/bin/python run_shadow.py --forever --no-websocket --interval 20
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from vouch.attest import AttestationConfig, Attestor, receipts
from vouch.config import load_settings
from vouch.engine.broker import Broker
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
from vouch.engine.wsfeed import WsPriceFeed
from vouch.venues import make_venue

COINBASE_SPOT = "https://api.coinbase.com/v2/prices/SOL-USD/spot"


def fetch_price(client: httpx.Client) -> float:
    r = client.get(COINBASE_SPOT, timeout=10)
    r.raise_for_status()
    return float(r.json()["data"]["amount"])


def main() -> None:
    ap = argparse.ArgumentParser(description="vouch shadow mode: live prices, paper fills")
    ap.add_argument("--minutes", type=int, default=60)
    ap.add_argument(
        "--forever",
        action="store_true",
        help="run until stopped (daemon mode; --minutes is ignored)",
    )
    ap.add_argument(
        "--interval", type=int, default=15, help="seconds between price polls (fallback mode)"
    )
    ap.add_argument(
        "--epoch-minutes",
        type=int,
        default=15,
        help="minutes between System-2 rule rewrites (0 = never)",
    )
    ap.add_argument(
        "--cash", type=float, default=10.0, help="paper stake to mirror the real experiment"
    )
    ap.add_argument(
        "--no-websocket", action="store_true", help="disable the trade websocket, poll spot instead"
    )
    ap.add_argument("--seed", type=str, default=datetime.now(UTC).strftime("%Y%m%d-%H%M%S"))
    ap.add_argument(
        "--attest",
        action="store_true",
        help="opt in to Covenant attestation (ledger-only unless the"
        " COVENANT_RPC_URL/KEY/CONTRACT env trio is also set)",
    )
    a = ap.parse_args()

    out_dir = Path("out") / f"live-{a.seed}"
    out_dir.mkdir(parents=True, exist_ok=True)
    broker = Broker(starting_cash=a.cash, max_drawdown_pct=0.20)
    rules = Rules.from_env()  # honors VOUCH_RULES (set by the UI)
    s1 = System1()
    client = httpx.Client()
    agg = BarAggregator()
    ws = None if a.no_websocket else WsPriceFeed().start()
    recent: list[str] = []
    decisions_log = out_dir / "decisions.jsonl"
    s2_log = out_dir / "s2-history.jsonl"
    deadline = None if a.forever else time.time() + a.minutes * 60
    next_epoch = time.time() + a.epoch_minutes * 60 if a.epoch_minutes else None
    epoch_windows: list[dict] = []
    epoch_start = {"trades": 0, "equity": a.cash, "ts": datetime.now(UTC).isoformat()}
    last_volume_fetch = 0.0
    tick_counter = 0

    # Covenant attestation: opt-in via COVENANT_ATTEST=1 or --attest. Entirely
    # disabled = no attestor, no fill observer, byte-identical legacy run.
    cfg = AttestationConfig.from_env()
    if a.attest:
        cfg.enabled = True
    attestor = None
    ledger = None
    epoch_index = 0
    strategy_name = ""
    if cfg.enabled:
        venue = make_venue()  # paper by default; class + instrument go into receipts
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

        def observe_fill(f: dict) -> None:
            # Side maps by POSITION DIRECTION, not by open/close: System-1
            # can open shorts (rules.allowed_actions defaults to
            # [long, flat, short]), so opening a short is a SELL of the base
            # asset and closing it is a BUY. Only long open / long close
            # map to BUY / SELL respectively. venue_order_id is unique per
            # run: seed-kind-tick, and at most one open or one close can
            # happen per tick (a stop-out close and a re-entry open on the
            # same tick differ in kind, and broker.open/close no-op when a
            # position already exists / is absent). parse_fixed8 accepts at
            # most 8 fractional digits, so quantize the human-unit floats
            # before handing them over; :.8f (not round()) so the repr can
            # never land in scientific notation, which parse_fixed8 rejects.
            is_buy = (f["kind"] == "open") == (f["side"] == "long")
            ledger.add_fill(
                venue_order_id=f"{a.seed}-{f['kind']}-{f['tick']}",
                side=receipts.SIDE_BUY if is_buy else receipts.SIDE_SELL,
                size_base=f"{f['size_usd'] / f['price']:.8f}",  # human base units (SOL)
                price=f"{f['price']:.8f}",
                fee=f"{f['fee']:.8f}",
                filled_at=int(time.time()),
            )

        broker.fill_observer = observe_fill
        mode = "onchain" if cfg.connected else "ledger-only"
        print(
            f"[{datetime.now():%H:%M:%S}] attestation {mode}: venue={venue.venue_id} "
            f"instrument={venue.instrument}"
        )

    def epoch_stats(price: float) -> dict:
        closed = [t for t in broker.trades if "pnl_usd" in t]
        new_trades = len(closed) - epoch_start["trades"]
        wins = sum(1 for t in closed[epoch_start["trades"] :] if t["pnl_usd"] > 0)
        return {
            "trades": new_trades,
            "win_rate": round(wins / new_trades, 3) if new_trades else None,
            "pnl_usd": round(broker.equity(price) - epoch_start["equity"], 4),
            "open_position": broker.position is not None,
        }

    def process_bar() -> None:
        """One completed 1m bar: book rails, ask laya, log everything."""
        nonlocal tick_counter, rules, ledger, epoch_index
        if not agg.bars:
            return
        tick_counter += 1
        tick = tick_from_bars(tick_counter, agg.bars, regime="live")
        price = tick.price

        broker.stop_check(price, tick_counter, rules)
        broker.mark(price, tick_counter)
        if broker.halted:
            print(f"KILL SWITCH: {broker.halt_reason}")
            raise KeyboardInterrupt  # exit the run loop via finally

        # epoch boundary: System-2 (the paired LLM) reviews and rewrites rules
        nonlocal next_epoch
        if next_epoch is not None and time.time() >= next_epoch:
            stats = epoch_stats(price)
            rules, s2info = rewrite(rules, stats, window=epoch_windows)
            epoch_windows.append(stats)
            broker.cooldown_ticks = rules.cooldown_ticks  # keep the rail in sync
            row = {
                "ts": datetime.now(UTC).isoformat(),
                "epoch": stats,
                "s2": s2info,
                "rules": rules.to_dict(),
            }
            with s2_log.open("a") as f:
                f.write(json.dumps(row) + "\n")
            print(
                f"[{datetime.now():%H:%M:%S}] SYSTEM-2 rewrite via {s2info['backend']}: "
                f"applied={s2info['applied']} rejected={s2info['rejected']}"
            )
            if attestor is not None and ledger is not None:
                # Epoch 0's netFlow carries the seed capital (the starting
                # cash), so cumulative PnL telescopes to finalEquity minus
                # deposits. Shadow mode has paper money and no deposits,
                # so every later epoch's net flow is 0.
                net_flow = a.cash if epoch_index == 0 else 0
                res = attestor.commit_epoch(
                    ledger,
                    equity_usd=broker.equity(price),
                    net_flow_usd=net_flow,
                    strategy_name=strategy_name,
                )
                if res["committed"]:
                    print(
                        f"[{datetime.now():%H:%M:%S}] attestation epoch {epoch_index} "
                        f"committed: tx {res['hash']}"
                    )
                else:
                    print(
                        f"[{datetime.now():%H:%M:%S}] attestation epoch {epoch_index} closed "
                        f"(ledger-only, {res['receipts']} receipts): {res['reason']}"
                    )
                epoch_index += 1
                ledger = attestor.start_epoch(epoch_index)
            epoch_start["trades"] = len([t for t in broker.trades if "pnl_usd" in t])
            epoch_start["equity"] = broker.equity(price)
            next_epoch = time.time() + a.epoch_minutes * 60

        # Transient System-1 failures (laya server hiccup, provider 5xx) must
        # not kill a hours-long shadow run. The rails above (stop_check /
        # mark / kill switch) already ran for this bar, so skipping only the
        # discretionary decision is the safe degradation: hold, retry next bar.
        try:
            d = s1.decide(tick, broker, rules, recent)
        except Exception as e:  # noqa: BLE001 - keep the loop alive
            print(
                f"[{datetime.now():%H:%M:%S}] tick {tick_counter}: System-1 backend "
                f"failed ({type(e).__name__}: {e}); holding, retrying next bar"
            )
            return
        acted = None
        if d["mode"] == "exit" and d["final_action"] == "exit":
            broker.close(price, tick_counter, reason="LAYA_EXIT")
            acted = "EXIT (laya)"
            recent.append(f"tick {tick_counter}: EXIT @ {price:.2f}")
        elif d["mode"] == "entry" and d["final_action"] != "flat" and d["veto"] is None:
            size = broker.equity(price) * rules.max_position_pct
            broker.open(
                d["final_action"],
                size,
                price,
                tick_counter,
                reason=f"conv={d['conviction']:.2f} enter={d['enter_p']:.2f}",
            )
            acted = f"OPEN {d['final_action']} ${size:.2f}"
            recent.append(
                f"tick {tick_counter}: OPEN {d['final_action']} @ {price:.2f} "
                f"(conv {d['conviction']:.2f})"
            )
        if d["veto"] and d["mode"] == "exit":
            pass  # hold; nothing to record beyond the row

        row = {
            "ts": datetime.now(UTC).isoformat(),
            "tick": tick_counter,
            "price": price,
            "equity": round(broker.equity(price), 4),
            "state": state_snapshot(tick, broker),
            "decision": d,
            "acted": acted,
        }
        with decisions_log.open("a") as f:
            f.write(json.dumps(row) + "\n")
        if acted:
            print(f"[{datetime.now():%H:%M:%S}] px {price:.2f} eq {row['equity']:.2f} -> {acted}")
        if broker.position:
            print(
                f"    position {broker.position.side} ${broker.position.size_usd:.2f} "
                f"@ {broker.position.entry:.2f} (peak fav {broker.position.peak_fav:+.2%})"
            )

    feed = "websocket" if (ws and ws.alive) else f"polling {a.interval}s"
    print(
        f"shadow session: {'forever' if a.forever else str(a.minutes) + ' min'}, "
        f"feed={feed}, paper stake ${a.cash:.2f}, fee {broker.fee:.2%}/side"
    )
    last_bar_ts = 0
    try:
        while deadline is None or time.time() < deadline:
            if ws and ws.alive:
                for ts, price, size in ws.drain():
                    agg.add(ts, price, size)
                time.sleep(1)
            else:
                try:
                    price = fetch_price(client)
                    agg.add(time.time(), price)
                except Exception as e:  # noqa: BLE001 - transient network, keep going
                    print(
                        f"[{datetime.now():%H:%M:%S}] price fetch failed "
                        f"({type(e).__name__}), retrying"
                    )
                time.sleep(a.interval)

            # real volume for the newest completed bar, once a minute
            if agg.bars and time.time() - last_volume_fetch > 60:
                vol = latest_candle_volume(client)
                if vol:
                    inject_volume(agg.bars, vol[0], vol[1])
                last_volume_fetch = time.time()

            # act on the newest newly completed bar (one per pass; passes are
            # 1s on websocket or interval-s polling, bars are 1m)
            if agg.bars and agg.bars[-1].ts > last_bar_ts:
                last_bar_ts = agg.bars[-1].ts
                process_bar()
    except KeyboardInterrupt:
        pass
    finally:
        if ws:
            ws.stop()
        agg.flush()
        # Shutdown commit of the in-flight epoch. Every exit path lands here:
        # the drawdown kill switch (which raises KeyboardInterrupt between
        # epoch boundaries), a Ctrl-C, and plain deadline expiry. Without
        # this, fills already fsynced into receipts-epoch-NNNN.jsonl would
        # exist in the ledger with no closing commit/evidence bundle, i.e.
        # receipts silently dropped. Commit with equity marked at the last
        # observed price; on kill switch the position was already closed by
        # broker.mark, so equity is realized cash. An empty ledger commits
        # as ZERO32, matching the epoch-boundary path.
        if ledger is not None and attestor is not None:
            price = list(agg.bars)[-1].close if agg.bars else a.cash
            net_flow = a.cash if epoch_index == 0 else 0
            try:
                res = attestor.commit_epoch(
                    ledger,
                    equity_usd=broker.equity(price),
                    net_flow_usd=net_flow,
                    strategy_name=strategy_name,
                )
                how = (
                    f"tx {res['hash']}"
                    if res["committed"]
                    else f"ledger-only ({res['receipts']} receipts)"
                )
                print(
                    f"[{datetime.now():%H:%M:%S}] attestation epoch {epoch_index} "
                    f"closed at shutdown: {how}"
                )
            except Exception as e:  # noqa: BLE001 - never mask the run summary
                print(
                    f"WARNING: shutdown commit of epoch {epoch_index} failed: {e}", file=sys.stderr
                )

    eq = broker.equity(list(agg.bars)[-1].close) if agg.bars else a.cash
    summary = {
        "mode": "shadow (live prices, paper fills)",
        "ended": datetime.now(UTC).isoformat(),
        "starting_cash": a.cash,
        "final_equity": round(eq, 4),
        "return_pct": round((eq / a.cash - 1) * 100, 3),
        "peak_equity": round(broker.peak_equity, 4),
        "halted": broker.halted,
        "halt_reason": broker.halt_reason,
        "closed_trades": len([t for t in broker.trades if "pnl_usd" in t]),
        "open_position": broker.position is not None,
        "fee_per_side": broker.fee,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    broker.save(out_dir)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
