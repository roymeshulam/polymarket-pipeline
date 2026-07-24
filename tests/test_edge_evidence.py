from __future__ import annotations

from datetime import datetime, timezone

import config
from classifier import Classification
from edge import detect_edge_v2
from markets import Market
from news_stream import NewsEvent


def _market() -> Market:
    return Market(
        "condition",
        "Will Israel strike Iran?",
        "israel",
        0.4,
        0.6,
        10_000,
        "",
        True,
        [],
    )


def _event() -> NewsEvent:
    now = datetime.now(timezone.utc)
    return NewsEvent(
        "ישראל תקפה באיראן",
        "rss",
        "",
        now,
        now,
        source_id="reviewed",
        relevance=0.9,
        max_age_seconds=120,
        required_confirmations=2,
        confirmation_count=2,
        allow_live=True,
    )


def test_topical_overlap_never_creates_signal():
    classification = Classification(
        "bullish",
        0.9,
        "Same topic only",
        5,
        "test",
        relation_level="topical",
        estimated_yes_probability=0.8,
    )

    assert detect_edge_v2(_market(), classification, _event()) is None


def test_resolution_evidence_carries_source_controls(monkeypatch):
    monkeypatch.setattr(config, "EDGE_THRESHOLD", 0.05)
    monkeypatch.setattr(config, "MATERIALITY_THRESHOLD", 0.5)
    classification = Classification(
        "bullish",
        0.9,
        "Directly meets the strike rule",
        5,
        "test",
        relation_level="resolution_evidence",
        estimated_yes_probability=0.9,
    )

    signal = detect_edge_v2(_market(), classification, _event())

    assert signal is not None
    assert signal.relation_level == "resolution_evidence"
    assert signal.confirmation_count == 2
    assert signal.source_allow_live
