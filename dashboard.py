#!/usr/bin/env python3
"""
Polymarket Pipeline — Live Terminal Dashboard
Bloomberg Terminal aesthetic. Runs the real pipeline on a loop.
"""
from __future__ import annotations

import time
import sys
from datetime import datetime, timezone

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

import config
import logger
from scraper import scrape_all
from markets import fetch_target_markets, filter_by_categories, Market
from classifier import classify_event
from edge import evaluate_edge_v2, REASON_LABELS
from executor import execute_trade
from matcher import match_news_to_markets
from news_stream import confirmed_events_from_news_items

console = Console()

# --- Color Palette ---
ACCENT = "bright_green"
DIM = "bright_black"
WARN = "yellow"
LOSS = "red"
WIN = "bright_green"
MUTED = "dim white"


class PipelineState:
    """Track live pipeline state across scan cycles."""

    def __init__(self):
        self.run_number = 0
        self.markets_scanned = 0
        self.headlines_found = 0
        self.signals_found = 0
        self.trades_executed = 0
        self.latest_signals = []
        self.latest_markets = []
        self.latest_headlines = []
        self.latest_scores = {}
        self.scanning = False
        self.scan_status = "Initializing..."


state = PipelineState()


def run_scan_cycle():
    """Execute one full pipeline scan and update state."""
    state.run_number += 1
    state.scanning = True
    state.scan_status = "Scraping news..."

    # Step 1: Scrape news
    news = scrape_all()
    state.headlines_found = len(news)
    state.latest_headlines = [
        {"headline": n.headline, "source": n.source, "age": f"{n.age_hours():.1f}h"}
        for n in news[:8]
    ]
    events = confirmed_events_from_news_items(news)

    # Step 2: Fetch markets
    state.scan_status = "Fetching markets..."
    all_markets = fetch_target_markets()
    markets = filter_by_categories(all_markets)[:12]
    state.markets_scanned = len(markets)
    state.latest_markets = markets

    # Step 3: Match and classify confirmed events independently.
    signals_by_market = {}
    scores = {}
    for index, event in enumerate(events, start=1):
        state.scan_status = (
            f"Classifying event [{index}/{len(events)}] "
            f"{event.headline[:40]}..."
        )
        matched_markets = match_news_to_markets(
            event.headline,
            markets,
            summary=event.summary,
            source_relevance=event.relevance,
            source_topics=event.topics,
        )
        for market in matched_markets:
            classification = classify_event(event, market)
            signal, reason, computed_edge = evaluate_edge_v2(market, classification, event)
            logger.log_classification(
                market_id=market.condition_id,
                market_question=market.question,
                news_headline=event.headline,
                news_source=event.source_id or event.source,
                relation_level=classification.relation_level,
                direction=classification.direction,
                materiality=classification.materiality,
                estimated_yes_probability=classification.estimated_yes_probability,
                market_price=market.yes_price,
                edge=computed_edge,
                confirmation_count=event.confirmation_count,
                required_confirmations=event.required_confirmations,
                signal_generated=signal is not None,
                rejection_reason=reason,
            )
            score = {
                "confidence": classification.estimated_yes_probability,
                "reasoning": classification.reasoning,
                "relation_level": classification.relation_level,
                "edge": signal.edge if signal else computed_edge,
                "reason": reason,
            }
            scores[market.condition_id] = score
            if signal:
                current = signals_by_market.get(market.condition_id)
                if current is None or signal.edge > current["signal"].edge:
                    signals_by_market[market.condition_id] = {
                        "market": market,
                        "score": score,
                        "signal": signal,
                    }

    signals = []
    for candidate in signals_by_market.values():
        trade_result = execute_trade(candidate["signal"])
        signals.append({
            "market": candidate["market"],
            "score": candidate["score"],
            "trade": trade_result,
        })

    state.latest_signals = signals
    state.latest_scores = scores
    state.signals_found = len(signals)
    state.trades_executed = len(signals)
    state.scanning = False
    state.scan_status = "Idle — waiting for next cycle"


NARROW_WIDTH = 100


def make_layout(narrow: bool) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )
    if narrow:
        # Phone-width terminal: stack every panel full-width instead of
        # splitting into columns, since side-by-side panels get squeezed
        # into unreadable slivers below ~100 columns.
        layout["body"].split_column(
            Layout(name="status", ratio=2),
            Layout(name="performance", ratio=2),
            Layout(name="scanner", ratio=3),
            Layout(name="trades", ratio=3),
        )
    else:
        layout["body"].split_row(
            Layout(name="left", ratio=1),
            Layout(name="right", ratio=2),
        )
        layout["left"].split_column(
            Layout(name="status", ratio=1),
            Layout(name="performance", ratio=1),
        )
        layout["right"].split_column(
            Layout(name="scanner", ratio=2),
            Layout(name="trades", ratio=3),
        )
    return layout


def render_header(compact: bool = False) -> Panel:
    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC" if compact else "%Y-%m-%d %H:%M:%S UTC")
    grid = Table.grid(expand=True)
    if compact:
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="right", ratio=1)
        grid.add_row(
            Text(" POLYMARKET", style="bold bright_green"),
            Text(f"{now} ", style=MUTED),
        )
    else:
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="center", ratio=2)
        grid.add_column(justify="right", ratio=1)
        grid.add_row(
            Text(" POLYMARKET PIPELINE", style="bold bright_green"),
            Text("EVENT MATCHER + RESOLUTION CLASSIFIER + GUARDED TRADER", style=DIM),
            Text(f"{now} ", style=MUTED),
        )
    return Panel(grid, style="bright_green", box=box.HEAVY)


def render_status() -> Panel:
    table = Table(show_header=False, box=None, padding=(0, 1), expand=True)
    table.add_column("label", style=MUTED, width=18)
    table.add_column("value", style=ACCENT)

    if state.scanning:
        status_dot = "[yellow]◌[/yellow]"
        status_text = f"{status_dot} SCANNING"
    elif state.run_number > 0:
        status_dot = "[bright_green]●[/bright_green]"
        status_text = f"{status_dot} ACTIVE"
    else:
        status_dot = "[yellow]○[/yellow]"
        status_text = f"{status_dot} STARTING"

    mode = "[bright_green]LIVE[/bright_green]" if not config.DRY_RUN else f"[{WARN}]DRY RUN[/{WARN}]"

    table.add_row("Pipeline", status_text)
    table.add_row("Model", config.OPENAI_MODEL)
    table.add_row("Scan Cycle", f"#{state.run_number}" if state.run_number > 0 else "—")
    table.add_row("Activity", f"[{DIM}]{state.scan_status[:30]}[/{DIM}]")
    table.add_row("Markets Scanned", str(state.markets_scanned) if state.run_number > 0 else "—")
    table.add_row("Headlines Found", str(state.headlines_found) if state.run_number > 0 else "—")
    table.add_row("Signals / Trades", f"{state.signals_found} / {state.trades_executed}" if state.run_number > 0 else "— / —")
    table.add_row("", "")
    table.add_row("Edge Threshold", f">= {config.EDGE_THRESHOLD:.0%}")
    table.add_row("Max Bet", f"${config.MAX_BET_USD:.2f}")
    table.add_row("Daily Limit", f"${config.DAILY_LOSS_LIMIT_USD:.2f}")
    table.add_row("Mode", mode)

    return Panel(table, title="[bold]PIPELINE STATUS[/bold]", border_style="bright_green", box=box.ROUNDED)


def render_performance() -> Panel:
    stats = logger.get_trade_stats()
    trades = logger.get_recent_trades(limit=100)
    daily_spent = abs(logger.get_daily_pnl())
    cls_stats = logger.get_classification_stats(hours=24)

    total = stats["total_trades"]
    by_status = stats["by_status"]
    dry_runs = by_status.get("dry_run", 0)
    executed = by_status.get("executed", 0)
    errors = sum(v for k, v in by_status.items() if k.startswith("error"))

    total_wagered = sum(t.get("amount_usd", 0) for t in trades)
    avg_edge = sum(t.get("edge", 0) for t in trades) / max(len(trades), 1) * 100

    table = Table(show_header=False, box=None, padding=(0, 1), expand=True)
    table.add_column("label", style=MUTED, width=18)
    table.add_column("value")

    table.add_row("Total Signals", f"[{ACCENT}]{total}[/{ACCENT}]")
    table.add_row("Dry Runs", f"[{WARN}]{dry_runs}[/{WARN}]")
    table.add_row("Executed", f"[{WIN}]{executed}[/{WIN}]")
    if errors:
        table.add_row("Errors", f"[{LOSS}]{errors}[/{LOSS}]")
    table.add_row("", "")
    table.add_row("Daily Exposure", f"[{ACCENT}]${daily_spent:.2f}[/{ACCENT}]")
    table.add_row("Total Wagered", f"[{ACCENT}]${total_wagered:.2f}[/{ACCENT}]")
    table.add_row("Avg Edge", f"[{ACCENT}]{avg_edge:.1f}%[/{ACCENT}]")
    table.add_row("", "")

    if trades:
        best = max(t.get("edge", 0) for t in trades)
        table.add_row("Best Edge", f"[{WIN}]{best:.1%}[/{WIN}]")

    table.add_row("", "")
    table.add_row("Classified (24h)", f"[{ACCENT}]{cls_stats['total']}[/{ACCENT}]")
    top_rejections = sorted(
        ((reason, count) for reason, count in cls_stats["by_rejection_reason"].items()
         if reason != "signal"),
        key=lambda item: -item[1],
    )[:3]
    for reason, count in top_rejections:
        label = REASON_LABELS.get(reason, reason)
        table.add_row(f"  {label}", f"[{DIM}]{count}[/{DIM}]")

    return Panel(table, title="[bold]PERFORMANCE[/bold]", border_style="bright_cyan", box=box.ROUNDED)


def render_scanner(compact: bool = False) -> Panel:
    title = "[bold]MARKET SCANNER[/bold]" if compact else "[bold]MARKET SCANNER[/bold]  ·  Model Confidence vs Market Odds"
    market_width = 20 if compact else 38
    content = Table(show_header=True, box=box.SIMPLE_HEAD, expand=True, padding=(0, 1))
    content.add_column("Market", max_width=market_width, no_wrap=True, overflow="ellipsis")
    if not compact:
        content.add_column("Mkt$", justify="right", width=5)
        content.add_column("Model", justify="right", width=6, style=ACCENT)
    content.add_column("Edge", justify="right", width=6)
    content.add_column("Side", justify="center", width=5)
    content.add_column("Bet", justify="right", width=7)
    content.add_column("Status", justify="center", width=9)

    blank_row = ["", "", "", ""] if compact else ["", "", "", "", "", ""]

    if not state.latest_markets:
        content.add_row(f"[{DIM}]Waiting for first scan...[/{DIM}]", *blank_row)
        return Panel(content, title=title, border_style="bright_green", box=box.ROUNDED)

    # Show signals first
    signal_questions = set()
    for sig in state.latest_signals[:5]:
        m = sig["market"]
        s = sig["score"]
        t = sig["trade"]
        signal_questions.add(m.question)
        edge_pct = f"{s['edge']:.0%}"
        side_style = WIN if t["side"] == "YES" else "bright_magenta"

        status = t.get("status", "dry_run")
        if status == "dry_run":
            status_str = f"[{WARN}]DRY RUN[/{WARN}]"
        elif status == "executed":
            status_str = f"[{WIN}]FILLED[/{WIN}]"
        else:
            status_str = f"[{DIM}]{status[:9]}[/{DIM}]"

        row = [m.question[:market_width]]
        if not compact:
            row += [f"{m.yes_price:.2f}", f"{s['confidence']:.2f}"]
        row += [
            f"[{WIN}]{edge_pct}[/{WIN}]",
            f"[{side_style}]{t['side']}[/{side_style}]",
            f"${t['amount']:.0f}",
            status_str,
        ]
        content.add_row(*row)

    # Fill with non-signal markets
    for m in state.latest_markets:
        if m.question in signal_questions:
            continue
        if len(content.rows) >= 8:
            break
        score = state.latest_scores.get(m.condition_id, {})
        confidence = score.get("confidence", 0.5)
        edge = score.get("edge", abs(confidence - m.yes_price))
        reason_label = REASON_LABELS.get(score.get("reason", ""), "no data")
        row = [f"[{DIM}]{m.question[:market_width]}[/{DIM}]"]
        if not compact:
            row += [f"[{DIM}]{m.yes_price:.2f}[/{DIM}]", f"[{DIM}]{confidence:.2f}[/{DIM}]"]
        row += [
            f"[{DIM}]{edge:.0%}[/{DIM}]",
            f"[{DIM}]—[/{DIM}]",
            f"[{DIM}]—[/{DIM}]",
            f"[{DIM}]{reason_label[:9]}[/{DIM}]",
        ]
        content.add_row(*row)

    return Panel(content, title=title, border_style="bright_green", box=box.ROUNDED)


def render_trades(compact: bool = False) -> Panel:
    trades = logger.get_recent_trades(limit=10)
    title = "[bold]TRADE LOG[/bold]" if compact else "[bold]TRADE LOG[/bold]  ·  Bets Placed by Pipeline"
    market_width = 20 if compact else 38

    table = Table(show_header=True, box=box.SIMPLE_HEAD, expand=True, padding=(0, 1))
    table.add_column("Time", width=8 if compact else 16, style=MUTED, no_wrap=True, overflow="ellipsis")
    table.add_column("Market", max_width=market_width, no_wrap=True, overflow="ellipsis")
    table.add_column("Side", justify="center", width=5)
    table.add_column("Bet", justify="right", width=7)
    table.add_column("Edge", justify="right", width=6)
    if not compact:
        table.add_column("Model", justify="right", width=6)
        table.add_column("Mkt$", justify="right", width=5)
    table.add_column("Status", justify="center", width=9)

    blank_row = ["", "", "", "", ""] if compact else ["", "", "", "", "", "", ""]

    if not trades:
        table.add_row(f"[{DIM}]No trades yet — pipeline scanning...[/{DIM}]", *blank_row)
    else:
        for t in trades:
            side_style = WIN if t["side"] == "YES" else "bright_magenta"
            status = t["status"]
            if status == "dry_run":
                status_str = f"[{WARN}]DRY RUN[/{WARN}]"
            elif status == "executed":
                status_str = f"[{WIN}]FILLED[/{WIN}]"
            elif status.startswith("error"):
                status_str = f"[{LOSS}]ERROR[/{LOSS}]"
            elif status == "rejected_daily_limit":
                status_str = f"[{LOSS}]LIMIT[/{LOSS}]"
            else:
                status_str = f"[{DIM}]{status[:9]}[/{DIM}]"

            time_str = t["created_at"][11:16] if compact and len(t["created_at"]) > 16 else t["created_at"][:16]
            row = [
                time_str,
                t["market_question"][:market_width],
                f"[{side_style}]{t['side']}[/{side_style}]",
                f"${t['amount_usd']:.2f}",
                f"{t['edge']:.0%}",
            ]
            if not compact:
                row += [f"{t['claude_score']:.2f}", f"{t['market_price']:.2f}"]
            row.append(status_str)
            table.add_row(*row)

    return Panel(table, title=title, border_style="bright_cyan", box=box.ROUNDED)


def render_footer(compact: bool = False) -> Panel:
    grid = Table.grid(expand=True)

    stats = logger.get_trade_stats()
    mode = "LIVE" if not config.DRY_RUN else "DRY"

    if compact:
        headline_len = 40
        if state.latest_headlines:
            h = state.latest_headlines[0]
            headline_text = f"[{ACCENT}]>[/{ACCENT}] {h['headline'][:headline_len]}"
        else:
            headline_text = f"[{DIM}]Waiting for news feed...[/{DIM}]"

        grid.add_column(justify="left", ratio=3)
        grid.add_column(justify="right", ratio=1)
        grid.add_row(
            headline_text,
            f"[{DIM}]{mode}[/{DIM}] [{ACCENT}]{stats['total_trades']}[/{ACCENT}]",
        )
    else:
        if state.latest_headlines:
            h = state.latest_headlines[0]
            headline_text = f"[{ACCENT}]>[/{ACCENT}] [{MUTED}]{h['source']}:[/{MUTED}] {h['headline'][:80]}"
        else:
            headline_text = f"[{DIM}]Waiting for news feed...[/{DIM}]"

        grid.add_column(justify="left", ratio=2)
        grid.add_column(justify="center", ratio=3)
        grid.add_column(justify="right", ratio=2)
        grid.add_row(
            headline_text,
            f"[{DIM}]Ctrl+C to exit[/{DIM}]",
            f"[{DIM}]{mode}[/{DIM}]  |  Signals: [{ACCENT}]{stats['total_trades']}[/{ACCENT}] ",
        )
    return Panel(grid, style="bright_green", box=box.HEAVY)


def render_all(layout: Layout, compact: bool) -> None:
    layout["header"].update(render_header(compact))
    layout["status"].update(render_status())
    layout["performance"].update(render_performance())
    layout["scanner"].update(render_scanner(compact))
    layout["trades"].update(render_trades(compact))
    layout["footer"].update(render_footer(compact))


def run_dashboard(scan_interval: float = 60.0):
    """Launch the live dashboard. Scans on a configurable interval.

    Layout adapts to terminal width so it stays usable when viewed from a
    narrow mobile SSH client, not just a wide desktop terminal.
    """
    narrow = console.width < NARROW_WIDTH
    layout = make_layout(narrow)
    render_all(layout, narrow)

    try:
        with Live(layout, console=console, refresh_per_second=2, screen=True) as live:
            last_scan = 0.0

            while True:
                now = time.time()

                if now - last_scan >= scan_interval:
                    run_scan_cycle()
                    last_scan = now

                new_narrow = console.width < NARROW_WIDTH
                if new_narrow != narrow:
                    narrow = new_narrow
                    layout = make_layout(narrow)
                    live.update(layout)

                render_all(layout, narrow)

                time.sleep(0.5)

    except KeyboardInterrupt:
        stats = logger.get_trade_stats()
        console.print(f"\n[{ACCENT}]Pipeline stopped. {stats['total_trades']} signals logged across {state.run_number} cycles.[/{ACCENT}]")


if __name__ == "__main__":
    interval = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    run_dashboard(scan_interval=interval)
