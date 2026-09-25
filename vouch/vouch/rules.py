"""Strategy rules, the hard constraint rails, and the System-2 validator.

System-2 (the rule rewriter) may only propose values INSIDE these rails.
Rails are code, not prompt text: a proposal that violates them is rejected
and the previous rules stay. This is the piece the $8-VPS article's setup
did not have, and it is why our version is allowed to exist (in paper).
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

ALLOWED_ACTIONS = ["long", "flat", "short"]


@dataclass
class Rules:
    min_conviction: float = 0.15        # System-1 conviction needed to act (normalized 0-1)
    flat_max_p: float = 0.93            # enter only when P(flat) drops below this
    max_position_pct: float = 0.15      # of equity, per position
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04
    cooldown_ticks: int = 10            # after a losing close
    breakeven_trigger_pct: float = 0.015  # once this far in profit, stop moves to entry (0 = off)
    trailing_stop_pct: float = 0.0      # giveback from peak favorable move before exit (0 = off)
    exit_pressure_min: float = 0.85     # P(exit) needed for laya to close a position early (0..1)
    min_edge_pct: float = 0.0           # min calibrated EV over costs per trade (0 = gate off)
    allowed_actions: list[str] = None   # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.allowed_actions is None:
            self.allowed_actions = ["long", "flat", "short"]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Rules":
        valid = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in valid})

    @classmethod
    def from_env(cls) -> "Rules":
        """Rules defaults, overridden by VOUCH_RULES (JSON dict) when present and
        valid. The UI sets VOUCH_RULES when launching runners, which is how
        dashboard settings reach the actual loop. Invalid JSON or rail
        violations are ignored silently: defaults are always safe."""
        import json as _json
        import os as _os

        rules = cls()
        raw = _os.environ.get("VOUCH_RULES", "")
        if raw:
            try:
                prop = _json.loads(raw)
                ok, _ = validate(prop if isinstance(prop, dict) else {})
                if ok:
                    merged = rules.to_dict() | {k: v for k, v in prop.items() if k in RAILS or k == "allowed_actions"}
                    return cls.from_dict(merged)
            except Exception:  # noqa: BLE001 - bad env JSON must never kill a run
                pass
        return rules


# --- RAILS: the validator System-2 proposals must survive -------------------

RAILS = {
    "min_conviction": (0.10, 0.80),
    "flat_max_p": (0.50, 0.98),            # zero-shot laya is flat-biased; the signal is the dip
    "max_position_pct": (0.01, 0.25),      # never more than a quarter of equity
    "stop_loss_pct": (0.005, 0.10),
    "take_profit_pct": (0.005, 0.25),
    "cooldown_ticks": (3, 200),
    "breakeven_trigger_pct": (0.0, 0.25),  # 0 disables the breakeven floor
    "trailing_stop_pct": (0.0, 0.25),      # 0 disables the trailing stop
    "exit_pressure_min": (0.50, 0.99),     # how sure laya must be to exit early
    "min_edge_pct": (0.0, 0.05),           # 0 disables the calibrated-edge gate
}


def validate(proposal: dict) -> tuple[bool, str]:
    """Return (ok, message). A proposal is applied only when ok is True."""
    if not isinstance(proposal, dict):
        return False, "proposal is not a dict"
    unknown = set(proposal) - set(RAILS) - {"allowed_actions"}
    if unknown:
        return False, f"unknown fields: {sorted(unknown)}"
    for key, (lo, hi) in RAILS.items():
        if key in proposal:
            v = proposal[key]
            if not isinstance(v, (int, float)) or not (lo <= v <= hi):
                return False, f"{key}={v} outside rail [{lo}, {hi}]"
    if "allowed_actions" in proposal:
        a = proposal["allowed_actions"]
        if not isinstance(a, list) or not a or set(a) - set(ALLOWED_ACTIONS) or "flat" not in a:
            return False, f"allowed_actions must be a non-empty subset of {ALLOWED_ACTIONS} containing 'flat'"
    if "take_profit_pct" in proposal and "stop_loss_pct" in proposal:
        if proposal["take_profit_pct"] < proposal["stop_loss_pct"]:
            return False, "take_profit_pct below stop_loss_pct: inverted bracket"
    return True, "ok"


def sanitize(proposal: dict, max_fields: int = 2) -> tuple[dict, str | None]:
    """Trim a validated proposal to at most max_fields changes (lowest first
    for determinism). System-2 tuning one field per epoch is the point: this
    keeps rewrites attributable and limits noise-chasing."""
    if len(proposal) <= max_fields:
        return proposal, None
    keys = sorted(proposal)[:max_fields]
    return {k: proposal[k] for k in keys}, f"trimmed to {keys} (max {max_fields} changes/epoch)"
