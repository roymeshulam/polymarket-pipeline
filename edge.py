from __future__ import annotations

from dataclasses import dataclass

import config
from markets import Market
from classifier import Classification
from news_stream import NewsEvent


@dataclass
class Signal:
    market: Market
    claude_score: float
    market_price: float
    edge: float
    side: str  # "YES" or "NO"
    bet_amount: float
    reasoning: str
    headlines: str
    # V2 fields
    news_source: str = ""
    classification: str = ""
    materiality: float = 0.0
    news_latency_ms: int = 0
    classification_latency_ms: int = 0
    total_latency_ms: int = 0
    source_id: str = ""
    source_max_age_seconds: int = 0
    source_allow_live: bool = False
    confirmation_count: int = 1
    required_confirmations: int = 1
    relation_level: str = ""
    news_age_seconds: float = 0.0


def detect_edge_v2(
    market: Market,
    classification: Classification,
    news_event: NewsEvent,
) -> Signal | None:
    """
    Compare the conservative model estimate with the executable market side.

    Topical overlap never becomes a signal. Unconfirmed probability evidence may
    appear in dry-run analytics, while the executor applies stricter live rules.
    """
    if not news_event.is_fresh():
        return None

    if classification.relation_level not in {
        "resolution_evidence",
        "probability_evidence",
    }:
        return None

    if classification.direction == "neutral":
        return None

    if classification.materiality < config.MATERIALITY_THRESHOLD:
        return None

    market_price = market.yes_price
    probability_delta = classification.estimated_yes_probability - market_price
    if classification.direction == "bullish" and probability_delta <= 0:
        return None
    if classification.direction == "bearish" and probability_delta >= 0:
        return None

    side = "YES" if probability_delta > 0 else "NO"
    edge = abs(probability_delta) * classification.materiality

    if edge < config.EDGE_THRESHOLD:
        return None

    bet_amount = size_position(edge)
    total_latency = news_event.latency_ms + classification.latency_ms

    return Signal(
        market=market,
        claude_score=classification.estimated_yes_probability,
        market_price=market_price,
        edge=edge,
        side=side,
        bet_amount=bet_amount,
        reasoning=classification.reasoning,
        headlines=news_event.headline,
        news_source=news_event.source_id or news_event.source,
        classification=classification.direction,
        materiality=classification.materiality,
        news_latency_ms=news_event.latency_ms,
        classification_latency_ms=classification.latency_ms,
        total_latency_ms=total_latency,
        source_id=news_event.source_id,
        source_max_age_seconds=news_event.max_age_seconds,
        source_allow_live=news_event.allow_live,
        confirmation_count=news_event.confirmation_count,
        required_confirmations=news_event.required_confirmations,
        relation_level=classification.relation_level,
        news_age_seconds=news_event.age_seconds(),
    )


def size_position(edge: float) -> float:
    """Quarter-Kelly position sizing. Capped at MAX_BET_USD."""
    fraction = edge * 0.25
    bankroll = config.DAILY_LOSS_LIMIT_USD * 10
    raw_size = bankroll * fraction
    return min(max(round(raw_size, 2), 1.0), config.MAX_BET_USD)
