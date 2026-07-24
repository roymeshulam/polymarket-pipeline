from __future__ import annotations

import httpx

import news_stream
from datetime import datetime, timezone

from news_stream import NewsAggregator, NewsEvent, TwitterStream


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
