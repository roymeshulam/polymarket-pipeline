"""RSS ingestion helpers driven by per-source editorial policies."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

import config
from source_config import SourceProfile, profiles_by_kind


@dataclass
class NewsItem:
    headline: str
    source: str
    url: str
    published_at: datetime
    summary: str = ""
    source_id: str = ""
    language: str = "he"

    def age_hours(self) -> float:
        delta = datetime.now(timezone.utc) - self.published_at
        return delta.total_seconds() / 3600


def scrape_rss(
    feed_url: str,
    lookback_hours: float,
    *,
    source_name: str = "",
    source_id: str = "",
    language: str = "he",
) -> list[NewsItem]:
    """Parse one RSS/Atom feed and return recent items."""
    try:
        response = httpx.get(
            feed_url,
            headers={"User-Agent": "IsraelEventIntelligence/1.0 (+RSS reader)"},
            follow_redirects=True,
            timeout=15,
        )
        response.raise_for_status()
        feed = feedparser.parse(response.content)
    except (httpx.HTTPError, OSError, ValueError):
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    resolved_name = source_name or feed.feed.get("title", feed_url)
    items: list[NewsItem] = []

    for entry in feed.entries:
        if getattr(entry, "published_parsed", None):
            published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        elif getattr(entry, "updated_parsed", None):
            published = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
        else:
            published = datetime.now(timezone.utc)

        if published < cutoff:
            continue

        headline = entry.get("title", "").strip()
        if not headline:
            continue
        items.append(
            NewsItem(
                headline=headline,
                source=resolved_name,
                source_id=source_id,
                language=language,
                url=entry.get("link", ""),
                published_at=published,
                summary=entry.get("summary", "")[:1000],
            )
        )
    return items


def scrape_rss_profile(
    profile: SourceProfile,
    lookback_hours: float | None = None,
) -> list[NewsItem]:
    """Scrape one configured RSS profile."""
    hours = lookback_hours
    if hours is None:
        hours = max(profile.max_age_seconds / 3600, 1 / 60)
    return scrape_rss(
        profile.url,
        hours,
        source_name=profile.name,
        source_id=profile.source_id,
        language=profile.language,
    )


def deduplicate(items: list[NewsItem]) -> list[NewsItem]:
    """Remove repeats from the same source while preserving corroboration."""
    seen: set[tuple[str, str]] = set()
    unique: list[NewsItem] = []
    for item in items:
        key = (item.source_id or item.source, item.headline.lower()[:120])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def scrape_all(lookback_hours: int | None = None) -> list[NewsItem]:
    """Scrape all enabled RSS profiles for synchronous inspection."""
    items: list[NewsItem] = []
    for profile in profiles_by_kind(config.SOURCE_PROFILES, "rss"):
        items.extend(scrape_rss_profile(profile, lookback_hours))
    unique = deduplicate(items)
    unique.sort(key=lambda item: item.published_at, reverse=True)
    return unique


if __name__ == "__main__":
    news = scrape_all()
    print(f"\n--- Scraped {len(news)} unique headlines ---\n")
    for item in news[:20]:
        print(f"  [{item.age_hours():.1f}h] [{item.source_id}] {item.headline}")
