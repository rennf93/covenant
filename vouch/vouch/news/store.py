"""The news event store: content-addressed JSONL, merged on every rerun.

News feeds are inherently gappy, so unlike bars this store has NO
grid-refusal policy (that would make every fetch a refusal): rows are one
pinned-schema JSON object per line, sorted by published_at, and reruns
MERGE - load what is there, add the new, dedupe by id, re-sort. Consumers
(judge documents) handle gaps themselves.

Layering: leaf, sits on feed.py only.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from vouch.news.feed import NewsEvent, event_time


class NewsStore:
    """JSONL file of news events under a path (default out/news/events.jsonl)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> list[NewsEvent]:
        """Read all events in file order. Malformed lines warn and are
        skipped: a torn write or hand edit must not kill forward collection."""
        if not self.path.exists():
            return []
        events: list[NewsEvent] = []
        with open(self.path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(NewsEvent.from_row(json.loads(line)))
                except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                    print(
                        f"news store {self.path}: skipping malformed line {lineno}",
                        flush=True,
                    )
        return events

    def merge(self, events: Iterable[NewsEvent]) -> int:
        """Merge events in, dedupe by id (rows already on disk win), re-sort
        by published_at, rewrite the file. Returns the row count."""
        by_id: dict[str, NewsEvent] = {}
        for event in [*self.load(), *events]:
            by_id.setdefault(event.id, event)
        merged = sorted(by_id.values(), key=lambda e: (event_time(e), e.id))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            for event in merged:
                f.write(json.dumps(event.to_row(), ensure_ascii=False) + "\n")
        return len(merged)
