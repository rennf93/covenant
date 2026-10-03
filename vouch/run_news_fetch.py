#!/usr/bin/env python
"""Fetch crypto news into the forward-lane store (docs/v5-phase1-spec.md C1).

RSS cannot backfill history, so this is forward collection only: every run
MERGES into one content-addressed JSONL store (dedupe by id, sorted by
published_at; no gap refusal). A dead feed warns and never kills the run;
zero reachable feeds still exits 0, loudly, because a missed hour of news
must not take the collector down.

    python run_news_fetch.py --limit-per-feed 30

Layering: root CLI over the vouch.news leaf package.
"""

from __future__ import annotations

import argparse
import json
import os
from urllib.parse import urlsplit

import httpx

from vouch.news import DEFAULT_FEEDS, NewsEvent, NewsStore, fetch_feed

USER_AGENT = "vouch-news/0.1 (covenant forward-lane collector)"


def parse_feed_spec(spec: str) -> list[tuple[str, str]]:
    """Comma-separated 'Name|url' entries; a bare url gets its host as the
    source name (www stripped)."""
    feeds: list[tuple[str, str]] = []
    for part in spec.split(","):
        entry = part.strip()
        if not entry:
            continue
        if "|" in entry:
            name, _, url = entry.partition("|")
            feeds.append((name.strip(), url.strip()))
        else:
            host = urlsplit(entry).hostname or entry
            feeds.append((host.removeprefix("www."), entry))
    return feeds


def resolve_feeds(flag: str | None) -> list[tuple[str, str]]:
    """--feeds flag wins, then $VOUCH_NEWS_FEEDS, then the pinned defaults."""
    if flag:
        return parse_feed_spec(flag)
    env = os.environ.get("VOUCH_NEWS_FEEDS", "").strip()
    if env:
        return parse_feed_spec(env)
    return list(DEFAULT_FEEDS)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="fetch crypto news feeds into the forward-lane JSONL store"
    )
    ap.add_argument(
        "--feeds",
        default=None,
        help="comma-separated 'Name|url' (or bare url); default $VOUCH_NEWS_FEEDS, "
        "else the pinned defaults",
    )
    ap.add_argument("--store", default="out/news/events.jsonl", help="news events JSONL")
    ap.add_argument(
        "--limit-per-feed",
        type=int,
        default=None,
        help="cap kept per feed, feed order (newest first)",
    )
    a = ap.parse_args()

    feeds = resolve_feeds(a.feeds)
    if not feeds:
        raise SystemExit("run_news_fetch: no feeds given; use --feeds or VOUCH_NEWS_FEEDS")

    store = NewsStore(a.store)
    seen = {event.id for event in store.load()}
    pending: list[NewsEvent] = []
    failures = 0
    fetched_total = 0
    with httpx.Client(follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        for name, url in feeds:
            try:
                fetched = fetch_feed(client, name, url)
            except Exception as exc:  # noqa: BLE001 - a dead feed never kills the run
                failures += 1
                print(f"run_news_fetch: WARNING {name} ({url}) failed: {exc}", flush=True)
                continue
            if a.limit_per_feed is not None:
                fetched = fetched[: max(a.limit_per_feed, 0)]
            new = 0
            deduped = 0
            for event in fetched:
                if event.id in seen:
                    deduped += 1
                else:
                    seen.add(event.id)
                    new += 1
                    pending.append(event)
            fetched_total += len(fetched)
            print(f"{name}: fetched {len(fetched)}, new {new}, deduped {deduped}", flush=True)

    total = store.merge(pending)
    print(
        json.dumps(
            {
                "store": a.store,
                "feeds": len(feeds),
                "feeds_failed": failures,
                "fetched": fetched_total,
                "new": len(pending),
                "store_events": total,
            },
            indent=2,
        )
    )
    if failures == len(feeds):
        print(
            f"run_news_fetch: WARNING all {len(feeds)} feeds unreachable, collected nothing "
            f"this run; store unchanged at {total} events. Forward collection is gappy by "
            "design, so this stays exit 0.",
            flush=True,
        )


if __name__ == "__main__":
    main()
