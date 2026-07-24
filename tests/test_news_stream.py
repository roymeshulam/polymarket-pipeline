from __future__ import annotations

import asyncio
from contextlib import suppress
import httpx

import news_stream
from datetime import datetime, timezone

from news_stream import (
    NewsAggregator,
    NewsEvent,
    TwitterStream,
    confirmed_events_from_news_items,
)
from scraper import NewsItem
from source_config import SourceProfile


def test_twitter_rate_limit_uses_retry_after():
    response = httpx.Response(429, headers={"retry-after": "75"})

    assert TwitterStream._rate_limit_delay(response, fallback=1) == 75


def test_twitter_rate_limit_uses_reset_timestamp(monkeypatch):
    monkeypatch.setattr(news_stream.time, "time", lambda: 1_000)
    response = httpx.Response(
        429,
        headers={"x-rate-limit-reset": "1120"},
    )

    assert TwitterStream._rate_limit_delay(response, fallback=1) == 121


def test_twitter_rate_limit_has_safe_default():
    response = httpx.Response(429)

    assert TwitterStream._rate_limit_delay(response, fallback=1) == 60


def test_twitter_api_problem_title_exposes_safe_error_name():
    response = httpx.Response(402, json={"title": "CreditsDepleted"})

    assert TwitterStream._api_problem_title(response) == "CreditsDepleted"


def test_corroboration_counts_independent_groups_only():
    aggregator = NewsAggregator(output_queue=None)
    now = datetime.now(timezone.utc)
    first = NewsEvent(
        "איראן שיגרה טילים לעבר ישראל",
        "rss",
        "",
        now,
        now,
        source_id="publisher_rss",
        independence_group="publisher",
    )
    syndication = NewsEvent(
        "איראן שיגרה טילים לעבר ישראל",
        "twitter",
        "",
        now,
        now,
        source_id="publisher_x",
        independence_group="publisher",
    )
    independent = NewsEvent(
        "איראן שיגרה טילים לעבר ישראל",
        "telegram",
        "",
        now,
        now,
        source_id="official",
        independence_group="official",
    )

    assert aggregator._confirmation_count(first) == 1
    assert aggregator._confirmation_count(syndication) == 1
    assert aggregator._confirmation_count(independent) == 2


def test_batch_suppresses_unconfirmed_and_emits_one_confirmed_event():
    now = datetime.now(timezone.utc)
    first_profile = SourceProfile(
        "publisher_rss",
        "rss",
        "Publisher",
        independence_group="publisher",
        enabled=True,
        relevance=0.9,
        min_confirmations=2,
        topics=("israel", "security"),
        url="https://publisher.example/rss",
    )
    official_profile = SourceProfile(
        "official_rss",
        "rss",
        "Official",
        independence_group="official",
        enabled=True,
        relevance=1.0,
        trust_tier=1,
        min_confirmations=2,
        topics=("israel", "security"),
        url="https://official.example/rss",
    )
    first = NewsItem(
        "איראן שיגרה טילים לעבר ישראל",
        "Publisher",
        "",
        now,
        source_id="publisher_rss",
    )
    official = NewsItem(
        "איראן שיגרה טילים לעבר ישראל",
        "Official",
        "",
        now,
        source_id="official_rss",
    )

    assert confirmed_events_from_news_items([first], [first_profile]) == []

    confirmed = confirmed_events_from_news_items(
        [first, official],
        [first_profile, official_profile],
    )

    assert len(confirmed) == 1
    assert confirmed[0].source_id == "official_rss"
    assert confirmed[0].confirmation_count == 2


def test_stream_router_does_not_emit_until_independently_confirmed():
    async def scenario():
        output = asyncio.Queue()
        aggregator = NewsAggregator(output)
        now = datetime.now(timezone.utc)
        first = NewsEvent(
            "איראן שיגרה טילים לעבר ישראל",
            "rss",
            "",
            now,
            now,
            source_id="publisher",
            independence_group="publisher",
            relevance=0.9,
            required_confirmations=2,
        )
        independent = NewsEvent(
            "איראן שיגרה טילים לעבר ישראל",
            "telegram",
            "",
            now,
            now,
            source_id="official",
            independence_group="official",
            relevance=1.0,
            required_confirmations=2,
        )
        router = asyncio.create_task(aggregator._policy_router())
        try:
            await aggregator._internal_queue.put(first)
            await asyncio.sleep(0)
            assert output.empty()

            await aggregator._internal_queue.put(independent)
            emitted = await asyncio.wait_for(output.get(), timeout=0.5)
            assert emitted.source_id == "official"
            assert emitted.confirmation_count == 2
            assert aggregator.stats["unconfirmed"] == 1
        finally:
            router.cancel()
            with suppress(asyncio.CancelledError):
                await router

    asyncio.run(scenario())
