"""Policy-driven RSS, X, and Telegram ingestion for Hebrew news."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

import config
from matcher import event_fingerprint, normalize_text
from scraper import NewsItem, scrape_rss_profile
from source_config import SourceProfile, profile_map, profiles_by_kind

log = logging.getLogger(__name__)


@dataclass
class NewsEvent:
    headline: str
    source: str  # adapter kind: rss, twitter, telegram
    url: str
    received_at: datetime
    published_at: datetime
    summary: str = ""
    raw_data: dict = field(default_factory=dict)
    latency_ms: int = 0
    source_id: str = ""
    source_name: str = ""
    independence_group: str = ""
    language: str = "he"
    relevance: float = 0.5
    trust_tier: int = 3
    max_age_seconds: int = 300
    required_confirmations: int = 2
    confirmation_count: int = 1
    allow_live: bool = False
    topics: tuple[str, ...] = field(default_factory=tuple)

    def age_seconds(self) -> float:
        return max(
            0.0,
            (datetime.now(timezone.utc) - self.published_at).total_seconds(),
        )

    def is_fresh(self) -> bool:
        """Apply this source's own validity window."""
        return self.age_seconds() <= self.max_age_seconds

    def is_confirmed(self) -> bool:
        return self.confirmation_count >= self.required_confirmations

    def is_live_eligible(self) -> bool:
        return self.allow_live and self.is_fresh() and self.is_confirmed()


def _event_from_profile(
    profile: SourceProfile,
    *,
    headline: str,
    url: str,
    published_at: datetime,
    summary: str = "",
    raw_data: dict | None = None,
) -> NewsEvent:
    now = datetime.now(timezone.utc)
    return NewsEvent(
        headline=headline,
        source=profile.kind,
        source_id=profile.source_id,
        source_name=profile.name,
        independence_group=profile.independence_group,
        language=profile.language,
        url=url,
        received_at=now,
        published_at=published_at,
        summary=summary,
        raw_data=raw_data or {},
        latency_ms=max(0, int((now - published_at).total_seconds() * 1000)),
        relevance=profile.relevance,
        trust_tier=profile.trust_tier,
        max_age_seconds=profile.max_age_seconds,
        required_confirmations=profile.min_confirmations,
        allow_live=profile.allow_live,
        topics=profile.topics,
    )


def confirmed_events_from_news_items(
    items: list[NewsItem],
    profiles: list[SourceProfile] | None = None,
) -> list[NewsEvent]:
    """Convert a synchronous RSS batch and retain only policy-approved events."""
    configured_profiles = profile_map(
        config.SOURCE_PROFILES if profiles is None else profiles
    )
    eligible: list[NewsEvent] = []

    for item in items:
        profile = configured_profiles.get(item.source_id)
        if profile is None or not profile.enabled:
            continue
        event = _event_from_profile(
            profile,
            headline=item.headline,
            url=item.url,
            published_at=item.published_at,
            summary=item.summary,
        )
        if event.is_fresh() and event.relevance >= config.MIN_SOURCE_RELEVANCE:
            eligible.append(event)

    groups_by_fingerprint: dict[str, set[str]] = defaultdict(set)
    for event in eligible:
        fingerprint = event_fingerprint(event.headline, event.summary)
        if fingerprint:
            groups_by_fingerprint[fingerprint].add(
                event.independence_group or event.source_id
            )

    confirmed_by_fingerprint: dict[str, NewsEvent] = {}
    for event in eligible:
        fingerprint = event_fingerprint(event.headline, event.summary)
        event.confirmation_count = len(groups_by_fingerprint.get(fingerprint, set()))
        if not event.is_confirmed():
            continue
        current = confirmed_by_fingerprint.get(fingerprint)
        if current is None or (
            event.trust_tier,
            -event.relevance,
            -event.published_at.timestamp(),
        ) < (
            current.trust_tier,
            -current.relevance,
            -current.published_at.timestamp(),
        ):
            confirmed_by_fingerprint[fingerprint] = event
    return sorted(
        confirmed_by_fingerprint.values(),
        key=lambda event: event.published_at,
        reverse=True,
    )


class TwitterStream:
    """X API v2 filtered stream with one tagged rule per source profile."""

    def __init__(
        self,
        bearer_token: str,
        profiles: list[SourceProfile] | list[str],
    ):
        self.bearer_token = bearer_token
        # Preserve the small rate-limit unit tests that construct keyword lists.
        resolved_profiles: list[SourceProfile]
        if profiles and all(isinstance(profile, str) for profile in profiles):
            keywords = [
                profile for profile in profiles if isinstance(profile, str)
            ]
            resolved_profiles = [
                SourceProfile(
                    source_id="legacy_test",
                    kind="twitter",
                    name="Legacy test rule",
                    enabled=True,
                    query=" OR ".join(f'"{value}"' for value in keywords),
                )
            ]
        elif all(isinstance(profile, SourceProfile) for profile in profiles):
            resolved_profiles = [
                profile
                for profile in profiles
                if isinstance(profile, SourceProfile)
            ]
        else:
            raise TypeError(
                "TwitterStream profiles must contain only SourceProfile "
                "instances or only strings"
            )

        self.profiles = resolved_profiles
        self._profiles = profile_map(self.profiles)
        self.base_url = "https://api.x.com/2"
        self.rule_tag_prefix = "israel_pipeline:"
        self.enabled = bool(bearer_token) and bool(self.profiles)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.bearer_token}"}

    @staticmethod
    def _rate_limit_delay(response: httpx.Response, fallback: int) -> int:
        retry_after = response.headers.get("retry-after")
        if retry_after:
            try:
                return max(1, min(900, int(float(retry_after))))
            except ValueError:
                pass
        reset_at = response.headers.get("x-rate-limit-reset")
        if reset_at:
            try:
                remaining = int(float(reset_at) - time.time()) + 1
                return max(1, min(900, remaining))
            except ValueError:
                pass
        return max(60, min(900, fallback))

    async def setup_rules(self) -> None:
        if not self.enabled:
            return
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/tweets/search/stream/rules",
                headers=self._headers(),
                timeout=15,
            )
            response.raise_for_status()
            existing = response.json().get("data", [])
            managed_ids = [
                rule["id"]
                for rule in existing
                if str(rule.get("tag", "")).startswith(self.rule_tag_prefix)
            ]
            if managed_ids:
                deletion = await client.post(
                    f"{self.base_url}/tweets/search/stream/rules",
                    headers=self._headers(),
                    json={"delete": {"ids": managed_ids}},
                    timeout=15,
                )
                deletion.raise_for_status()

            rules = [
                {
                    "value": profile.query,
                    "tag": f"{self.rule_tag_prefix}{profile.source_id}",
                }
                for profile in self.profiles
            ]
            addition = await client.post(
                f"{self.base_url}/tweets/search/stream/rules",
                headers=self._headers(),
                json={"add": rules},
                timeout=15,
            )
            addition.raise_for_status()

    def _matching_profile(self, payload: dict) -> SourceProfile | None:
        tags = {
            str(rule.get("tag", "")).removeprefix(self.rule_tag_prefix)
            for rule in payload.get("matching_rules", [])
            if isinstance(rule, dict)
        }
        matches = [self._profiles[tag] for tag in tags if tag in self._profiles]
        return max(matches, key=lambda item: item.relevance) if matches else None

    async def stream(self, queue: asyncio.Queue) -> None:
        if not self.enabled:
            log.info("[twitter] No enabled profiles or bearer token")
            return
        try:
            await self.setup_rules()
        except Exception as exc:
            log.warning("[twitter] Rule setup failed: %s", type(exc).__name__)
            return

        backoff = 1
        while True:
            try:
                async with httpx.AsyncClient() as client:
                    async with client.stream(
                        "GET",
                        f"{self.base_url}/tweets/search/stream",
                        headers=self._headers(),
                        params={"tweet.fields": "created_at,author_id,text"},
                        timeout=None,
                    ) as response:
                        if response.status_code == 429:
                            delay = self._rate_limit_delay(response, backoff)
                            await asyncio.sleep(delay)
                            backoff = min(max(backoff * 2, delay), 900)
                            continue
                        response.raise_for_status()
                        backoff = 1
                        async for line in response.aiter_lines():
                            if not line.strip():
                                continue
                            try:
                                payload = json.loads(line)
                                profile = self._matching_profile(payload)
                                tweet = payload.get("data", {})
                                if profile is None or not tweet.get("text"):
                                    continue
                                created = tweet.get("created_at", "")
                                try:
                                    published = datetime.fromisoformat(
                                        created.replace("Z", "+00:00")
                                    )
                                except (ValueError, AttributeError):
                                    published = datetime.now(timezone.utc)
                                await queue.put(
                                    _event_from_profile(
                                        profile,
                                        headline=tweet["text"][:1000],
                                        url=f"https://x.com/i/status/{tweet.get('id', '')}",
                                        published_at=published,
                                        raw_data=payload,
                                    )
                                )
                            except (ValueError, TypeError, KeyError) as exc:
                                log.debug("[twitter] Parse error: %s", type(exc).__name__)
            except Exception as exc:
                log.warning(
                    "[twitter] Stream error %s; reconnecting in %ss",
                    type(exc).__name__,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)


class TelegramMonitor:
    """Monitor explicitly configured channels through the Telegram Bot API."""

    def __init__(self, bot_token: str, profiles: list[SourceProfile]):
        self.bot_token = bot_token
        self.profiles = {profile.channel_id: profile for profile in profiles}
        self.enabled = bool(bot_token) and bool(self.profiles)
        self.last_update_id = 0

    async def stream(self, queue: asyncio.Queue) -> None:
        if not self.enabled:
            log.info("[telegram] No enabled profiles or bot token")
            return
        base_url = f"https://api.telegram.org/bot{self.bot_token}"

        while True:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(
                        f"{base_url}/getUpdates",
                        params={"offset": self.last_update_id + 1, "timeout": 30},
                        timeout=35,
                    )
                    response.raise_for_status()
                    payload = response.json()
                for update in payload.get("result", []):
                    self.last_update_id = update["update_id"]
                    message = update.get("channel_post") or update.get("message") or {}
                    chat_id = str(message.get("chat", {}).get("id", ""))
                    profile = self.profiles.get(chat_id)
                    text = message.get("text") or message.get("caption") or ""
                    if profile is None or not text:
                        continue
                    timestamp = message.get("date")
                    published = (
                        datetime.fromtimestamp(timestamp, tz=timezone.utc)
                        if timestamp
                        else datetime.now(timezone.utc)
                    )
                    message_id = message.get("message_id", "")
                    username = message.get("chat", {}).get("username", "")
                    url = (
                        f"https://t.me/{username}/{message_id}"
                        if username and message_id
                        else ""
                    )
                    await queue.put(
                        _event_from_profile(
                            profile,
                            headline=text[:1500],
                            url=url,
                            published_at=published,
                            raw_data=update,
                        )
                    )
            except Exception as exc:
                # Never print an exception containing the bot-token URL.
                log.warning("[telegram] Request failed: %s", type(exc).__name__)
                await asyncio.sleep(5)


class RSSStream:
    """Poll one RSS profile at its own configured interval."""

    def __init__(self, profile: SourceProfile):
        self.profile = profile
        self._seen: set[str] = set()

    async def stream(self, queue: asyncio.Queue) -> None:
        while True:
            try:
                items = await asyncio.get_running_loop().run_in_executor(
                    None, scrape_rss_profile, self.profile
                )
                for item in items:
                    key = normalize_text(item.url or item.headline)
                    if key in self._seen:
                        continue
                    self._seen.add(key)
                    await queue.put(
                        _event_from_profile(
                            self.profile,
                            headline=item.headline,
                            url=item.url,
                            published_at=item.published_at,
                            summary=item.summary,
                        )
                    )
                if len(self._seen) > 5000:
                    self._seen = set(list(self._seen)[-2000:])
            except Exception as exc:
                log.warning(
                    "[rss:%s] Poll failed: %s",
                    self.profile.source_id,
                    type(exc).__name__,
                )
            await asyncio.sleep(self.profile.poll_interval_seconds)


class NewsAggregator:
    """Run adapters, apply source policy, corroborate, and deduplicate events."""

    def __init__(self, output_queue: asyncio.Queue):
        self.output_queue = output_queue
        self._internal_queue: asyncio.Queue = asyncio.Queue()
        self._seen: set[tuple[str, str]] = set()
        self._corroboration: dict[str, deque[tuple[float, str]]] = defaultdict(deque)

        self.twitter_profiles = profiles_by_kind(config.SOURCE_PROFILES, "twitter")
        self.telegram_profiles = profiles_by_kind(config.SOURCE_PROFILES, "telegram")
        self.rss_profiles = profiles_by_kind(config.SOURCE_PROFILES, "rss")
        self.twitter = TwitterStream(config.TWITTER_BEARER_TOKEN, self.twitter_profiles)
        self.telegram = TelegramMonitor(
            config.TELEGRAM_BOT_TOKEN,
            self.telegram_profiles,
        )
        self.rss_streams = [RSSStream(profile) for profile in self.rss_profiles]
        self.stats = {
            "twitter": 0,
            "telegram": 0,
            "rss": 0,
            "total": 0,
            "deduped": 0,
            "stale": 0,
            "low_relevance": 0,
            "unconfirmed": 0,
        }

    async def run(self) -> None:
        tasks = [
            self.twitter.stream(self._internal_queue),
            self.telegram.stream(self._internal_queue),
            *(stream.stream(self._internal_queue) for stream in self.rss_streams),
            self._policy_router(),
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

    def _confirmation_count(self, event: NewsEvent) -> int:
        fingerprint = event_fingerprint(event.headline, event.summary)
        if not fingerprint:
            return 1
        now = time.time()
        cutoff = now - config.CORROBORATION_WINDOW_SECONDS
        observations = self._corroboration[fingerprint]
        while observations and observations[0][0] < cutoff:
            observations.popleft()
        group = event.independence_group or event.source_id
        observations.append((now, group))
        return len({group_id for _, group_id in observations})

    async def _policy_router(self) -> None:
        while True:
            event: NewsEvent = await self._internal_queue.get()
            if not event.is_fresh():
                self.stats["stale"] += 1
                continue
            if event.relevance < config.MIN_SOURCE_RELEVANCE:
                self.stats["low_relevance"] += 1
                continue
            event.confirmation_count = self._confirmation_count(event)

            dedup_key = (
                event.source_id,
                normalize_text(event.url or event.headline)[:200],
            )
            if dedup_key in self._seen:
                self.stats["deduped"] += 1
                continue
            self._seen.add(dedup_key)

            if not event.is_confirmed():
                self.stats["unconfirmed"] += 1
                continue
            self.stats[event.source] = self.stats.get(event.source, 0) + 1
            self.stats["total"] += 1
            await self.output_queue.put(event)

            if len(self._seen) > 10000:
                self._seen = set(list(self._seen)[-5000:])
