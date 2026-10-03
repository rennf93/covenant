"""Crypto news feeds (forward lane): RSS/Atom into the pinned event schema.

docs/v5-phase1-spec.md "Chunk C": the navigator/judge lane's future input,
NOT a training-state feature (RSS cannot backfill 7 months). Parsing is
stdlib-only (xml.etree + email.utils for RFC-822 dates); events are
content-addressed on the URL so the same story arriving via two feeds
dedupes in the store. Item-level tolerance: a malformed item (bad date, no
link) is skipped with a loud count, because news XML is third-party output
and one bad item must not drop the whole fetch. Feed-level failure (HTTP,
broken XML) raises: the caller warns per feed and continues.

Layering: leaf (imports nothing from vouch); analysis (judge documents) and
the run_news_fetch CLI sit above it.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

DEFAULT_FEEDS: tuple[tuple[str, str], ...] = (
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("CryptoSlate", "https://cryptoslate.com/feed/"),
)
FEED_TIMEOUT_S = 20

_CURRENCY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("SOL", ("sol", "solana")),
    ("BTC", ("btc", "bitcoin")),
    ("ETH", ("eth", "ethereum")),
)


@dataclass(frozen=True)
class NewsEvent:
    """One pinned-schema event (docs/v5-phase1-spec.md, news JSONL)."""

    id: str  # sha256(url)[:16], content address
    title: str
    url: str
    source: str
    published_at: str  # ISO8601 UTC
    currencies: tuple[str, ...]
    kind: str  # "news" in v1; the schema grows only forward
    via: str  # "rss" | "cryptopanic" | "gdelt"

    def to_row(self) -> dict[str, Any]:
        """The pinned JSON object, fields in schema order (key order is
        part of the format: reruns must stay byte-identical)."""
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published_at": self.published_at,
            "currencies": list(self.currencies),
            "kind": self.kind,
            "via": self.via,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> NewsEvent:
        """Pinned fields required; unknown extra keys are ignored so the
        schema can grow forward without breaking old readers."""
        return cls(
            id=str(row["id"]),
            title=str(row["title"]),
            url=str(row["url"]),
            source=str(row["source"]),
            published_at=str(row["published_at"]),
            currencies=tuple(str(c) for c in row["currencies"]),
            kind=str(row["kind"]),
            via=str(row["via"]),
        )


def event_id(url: str) -> str:
    """Content address on the URL (pinned): the same story via two feeds
    collapses to one store row."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def tag_currencies(title: str, url: str) -> tuple[str, ...]:
    """Pinned v1 rule: lowercase substring match over title+url, in the
    fixed SOL/BTC/ETH order, else the CRYPTO catch-all."""
    text = f"{title} {url}".lower()
    tags = tuple(tag for tag, needles in _CURRENCY_RULES if any(n in text for n in needles))
    return tags or ("CRYPTO",)


def make_event(source: str, title: str, url: str, published: datetime) -> NewsEvent:
    """Build an event, normalizing published to ISO8601 UTC (a naive stamp
    is taken as UTC; RFC-822 offsets like -0500 convert)."""
    utc = published if published.tzinfo is not None else published.replace(tzinfo=UTC)
    return NewsEvent(
        id=event_id(url),
        title=title,
        url=url,
        source=source,
        published_at=utc.astimezone(UTC).isoformat(),
        currencies=tag_currencies(title, url),
        kind="news",
        via="rss",
    )


def event_time(event: NewsEvent) -> datetime:
    """published_at as a datetime (store sort, judge-doc windows)."""
    return datetime.fromisoformat(event.published_at)


def parse_feed(source: str, raw: bytes) -> list[NewsEvent]:
    """Parse one RSS 2.0 or Atom document into pinned-schema events.

    Malformed items are skipped with a loud count, never a crash; an
    unrecognizable document raises so the caller can fail that feed only."""
    root = ET.fromstring(raw)
    root_name = _local(root.tag)
    if root_name == "rss":
        channel = _child(root, "channel")
        if channel is None:
            raise ValueError(f"{source}: rss document without a channel")
        entries = [el for el in channel if _local(el.tag) == "item"]
    elif root_name == "feed":
        entries = [el for el in root if _local(el.tag) == "entry"]
    else:
        raise ValueError(f"{source}: unsupported feed root <{root.tag}>, expected rss or Atom")

    events: list[NewsEvent] = []
    skipped = 0
    for entry in entries:
        event = _entry_event(source, entry)
        if event is None:
            skipped += 1
        else:
            events.append(event)
    if skipped:
        print(f"news: {source}: skipped {skipped} malformed item(s)", flush=True)
    return events


def fetch_feed(client: httpx.Client, source: str, url: str) -> list[NewsEvent]:
    """Fetch one feed; raises on transport or HTTP errors so the caller can
    warn and move on to the next feed."""
    r = client.get(url, timeout=FEED_TIMEOUT_S)
    r.raise_for_status()
    return parse_feed(source, r.content)


def _entry_event(source: str, entry: ET.Element) -> NewsEvent | None:
    """Build one event; None when the item is unusable (no title/url, or a
    date that will not parse)."""
    title = _text(_child(entry, "title"))
    url = _link(entry)
    if not title or not url:
        return None
    try:
        if _local(entry.tag) == "item":  # RSS 2.0: RFC-822 dates
            published = parsedate_to_datetime(_text(_child(entry, "pubDate")))
        else:  # Atom: ISO8601, published preferred over updated
            stamp = _text(_child(entry, "published")) or _text(_child(entry, "updated"))
            published = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return make_event(source, title, url, published)


def _link(entry: ET.Element) -> str:
    """RSS: <link> text. Atom: the rel=alternate href, else the first href."""
    if _local(entry.tag) == "item":
        return _text(_child(entry, "link"))
    fallback = ""
    for el in entry:
        if _local(el.tag) != "link":
            continue
        href = el.get("href", "").strip()
        if not href:
            continue
        if el.get("rel", "alternate") == "alternate":
            return href
        if not fallback:
            fallback = href
    return fallback


def _local(tag: str) -> str:
    """Local name of a possibly namespaced tag (Atom lives in its ns)."""
    return tag.rsplit("}", 1)[-1]


def _child(parent: ET.Element, name: str) -> ET.Element | None:
    for el in parent:
        if _local(el.tag) == name:
            return el
    return None


def _text(el: ET.Element | None) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""
