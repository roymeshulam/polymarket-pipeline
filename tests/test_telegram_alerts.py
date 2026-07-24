from __future__ import annotations

import config
import telegram_alerts
from edge import Signal
from markets import Market
from telegram_alerts import _alert_payload


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"ok": True}


def _signal() -> Signal:
    market = Market(
        "condition",
        "Will Core CPI MoM be 0.1% in July?",
        "economics",
        0.26,
        0.74,
        25_000,
        "",
        True,
        [],
        "https://polymarket.com/event/core-cpi/core-cpi-july",
    )
    return Signal(
        market,
        0.43,
        0.26,
        0.17,
        "YES",
        1.0,
        "The report materially supports YES.",
        "Core CPI rose 0.1% in July.",
        news_source="twitter",
        classification="bullish",
        materiality=0.8,
    )


def test_sends_dry_run_alert_with_market_button(monkeypatch):
    request: dict = {}
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setattr(config, "TELEGRAM_ALERT_CHAT_ID", "12345")

    def fake_post(url, **kwargs):
        request["url"] = url
        request.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(telegram_alerts.httpx, "post", fake_post)

    sent = telegram_alerts.send_trade_alert(
        _signal(),
        {"status": "dry_run"},
    )

    assert sent
    assert request["json"]["chat_id"] == "12345"
    assert "SIMULATED EDGE TRADE" in request["json"]["text"]
    assert request["json"]["reply_markup"]["inline_keyboard"][0][0] == {
        "text": "Open market",
        "url": "https://polymarket.com/event/core-cpi/core-cpi-july",
    }


def test_does_not_alert_for_rejected_trade(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setattr(config, "TELEGRAM_ALERT_CHAT_ID", "12345")
    monkeypatch.setattr(
        telegram_alerts.httpx,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Telegram must not be called")
        ),
    )

    assert not telegram_alerts.send_trade_alert(
        _signal(),
        {"status": "rejected_slippage"},
    )


def test_alert_contains_only_selected_evidence_and_policy_metadata():
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
    )
    signal = Signal(
        market=market,
        claude_score=0.8,
        market_price=0.4,
        edge=0.3,
        side="YES",
        bet_amount=5.0,
        reasoning="Official aviation authority announced a broad closure.",
        headlines="ישראל סגרה את המרחב האווירי לטיסות מסחריות",
        news_source="rss",
        classification="bullish",
        source_id="official_aviation",
        confirmation_count=2,
        required_confirmations=2,
        relation_level="resolution_evidence",
    )

    text = _alert_payload(signal, {"status": "dry_run"})["text"]

    assert "Relation: resolution_evidence" in text
    assert "Confirmations: 2/2" in text
    assert "Source: official_aviation" in text
    assert (
        "Selected headline: ישראל סגרה את המרחב האווירי לטיסות מסחריות"
        in text
    )
    assert "Headline:" not in text.replace("Selected headline:", "")
