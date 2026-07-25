from __future__ import annotations

import executor
import logger
from edge import Signal
from markets import Market


class FakeV2Client:
    def __init__(self):
        self.order_args = None
        self.options = None
        self.order_type = None

    def get_price(self, token_id, side):
        assert token_id == "yes-token"
        return {"price": "0.40"}

    def get_open_orders(self, params=None):
        return []

    def create_and_post_order(self, *, order_args, options, order_type):
        self.order_args = order_args
        self.options = options
        self.order_type = order_type
        return {"orderID": "v2-order"}


def test_live_order_uses_v2_sdk_surface(monkeypatch):
    market = Market(
        "condition",
        "Will Israel strike Iran?",
        "israel",
        0.5,
        0.5,
        10_000,
        "",
        True,
        [{"token_id": "yes-token", "outcome": "Yes"}],
        tick_size="0.005",
    )
    signal = Signal(
        market,
        0.8,
        0.5,
        0.3,
        "YES",
        5.0,
        "reason",
        "headline",
    )
    client = FakeV2Client()
    updates = []
    monkeypatch.setattr(executor, "_build_client", lambda: client)
    monkeypatch.setattr(
        executor.logger,
        "update_reservation",
        lambda *args: updates.append(args),
    )
    market_updates = []

    def mark_market_open(*args):
        market_updates.append(args)
        return True

    monkeypatch.setattr(
        executor.logger,
        "mark_market_trade_open",
        mark_market_open,
    )
    monkeypatch.setattr(
        executor,
        "_log_and_return",
        lambda _signal, status, order_id: {
            "status": status,
            "order_id": order_id,
        },
    )

    result = executor._execute_live(signal, reservation_id=7, market_claim_id="claim-1")

    assert result == {"status": "posted", "order_id": "v2-order"}
    assert client.order_args.token_id == "yes-token"
    assert client.order_args.price == 0.4
    assert client.options.tick_size == "0.005"
    assert updates == [(7, "posted", "v2-order")]
    assert market_updates == [("condition", "claim-1", "v2-order", "yes-token")]


def test_ambiguous_post_failure_keeps_claim_and_exposure_reserved(monkeypatch):
    market = Market(
        "condition", "Will X happen?", "israel", 0.5, 0.5, 10_000, "", True,
        [{"token_id": "yes-token", "outcome": "Yes"}],
    )
    signal = Signal(market, 0.8, 0.5, 0.3, "YES", 5.0, "reason", "headline")

    class AmbiguousClient(FakeV2Client):
        def create_and_post_order(self, *, order_args, options, order_type):
            raise TimeoutError("response lost after submission")

    updates = []
    releases = []
    monkeypatch.setattr(executor, "_build_client", lambda: AmbiguousClient())
    monkeypatch.setattr(executor.logger, "update_reservation", lambda *args: updates.append(args))
    monkeypatch.setattr(executor.logger, "release_market_trade", lambda *args: releases.append(args))
    monkeypatch.setattr(
        executor,
        "_log_and_return",
        lambda _signal, status, order_id: {"status": status, "order_id": order_id},
    )

    result = executor._execute_live(signal, 7, "claim-1")

    assert result["status"] == "error_TimeoutError_order_state_unknown"
    assert updates == []
    assert releases == []


def test_remote_market_exposure_checks_orders_and_positions(monkeypatch):
    class Client:
        def __init__(self, orders):
            self.orders = orders

        def get_open_orders(self, params=None):
            assert params.market == "condition"
            return self.orders

    monkeypatch.setattr(executor, "_get_open_positions", lambda market_id: [])
    assert executor._remote_market_is_open(Client([{"id": "order"}]), "condition")
    assert not executor._remote_market_is_open(Client([]), "condition")

    monkeypatch.setattr(
        executor,
        "_get_open_positions",
        lambda market_id: [{"conditionId": market_id, "size": 2.5}],
    )
    assert executor._remote_market_is_open(Client([]), "condition")


def test_market_claim_blocks_until_remote_trade_is_closed(monkeypatch, tmp_path):
    class Client:
        def get_open_orders(self, params=None):
            return []

    monkeypatch.setattr(logger, "DB_PATH", tmp_path / "trades.db")
    logger.init_db()
    positions = []
    monkeypatch.setattr(executor, "_get_open_positions", lambda _market_id: positions)

    first_claim, rejection = executor._claim_market_for_live_trade(Client(), "condition")
    assert first_claim is not None
    assert rejection is None

    duplicate_claim, rejection = executor._claim_market_for_live_trade(Client(), "condition")
    assert duplicate_claim is None
    assert rejection == "rejected_market_already_open"

    logger.mark_market_trade_open("condition", first_claim, "order-1", "yes-token")
    positions.append({"conditionId": "condition", "size": 3})
    still_open_claim, rejection = executor._claim_market_for_live_trade(Client(), "condition")
    assert still_open_claim is None
    assert rejection == "rejected_market_already_open"

    positions.clear()
    monkeypatch.setattr(
        executor,
        "_claim_age_seconds",
        lambda _lock: executor.MARKET_CLAIM_STALE_SECONDS + 1,
    )
    next_claim, rejection = executor._claim_market_for_live_trade(Client(), "condition")
    assert next_claim is not None
    assert next_claim != first_claim
    assert rejection is None
