#!/usr/bin/env python3
"""
Polymarket Pipeline — synchronous and asynchronous event-driven workflows.
Both paths use: News event → predicate match → classify → edge → trade.
"""
from __future__ import annotations

import asyncio
import logging

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

import config
import logger
from scraper import scrape_all
from markets import fetch_target_markets, filter_by_categories
from edge import detect_edge_v2, Signal
from executor import execute_trade, execute_trade_async
from news_stream import (
    NewsAggregator,
    NewsEvent,
    confirmed_events_from_news_items,
)
from market_watcher import MarketWatcher
from matcher import match_news_to_markets
from classifier import classify_event, classify_event_async
from telegram_alerts import send_trade_alert, send_trade_alert_async

console = Console()
log = logging.getLogger(__name__)


# ============================================================
# V2: Event-Driven Pipeline
# ============================================================

class PipelineV2:
    """Async event-driven pipeline. Runs indefinitely."""

    def __init__(self):
        self.news_queue: asyncio.Queue = asyncio.Queue()
        self.signal_queue: asyncio.Queue = asyncio.Queue()
        self.news_aggregator = NewsAggregator(self.news_queue)
        self.market_watcher = MarketWatcher()
        self.running = False
        self.stats = {
            "news_processed": 0,
            "markets_matched": 0,
            "signals_found": 0,
            "trades_executed": 0,
        }

    async def run(self):
        """Start all pipeline components concurrently."""
        self.running = True
        mode = "[red bold]LIVE[/red bold]" if not config.DRY_RUN else "[yellow]DRY RUN[/yellow]"
        console.print(Panel(f"Pipeline V2 Starting  |  Mode: {mode}", style="bright_green"))
        console.print(f"  Niche filter: ${config.MIN_VOLUME_USD:,.0f} - ${config.MAX_VOLUME_USD:,.0f} volume")
        console.print(f"  Materiality threshold: {config.MATERIALITY_THRESHOLD}")
        console.print(f"  Speed target: {config.SPEED_TARGET_SECONDS}s")
        console.print()

        try:
            await asyncio.gather(
                self.news_aggregator.run(),
                self.market_watcher.run(),
                self._process_news(),
                self._execute_signals(),
                self._status_printer(),
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            self.running = False

    async def _process_news(self):
        """Process each news event: match → classify → detect edge."""
        while True:
            event: NewsEvent = await self.news_queue.get()
            self.stats["news_processed"] += 1

            # Log the news event
            logger.log_news_event(
                headline=event.headline,
                source=event.source_id or event.source,
                received_at=event.received_at.isoformat(),
                latency_ms=event.latency_ms,
            )

            # Match to niche markets
            matched = match_news_to_markets(
                event.headline,
                self.market_watcher.tracked_markets,
                summary=event.summary,
                source_relevance=event.relevance,
                source_topics=event.topics,
            )

            if not matched:
                continue

            self.stats["markets_matched"] += len(matched)

            # Classify against each matched market
            for market in matched:
                try:
                    classification = await classify_event_async(event, market)

                    signal = detect_edge_v2(market, classification, event)
                    if signal:
                        self.stats["signals_found"] += 1
                        await self.signal_queue.put(signal)
                        console.print(
                            f"  [bright_green]SIGNAL[/bright_green] "
                            f"[{event.source_id}] {classification.direction.upper()} "
                            f"{classification.relation_level} "
                            f"mat:{classification.materiality:.2f} "
                            f"confirm:{event.confirmation_count}/"
                            f"{event.required_confirmations} "
                            f"→ {signal.side} ${signal.bet_amount} "
                            f"on \"{market.question[:40]}...\" "
                            f"({signal.total_latency_ms}ms)"
                        )
                except Exception as e:
                    log.warning(f"[pipeline] Classification error: {e}")

    async def _execute_signals(self):
        """Execute trades from the signal queue."""
        while True:
            signal: Signal = await self.signal_queue.get()
            result = await execute_trade_async(signal)
            self.stats["trades_executed"] += 1
            await send_trade_alert_async(signal, result)

            status_color = "bright_green" if result["status"] in ("dry_run", "posted") else "red"
            console.print(
                f"  [{status_color}]{result['status']}[/{status_color}] "
                f"{result['side']} ${result['amount']:.2f} "
                f"on \"{result['market'][:40]}\" "
                f"(edge:{result['edge']:.1%} latency:{result.get('latency_ms', 0)}ms)"
            )

    async def _status_printer(self):
        """Print periodic status updates."""
        while True:
            await asyncio.sleep(30)
            ns = self.news_aggregator.stats
            log.info(
                "Status: news=%s (x:%s tg:%s rss:%s) stale=%s unconfirmed=%s "
                "matched=%s signals=%s trades=%s markets=%s",
                self.stats["news_processed"],
                ns.get("twitter", 0),
                ns.get("telegram", 0),
                ns.get("rss", 0),
                ns.get("stale", 0),
                ns.get("unconfirmed", 0),
                self.stats["markets_matched"],
                self.stats["signals_found"],
                self.stats["trades_executed"],
                len(self.market_watcher.tracked_markets),
            )


def run_pipeline_v2():
    """Entry point for V2 event-driven pipeline."""
    pipeline = PipelineV2()
    try:
        asyncio.run(pipeline.run())
    except KeyboardInterrupt:
        console.print(f"\n[bright_green]Pipeline stopped. {pipeline.stats}[/bright_green]")


# ============================================================
# Synchronous event pipeline
# ============================================================

def run_pipeline(
    max_markets: int = 10,
    lookback_hours: int | None = None,
    categories: list[str] | None = None,
) -> list[dict]:
    """Run one resolution-aware event scan and return trade results."""

    run_id = logger.log_run_start()
    results = []
    signals: list[Signal] = []

    mode = "[yellow]DRY RUN[/yellow]" if config.DRY_RUN else "[red bold]LIVE[/red bold]"
    console.print(Panel(f"Pipeline Run #{run_id}  |  Mode: {mode}", style="cyan"))

    # Step 1: Scrape News
    console.print("\n[bold]1. Scraping news...[/bold]")
    news = scrape_all(lookback_hours)
    console.print(f"   Found {len(news)} unique headlines")

    if not news:
        console.print("[yellow]   No news found. Aborting run.[/yellow]")
        logger.log_run_end(run_id, 0, 0, 0, "no_news")
        return results

    events = confirmed_events_from_news_items(news)
    suppressed = len(news) - len(events)
    console.print(
        f"   Confirmed events: {len(events)} "
        f"([dim]{suppressed} suppressed by source policy[/dim])"
    )
    if not events:
        console.print(
            "[yellow]   No independently confirmed events. Aborting run.[/yellow]"
        )
        logger.log_run_end(run_id, 0, 0, 0, "no_confirmed_news")
        return results

    # Step 2: Fetch Markets
    console.print("\n[bold]2. Fetching Polymarket markets...[/bold]")
    all_markets = fetch_target_markets()
    markets = filter_by_categories(all_markets, categories)[:max_markets]
    console.print(f"   {len(markets)} markets in target categories (of {len(all_markets)} total)")

    if not markets:
        console.print("[yellow]   No markets found. Aborting run.[/yellow]")
        logger.log_run_end(run_id, 0, 0, 0, "no_markets")
        return results

    # Step 3: Match and classify each confirmed event independently.
    console.print(
        f"\n[bold]3. Matching {len(events)} confirmed events "
        f"to {len(markets)} markets...[/bold]"
    )

    for event_index, event in enumerate(events, start=1):
        console.print(
            f"\n   [cyan][{event_index}/{len(events)}][/cyan] "
            f"[{event.source_id}] {event.headline[:120]}"
        )
        logger.log_news_event(
            headline=event.headline,
            source=event.source_id or event.source,
            received_at=event.received_at.isoformat(),
            latency_ms=event.latency_ms,
        )
        matched_markets = match_news_to_markets(
            event.headline,
            markets,
            summary=event.summary,
            source_relevance=event.relevance,
            source_topics=event.topics,
        )
        if not matched_markets:
            console.print(
                "   [dim]No market shares both an entity and a "
                "resolution predicate.[/dim]"
            )
            continue

        for market in matched_markets:
            heading = Text(f"   → {market.question[:80]}")
            if market.url:
                heading.append("  ")
                heading.append("Open market", style=f"link {market.url}")
            console.print(heading)

            classification = classify_event(event, market)
            console.print(
                f"     Relation: {classification.relation_level} | "
                f"Direction: {classification.direction} | "
                f"Materiality: {classification.materiality:.2f} | "
                f"Fair YES: {classification.estimated_yes_probability:.2f}"
            )
            signal = detect_edge_v2(market, classification, event)
            if signal:
                console.print(
                    f"     [green bold]SIGNAL: {signal.side} | "
                    f"Edge: {signal.edge:.1%} | "
                    f"Confirmations: {signal.confirmation_count}/"
                    f"{signal.required_confirmations} | "
                    f"Size: ${signal.bet_amount}[/green bold]"
                )
                signals.append(signal)
            else:
                console.print(
                    "     [dim]No actionable resolution-aware edge.[/dim]"
                )

    # A scan may contain several reports about one market. Execute at most the
    # strongest independently classified signal for that market in this run.
    strongest_by_market: dict[str, Signal] = {}
    for signal in signals:
        current = strongest_by_market.get(signal.market.condition_id)
        if current is None or signal.edge > current.edge:
            strongest_by_market[signal.market.condition_id] = signal
    signals = list(strongest_by_market.values())

    # Step 4: Execute Trades
    if signals:
        console.print(f"\n[bold]4. Executing {len(signals)} trades...[/bold]")
        for signal in signals:
            result = execute_trade(signal)
            results.append(result)
            send_trade_alert(signal, result)
            status_color = "green" if result["status"] in ("dry_run", "posted") else "red"
            console.print(f"   [{status_color}]{result['status']}[/{status_color}] {result['market'][:60]} | {result['side']} ${result['amount']}")
    else:
        console.print("\n[bold]4. No signals — nothing to execute.[/bold]")

    logger.log_run_end(run_id, len(markets), len(signals), len(results))
    _print_summary(results, len(markets), len(signals))
    return results


def _print_summary(results: list[dict], markets_scanned: int, signals_found: int):
    table = Table(title="Pipeline Summary", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Markets scanned", str(markets_scanned))
    table.add_row("Signals found", str(signals_found))
    table.add_row("Trades placed", str(len(results)))
    table.add_row("Mode", "DRY RUN" if config.DRY_RUN else "LIVE")
    console.print(table)


if __name__ == "__main__":
    run_pipeline()
