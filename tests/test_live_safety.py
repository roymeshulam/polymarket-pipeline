from __future__ import annotations

from datetime import datetime, timedelta, timezone

import config
import logger
from executor import execute_trade, validate_live_configuration
from markets import Market
from news_stream import NewsEvent
from edge import Signal


def _signal(source: str = "rss", latency_ms: int = 1000) -> Signal:
    market = Market("condition", "Will X happen?", "ai", 0.5, 0.5, 10_000, "", True, [])
    return Signal(
        market, 0.8, 0.5, 0.3, "YES", 5.0, "reason", "headline",
        news_source=source, classification="bullish", materiality=0.8,
        news_latency_ms=latency_ms,
    )


def test_live_configuration_fails_closed(monkeypatch):
    monkeypatch.setattr(config, "POLYMARKET_PRIVATE_KEY", "")
    assert validate_live_configuration()


def test_dry_run_never_reserves_exposure(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DRY_RUN", True)
    monkeypatch.setattr(logger, "DB_PATH", tmp_path / "trades.db")
    logger.init_db()
    result = execute_trade(_signal())
    assert result["status"] == "dry_run"
    assert logger.get_open_exposure() == 0


def test_reservations_enforce_limit_atomically(monkeypatch, tmp_path):
    monkeypatch.setattr(logger, "DB_PATH", tmp_path / "trades.db")
    logger.init_db()
    assert logger.reserve_exposure(6, 10, 10) is not None
    assert logger.reserve_exposure(5, 10, 10) is None


def test_news_freshness_uses_source_policy():
    now = datetime.now(timezone.utc)
    event = NewsEvent(
        "h",
        "rss",
        "",
        now,
        now - timedelta(seconds=61),
        max_age_seconds=60,
    )
    assert not event.is_fresh()
