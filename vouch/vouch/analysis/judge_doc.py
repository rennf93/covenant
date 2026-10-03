"""Judge document renderer: one plain-text document per probe (v5 C2).

Composes the re-judging instrument from docs/v5-phase1-spec.md, chunk C2:

- MARKET STATE: the probe's v1 rendering from run_judge_validate.render_document
  byte-for-byte, then the pinned wire-v2 context sentence built from the
  context cache. A cache that lacks the probe's minute refuses the render
  (the 127-probe re-judging MUST ride enriched state; a silent v1 fallback
  would poison the comparison).
- COMPASS READS: the calibrated reads, unchanged.
- NEWS DIGEST: events published in [ts-24h, ts], newest first, capped at
  20. An empty window renders the pinned literal line - never a crash - so
  the September probes re-judge with context but without news (forward
  collection starts 2026-10).
- the entry QUESTION at the END, wording verbatim from run_judge_validate
  (the pre-registered instrument; do not reword it).

The news store is consumed through the pinned C1 JSONL schema via the
local parser below: vouch.news is a sibling leaf this module must not
depend on (and the schema, not the module, is the contract).

Layering: analysis over engine (ContextSeries).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vouch.engine.context_features import ContextPoint, ContextSeries

DAY = 86400
NEWS_WINDOW = DAY  # published_at in [ts - NEWS_WINDOW, ts]
NEWS_CAP = 20
EMPTY_NEWS_LINE = "No news data available for this window (forward collection starts 2026-10)."

# The pre-registered instrument (run_judge_validate.py), verbatim; the doc
# adds only list markers, never words.
QUESTION_INSTRUCTIONS = (
    "A trading compass flagged this moment as notable. Decide exactly one "
    "thing: should the book open a directional position NOW? Judge the setup "
    "on its merits; when uncertain or conditions look choppy, choose no_trade."
)
QUESTION_CRITERIA = {
    "long": "open a long on demonstrated strength",
    "short": "open a short on demonstrated weakness",
    "no_trade": "no clean directional setup, stand aside",
}

# Pinned to %A's English names: strftime would localize the wire v2 weekday
# under a non-C locale and silently change the rendered document.
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def context_sentence(cp: ContextPoint) -> str:
    """The pinned wire-v2 context sentence (docs/v5-phase1-spec.md, the same
    byte contract System1.decide appends for wire v2), leading space
    included so it can follow any v1 market string."""
    minute = datetime.fromtimestamp(cp.ts, tz=UTC)
    fund_pp = cp.funding_rate_8h * 100
    fund_chg_pp = fund_pp - cp.funding_24h_ago * 100
    return (
        f" Funding {fund_pp:.4f}%/8h, 24h change {fund_chg_pp:+.4f}pp. "
        f"Open interest ${cp.oi_usd_m:.0f}M, "
        f"1h {cp.oi_chg_1h_pct:+.1f}%, 24h {cp.oi_chg_24h_pct:+.1f}%. "
        f"BTC 1m {cp.btc_ret_1m:+.2%}, 60m {cp.btc_ret_60m:+.2%}, "
        f"24h {cp.btc_ret_24h:+.2%}. "
        f"Price {cp.ema4h_dist_pct:+.1f}% vs 4h EMA, "
        f"30d range position {cp.range_pos_30d * 100:.0f}%, "
        f"vol regime {cp.vol_regime}. "
        f"{_WEEKDAYS[minute.weekday()]} {minute:%H:%M} UTC."
    )


def probe_minute_ts(probe: dict[str, Any]) -> int:
    """The probe's ISO ts floored to its minute in unix seconds (the key the
    context cache and the news window are looked up with)."""
    raw = probe.get("ts")
    if not isinstance(raw, str):
        raise SystemExit(f"judge doc: probe ts {raw!r} is not an ISO stamp")
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SystemExit(f"judge doc: probe ts {raw!r} is not an ISO stamp: {exc}") from exc
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return int(ts.timestamp()) // 60 * 60


def _published_ts(row: dict[str, Any]) -> int | None:
    """The event's published_at (pinned C1 schema) as unix seconds; None for
    a row that does not parse. The store tolerates bad items on write, so
    the consumer skips them too - a news gap never kills a re-judge."""
    raw = row.get("published_at")
    if not isinstance(raw, str):
        return None
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return int(ts.timestamp())


def news_lines(news_rows: list[dict[str, Any]], ts: int) -> list[str]:
    """Digest lines for events published in [ts - NEWS_WINDOW, ts], newest
    first, capped at NEWS_CAP: "- {age}h ago [{source}] {title}"."""
    events: list[tuple[int, str, str]] = []
    for row in news_rows:
        published = _published_ts(row)
        if published is None or not ts - NEWS_WINDOW <= published <= ts:
            continue
        source = row.get("source")
        title = row.get("title")
        if not isinstance(source, str) or not isinstance(title, str):
            continue
        events.append((published, source, " ".join(title.split())))
    events.sort(key=lambda e: e[0], reverse=True)
    return [
        f"- {round((ts - published) / 3600, 1):.1f}h ago [{source}] {title}"
        for published, source, title in events[:NEWS_CAP]
    ]


def render(
    probe: dict[str, Any], context_series: ContextSeries, news_rows: list[dict[str, Any]]
) -> str:
    """One judge document: MARKET STATE (v1 bytes + the wire-v2 context
    sentence), COMPASS READS, NEWS DIGEST, and the entry question LAST (the
    position the upstream long-context bench validates)."""
    st = probe["state"]
    laya = probe["laya"]
    ts = probe_minute_ts(probe)
    point = context_series.at(ts)
    if point is None:
        raise SystemExit(
            f"judge doc: context cache has no row for probe minute {ts} "
            f"({probe.get('ts')}); refusing to render un-enriched state"
        )
    path = " ".join(f"{x:.2f}" for x in probe["context"]["path_15"])
    # The v1 market/compass strings are byte-pinned to
    # run_judge_validate.render_document; never reflow them (the re-judge
    # must see the served v1 state, enriched).
    market = (
        f"SOL/USDC. Price {probe['price']:.2f}. "
        f"1m return {st.get('ret_1m', 0):+.2%}, 15m {st.get('ret_15m', 0):+.2%}, "
        f"60m {st.get('ret_60m', 0):+.2%}. "
        f"Volume ratio {st.get('volume_ratio', 1.0):.2f} vs average. "
        f"60m range {probe['context']['window_low']:.2f}-{probe['context']['window_high']:.2f} "
        f"(position in range {st.get('range_pos_60m', 0.5):.2f})."
    ) + context_sentence(point)
    compass = (
        f"The calibrated compass flagged this moment ({', '.join(probe['flag_reasons'])}). "
        f"Its readings: upward pressure {laya['probs']['long']:.2f}, downward pressure "
        f"{laya['probs']['short']:.2f}, no-direction pressure {laya['probs']['flat']:.2f}, "
        f"conviction {laya['conviction']}/4, entry pressure {laya['enter_p']:.2f}, "
        f"direction spread {laya['ls_spread']:+.2f}. The compass does NOT pick direction; "
        "it only reports how notable the moment is."
    )
    digest = news_lines(news_rows, ts) or [EMPTY_NEWS_LINE]
    return (
        "MARKET STATE\n"
        f"{market}\n"
        f"last 15 one-minute closes: {path}\n"
        "\n"
        "COMPASS READS\n"
        f"{compass}\n"
        "\n"
        "NEWS DIGEST\n" + "\n".join(digest) + "\n"
        "\n"
        "QUESTION\n"
        f"{QUESTION_INSTRUCTIONS}\n"
        + "".join(f"- {k}: {v}\n" for k, v in QUESTION_CRITERIA.items())
    )


def load_news(path: str | Path) -> list[dict[str, Any]]:
    """The pinned C1 news store (one JSON event per line). Lines that do not
    parse are skipped, not fatal: feeds are inherently gappy and consumers
    handle gaps (docs/v5-phase1-spec.md, chunk C)."""
    rows: list[dict[str, Any]] = []
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        try:
            row: Any = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


__all__ = [
    "EMPTY_NEWS_LINE",
    "NEWS_CAP",
    "NEWS_WINDOW",
    "QUESTION_CRITERIA",
    "QUESTION_INSTRUCTIONS",
    "context_sentence",
    "load_news",
    "news_lines",
    "probe_minute_ts",
    "render",
]
