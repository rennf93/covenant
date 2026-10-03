"""vouch.news: forward-lane crypto news (docs/v5-phase1-spec.md, chunk C).

RSS/Atom events normalized to the pinned CryptoPanic-shaped schema and
merged into one content-addressed JSONL store. Leaf package: imports
nothing from vouch; analysis (judge documents) and the run_news_fetch CLI
sit above it. News is NOT a training-state feature (RSS cannot backfill
history); consumers treat the store as gappy by design.
"""

from __future__ import annotations

from vouch.news.feed import (
    DEFAULT_FEEDS,
    FEED_TIMEOUT_S,
    NewsEvent,
    event_id,
    event_time,
    fetch_feed,
    make_event,
    parse_feed,
    tag_currencies,
)
from vouch.news.store import NewsStore

__all__ = [
    "DEFAULT_FEEDS",
    "FEED_TIMEOUT_S",
    "NewsEvent",
    "NewsStore",
    "event_id",
    "event_time",
    "fetch_feed",
    "make_event",
    "parse_feed",
    "tag_currencies",
]
