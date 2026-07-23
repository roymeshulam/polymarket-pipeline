from __future__ import annotations

from datetime import datetime, timezone

from market_watcher import MarketSnapshot, MarketWatcher
from markets import Market


def _watcher() -> MarketWatcher:
    market = Market(
        "condition",
        "Will X happen?",
        "test",
        0.4,
        0.6,
        10_000,
        "",
        True,
        [
            {"token_id": "yes-token", "outcome": "Yes"},
            {"token_id": "no-token", "outcome": "No"},
        ],
    )
    watcher = MarketWatcher()
    watcher.snapshots[market.condition_id] = MarketSnapshot(
        market,
        0.4,
        0.4,
        datetime.now(timezone.utc),
    )
    return watcher


def test_handles_nested_price_change_event():
    watcher = _watcher()

    watcher._handle_ws_message({
        "event_type": "price_change",
        "market": "condition",
        "price_changes": [
            {"asset_id": "yes-token", "price": "0.45"},
        ],
    })

    assert watcher.snapshots["condition"].last_price == 0.45
    assert watcher.stats["price_updates"] == 1


def test_converts_no_token_price_to_yes_price():
    watcher = _watcher()

    watcher._handle_ws_message({
        "event_type": "last_trade_price",
        "market": "condition",
        "asset_id": "no-token",
        "price": "0.65",
    })

    assert watcher.snapshots["condition"].last_price == 0.35
    assert watcher.stats["price_updates"] == 1
