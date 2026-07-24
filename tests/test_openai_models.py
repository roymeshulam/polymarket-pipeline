from __future__ import annotations

from types import SimpleNamespace

import classifier
import config
from markets import Market


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
        '{"relation_level":"probability_evidence","direction":"bullish",'
        '"materiality":0.8,"estimated_yes_probability":0.7,'
        '"reasoning":"Material news."}'
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


def test_classifier_cannot_turn_irrelevant_absence_into_no_signal(monkeypatch):
    responses = FakeResponses(
        '{"relation_level":"irrelevant","direction":"bearish",'
        '"materiality":0.9,"estimated_yes_probability":0.05,'
        '"reasoning":"The report does not mention the event."}'
    )
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(
        classifier,
        "OpenAI",
        lambda api_key: SimpleNamespace(responses=responses),
    )

    result = classifier.classify("Unrelated report", _market(), "wire")

    assert result.direction == "neutral"
    assert result.materiality == 0.0
    assert result.estimated_yes_probability == _market().yes_price
