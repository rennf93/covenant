"""Backtest: replay REAL historical candles through the exact live path.

Same System-1 head, same rules/rails, same broker cost model, same
System-2 epoch rewrites (optional). This replaces waiting hours of shadow
time for one answer: months of 1m bars decide in minutes, and every tick
is logged with laya's full probability vector so the same output feeds
the analysis harness (IC) and the SFT dataset builder.

Usage:
    python run_backtest.py --minutes 4320 --epoch-len 120 --tag sept

Layering: analysis, but this module is the DOCUMENTED EXCEPTION to
"analysis is a leaf": it replays the exact live path, so it sits ABOVE
engine (imports broker/candles/rules/system1/system2). It must not import
server, and engine must not import it (only this direction).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from vouch.engine.broker import Broker
from vouch.engine.candles import fetch_candles, state_snapshot, tick_from_bars
from vouch.engine.rules import Rules
from vouch.engine.system1 import System1
from vouch.engine.system2 import rewrite


def run_backtest(
    product: str = "SOL-USD",
    minutes: int = 1440,
    cash: float = 1000.0,
    epoch_len: int = 60,
    out_root: str = "out",
    tag: str | None = None,
    warmup: int = 60,
    use_s2: bool = True,
    data_harvest: bool = False,
) -> dict:
    """data_harvest: force-close any position right after it opens (and park
    the kill-switch halt) so the loop keeps producing flat-state ENTRY
    decisions over the whole history. This is a DATA mode: it maximizes
    state diversity for calibration/SFT and its PnL is meaningless."""
    client = httpx.Client()
    bars = fetch_candles(client, product=product, minutes=minutes)
    client.close()
    if len(bars) < warmup + 30:
        raise SystemExit(f"only got {len(bars)} bars from Coinbase; need >= {warmup + 30}")

    stamp = tag or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(out_root) / f"backtest-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "candles.jsonl").write_text("".join(json.dumps(b.__dict__) + "\n" for b in bars))

    broker = Broker(starting_cash=cash, max_drawdown_pct=1.0 if data_harvest else 0.20)
    rules = Rules.from_env()  # honors VOUCH_RULES (set by the UI)
    s1 = System1()
    decisions_log = out_dir / "decisions.jsonl"
    s2_log = out_dir / "s2-history.jsonl"
    recent: list[str] = []
    epoch_windows: list[dict] = []
    epoch_start_eq = cash
    epoch_start_closed = 0
    t0 = time.time()

    for i in range(warmup, len(bars)):
        window = list(bars)[: i + 1]
        bar = bars[i]
        tick = tick_from_bars(i, window, regime="backtest")
        price = bar.close

        broker.stop_check(price, i, rules)
        broker.mark(price, i)
        if broker.halted:
            recent.append(f"tick {i}: HALTED ({broker.halt_reason})")
            break

        d = s1.decide(tick, broker, rules, recent)
        acted = None
        if d["mode"] == "exit" and d["final_action"] == "exit":
            broker.close(price, i, reason="LAYA_EXIT")
            acted = "EXIT (laya)"
            recent.append(f"tick {i}: EXIT @ {price:.2f} (exit pressure {d['enter_p']:.2f})")
        elif d["mode"] == "entry" and d["final_action"] != "flat" and d["veto"] is None:
            size = broker.equity(price) * rules.max_position_pct
            broker.open(
                d["final_action"],
                size,
                price,
                i,
                reason=f"conv={d['conviction']:.2f} enter={d['enter_p']:.2f}",
            )
            acted = f"OPEN {d['final_action']} ${size:.2f}"
            recent.append(
                f"tick {i}: OPEN {d['final_action']} @ {price:.2f} (conv {d['conviction']:.2f})"
            )
            if data_harvest:
                broker.close(price, i, reason="HARVEST")
                recent.append(f"tick {i}: HARVEST-CLOSE @ {price:.2f}")

        row = {
            "ts": datetime.fromtimestamp(bar.ts, tz=UTC).isoformat(),
            "tick": i,
            "price": price,
            "equity": round(broker.equity(price), 4),
            "volume_ratio": round(tick.volume_ratio, 3),
            "state": state_snapshot(tick, broker),
            "decision": d,
            "acted": acted,
        }
        with decisions_log.open("a") as f:
            f.write(json.dumps(row) + "\n")

        # epoch boundary: System-2 reviews the rolling window and rewrites rules
        if use_s2 and epoch_len > 0 and (i - warmup) > 0 and (i - warmup) % epoch_len == 0:
            closed = [t for t in broker.trades if "pnl_usd" in t]
            new_trades = len(closed) - epoch_start_closed
            wins = sum(1 for t in closed[epoch_start_closed:] if t["pnl_usd"] > 0)
            stats = {
                "ticks": epoch_len,
                "trades": new_trades,
                "win_rate": round(wins / new_trades, 3) if new_trades else None,
                "pnl_usd": round(broker.equity(price) - epoch_start_eq, 4),
                "open_position": broker.position is not None,
            }
            rules, s2info = rewrite(rules, stats, window=epoch_windows)
            with s2_log.open("a") as f:
                f.write(
                    json.dumps({"tick": i, "epoch": stats, "s2": s2info, "rules": rules.to_dict()})
                    + "\n"
                )
            epoch_windows.append(stats)
            epoch_start_eq = broker.equity(price)
            epoch_start_closed = len([t for t in broker.trades if "pnl_usd" in t])

    eq = broker.equity(bars[-1].close)
    summary = {
        "mode": f"backtest {product} 1m candles" + (" [data harvest]" if data_harvest else ""),
        "bars": len(bars) - warmup,
        "started": datetime.fromtimestamp(bars[warmup].ts, tz=UTC).isoformat(),
        "ended": datetime.fromtimestamp(bars[-1].ts, tz=UTC).isoformat(),
        "starting_cash": cash,
        "final_equity": round(eq, 4),
        "return_pct": round((eq / cash - 1) * 100, 3),
        "peak_equity": round(broker.peak_equity, 4),
        "halted": broker.halted,
        "halt_reason": broker.halt_reason,
        "closed_trades": len([t for t in broker.trades if "pnl_usd" in t]),
        "fee_per_side": broker.fee,
        "wall_seconds": round(time.time() - t0, 1),
    }
    broker.save(out_dir)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary
