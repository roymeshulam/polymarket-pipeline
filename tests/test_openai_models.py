from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import classifier
import config
import scorer
from markets import Market
from scraper import NewsItem


class FakeResponses:
    def __init__(self, output_text: str):
        self.output_text = output_text
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.output_text)


def _market() -> Market:
    return Market(
        "condition",
        "Will X happen?",
        "test",
        0.4,
        0.6,
        10_000,
        "",
        True,
        [],
    )


def test_classifier_uses_configured_openai_model(monkeypatch):
    responses = FakeResponses(
        '{"direction":"bullish","materiality":0.8,"reasoning":"Material news."}'
    )
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "OPENAI_MODEL", "test-model")
    monkeypatch.setattr(
        classifier,
        "OpenAI",
        lambda api_key: SimpleNamespace(responses=responses),
    )

    result = classifier.classify("Breaking news", _market(), "wire")

    assert result.direction == "bullish"
    assert result.materiality == 0.8
    assert result.model == "test-model"
    assert responses.calls[0]["model"] == "test-model"
    assert responses.calls[0]["max_output_tokens"] == 350


def test_scorer_uses_configured_openai_model(monkeypatch):
    responses = FakeResponses(
        '{"confidence":0.7,"reasoning":"Relevant news.","relevant_headlines":[0]}'
    )
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "OPENAI_MODEL", "test-model")
    monkeypatch.setattr(
        scorer,
        "OpenAI",
        lambda api_key: SimpleNamespace(responses=responses),
    )
    news = [
        NewsItem(
            "Breaking news",
            "wire",
            "",
            datetime.now(timezone.utc),
        )
    ]

    result = scorer.score_market(_market(), news)

    assert result["confidence"] == 0.7
    assert responses.calls[0]["model"] == "test-model"
    assert responses.calls[0]["max_output_tokens"] == 500
