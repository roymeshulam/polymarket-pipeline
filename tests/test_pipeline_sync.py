from datetime import datetime, timezone

import config
import pipeline
from classifier import Classification
from markets import Market
from scraper import NewsItem
from source_config import SourceProfile


def _profile(*, min_confirmations: int, topics: tuple[str, ...]) -> SourceProfile:
    return SourceProfile(
        "reviewed_rss",
        "rss",
        "Reviewed RSS",
        independence_group="reviewed",
        enabled=True,
        relevance=0.9,
        trust_tier=1,
        min_confirmations=min_confirmations,
        topics=topics,
        url="https://reviewed.example/rss",
    )


def _item(headline: str) -> NewsItem:
    return NewsItem(
        headline,
        "Reviewed RSS",
        "https://reviewed.example/item",
        datetime.now(timezone.utc),
        source_id="reviewed_rss",
    )


def _stub_run_logging(monkeypatch):
    endings = []
    monkeypatch.setattr(pipeline.logger, "log_run_start", lambda: 1)
    monkeypatch.setattr(
        pipeline.logger,
        "log_run_end",
        lambda *args: endings.append(args),
    )
    monkeypatch.setattr(pipeline.logger, "log_news_event", lambda **kwargs: 1)
    return endings


def test_run_stops_before_markets_and_openai_when_news_is_unconfirmed(
    monkeypatch,
):
    endings = _stub_run_logging(monkeypatch)
    monkeypatch.setattr(
        config,
        "SOURCE_PROFILES",
        [_profile(min_confirmations=2, topics=("israel", "security"))],
    )
    monkeypatch.setattr(
        pipeline,
        "scrape_all",
        lambda lookback: [_item("ישראל הודיעה על עדכון ביטחוני")],
    )
    monkeypatch.setattr(
        pipeline,
        "fetch_target_markets",
        lambda: (_ for _ in ()).throw(
            AssertionError("unconfirmed news must not reach market discovery")
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "classify_event",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("unconfirmed news must not reach OpenAI")
        ),
    )

    assert pipeline.run_pipeline(max_markets=5, lookback_hours=1) == []
    assert endings[-1][-1] == "no_confirmed_news"


def test_run_classifies_one_exact_confirmed_event(monkeypatch):
    _stub_run_logging(monkeypatch)
    headline = "ישראל סגרה את המרחב האווירי לטיסות מסחריות"
    profile = _profile(min_confirmations=1, topics=("israel", "aviation"))
    market = Market(
        "condition",
        "Will Israel close its airspace by July 31?",
        "israel",
        0.4,
        0.6,
        10_000,
        "",
        True,
        [],
        rules="A broad closure of commercial aviation qualifies.",
    )
    classified_headlines = []
    alerted_signals = []

    monkeypatch.setattr(config, "SOURCE_PROFILES", [profile])
    monkeypatch.setattr(config, "MARKET_MATCH_THRESHOLD", 0.1)
    monkeypatch.setattr(config, "EDGE_THRESHOLD", 0.05)
    monkeypatch.setattr(config, "MATERIALITY_THRESHOLD", 0.5)
    monkeypatch.setattr(
        pipeline,
        "scrape_all",
        lambda lookback: [_item(headline)],
    )
    monkeypatch.setattr(pipeline, "fetch_target_markets", lambda: [market])
    monkeypatch.setattr(
        pipeline,
        "filter_by_categories",
        lambda markets, categories: markets,
    )

    def classify(event, matched_market):
        classified_headlines.append(event.headline)
        assert matched_market is market
        return Classification(
            "bullish",
            1.0,
            "Direct official closure evidence.",
            5,
            "test",
            relation_level="resolution_evidence",
            estimated_yes_probability=0.8,
        )

    monkeypatch.setattr(pipeline, "classify_event", classify)
    monkeypatch.setattr(
        pipeline,
        "execute_trade",
        lambda signal: {
            "status": "dry_run",
            "market": signal.market.question,
            "side": signal.side,
            "amount": signal.bet_amount,
        },
    )
    monkeypatch.setattr(
        pipeline,
        "send_trade_alert",
        lambda signal, result: alerted_signals.append(signal),
    )

    results = pipeline.run_pipeline(max_markets=5, lookback_hours=1)

    assert len(results) == 1
    assert classified_headlines == [headline]
    assert alerted_signals[0].headlines == headline
    assert alerted_signals[0].relation_level == "resolution_evidence"
    assert alerted_signals[0].confirmation_count == 1
