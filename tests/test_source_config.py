from __future__ import annotations

import json

import pytest

from source_config import load_source_profiles


def test_loads_source_specific_policy(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "id": "hebrew_wire",
                        "kind": "rss",
                        "name": "Hebrew wire",
                        "enabled": True,
                        "url": "https://example.test/feed.xml",
                        "max_age_seconds": 90,
                        "relevance": 0.9,
                        "trust_tier": 2,
                        "min_confirmations": 2,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    profile = load_source_profiles(path)[0]

    assert profile.source_id == "hebrew_wire"
    assert profile.independence_group == "hebrew_wire"
    assert profile.max_age_seconds == 90
    assert profile.relevance == 0.9
    assert profile.min_confirmations == 2


def test_rejects_low_trust_live_source(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "id": "rumors",
                        "kind": "rss",
                        "name": "Rumors",
                        "enabled": True,
                        "url": "https://example.test/feed.xml",
                        "trust_tier": 4,
                        "allow_live": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="trust tiers"):
        load_source_profiles(path)


def test_rejects_twitter_query_that_can_include_replies_or_retweets(tmp_path):
    path = tmp_path / "sources.json"
    path.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "id": "news_account",
                        "kind": "twitter",
                        "name": "News account",
                        "enabled": True,
                        "query": "from:NewsAccount",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="exclude replies and retweets"):
        load_source_profiles(path)
