"""Token budget for the judge document (v5 C2): a realistic document must
fit the measured laya-multilingual reliability cap (4000 tokens). The count
is measured with the laya-multilingual tokenizer from the local HF cache
(HF_HOME/HF_HUB_CACHE honored) and skipped when it is unavailable: over
budget is a build error, never a silent trim. Stdlib unittest only; run
from the vouch/ directory.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from vouch.analysis.judge_doc import probe_minute_ts, render
from vouch.engine.context_features import ContextSeries

BUDGET_TOKENS = 4000

_MULTILINGUAL_GLOB = (
    "models--convaiinnovations--laya-multilingual/snapshots/*/tokenizer/tokenizer.json"
)
_BASE_GLOB = "models--convaiinnovations--laya/snapshots/*/tokenizer/tokenizer.json"

PROBE_TS = "2026-09-26T14:05:00+00:00"

# Realistic digest: the cap-sized window (20 events) with feed-style titles.
TITLES = [
    "Solana ETF posts record weekly inflows as institutions add exposure",
    "Fed officials signal patience on rate cuts ahead of the next meeting",
    "Solana validators approve a new priority-fee market for DEX trades",
    "Bitcoin holds 105k as ETF volumes cool from last week's surge",
    "Solana DeFi TVL reclaims its 2025 high amid perp DEX rotation",
    "SEC delays decision on staking features for spot ether funds",
    "Stablecoin supply hits fresh all-time high on Tron and Solana",
    "Solana network uptime stretches past a year since the last halt",
    "Japan's FSA moves to classify certain crypto tokens as securities",
    "Solana futures open interest climbs to a three-month high",
    "Crypto treasury firms pivot from bitcoin to fast finality chains",
    "Solana's Firedancer client passes third-party audit, rollout nears",
    "Ethereum fee burn dips to a two-year low after blob upgrade",
    "Solana payment rails onboard two large remittance corridors",
    "Treasury yields slip as CPI comes in line with expectations",
    "Solana NFT marketplace volumes jump on new royalty standard",
    "Binance adjusts SOL margin tiers amid rising derivatives demand",
    "Solana L2s debate interoperability standards for shared sequencing",
    "Bullish vaneck report projects 3x addressable market for SOL by 2028",
    "Solana bootcamp graduates join growing paid-networks developer base",
]

# Realistic 15-minute price path (the last 15 closes a probe carries).
PATH_15 = [
    199.82,
    199.75,
    199.9,
    199.68,
    199.51,
    199.77,
    200.05,
    200.18,
    200.02,
    199.86,
    199.9,
    200.11,
    200.34,
    200.28,
    200.4,
]


def laya_tokenizer():
    """The laya-multilingual tokenizer from the local HF cache, or None when
    it is not installed/cached (the budget test skips): the budget is
    measured, never estimated."""
    try:
        from transformers import AutoTokenizer
    except ImportError:
        return None
    hf_home = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))
    hub = Path(os.environ.get("HF_HUB_CACHE", hf_home / "hub"))
    hits = sorted(hub.glob(_MULTILINGUAL_GLOB))
    if not hits:  # the base laya repo ships the same tokenizer under tokenizer/
        hits = sorted(hub.glob(_BASE_GLOB))
    if not hits:
        return None
    try:
        return AutoTokenizer.from_pretrained(str(hits[-1].parent), local_files_only=True)
    except Exception:  # noqa: BLE001 - a broken cache is a skip, not a failure
        return None


def realistic_probe() -> dict:
    return {
        "ts": PROBE_TS,
        "price": 200.4,
        "state": {
            "ret_1m": 0.0006,
            "ret_15m": 0.0029,
            "ret_60m": 0.0074,
            "volume_ratio": 2.84,
            "range_pos_60m": 0.93,
            "in_position": False,
        },
        "laya": {
            "probs": {"long": 0.4211, "flat": 0.3127, "short": 0.2662},
            "conviction": 3,
            "enter_p": 0.6873,
            "ls_spread": 0.1549,
            "action_conf": 0.4211,
        },
        "flag_reasons": ["pressure", "spread"],
        "label": "long",
        "fwd_ret": 0.0131,
        "tp_net": 0.018,
        "context": {
            "window_low": 198.44,
            "window_high": 200.4,
            "window_ret": 0.0074,
            "path_15": PATH_15,
        },
    }


def realistic_news() -> list[dict]:
    base = datetime.fromtimestamp(probe_minute_ts(realistic_probe()), tz=UTC)
    return [
        {
            "id": f"{i:016x}",
            "title": title,
            "url": f"https://example.com/articles/{i}",
            "source": ("CoinDesk", "Decrypt", "Cointelegraph", "CryptoSlate")[i % 4],
            "published_at": (base - timedelta(minutes=37 * (i + 1))).isoformat(),
            "currencies": ["SOL"],
            "kind": "news",
            "via": "rss",
        }
        for i, title in enumerate(TITLES)
    ]


class TokenBudgetTest(unittest.TestCase):
    def test_realistic_document_fits_the_4000_token_cap(self):
        tok = laya_tokenizer()
        if tok is None:
            self.skipTest("laya-multilingual tokenizer not in the local HF cache")
        ts = probe_minute_ts(realistic_probe())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        rows = []
        for minute in range(-2, 3):
            rows.append(
                {
                    "ts": ts + minute * 60,
                    "funding_rate_8h": 0.000092,
                    "funding_24h_ago": -0.000031,
                    "oi_usd_m": 1452.7,
                    "oi_chg_1h_pct": 0.31,
                    "oi_chg_24h_pct": 4.12,
                    "btc_ret_1m": 0.0002,
                    "btc_ret_60m": 0.0031,
                    "btc_ret_24h": 0.0144,
                    "ema4h_dist_pct": 0.87,
                    "range_pos_30d": 0.68,
                    "vol_regime": "normal",
                }
            )
        cache = Path(tmp.name) / "ctx.jsonl"
        cache.write_text("".join(json.dumps(r) + "\n" for r in rows))
        series = ContextSeries.load_jsonl(cache)
        doc = render(realistic_probe(), series, realistic_news())
        n = len(tok.encode(doc, add_special_tokens=False))
        self.assertLessEqual(
            n, BUDGET_TOKENS, f"judge doc is {n} tokens (budget {BUDGET_TOKENS}):\n{doc}"
        )


if __name__ == "__main__":
    unittest.main()
