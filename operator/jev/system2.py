"""System-2: the rule rewriter.

Reviews recent epoch results and proposes new values for the ADJUSTABLE
rules. Every proposal passes rules.validate() (the RAILS), is trimmed to at
most MAX_CHANGES fields per epoch (attribution: one change, one outcome),
and is refused entirely when there is not enough evidence (MIN_TRADES
closed trades in the window). Rejected proposals are logged and ignored.
This is the article's "second model rewrites the first one's rules at 3am",
minus the part where it can rewrite its own leash.

Two backends:
- "llm": any OpenAI-compatible endpoint (default: local qwen llama.cpp).
- "heuristic": built-in adaptive fallback, fully offline and deterministic,
  used when no endpoint is configured or the call fails.
"""

from __future__ import annotations

import json
import os

import httpx

from .rules import Rules, sanitize, validate

MAX_CHANGES = 2   # fields per epoch; keeps every rewrite attributable
MIN_TRADES = 3    # closed trades in the review window before touching anything

SYSTEM_PROMPT = """You are System-2, the strategy rewriter for a paper-trading bot.
You receive the current strategy parameters and a rolling window of recent epoch results.
Propose adjusted values for AT MOST 2 of these fields (the most important ones only):
min_conviction, flat_max_p, max_position_pct, stop_loss_pct, take_profit_pct,
cooldown_ticks, breakeven_trigger_pct, trailing_stop_pct, exit_pressure_min,
min_edge_pct, allowed_actions.
Semantics: breakeven_trigger_pct=0 disables the breakeven floor, trailing_stop_pct=0
disables the trailing stop, min_edge_pct=0 disables the calibrated-edge gate.
Hard rails you cannot violate:
min_conviction [0.10,0.80], flat_max_p [0.50,0.98],
max_position_pct [0.01,0.25], stop_loss_pct [0.005,0.10],
take_profit_pct [0.005,0.25], cooldown_ticks [3,200],
breakeven_trigger_pct [0,0.25], trailing_stop_pct [0,0.25],
exit_pressure_min [0.50,0.99], min_edge_pct [0,0.05],
allowed_actions: subset of [long, flat, short] that must contain "flat".
Tuning guidance: with 60 bps per-side fees, take_profit_pct below ~0.02 is
usually a fee donor; stop_loss_pct below ~0.01 dies to noise. Change ONE thing
when the evidence points one way; only change two when they serve one goal.
Reply with ONLY a JSON object of the fields you want to change. No prose."""


def _heuristic_rewrite(rules: Rules, window: list[dict]) -> dict:
    """Offline adaptive fallback: tighten after losses, loosen after quiet wins."""
    prop: dict = {}
    if not window:
        return prop
    latest = window[-1]
    pnl = latest.get("pnl_usd", 0.0)
    trades = latest.get("trades", 0)
    if pnl < 0 and trades > 0:
        prop["min_conviction"] = min(0.80, rules.min_conviction + 0.05)
        prop["flat_max_p"] = max(0.50, rules.flat_max_p - 0.02)
        prop["max_position_pct"] = max(0.01, rules.max_position_pct * 0.8)
    elif trades == 0:
        prop["flat_max_p"] = min(0.98, rules.flat_max_p + 0.01)
        prop["min_conviction"] = max(0.10, rules.min_conviction - 0.02)
    elif latest.get("win_rate") is not None and latest["win_rate"] >= 0.6 and pnl > 0:
        prop["max_position_pct"] = min(0.25, rules.max_position_pct * 1.1)
    return prop


def _extract_json(text: str) -> str:
    """Strip reasoning blocks and prose fences, return the JSON object text."""
    if "<think>" in text:  # tolerate thinking models on any backend
        text = text.split("</think>")[-1]
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if (start != -1 and end > start) else text


def rewrite(rules: Rules, epoch: dict, window: list[dict] | None = None) -> tuple[Rules, dict]:
    """Returns (new_rules, info). `window` is the list of the last N epoch
    stat dicts (oldest first); `epoch` is the latest, kept for compatibility.
    Falls back to the heuristic backend on any LLM error, and to the previous
    rules entirely if the proposal fails rails or lacks evidence."""
    window = list(window or []) + ([epoch] if epoch not in (window or []) else [])
    base_url = os.environ.get("JEV_S2_BASE_URL", "")
    model = os.environ.get("JEV_S2_MODEL", "")
    proposal: dict = {}
    backend = "heuristic"

    closed_in_window = sum(w.get("trades", 0) for w in window)
    if closed_in_window < MIN_TRADES:
        return rules, {"backend": "none", "applied": None,
                       "rejected": {"proposal": None,
                                    "why": f"only {closed_in_window} closed trade(s) in the last "
                                           f"{len(window)} epoch(s); need {MIN_TRADES} before rewriting"}}

    if base_url and model:
        try:
            r = httpx.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {os.environ.get('JEV_S2_KEY', 'none')}"},
                json={
                    "model": model,
                    # qwen-family chat template reasons by default; the rewrite
                    # is a small structured task, thinking off makes it ~10x
                    # faster and keeps the JSON in `content`
                    "chat_template_kwargs": {"enable_thinking": False},
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": json.dumps(
                                {"current_rules": rules.to_dict(), "recent_epochs": window[-6:]}
                            ),
                        },
                    ],
                    "temperature": 0.2,
                    "max_tokens": 400,
                },
                timeout=180,
            )
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
            proposal = json.loads(_extract_json(text))
            backend = f"llm:{model}"
        except Exception as e:  # noqa: BLE001 - fallback is the feature
            proposal = _heuristic_rewrite(rules, window)
            backend = f"heuristic (llm failed: {type(e).__name__})"
    else:
        proposal = _heuristic_rewrite(rules, window)

    ok, msg = validate(proposal)
    if not ok:
        return rules, {"backend": backend, "applied": None, "rejected": {"proposal": proposal, "why": msg}}
    proposal, trimmed = sanitize(proposal, MAX_CHANGES)
    ok, msg = validate(proposal)  # re-check after the trim
    if not ok:
        return rules, {"backend": backend, "applied": None, "rejected": {"proposal": proposal, "why": msg}}
    merged = rules.to_dict() | proposal
    return Rules.from_dict(merged), {"backend": backend, "applied": proposal,
                                     "rejected": None, "trimmed": trimmed}
