"""
Polymarket WebSocket subscriber — live price feed + niche market filtering.
Maintains a live snapshot of tracked markets and detects momentum shifts.
"""
from __future__ import annotations

import asyncio
import json
import time
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field

import config
from markets import Market, fetch_target_markets

log = logging.getLogger(__name__)


@dataclass
class MarketSnapshot:
    market: Market
    last_price: float
    prev_price: float
    last_update: datetime
    momentum: float = 0.0  # price change per minute

    @property
    def price_change(self) -> float:
        return self.last_price - self.prev_price


class MarketWatcher:
    """Watches niche Polymarket markets via WebSocket + periodic Gamma API refresh."""

    def __init__(self):
        self.snapshots: dict[str, MarketSnapshot] = {}
        self.tracked_markets: list[Market] = []
        self._refresh_interval = 300  # refresh market list every 5 min
        self._ws_connected = False
        self.stats = {
            "ws_messages": 0,
            "price_updates": 0,
            "market_refreshes": 0,
        }

    def get_niche_markets(self, markets: list[Market]) -> list[Market]:
        """Filter to niche markets within volume bounds."""
        return [
            m for m in markets
            if config.MIN_VOLUME_USD <= m.volume <= config.MAX_VOLUME_USD
            and m.active
        ]

    async def refresh_markets(self):
        """Fetch and filter markets from Gamma API."""
        try:
            all_markets = await asyncio.get_event_loop().run_in_executor(
                None, fetch_target_markets
            )
            self.tracked_markets = self.get_niche_markets(all_markets)

            # Update snapshots
            now = datetime.now(timezone.utc)
            existing_ids = set(self.snapshots.keys())
            new_ids = set()

            for m in self.tracked_markets:
                new_ids.add(m.condition_id)
                if m.condition_id not in self.snapshots:
                    self.snapshots[m.condition_id] = MarketSnapshot(
                        market=m,
                        last_price=m.yes_price,
                        prev_price=m.yes_price,
                        last_update=now,
                    )
                else:
                    snap = self.snapshots[m.condition_id]
                    snap.market = m  # update metadata

            # Remove stale snapshots
            for stale_id in existing_ids - new_ids:
                del self.snapshots[stale_id]

            self.stats["market_refreshes"] += 1
            log.info(f"[watcher] Tracking {len(self.tracked_markets)} niche markets")

        except Exception as e:
            log.warning(f"[watcher] Market refresh error: {e}")

    async def _connect_websocket(self):
        """Connect to Polymarket WebSocket for live price updates."""
        try:
            import websockets
        except ImportError:
            log.warning("[watcher] websockets not installed — using polling fallback")
            return

        while True:
            try:
                async with websockets.connect(
                    config.POLYMARKET_WS_HOST,
                    ping_interval=None,
                ) as ws:
                    self._ws_connected = True
                    log.info("[watcher] WebSocket connected")

                    asset_ids = [
                        str(token["token_id"])
                        for market in self.tracked_markets
                        for token in market.tokens
                        if token.get("token_id")
                    ]
                    if not asset_ids:
                        log.warning("[watcher] No token IDs available for WebSocket subscription")
                        return

                    await ws.send(json.dumps({
                        "type": "market",
                        "assets_ids": asset_ids,
                        "custom_feature_enabled": True,
                    }))

                    # Listen for updates
                    while True:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=10)
                            if isinstance(msg, bytes):
                                msg = msg.decode("utf-8")
                            if not msg or msg == "PONG":
                                continue

                            self.stats["ws_messages"] += 1
                            data = json.loads(msg)
                            messages = data if isinstance(data, list) else [data]
                            for item in messages:
                                if isinstance(item, dict):
                                    self._handle_ws_message(item)
                        except asyncio.TimeoutError:
                            await ws.send("PING")

            except Exception as e:
                self._ws_connected = False
                log.warning(f"[watcher] WebSocket error: {e}, reconnecting in 5s")
                await asyncio.sleep(5)

    def _handle_ws_message(self, data: dict):
        """Process a WebSocket price update."""
        msg_type = data.get("event_type", data.get("type", ""))
        if msg_type not in ("price_change", "last_trade_price"):
            return

        if msg_type == "price_change":
            for change in data.get("price_changes", []):
                if isinstance(change, dict):
                    self._update_snapshot(
                        change.get("asset_id", ""),
                        change.get("price"),
                        data.get("market", ""),
                    )
            return

        self._update_snapshot(
            data.get("asset_id", ""),
            data.get("price"),
            data.get("market", data.get("condition_id", "")),
        )

    def _update_snapshot(self, asset_id: str, price, market_id: str = ""):
        """Apply a token price update to its market's YES-price snapshot."""
        if not asset_id and not market_id:
            return
        if price is None:
            return

        for cid, snap in self.snapshots.items():
            matching_token = next(
                (
                    token for token in snap.market.tokens
                    if str(token.get("token_id", "")) == str(asset_id)
                ),
                None,
            )
            if matching_token is not None or market_id == cid:
                now = datetime.now(timezone.utc)
                elapsed = (now - snap.last_update).total_seconds()
                updated_price = float(price)
                if (
                    matching_token is not None
                    and matching_token.get("outcome", "").upper() == "NO"
                ):
                    updated_price = 1.0 - updated_price
                snap.prev_price = snap.last_price
                snap.last_price = updated_price
                snap.last_update = now
                if elapsed > 0:
                    snap.momentum = (snap.last_price - snap.prev_price) / (elapsed / 60)
                self.stats["price_updates"] += 1
                break

    async def _polling_fallback(self):
        """Poll Gamma API for price updates when WebSocket unavailable."""
        while True:
            await asyncio.sleep(30)
            if self._ws_connected:
                continue
            await self.refresh_markets()

    async def run(self):
        """Start the market watcher — refresh + WebSocket + polling fallback."""
        await self.refresh_markets()

        async def refresh_loop():
            while True:
                await asyncio.sleep(self._refresh_interval)
                await self.refresh_markets()

        await asyncio.gather(
            refresh_loop(),
            self._connect_websocket(),
            self._polling_fallback(),
            return_exceptions=True,
        )

    def get_market_by_question(self, question_fragment: str) -> Market | None:
        """Find a tracked market by partial question match."""
        frag = question_fragment.lower()
        for m in self.tracked_markets:
            if frag in m.question.lower():
                return m
        return None

    def get_snapshot(self, condition_id: str) -> MarketSnapshot | None:
        return self.snapshots.get(condition_id)


if __name__ == "__main__":
    async def _test():
        watcher = MarketWatcher()
        await watcher.refresh_markets()
        print(f"Tracking {len(watcher.tracked_markets)} niche markets:")
        for m in watcher.tracked_markets[:10]:
            print(f"  [{m.category}] ${m.volume:,.0f} | YES:{m.yes_price:.2f} | {m.question[:60]}")

    asyncio.run(_test())
