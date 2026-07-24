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
_CLOB_CLIENT = None


def validate_live_configuration() -> list[str]:
    """Return configuration errors that must block all live orders."""
    errors = []
    required = {
        "POLYMARKET_PRIVATE_KEY": config.POLYMARKET_PRIVATE_KEY,
        "POLYMARKET_FUNDER_ADDRESS": config.POLYMARKET_FUNDER_ADDRESS,
    }
    errors.extend(f"{name} is required" for name, value in required.items() if not value)
    if config.POLYMARKET_PRIVATE_KEY and not PRIVATE_KEY_RE.fullmatch(config.POLYMARKET_PRIVATE_KEY):
        errors.append("POLYMARKET_PRIVATE_KEY is not a 32-byte hex key")
    if config.POLYMARKET_FUNDER_ADDRESS and not ADDRESS_RE.fullmatch(config.POLYMARKET_FUNDER_ADDRESS):
        errors.append("POLYMARKET_FUNDER_ADDRESS is not a valid address")
    if config.POLYMARKET_SIGNATURE_TYPE not in {0, 1, 2, 3}:
        errors.append("POLYMARKET_SIGNATURE_TYPE must be 0, 1, 2, or 3")
    if config.LIVE_TRADING_ACK.lower() != config.POLYMARKET_FUNDER_ADDRESS.lower():
        errors.append("LIVE_TRADING_ACK must exactly match POLYMARKET_FUNDER_ADDRESS")
    if not config.LIVE_SOURCE_ALLOWLIST:
        errors.append("LIVE_SOURCE_ALLOWLIST must explicitly list reviewed source IDs")
    configured_sources = {
        profile.source_id.lower(): profile for profile in config.SOURCE_PROFILES
    }
    for source_id in config.LIVE_SOURCE_ALLOWLIST:
        profile = configured_sources.get(source_id)
        if profile is None:
            errors.append(f"LIVE_SOURCE_ALLOWLIST contains unknown source: {source_id}")
        elif not profile.enabled or not profile.allow_live:
            errors.append(
                f"{source_id} must be enabled and allow_live in the source configuration"
            )
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
    source_id = (signal.source_id or signal.news_source).lower()
    profiles = {
        profile.source_id.lower(): profile for profile in config.SOURCE_PROFILES
    }
    profile = profiles.get(source_id)
    if (
        source_id not in config.LIVE_SOURCE_ALLOWLIST
        or profile is None
        or not profile.enabled
        or not profile.allow_live
        or profile.trust_tier > 2
        or profile.relevance < config.MIN_SOURCE_RELEVANCE
    ):
        return _log_and_return(signal, "rejected_news_source", None)
    if (
        profile.max_age_seconds <= 0
        or signal.news_age_seconds < 0
        or signal.news_age_seconds > profile.max_age_seconds
    ):
        return _log_and_return(signal, "rejected_stale_news", None)
    required_confirmations = max(
        profile.min_confirmations,
        signal.required_confirmations,
    )
    if signal.confirmation_count < required_confirmations:
        return _log_and_return(signal, "rejected_unconfirmed", None)
    if signal.relation_level != "resolution_evidence":
        return _log_and_return(signal, "rejected_non_resolution_evidence", None)
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
    """Build and cache an authenticated CLOB V2 client."""
    global _CLOB_CLIENT
    if _CLOB_CLIENT is not None:
        return _CLOB_CLIENT

    from py_clob_client_v2 import ClobClient

    bootstrap = ClobClient(
        host=config.POLYMARKET_HOST,
        key=config.POLYMARKET_PRIVATE_KEY,
        chain_id=config.POLYMARKET_CHAIN_ID,
        signature_type=config.POLYMARKET_SIGNATURE_TYPE,
        funder=config.POLYMARKET_FUNDER_ADDRESS,
    )
    creds = bootstrap.create_or_derive_api_key()
    _CLOB_CLIENT = ClobClient(
        host=config.POLYMARKET_HOST,
        key=config.POLYMARKET_PRIVATE_KEY,
        chain_id=config.POLYMARKET_CHAIN_ID,
        creds=creds,
        signature_type=config.POLYMARKET_SIGNATURE_TYPE,
        funder=config.POLYMARKET_FUNDER_ADDRESS,
    )
    return _CLOB_CLIENT


def _execute_live(signal: Signal, reservation_id: int) -> dict:
    """Place a bounded GTC order using a fresh executable price."""
    try:
        from py_clob_client_v2 import (
            OrderArgs,
            OrderType,
            PartialCreateOrderOptions,
            Side,
        )

        client = _build_client()
        token_id = get_token_id(signal.market, signal.side)
        if not token_id:
            raise ValueError("market has no verified outcome token")

        price_response = client.get_price(token_id, Side.BUY)
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

        tick_size = float(signal.market.tick_size or "0.01")
        if tick_size not in {0.1, 0.01, 0.005, 0.0025, 0.001, 0.0001}:
            raise ValueError("unsupported market tick size")
        tick_decimals = max(0, len(str(tick_size).split(".")[-1]))
        limit_price = round(round(live_price / tick_size) * tick_size, tick_decimals)
        shares = round(signal.bet_amount / limit_price, 2)
        if shares <= 0 or shares * limit_price > signal.bet_amount + 0.01:
            raise ValueError("invalid order notional")

        response = client.create_and_post_order(
            order_args=OrderArgs(
                price=limit_price,
                size=shares,
                side=Side.BUY,
                token_id=token_id,
            ),
            options=PartialCreateOrderOptions(
                tick_size=signal.market.tick_size or "0.01",
                neg_risk=signal.market.neg_risk,
            ),
            order_type=OrderType.GTC,
        )
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
