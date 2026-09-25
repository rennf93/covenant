"""Session orchestration: market -> System-1 (laya) -> rails -> paper broker,
with System-2 rewriting rules between epochs. Everything saved to out/.

Usage:
    python run.py --ticks 300 --epoch 50 --seed 42
    VOUCH_S2_BASE_URL=http://192.168.50.111:11434/v1 VOUCH_S2_MODEL=qwen2.5:3b python run.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from vouch.engine.broker import Broker
from vouch.engine.market import Market
from vouch.engine.rules import Rules
from vouch.engine.system1 import System1
from vouch.engine.system2 import rewrite


def save_state(out_dir: Path, state: dict) -> None:
    """Persist session state to <out_dir>/state.json atomically.

    Written by the shadow/real loops at every epoch boundary and on exit so
    a later run with --resume restores open position, peak equity, cooldown,
    the current epoch window and the epoch index.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "state.json.tmp"
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out_dir / "state.json")


def load_state(out_dir: Path) -> dict | None:
    """Read <out_dir>/state.json; None when absent or unreadable (the caller
    decides whether that is fatal or a fresh start)."""
    path = Path(out_dir) / "state.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def run_session(
    ticks: int = 300,
    epoch_len: int = 50,
    seed: int = 42,
    starting_cash: float = 1000.0,
    out: str = "out",
) -> dict:
    out_dir = Path(out)
    market = Market(seed=seed)
    broker = Broker(starting_cash=starting_cash, max_drawdown_pct=0.20)
    rules = Rules.from_env()  # honors VOUCH_RULES (set by the UI)
    broker.cooldown_ticks = rules.cooldown_ticks
    s1 = System1()
    rules_history: list[dict] = [{"tick": 0, "rules": rules.to_dict(), "s2": None}]
    recent: list[str] = []
    epoch_windows: list[dict] = []
    epoch_start_eq = broker.equity(market.price)
    epoch_start_tick = 0
    t0 = time.time()

    for i in range(ticks):
        tick = market.tick()
        broker.stop_check(tick.price, tick.i, rules)
        broker.mark(tick.price, tick.i)
        if broker.halted:
            recent.append(f"tick {tick.i}: HALTED ({broker.halt_reason})")
            break

        d = s1.decide(tick, broker, rules, recent)
        if d["mode"] == "exit" and d["final_action"] == "exit":
            broker.close(tick.price, tick.i, reason="LAYA_EXIT")
            recent.append(
                f"tick {tick.i}: EXIT @ {tick.price:.2f} (exit pressure {d['enter_p']:.2f})"
            )
        elif d["mode"] == "entry" and d["final_action"] != "flat" and d["veto"] is None:
            size = broker.equity(tick.price) * rules.max_position_pct
            broker.open(
                d["final_action"],
                size,
                tick.price,
                tick.i,
                reason=f"conv={d['conviction']:.2f} enter={d['enter_p']:.2f}",
            )
            recent.append(
                f"tick {tick.i}: OPEN {d['final_action']} @ {tick.price:.2f} "
                f"(conv {d['conviction']:.2f})"
            )
        elif d["veto"]:
            recent.append(f"tick {tick.i}: veto {d['raw_action']} ({d['veto']})")

        # epoch boundary: System-2 reviews and proposes
        if (i - epoch_start_tick) >= epoch_len and i < ticks - 1:
            eq = broker.equity(tick.price)
            closed = [t for t in broker.trades if "pnl_usd" in t and t["tick"] > epoch_start_tick]
            wins = sum(1 for t in closed if t["pnl_usd"] > 0)
            epoch = {
                "ticks": i - epoch_start_tick,
                "trades": len(closed),
                "win_rate": round(wins / len(closed), 3) if closed else None,
                "pnl_usd": round(eq - epoch_start_eq, 2),
                "open_position": broker.position is not None,
            }
            rules, s2info = rewrite(rules, epoch, window=epoch_windows)
            epoch_windows.append(epoch)
            broker.cooldown_ticks = rules.cooldown_ticks  # keep the rail in sync
            rules_history.append(
                {"tick": i, "epoch": epoch, "s2": s2info, "rules": rules.to_dict()}
            )
            epoch_start_eq, epoch_start_tick = eq, i

    final_eq = broker.equity(market.price)
    summary = {
        "ticks_run": len(market.history) - 1,
        "seed": seed,
        "starting_cash": starting_cash,
        "final_equity": round(final_eq, 2),
        "total_return_pct": round((final_eq / starting_cash - 1) * 100, 2),
        "peak_equity": round(broker.peak_equity, 2),
        "halted": broker.halted,
        "halt_reason": broker.halt_reason,
        "closed_trades": len([t for t in broker.trades if "pnl_usd" in t]),
        "wall_seconds": round(time.time() - t0, 1),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    broker.save(out_dir)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "rules-history.json").write_text(json.dumps(rules_history, indent=2))
    (out_dir / "equity.csv").write_text(
        "tick,equity\n"
        + "".join(f"{i},{broker.equity(p):.2f}\n" for i, p in enumerate(market.history))
    )
    return summary
