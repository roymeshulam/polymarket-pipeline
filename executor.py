from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import datetime, timezone

import httpx

import config
import logger
from edge import Signal
from markets import get_token_id

ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
PRIVATE_KEY_RE = re.compile(r"^(0x)?[a-fA-F0-9]{64}$")
_CLOB_CLIENT = None
POSITIONS_API = "https://data-api.polymarket.com/positions"
MARKET_CLAIM_STALE_SECONDS = 300


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

    try:
        client = _build_client()
        market_claim_id, rejection = _claim_market_for_live_trade(
            client, signal.market.condition_id
        )
    except ImportError:
        return _log_and_return(signal, "error_no_clob_client", None)
    except Exception as exc:
        return _log_and_return(
            signal, f"error_market_state_{type(exc).__name__}", None
        )
    if market_claim_id is None:
        return _log_and_return(
            signal, rejection or "rejected_market_already_open", None
        )

    reservation_id = logger.reserve_exposure(
        signal.bet_amount,
        config.DAILY_LOSS_LIMIT_USD,
        config.MAX_OPEN_EXPOSURE_USD,
    )
    if reservation_id is None:
        logger.release_market_trade(signal.market.condition_id, market_claim_id)
        return _log_and_return(signal, "rejected_exposure_limit", None)
    if not logger.attach_market_exposure(
        signal.market.condition_id, market_claim_id, reservation_id
    ):
        logger.update_reservation(reservation_id, "released")
        logger.release_market_trade(signal.market.condition_id, market_claim_id)
        return _log_and_return(signal, "error_market_claim_lost", None)
    return _execute_live(signal, reservation_id, market_claim_id)


async def execute_trade_async(signal: Signal) -> dict:
    return await asyncio.get_running_loop().run_in_executor(None, execute_trade, signal)


def _create_or_derive_api_key(client):
    """Return CLOB credentials, suppressing expected create-to-derive noise."""
    sdk_logger = logging.getLogger("py_clob_client_v2.http_helpers.helpers")
    was_disabled = sdk_logger.disabled
    sdk_logger.disabled = True
    try:
        try:
            return client.create_or_derive_api_key()
        except Exception as exc:
            raise RuntimeError(
                "CLOB API credential creation and derivation both failed"
            ) from exc
    finally:
        sdk_logger.disabled = was_disabled


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
    creds = _create_or_derive_api_key(bootstrap)
    _CLOB_CLIENT = ClobClient(
        host=config.POLYMARKET_HOST,
        key=config.POLYMARKET_PRIVATE_KEY,
        chain_id=config.POLYMARKET_CHAIN_ID,
        creds=creds,
        signature_type=config.POLYMARKET_SIGNATURE_TYPE,
        funder=config.POLYMARKET_FUNDER_ADDRESS,
    )
    return _CLOB_CLIENT


def _get_open_positions(market_id: str) -> list[dict]:
    """Return non-zero positions for this wallet and condition from the Data API."""
    response = httpx.get(
        POSITIONS_API,
        params={
            "user": config.POLYMARKET_FUNDER_ADDRESS,
            "market": market_id,
            "sizeThreshold": 0,
            "limit": 500,
        },
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("positions API returned a non-list response")
    positions = []
    for position in payload:
        if not isinstance(position, dict):
            continue
        if str(position.get("conditionId", "")).lower() != market_id.lower():
            continue
        try:
            size = float(position.get("size", 0))
        except (TypeError, ValueError):
            raise ValueError("positions API returned an invalid position size")
        if size > 0:
            positions.append(position)
    return positions


def _get_all_positions() -> list[dict]:
    """All non-zero positions for the funder wallet, across every market."""
    response = httpx.get(
        POSITIONS_API,
        params={"user": config.POLYMARKET_FUNDER_ADDRESS, "sizeThreshold": 0, "limit": 500},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("positions API returned a non-list response")
    return [position for position in payload if isinstance(position, dict)]


def _get_collateral_balance_usd() -> float:
    """USDC collateral balance held by the funder wallet, via the CLOB API."""
    from py_clob_client_v2 import AssetType, BalanceAllowanceParams

    client = _build_client()
    response = client.get_balance_allowance(
        params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    )
    raw_balance = response.get("balance") if isinstance(response, dict) else response
    return float(raw_balance) / 1_000_000


def fetch_portfolio_snapshot() -> dict:
    """Read-only wallet balance + open (unrealized) P&L from Polymarket.

    Independent of DRY_RUN/live trading — this only reads account state and
    never places orders. Open P&L only needs POLYMARKET_FUNDER_ADDRESS (public
    Data API); balance additionally needs a working CLOB L2 auth (private key).
    """
    snapshot: dict = {"balance_usd": None, "open_pnl_usd": None, "error": None}
    if not config.POLYMARKET_FUNDER_ADDRESS:
        snapshot["error"] = "not_configured"
        return snapshot

    try:
        positions = _get_all_positions()
        snapshot["open_pnl_usd"] = sum(float(p.get("cashPnl", 0)) for p in positions)
    except Exception as exc:
        snapshot["error"] = f"positions_{type(exc).__name__}"

    try:
        snapshot["balance_usd"] = _get_collateral_balance_usd()
    except ImportError:
        snapshot["error"] = snapshot["error"] or "no_clob_client"
    except Exception as exc:
        snapshot["error"] = snapshot["error"] or f"balance_{type(exc).__name__}"

    return snapshot


def _remote_market_is_open(client, market_id: str) -> bool:
    """Return whether the wallet has an open order or position in this market."""
    from py_clob_client_v2 import OpenOrderParams

    if client.get_open_orders(params=OpenOrderParams(market=market_id)):
        return True
    return bool(_get_open_positions(market_id))


def _claim_age_seconds(lock: dict) -> float:
    updated_at = str(lock.get("updated_at") or lock.get("created_at") or "")
    if not updated_at:
        return float("inf")
    timestamp = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds())


def _claim_market_for_live_trade(client, market_id: str) -> tuple[str | None, str | None]:
    """Claim one market atomically and reconcile old locks with Polymarket."""
    lock = logger.get_market_trade_lock(market_id)
    if lock is None:
        claim_id = logger.reserve_market_trade(market_id)
        if claim_id is None:
            return None, "rejected_market_already_open"
    else:
        # A fresh reservation may not have reached Polymarket yet, and a newly
        # posted order may not be visible in the positions API immediately.
        # Never steal either lock during the reconciliation grace period.
        if _claim_age_seconds(lock) < MARKET_CLAIM_STALE_SECONDS:
            return None, "rejected_market_already_open"
        if _remote_market_is_open(client, market_id):
            if lock["status"] == "reserved":
                logger.mark_market_trade_open(
                    market_id,
                    lock["claim_id"],
                    lock.get("order_id"),
                    lock.get("token_id"),
                )
            return None, "rejected_market_already_open"
        claim_id = logger.reclaim_market_trade(market_id, lock["claim_id"])
        if claim_id is None:
            return None, "rejected_market_already_open"

    # The local claim closes the race between workers. This remote check catches
    # orders or positions created outside this process before a new order posts.
    try:
        if _remote_market_is_open(client, market_id):
            logger.mark_market_trade_open(market_id, claim_id, None, None)
            return None, "rejected_market_already_open"
    except Exception:
        logger.release_market_trade(market_id, claim_id)
        raise
    return claim_id, None


def _execute_live(signal: Signal, reservation_id: int, market_claim_id: str) -> dict:
    """Place a bounded GTC order using a fresh executable price."""
    post_attempted = False
    token_id = None
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
            logger.release_market_trade(signal.market.condition_id, market_claim_id)
            return _log_and_return(signal, "rejected_slippage", None)

        tick_size = float(signal.market.tick_size or "0.01")
        if tick_size not in {0.1, 0.01, 0.005, 0.0025, 0.001, 0.0001}:
            raise ValueError("unsupported market tick size")
        tick_decimals = max(0, len(str(tick_size).split(".")[-1]))
        limit_price = round(round(live_price / tick_size) * tick_size, tick_decimals)
        shares = round(signal.bet_amount / limit_price, 2)
        if shares <= 0 or shares * limit_price > signal.bet_amount + 0.01:
            raise ValueError("invalid order notional")

        post_attempted = True
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
        if not logger.mark_market_trade_open(
            signal.market.condition_id,
            market_claim_id,
            str(order_id),
            token_id,
        ):
            raise RuntimeError("lost per-market trade claim after posting order")
        logger.update_reservation(reservation_id, "posted", str(order_id))
        return _log_and_return(signal, "posted", str(order_id))
    except ImportError:
        logger.update_reservation(reservation_id, "released")
        logger.release_market_trade(signal.market.condition_id, market_claim_id)
        return _log_and_return(signal, "error_no_clob_client", None)
    except Exception as exc:
        if not post_attempted:
            logger.update_reservation(reservation_id, "released")
            logger.release_market_trade(signal.market.condition_id, market_claim_id)
        suffix = "_order_state_unknown" if post_attempted else ""
        return _log_and_return(
            signal, f"error_{type(exc).__name__}{suffix}", None
        )


def _log_and_return(signal: Signal, status: str, order_id: str | None) -> dict:
    trade_id = logger.log_trade(
        market_id=signal.market.condition_id,
        market_question=signal.market.question,
        market_url=signal.market.url,
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
