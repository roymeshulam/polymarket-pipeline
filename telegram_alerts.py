from __future__ import annotations

import asyncio
import logging

import httpx

import config
from edge import Signal

log = logging.getLogger(__name__)


def _alert_payload(signal: Signal, result: dict) -> dict:
    """Build a Telegram signal notification with a direct market button."""
    mode = "SIMULATED" if result["status"] == "dry_run" else "LIVE"
    source = signal.source_id or signal.news_source or "unknown"
    classification = signal.classification or "unknown"
    relation = signal.relation_level or "unknown"
    text = (
        f"🚨 {mode} EDGE TRADE\n\n"
        f"{signal.market.question}\n\n"
        f"Market price: YES={signal.market.yes_price:.2f} "
        f"NO={signal.market.no_price:.2f}\n"
        f"Model score: {signal.claude_score:.2f}\n"
        f"Direction: {classification.upper()} → {signal.side}\n"
        f"Edge: {signal.edge:.1%}\n"
        f"Amount: ${signal.bet_amount:.2f}\n"
        f"Status: {result['status']}\n"
        f"Source: {source}\n"
        f"Relation: {relation}\n"
        f"Confirmations: {signal.confirmation_count}/"
        f"{signal.required_confirmations}\n"
        f"Selected headline: {signal.headlines[:500] or '—'}\n"
        f"Reasoning: {signal.reasoning[:500] or '—'}"
    )
    payload: dict = {
        "chat_id": config.TELEGRAM_ALERT_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    if signal.market.url:
        payload["reply_markup"] = {
            "inline_keyboard": [[{
                "text": "Open market",
                "url": signal.market.url,
            }]]
        }
    return payload


def send_trade_alert(signal: Signal, result: dict) -> bool:
    """Send an alert for a simulated or posted edge trade."""
    if result.get("status") not in {"dry_run", "posted"}:
        return False
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_ALERT_CHAT_ID:
        return False

    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json=_alert_payload(signal, result),
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise ValueError("Telegram returned an unsuccessful response")
        return True
    except Exception as exc:
        # Never include the exception text: Telegram request URLs contain the bot token.
        log.warning("[telegram] Trade alert failed: %s", type(exc).__name__)
        return False


async def send_trade_alert_async(signal: Signal, result: dict) -> bool:
    """Send a trade alert without blocking the event loop."""
    return await asyncio.get_running_loop().run_in_executor(
        None,
        send_trade_alert,
        signal,
        result,
    )
