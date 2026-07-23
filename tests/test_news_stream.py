from __future__ import annotations

import httpx

import news_stream
from news_stream import TwitterStream


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
