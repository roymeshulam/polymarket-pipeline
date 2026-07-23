from __future__ import annotations

import asyncio
import math
import re

import config
import logger
from edge import Signal
from markets import get_token_id

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
PRIVATE_KEY_RE = re.compile(r"^(0x)?[a-fA-F0-9]{64}$")


def validate_live_configuration() -> list[str]:
    """Return configuration errors that must block all live orders."""
    errors = []
    required = {
        "POLYMARKET_API_KEY": config.POLYMARKET_API_KEY,
        "POLYMARKET_API_SECRET": config.POLYMARKET_API_SECRET,
        "POLYMARKET_API_PASSPHRASE": config.POLYMARKET_API_PASSPHRASE,
        "POLYMARKET_PRIVATE_KEY": config.POLYMARKET_PRIVATE_KEY,
        "POLYMARKET_FUNDER_ADDRESS": config.POLYMARKET_FUNDER_ADDRESS,
    }
    errors.extend(f"{name} is required" for name, value in required.items() if not value)
    if config.POLYMARKET_PRIVATE_KEY and not PRIVATE_KEY_RE.fullmatch(config.POLYMARKET_PRIVATE_KEY):
        errors.append("POLYMARKET_PRIVATE_KEY is not a 32-byte hex key")
    if config.POLYMARKET_FUNDER_ADDRESS and not ADDRESS_RE.fullmatch(config.POLYMARKET_FUNDER_ADDRESS):
        errors.append("POLYMARKET_FUNDER_ADDRESS is not a valid address")
    if config.POLYMARKET_SIGNATURE_TYPE not in {0, 1, 2}:
        errors.append("POLYMARKET_SIGNATURE_TYPE must be 0, 1, or 2")
    if config.LIVE_TRADING_ACK.lower() != config.POLYMARKET_FUNDER_ADDRESS.lower():
        errors.append("LIVE_TRADING_ACK must exactly match POLYMARKET_FUNDER_ADDRESS")
    if not config.LIVE_ALLOWED_NEWS_SOURCES:
        errors.append("LIVE_ALLOWED_NEWS_SOURCES must explicitly list reviewed sources")
    if not 0 < config.MAX_BET_USD <= config.MAX_OPEN_EXPOSURE_USD <= config.DAILY_LOSS_LIMIT_USD:
        errors.append("limits must satisfy MAX_BET <= MAX_OPEN_EXPOSURE <= DAILY_LOSS_LIMIT")
    if not 0 <= config.MAX_SLIPPAGE_BPS <= 500:
        errors.append("MAX_SLIPPAGE_BPS must be between 0 and 500")
    return errors


def execute_trade(signal: Signal) -> dict:
    """Execute a trade or log a fail-closed rejection."""
    if config.DRY_RUN:
        return _log_and_return(signal, "dry_run", None)

    errors = validate_live_configuration()
    if errors:
        return _log_and_return(signal, "rejected_live_config", None)
    if signal.news_source.lower() not in config.LIVE_ALLOWED_NEWS_SOURCES:
        return _log_and_return(signal, "rejected_news_source", None)
    if signal.news_latency_ms < 0 or signal.news_latency_ms > config.MAX_NEWS_AGE_SECONDS * 1000:
        return _log_and_return(signal, "rejected_stale_news", None)
    if not 0 < signal.bet_amount <= config.MAX_BET_USD:
        return _log_and_return(signal, "rejected_bet_size", None)

    reservation_id = logger.reserve_exposure(
        signal.bet_amount,
        config.DAILY_LOSS_LIMIT_USD,
        config.MAX_OPEN_EXPOSURE_USD,
    )
    if reservation_id is None:
        return _log_and_return(signal, "rejected_exposure_limit", None)
    return _execute_live(signal, reservation_id)


async def execute_trade_async(signal: Signal) -> dict:
    return await asyncio.get_running_loop().run_in_executor(None, execute_trade, signal)


def _build_client():
    from py_clob_client.client import ClobClient
    from py_clob_client.clob_types import ApiCreds

    creds = ApiCreds(
        api_key=config.POLYMARKET_API_KEY,
        api_secret=config.POLYMARKET_API_SECRET,
        api_passphrase=config.POLYMARKET_API_PASSPHRASE,
    )
    return ClobClient(
        host=config.POLYMARKET_HOST,
        key=config.POLYMARKET_PRIVATE_KEY,
        chain_id=137,
        creds=creds,
        signature_type=config.POLYMARKET_SIGNATURE_TYPE,
        funder=config.POLYMARKET_FUNDER_ADDRESS,
    )


def _execute_live(signal: Signal, reservation_id: int) -> dict:
    """Place a bounded GTC order using a fresh executable price."""
    try:
        from py_clob_client.clob_types import OrderArgs, OrderType
        BUY = "BUY"

        client = _build_client()
        token_id = get_token_id(signal.market, signal.side)
        if not token_id:
            raise ValueError("market has no verified outcome token")

        price_response = client.get_price(token_id, BUY)
        live_price = float(
            price_response.get("price") if isinstance(price_response, dict) else price_response
        )
        if not math.isfinite(live_price) or not 0.01 <= live_price <= 0.99:
            raise ValueError("invalid live price")

        reference_price = signal.market_price if signal.side == "YES" else 1.0 - signal.market_price
        max_price = reference_price * (1 + config.MAX_SLIPPAGE_BPS / 10_000)
        if live_price > max_price:
            logger.update_reservation(reservation_id, "released")
            return _log_and_return(signal, "rejected_slippage", None)

        limit_price = round(live_price, 4)
        shares = round(signal.bet_amount / limit_price, 2)
        if shares <= 0 or shares * limit_price > signal.bet_amount + 0.01:
            raise ValueError("invalid order notional")

        signed_order = client.create_order(
            OrderArgs(price=limit_price, size=shares, side=BUY, token_id=token_id)
        )
        response = client.post_order(signed_order, OrderType.GTC)
        order_id = response.get("orderID") or response.get("id")
        if not order_id:
            raise RuntimeError("CLOB did not return an order id")
        logger.update_reservation(reservation_id, "posted", str(order_id))
        return _log_and_return(signal, "posted", str(order_id))
    except ImportError:
        logger.update_reservation(reservation_id, "released")
        return _log_and_return(signal, "error_no_clob_client", None)
    except Exception as exc:
        logger.update_reservation(reservation_id, "released")
        return _log_and_return(signal, f"error_{type(exc).__name__}", None)


def _log_and_return(signal: Signal, status: str, order_id: str | None) -> dict:
    trade_id = logger.log_trade(
        market_id=signal.market.condition_id,
        market_question=signal.market.question,
        claude_score=signal.claude_score,
        market_price=signal.market_price,
        edge=signal.edge,
        side=signal.side,
        amount_usd=signal.bet_amount,
        order_id=order_id,
        status=status,
        reasoning=signal.reasoning,
        headlines=signal.headlines,
        news_source=signal.news_source,
        classification=signal.classification,
        materiality=signal.materiality,
        news_latency_ms=signal.news_latency_ms,
        classification_latency_ms=signal.classification_latency_ms,
        total_latency_ms=signal.total_latency_ms,
    )
    return {
        "trade_id": trade_id, "market": signal.market.question, "side": signal.side,
        "amount": signal.bet_amount, "edge": signal.edge, "status": status,
        "order_id": order_id, "classification": signal.classification,
        "materiality": signal.materiality, "latency_ms": signal.total_latency_ms,
    }
