from __future__ import annotations

import logging
from dataclasses import dataclass

import config
from markets import Market
from classifier import Classification
from news_stream import NewsEvent

log = logging.getLogger(__name__)

# Short display labels for evaluate_edge_v2's rejection_reason codes — shared by
# the terminal and web dashboards so a scanner row can show *why* a matched
# market didn't produce a signal instead of a generic "no edge".
REASON_LABELS: dict[str, str] = {
    "": "no data",
    "stale_news": "stale",
    "relation_level_topical": "topical",
    "relation_level_irrelevant": "irrelevant",
    "neutral_direction": "neutral",
    "materiality_below_threshold": "low materiality",
    "wrong_direction_vs_price": "wrong direction",
    "edge_below_threshold": "low edge",
}


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


def evaluate_edge_v2(
    market: Market,
    classification: Classification,
    news_event: NewsEvent,
) -> tuple[Signal | None, str, float]:
    """
    Compare the conservative model estimate with the executable market side.

    Topical overlap never becomes a signal. Unconfirmed probability evidence may
    appear in dry-run analytics, while the executor applies stricter live rules.

    Returns (signal_or_none, rejection_reason, computed_edge). rejection_reason is
    "" when a signal is produced; computed_edge is 0.0 whenever it could not be
    computed (rejected before the probability delta was known).
    """
    if not news_event.is_fresh():
        return None, "stale_news", 0.0

    if classification.relation_level not in {
        "resolution_evidence",
        "probability_evidence",
    }:
        return None, f"relation_level_{classification.relation_level}", 0.0

    if classification.direction == "neutral":
        return None, "neutral_direction", 0.0

    if classification.materiality < config.MATERIALITY_THRESHOLD:
        return None, "materiality_below_threshold", 0.0

    market_price = market.yes_price
    probability_delta = classification.estimated_yes_probability - market_price
    if classification.direction == "bullish" and probability_delta <= 0:
        return None, "wrong_direction_vs_price", 0.0
    if classification.direction == "bearish" and probability_delta >= 0:
        return None, "wrong_direction_vs_price", 0.0

    side = "YES" if probability_delta > 0 else "NO"
    edge = abs(probability_delta) * classification.materiality

    if edge < config.EDGE_THRESHOLD:
        return None, "edge_below_threshold", edge

    bet_amount = size_position(edge)
    total_latency = news_event.latency_ms + classification.latency_ms

    signal = Signal(
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
    return signal, "", edge


def detect_edge_v2(
    market: Market,
    classification: Classification,
    news_event: NewsEvent,
) -> Signal | None:
    """Backward-compatible wrapper around evaluate_edge_v2."""
    signal, _reason, _edge = evaluate_edge_v2(market, classification, news_event)
    return signal


def size_position(edge: float) -> float:
    """Quarter-Kelly position sizing. Capped at MAX_BET_USD."""
    fraction = edge * 0.25
    bankroll = config.DAILY_LOSS_LIMIT_USD * 10
    raw_size = bankroll * fraction
    return min(max(round(raw_size, 2), 1.0), config.MAX_BET_USD)
