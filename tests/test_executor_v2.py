from __future__ import annotations

import executor
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
    monkeypatch.setattr(
        executor,
        "_log_and_return",
        lambda _signal, status, order_id: {
            "status": status,
            "order_id": order_id,
        },
    )

    result = executor._execute_live(signal, reservation_id=7)

    assert result == {"status": "posted", "order_id": "v2-order"}
    assert client.order_args.token_id == "yes-token"
    assert client.order_args.price == 0.4
    assert client.options.tick_size == "0.005"
    assert updates == [(7, "posted", "v2-order")]
