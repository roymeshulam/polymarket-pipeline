#!/usr/bin/env python3
"""
Polymarket Pipeline — Web Dashboard
Read-only HTML/JSON view of the same live state as dashboard.py's terminal
UI, served over plain HTTP on localhost. Port is set via DASHBOARD_PORT
in .env.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import classifier
import config
import executor
import logger
import markets
from dashboard import state, run_scan_cycle, _format_volume
from edge import REASON_LABELS
from matcher import CoverageGap, find_coverage_gaps
from source_config import profiles_by_kind

# Number of trades shown in the Trade Log panel; also bounds how many
# markets get a live price lookup for the P&L columns.
_TRADE_LOG_LIMIT = 10

# sources.json is loaded once at process start (config.py import time), so
# these counts are fixed for the life of the process, same as everything
# else sourced from config.SOURCE_PROFILES.
_TWITTER_SOURCE_COUNT = len(profiles_by_kind(config.SOURCE_PROFILES, "twitter"))
_RSS_SOURCE_COUNT = len(profiles_by_kind(config.SOURCE_PROFILES, "rss"))

# Set by start_attached_dashboard() when serving alongside a live PipelineV2
# (i.e. `cli.py watch`). While set, the server reads that pipeline's in-memory
# stats instead of running its own scan loop — running a second independent
# scan+trade loop concurrently with `watch` would double-execute real trades.
_ACTIVE_PIPELINE = None

# Wallet balance + open P&L, refreshed on its own timer (config.PORTFOLIO_REFRESH_MINUTES)
# by _portfolio_loop rather than on every /api/state poll, since it's a network
# round-trip to Polymarket rather than a local trades.db read.
_PORTFOLIO_STATE = {
    "balance_usd": None, "open_pnl_usd": None, "updated_at": None, "error": "not_checked_yet",
}
_portfolio_thread_started = False

# Live YES price per market_id (condition_id), refreshed on the same timer as
# the portfolio snapshot below. Backs the Trade Log's Mkt$/P&L columns without
# hitting Gamma on every 2s /api/state poll.
_TRADE_PRICE_CACHE: dict[str, float] = {}


def _refresh_portfolio_state():
    _PORTFOLIO_STATE.update(executor.fetch_portfolio_snapshot())
    _PORTFOLIO_STATE["updated_at"] = _now_str()


def _refresh_trade_prices():
    trades = logger.get_recent_trades(limit=_TRADE_LOG_LIMIT)
    market_ids = [t["market_id"] for t in trades]
    _TRADE_PRICE_CACHE.update(markets.fetch_current_prices(market_ids))


def _portfolio_loop(stop_event: threading.Event):
    interval = max(config.PORTFOLIO_REFRESH_MINUTES, 1) * 60
    while not stop_event.is_set():
        _refresh_portfolio_state()
        _refresh_trade_prices()
        stop_event.wait(interval)


def _start_portfolio_thread(stop_event: threading.Event | None = None):
    """Idempotent: safe to call from both dashboard entry points."""
    global _portfolio_thread_started
    if _portfolio_thread_started:
        return
    _portfolio_thread_started = True
    threading.Thread(
        target=_portfolio_loop, args=(stop_event or threading.Event(),), daemon=True
    ).start()

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Polymarket Pipeline</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2032%2032'%3E%3Crect%20width='32'%20height='32'%20rx='9'%20fill='%230b0d14'%20stroke='%23818cf8'%20stroke-width='2'/%3E%3Ctext%20x='16'%20y='23'%20font-family='-apple-system,Segoe%20UI,Arial,sans-serif'%20font-size='16'%20font-weight='700'%20fill='%23818cf8'%20text-anchor='middle'%3EP%3C/text%3E%3C/svg%3E">
<style>
  :root {
    color-scheme: dark light;
    --bg: #0b0d14; --panel: #12151f; --panel-2: #171b28; --border: #242938;
    --accent: #818cf8; --accent-strong: #6366f1; --accent-soft: rgba(129, 140, 248, 0.14);
    --win: #34d399; --win-soft: rgba(52, 211, 153, 0.14);
    --warn: #fbbf24; --warn-soft: rgba(251, 191, 36, 0.14);
    --loss: #f87171; --loss-soft: rgba(248, 113, 113, 0.14);
    --muted: #8b93a7; --text: #e7e9ee;
    --shadow: 0 1px 2px rgba(0, 0, 0, 0.4), 0 10px 30px rgba(0, 0, 0, 0.28);
  }
  @media (prefers-color-scheme: light) {
    :root {
      --bg: #f3f4f9; --panel: #ffffff; --panel-2: #f7f8fc; --border: #e3e6f0;
      --accent: #4f46e5; --accent-strong: #4338ca; --accent-soft: rgba(79, 70, 229, 0.1);
      --win: #059669; --win-soft: rgba(5, 150, 105, 0.1);
      --warn: #d97706; --warn-soft: rgba(217, 119, 6, 0.1);
      --loss: #dc2626; --loss-soft: rgba(220, 38, 38, 0.1);
      --muted: #667085; --text: #1a1d29;
      --shadow: 0 1px 2px rgba(15, 23, 42, 0.05), 0 10px 30px rgba(15, 23, 42, 0.06);
    }
  }
  * { box-sizing: border-box; }
  html, body { max-width: 100%; overflow-x: hidden; }
  body {
    background: var(--bg);
    background-image: radial-gradient(circle at 12% -10%, var(--accent-soft), transparent 45%);
    background-attachment: fixed;
    color: var(--text);
    font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 14px; margin: 0; padding: 16px;
    -webkit-font-smoothing: antialiased;
  }
  header {
    display: flex; justify-content: space-between; align-items: center; gap: 14px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
    padding: 12px 20px; margin-bottom: 14px; box-shadow: var(--shadow);
  }
  header .brand { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
  header .mark {
    width: 30px; height: 30px; border-radius: 9px; flex-shrink: 0;
    background: linear-gradient(135deg, var(--accent), var(--accent-strong));
    display: flex; align-items: center; justify-content: center;
    color: #fff; font-weight: 700; font-size: 14px;
  }
  header h1 { font-size: 15px; font-weight: 700; margin: 0; letter-spacing: 0.1px; flex-shrink: 0; }
  header .sub { color: var(--muted); font-size: 12px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  header .sub:last-child { flex-shrink: 0; font-variant-numeric: tabular-nums; }
  .stack { display: flex; flex-direction: column; gap: 14px; }
  .grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; min-width: 0; }
  .panel {
    border: 1px solid var(--border); border-radius: 14px; padding: 16px 18px;
    background: var(--panel); min-width: 0; max-width: 100%; overflow: hidden;
    box-shadow: var(--shadow);
  }
  .panel h2 {
    font-size: 11px; font-weight: 700; color: var(--muted); margin: 0 0 12px 0;
    text-transform: uppercase; letter-spacing: 0.08em;
    display: flex; align-items: center; gap: 7px;
  }
  .panel h2::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: var(--accent); }
  .row { display: flex; justify-content: space-between; align-items: center; padding: 5px 0; border-bottom: 1px solid var(--border); }
  .row:last-child { border-bottom: 0; }
  .row .label { color: var(--muted); font-size: 13px; }
  .row > span:last-child { font-variant-numeric: tabular-nums; font-weight: 600; }
  .table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; max-width: 100%; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 8px 10px; white-space: nowrap; font-size: 13px; }
  th { color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; border-bottom: 1px solid var(--border); }
  td { border-bottom: 1px solid var(--border); }
  tbody tr:last-child td { border-bottom: 0; }
  tbody tr:hover td { background: var(--panel-2); }
  td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
  td.center, th.center { text-align: center; }
  .dim { color: var(--muted); }
  .warn { color: var(--warn); }
  .win { color: var(--win); }
  .loss { color: var(--loss); }
  .side-yes { color: var(--win); }
  .side-no { color: #ec4899; }
  td.center.win, td.center.warn, td.center.loss, td.center.side-yes, td.center.side-no {
    font-weight: 600; border-radius: 8px;
  }
  td.center.win { background: var(--win-soft); }
  td.center.warn { background: var(--warn-soft); }
  td.center.loss { background: var(--loss-soft); }
  td.center.side-yes { background: var(--win-soft); }
  td.center.side-no { background: rgba(236, 72, 153, 0.14); }
  .mkt-link { color: inherit; text-decoration: none; border-bottom: 1px solid transparent; transition: color .15s ease, border-color .15s ease; }
  .mkt-link:hover { color: var(--accent); border-bottom-color: var(--accent); }
  .gap-badge {
    display: inline-flex; align-items: center; background: var(--warn-soft); color: var(--warn);
    border-radius: 999px; padding: 2px 9px; margin-left: 8px; font-size: 11px; font-weight: 600;
  }
  #coverage.ok { color: var(--muted); font-style: italic; }
  .info-icon {
    display: inline-flex; align-items: center; justify-content: center;
    width: 14px; height: 14px; margin-left: 6px; border-radius: 50%;
    background: var(--panel-2); color: var(--muted); font-size: 10px; font-weight: 700;
    font-style: normal; line-height: 1; border: 1px solid var(--border); cursor: pointer;
    vertical-align: middle;
  }
  .info-icon:hover { color: var(--accent); border-color: var(--accent); }
  .info-overlay {
    display: none; position: fixed; inset: 0; background: rgba(0, 0, 0, 0.5);
    align-items: center; justify-content: center; z-index: 1000; padding: 20px;
  }
  .info-overlay.open { display: flex; }
  .info-modal {
    background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
    padding: 16px 20px; max-width: 380px; width: 100%; box-shadow: var(--shadow);
  }
  .info-modal-head {
    display: flex; justify-content: space-between; align-items: center;
    margin-bottom: 8px; font-weight: 700; font-size: 14px;
  }
  .info-modal-close { cursor: pointer; color: var(--muted); font-size: 18px; line-height: 1; }
  .info-modal-close:hover { color: var(--text); }
  .info-modal-body { color: var(--text); font-size: 13px; line-height: 1.5; }
  footer {
    background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
    padding: 10px 18px; margin-top: 14px; box-shadow: var(--shadow);
    display: flex; justify-content: flex-end; align-items: center; gap: 14px; color: var(--muted); font-size: 13px;
  }
  footer #mode {
    flex-shrink: 0; font-weight: 700; font-size: 11px; letter-spacing: 0.04em; text-transform: uppercase;
    padding: 3px 12px; border-radius: 999px; background: var(--accent-soft); color: var(--accent);
  }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
  @media (max-width: 640px) {
    body { font-size: 13px; padding: 10px; }
    header { padding: 10px 14px; border-radius: 12px; }
    header .sub:first-of-type { display: none; }
    .panel { padding: 12px 14px; border-radius: 12px; }
    .row { gap: 12px; }
    .row > :last-child { min-width: 0; text-align: right; overflow-wrap: anywhere; }
    .table-scroll { overflow: visible; }
    #scanner table, #scanner tbody, #trades table, #trades tbody, #news table, #news tbody { display: block; width: 100%; }
    #scanner tr:first-child, #trades tr:first-child, #news tr:first-child { display: none; }
    #scanner tr:not(:first-child), #trades tr:not(:first-child), #news tr:not(:first-child) {
      display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 4px 14px; width: 100%; padding: 10px 12px; margin-bottom: 8px;
      background: var(--panel-2); border: 1px solid var(--border); border-radius: 10px;
    }
    #scanner tr:last-child, #trades tr:last-child, #news tr:last-child { margin-bottom: 0; }
    #scanner td, #trades td, #news td {
      display: flex; justify-content: space-between; gap: 8px;
      min-width: 0; padding: 2px 0; white-space: normal; text-align: right; border-bottom: 0;
    }
    #scanner td::before, #trades td::before, #news td::before {
      content: attr(data-label); color: var(--muted); text-align: left; font-weight: 600;
    }
    #scanner td:first-child, #trades td:nth-child(2), #news td:nth-child(3) {
      grid-column: 1 / -1; padding-bottom: 6px; margin-bottom: 4px;
      border-bottom: 1px dashed var(--border); overflow-wrap: anywhere;
    }
    #scanner td.empty, #trades td.empty, #news td.empty {
      grid-column: 1 / -1; display: block; text-align: left; border: 0; background: none;
    }
    #scanner td.empty::before, #trades td.empty::before, #news td.empty::before { content: none; }
  }
</style>
</head>
<body>
  <header>
    <div class="brand">
      <div class="mark">P</div>
      <h1>Polymarket Pipeline</h1>
    </div>
    <span class="sub">Event Matcher · Resolution Classifier · Guarded Trader</span>
    <span class="sub" id="clock">—</span>
  </header>
  <div class="stack">
    <div class="grid">
      <div class="panel"><h2>Portfolio</h2><div id="portfolio"></div></div>
      <div class="panel"><h2>Pipeline</h2><div id="status"></div></div>
    </div>
    <div class="panel"><h2>Trade Log</h2><div id="trades"></div></div>
    <div class="panel"><h2>Market Scanner</h2><div id="scanner"></div></div>
    <div class="panel" id="coverage-panel">
      <h2>Matcher Coverage Gaps</h2>
      <div id="coverage"></div>
    </div>
    <div class="panel"><h2>Latest News</h2><div id="news"></div></div>
  </div>
  <footer>
    <span id="mode">—</span>
  </footer>

  <div class="info-overlay" id="info-overlay" onclick="if (event.target === this) closeInfo()">
    <div class="info-modal">
      <div class="info-modal-head">
        <span id="info-title"></span>
        <span class="info-modal-close" onclick="closeInfo()">&times;</span>
      </div>
      <div class="info-modal-body" id="info-body"></div>
    </div>
  </div>

<script>
function statusClass(status) {
  if (status === "dry_run") return "warn";
  if (status === "executed" || status === "filled") return "win";
  if (status && status.startsWith("error")) return "loss";
  return "dim";
}
function statusLabel(status) {
  if (status === "dry_run") return "DRY RUN";
  if (status === "executed" || status === "filled") return "FILLED";
  return (status || "").slice(0, 9).toUpperCase() || "—";
}
function marketCell(question, url) {
  if (!url) return question;
  const safeUrl = url.replace(/"/g, "%22");
  return `<a class="mkt-link" href="${safeUrl}" target="_blank" rel="noopener noreferrer">${question}</a>`;
}
const INFO_TEXT = {
  pipeline_status: "Whether the pipeline is actively scanning/watching for news, or still starting up.",
  model: "The OpenAI model used to classify headlines against each market's exact resolution rules.",
  model_calls_today: "Number of calls made to the classification model since UTC midnight (resets daily).",
  scan_cycle: "The sequential number of the current scan pass (synchronous `run` mode only; blank while running the async `watch` pipeline).",
  activity: "A live one-line summary of what the pipeline is doing right now.",
  markets_scanned: "How many active Polymarket markets are currently being tracked for matching.",
  headlines_found: "Total news headlines ingested from all sources since this process started.",
  tweets_today: "Tweets read from the X/Twitter stream since UTC midnight, out of the daily cap (TWITTER_DAILY_TWEET_CAP). Once the cap is hit, Twitter pauses for the rest of the day while RSS keeps running unaffected.",
  rss_today: "New RSS articles digested across all configured RSS feeds since UTC midnight.",
  signals_trades: "Trading signals detected this run, and how many of them resulted in an executed trade.",
  twitter_sources: "Number of enabled X/Twitter source profiles configured in sources.json.",
  rss_sources: "Number of enabled RSS source profiles configured in sources.json.",
  edge_threshold: "The minimum fair-probability edge required before a signal is generated (EDGE_THRESHOLD).",
  max_bet: "The maximum USD size for a single trade (MAX_BET_USD).",
  daily_limit: "The maximum USD loss allowed in a day before live trading halts (DAILY_LOSS_LIMIT_USD).",
  trading_mode: "Whether trades are placed for real on Polymarket (LIVE) or only logged without hitting the exchange (DRY RUN).",
  balance: "Current USDC balance in the configured Polymarket wallet, refreshed on a timer (PORTFOLIO_REFRESH_MINUTES).",
  open_pnl: "Unrealized profit/loss on currently open positions, marked to the latest market price.",
  total_signals: "Total number of trading signals ever detected and logged to trades.db.",
  dry_runs: "Signals that were logged but not sent as real orders, because dry-run mode was on or the signal wasn't live-eligible.",
  executed: "Signals that resulted in an actual order placed on Polymarket.",
  errors: "Signals that failed during execution due to an error.",
  daily_exposure: "Total USD committed to trades placed so far today.",
  total_wagered: "Cumulative USD wagered across all trades ever placed (most recent 100).",
  avg_edge: "Average fair-probability edge across recent signals.",
  best_edge: "The largest fair-probability edge seen among recent signals.",
  classified_24h: "Number of news events run through the resolution classifier in the last 24 hours, broken down below by why non-signals were rejected.",
  scanner_market: "The Polymarket question being tracked. Click to open the market on Polymarket.",
  scanner_volume: "Total trading volume on this market, used to pick the top 10 markets shown here.",
  scanner_mkt: "The market's current YES price — the crowd's implied probability of a YES resolution.",
  scanner_model: "The classification model's estimated fair probability of a YES resolution, based on matched news.",
  scanner_edge: "The absolute gap between the model's fair probability and the market's YES price.",
  scanner_side: "Which side (YES/NO) a detected signal would trade, if any.",
  scanner_bet: "The USD size of the trade a detected signal would place, if any.",
  scanner_status: "Whether a detected signal was a real order (FILLED), a logged-only dry run (DRY RUN), or — for rows with no signal — why one wasn't generated.",
};
function infoIcon(key, label) {
  return `<i class="info-icon" onclick="showInfo('${key}', '${label.replace(/'/g, "\\'")}')" title="What is this?">i</i>`;
}
function showInfo(key, label) {
  const text = INFO_TEXT[key];
  if (!text) return;
  document.getElementById("info-title").textContent = label;
  document.getElementById("info-body").textContent = text;
  document.getElementById("info-overlay").classList.add("open");
}
function closeInfo() {
  document.getElementById("info-overlay").classList.remove("open");
}
document.addEventListener("keydown", e => { if (e.key === "Escape") closeInfo(); });
function render(data) {
  document.getElementById("clock").textContent = data.now;
  document.getElementById("mode").textContent =
    data.mode + "  |  Signals: " + data.portfolio.total_signals;

  const s = data.status;
  document.getElementById("status").innerHTML = `
    <div class="row"><span class="label">Pipeline${infoIcon('pipeline_status', 'Pipeline')}</span><span class="${s.pipeline_status === 'SCANNING' ? 'warn' : 'win'}">${s.pipeline_status}</span></div>
    <div class="row"><span class="label">Model${infoIcon('model', 'Model')}</span><span>${s.openai_model}</span></div>
    <div class="row"><span class="label">Model Calls Today${infoIcon('model_calls_today', 'Model Calls Today')}</span><span>${s.model_calls_today ?? "—"}</span></div>
    <div class="row"><span class="label">Scan Cycle${infoIcon('scan_cycle', 'Scan Cycle')}</span><span>${s.scan_cycle ?? "—"}</span></div>
    <div class="row"><span class="label">Activity${infoIcon('activity', 'Activity')}</span><span class="dim">${s.activity}</span></div>
    <div class="row"><span class="label">Markets Scanned${infoIcon('markets_scanned', 'Markets Scanned')}</span><span>${s.markets_scanned ?? "—"}</span></div>
    <div class="row"><span class="label">Headlines Found${infoIcon('headlines_found', 'Headlines Found')}</span><span>${s.headlines_found ?? "—"}</span></div>
    <div class="row"><span class="label">Tweets Today${infoIcon('tweets_today', 'Tweets Today')}</span><span>${s.tweets_today ?? "—"}${s.tweets_cap ? ` / ${s.tweets_cap}` : ""}</span></div>
    <div class="row"><span class="label">RSS Today${infoIcon('rss_today', 'RSS Today')}</span><span>${s.rss_today ?? "—"}</span></div>
    <div class="row"><span class="label">Signals / Trades${infoIcon('signals_trades', 'Signals / Trades')}</span><span>${s.signals ?? "—"} / ${s.trades ?? "—"}</span></div>
    <div class="row"><span class="label">Twitter Sources${infoIcon('twitter_sources', 'Twitter Sources')}</span><span>${s.twitter_sources}</span></div>
    <div class="row"><span class="label">RSS Sources${infoIcon('rss_sources', 'RSS Sources')}</span><span>${s.rss_sources}</span></div>
    <div class="row"><span class="label">Edge Threshold${infoIcon('edge_threshold', 'Edge Threshold')}</span><span>&gt;= ${(s.edge_threshold * 100).toFixed(0)}%</span></div>
    <div class="row"><span class="label">Max Bet${infoIcon('max_bet', 'Max Bet')}</span><span>$${s.max_bet.toFixed(2)}</span></div>
    <div class="row"><span class="label">Daily Limit${infoIcon('daily_limit', 'Daily Limit')}</span><span>$${s.daily_limit.toFixed(2)}</span></div>
    <div class="row"><span class="label">Mode${infoIcon('trading_mode', 'Mode')}</span><span class="${data.mode === 'LIVE' ? 'win' : 'warn'}">${data.mode}</span></div>
  `;

  const p = data.portfolio;
  const balanceKnown = p.balance_usd !== null;
  const pnlKnown = p.open_pnl_usd !== null;
  const syncedTitle = p.portfolio_updated_at ? `Last checked ${p.portfolio_updated_at}` : "Not yet checked";
  document.getElementById("portfolio").innerHTML = `
    <div class="row"><span class="label" title="${syncedTitle}">Balance${infoIcon('balance', 'Balance')}</span><span class="${balanceKnown ? 'win' : 'dim'}">${balanceKnown ? "$" + p.balance_usd.toFixed(2) : "—"}</span></div>
    <div class="row"><span class="label" title="${syncedTitle}">Open P&amp;L${infoIcon('open_pnl', 'Open P&L')}</span><span class="${!pnlKnown ? 'dim' : (p.open_pnl_usd >= 0 ? 'win' : 'loss')}">${pnlKnown ? (p.open_pnl_usd >= 0 ? "+" : "") + "$" + p.open_pnl_usd.toFixed(2) : "—"}</span></div>
    <div class="row"><span class="label">Total Signals${infoIcon('total_signals', 'Total Signals')}</span><span class="win">${p.total_signals}</span></div>
    <div class="row"><span class="label">Dry Runs${infoIcon('dry_runs', 'Dry Runs')}</span><span class="warn">${p.dry_runs}</span></div>
    <div class="row"><span class="label">Executed${infoIcon('executed', 'Executed')}</span><span class="win">${p.executed}</span></div>
    ${p.errors ? `<div class="row"><span class="label">Errors${infoIcon('errors', 'Errors')}</span><span class="loss">${p.errors}</span></div>` : ""}
    <div class="row"><span class="label">Daily Exposure${infoIcon('daily_exposure', 'Daily Exposure')}</span><span class="win">$${p.daily_exposure.toFixed(2)}</span></div>
    <div class="row"><span class="label">Total Wagered${infoIcon('total_wagered', 'Total Wagered')}</span><span class="win">$${p.total_wagered.toFixed(2)}</span></div>
    <div class="row"><span class="label">Avg Edge${infoIcon('avg_edge', 'Avg Edge')}</span><span class="win">${p.avg_edge.toFixed(1)}%</span></div>
    ${p.best_edge !== null ? `<div class="row"><span class="label">Best Edge${infoIcon('best_edge', 'Best Edge')}</span><span class="win">${(p.best_edge * 100).toFixed(1)}%</span></div>` : ""}
    <div class="row"><span class="label">Classified (24h)${infoIcon('classified_24h', 'Classified (24h)')}</span><span class="win">${p.classified_24h}</span></div>
    ${p.top_rejection_reasons.map(r => `<div class="row"><span class="label dim">&nbsp;&nbsp;${r.label}</span><span class="dim">${r.count}</span></div>`).join("")}
  `;

  let scannerRows = "";
  if (!data.scanner_rows.length) {
    scannerRows = `<tr><td class="dim empty" colspan="8">Waiting for first scan...</td></tr>`;
  } else {
    scannerRows = data.scanner_rows.map(r => `
      <tr class="${r.is_signal ? '' : 'dim'}">
        <td data-label="Market">${marketCell(r.question, r.url)}</td>
        <td data-label="Vol" class="num">${r.volume}</td>
        <td data-label="Mkt$" class="num">${r.mkt_price.toFixed(2)}</td>
        <td data-label="Model" class="num win">${r.model_confidence.toFixed(2)}</td>
        <td data-label="Edge" class="num ${r.is_signal ? 'win' : ''}">${(r.edge * 100).toFixed(0)}%</td>
        <td data-label="Side" class="center ${r.side === 'YES' ? 'side-yes' : 'side-no'}">${r.side ?? "—"}</td>
        <td data-label="Bet" class="num">${r.bet !== null ? "$" + r.bet.toFixed(0) : "—"}</td>
        <td data-label="Status" class="center ${statusClass(r.status)}">${r.is_signal ? statusLabel(r.status) : r.reason_label}</td>
      </tr>`).join("");
  }
  document.getElementById("scanner").innerHTML = `
    <div class="table-scroll">
    <table>
      <tr>
        <th>Market${infoIcon('scanner_market', 'Market')}</th>
        <th class="num">Vol${infoIcon('scanner_volume', 'Volume')}</th>
        <th class="num">Mkt$${infoIcon('scanner_mkt', 'Mkt$')}</th>
        <th class="num">Model${infoIcon('scanner_model', 'Model')}</th>
        <th class="num">Edge${infoIcon('scanner_edge', 'Edge')}</th>
        <th class="center">Side${infoIcon('scanner_side', 'Side')}</th>
        <th class="num">Bet${infoIcon('scanner_bet', 'Bet')}</th>
        <th class="center">Status${infoIcon('scanner_status', 'Status')}</th>
      </tr>
      ${scannerRows}
    </table>
    </div>`;

  let tradeRows = "";
  if (!data.trade_rows.length) {
    tradeRows = `<tr><td class="dim empty" colspan="11">No trades yet — pipeline scanning...</td></tr>`;
  } else {
    tradeRows = data.trade_rows.map(t => `
      <tr>
        <td data-label="Time" class="dim">${t.time}</td>
        <td data-label="Market">${marketCell(t.question, t.url)}</td>
        <td data-label="Side" class="center ${t.side === 'YES' ? 'side-yes' : 'side-no'}">${t.side}</td>
        <td data-label="Bet" class="num">$${t.bet.toFixed(2)}</td>
        <td data-label="Edge" class="num">${(t.edge * 100).toFixed(0)}%</td>
        <td data-label="Model" class="num">${t.model.toFixed(2)}</td>
        <td data-label="Entry" class="num">${t.entry_price.toFixed(4)}</td>
        <td data-label="Mkt$" class="num">${t.mkt_price.toFixed(4)}</td>
        <td data-label="P&L¢" class="num ${t.pnl_cents > 0 ? 'win' : (t.pnl_cents < 0 ? 'loss' : '')}">${t.pnl_cents >= 0 ? "+" : ""}${t.pnl_cents.toFixed(1)}¢</td>
        <td data-label="P&L%" class="num ${t.pnl_pct > 0 ? 'win' : (t.pnl_pct < 0 ? 'loss' : '')}">${t.pnl_pct >= 0 ? "+" : ""}${t.pnl_pct.toFixed(1)}%</td>
        <td data-label="Status" class="center ${statusClass(t.status)}">${statusLabel(t.status)}</td>
      </tr>`).join("");
  }
  document.getElementById("trades").innerHTML = `
    <div class="table-scroll">
    <table>
      <tr><th>Time</th><th>Market</th><th class="center">Side</th><th class="num">Bet</th><th class="num">Edge</th><th class="num">Model</th><th class="num">Entry</th><th class="num">Mkt$</th><th class="num">P&L¢</th><th class="num">P&L%</th><th class="center">Status</th></tr>
      ${tradeRows}
    </table>
    </div>`;

  const cov = document.getElementById("coverage");
  const covPanel = document.getElementById("coverage-panel");
  if (!data.coverage_gaps.length) {
    cov.className = "ok";
    cov.innerHTML = `No gaps — every tracked market has a matchable entity + predicate concept.`;
    covPanel.querySelector("h2").textContent = "Matcher Coverage Gaps";
  } else {
    cov.className = "";
    covPanel.querySelector("h2").textContent = `Matcher Coverage Gaps (${data.coverage_gaps.length})`;
    cov.innerHTML = data.coverage_gaps.map(g => `
      <div class="row">
        <span>${marketCell(g.question, g.url)}${g.missing.map(m => `<span class="gap-badge">missing ${m}</span>`).join("")}</span>
      </div>`).join("");
  }

  let newsRows = "";
  if (!data.latest_headlines.length) {
    newsRows = `<tr><td class="dim empty" colspan="3">Waiting for news feed...</td></tr>`;
  } else {
    newsRows = data.latest_headlines.map(h => `
      <tr>
        <td data-label="Time" class="dim">${h.time}</td>
        <td data-label="Source">${h.source}</td>
        <td data-label="Headline">${marketCell(h.headline, h.url)}</td>
      </tr>`).join("");
  }
  document.getElementById("news").innerHTML = `
    <div class="table-scroll">
    <table>
      <tr><th>Time</th><th>Source</th><th>Headline</th></tr>
      ${newsRows}
    </table>
    </div>`;
}

async function poll() {
  try {
    const res = await fetch("/api/state");
    render(await res.json());
  } catch (e) {
    console.error(e);
  } finally {
    setTimeout(poll, 2000);
  }
}
poll();
</script>
</body>
</html>
"""


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _format_ago(elapsed_hours: float) -> str:
    """Render elapsed time as HH:MM ago. Clock skew or feeds publishing a
    timestamp slightly in the future can make elapsed_hours negative; treat
    that as "just now" rather than showing a nonsensical negative duration."""
    total_minutes = max(round(elapsed_hours * 60), 0)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d} ago"


def _portfolio_snapshot() -> dict:
    """Wallet balance + open P&L (refreshed on its own timer, see
    _portfolio_loop) plus a trade performance summary sourced from trades.db —
    the latter is identical regardless of whether dashboard.py's polling loop
    or the live PipelineV2 (`watch`) produced the trades."""
    stats = logger.get_trade_stats()
    perf_trades = logger.get_recent_trades(limit=100)
    daily_spent = abs(logger.get_daily_pnl())

    by_status = stats["by_status"]
    errors = sum(v for k, v in by_status.items() if k.startswith("error"))
    total_wagered = sum(t.get("amount_usd", 0) for t in perf_trades)
    avg_edge = sum(t.get("edge", 0) for t in perf_trades) / max(len(perf_trades), 1) * 100
    best_edge = max((t.get("edge", 0) for t in perf_trades), default=None)

    cls_stats = logger.get_classification_stats(hours=24)
    top_rejections = sorted(
        ((reason, count) for reason, count in cls_stats["by_rejection_reason"].items()
         if reason != "signal"),
        key=lambda item: -item[1],
    )[:3]

    return {
        "balance_usd": _PORTFOLIO_STATE["balance_usd"],
        "open_pnl_usd": _PORTFOLIO_STATE["open_pnl_usd"],
        "portfolio_updated_at": _PORTFOLIO_STATE["updated_at"],
        "total_signals": stats["total_trades"],
        "dry_runs": by_status.get("dry_run", 0),
        "executed": by_status.get("executed", 0),
        "errors": errors,
        "daily_exposure": daily_spent,
        "total_wagered": total_wagered,
        "avg_edge": avg_edge,
        "best_edge": best_edge,
        "classified_24h": cls_stats["total"],
        "top_rejection_reasons": [
            {"label": REASON_LABELS.get(reason, reason), "count": count}
            for reason, count in top_rejections
        ],
    }


def _coverage_gap_rows(gaps: list[CoverageGap]) -> list[dict]:
    return [{
        "question": gap.question[:80],
        "url": gap.url or None,
        "missing": list(gap.missing),
    } for gap in gaps]


def _recent_trade_rows(limit: int = _TRADE_LOG_LIMIT) -> list[dict]:
    trades = logger.get_recent_trades(limit=limit)
    rows = []
    for t in trades:
        # trades.market_price is always the YES price; convert to the price
        # of the side actually traded so Entry/Mkt$/P&L read as what was paid.
        yes_entry = t["market_price"]
        yes_mkt = _TRADE_PRICE_CACHE.get(t["market_id"], yes_entry)
        is_yes = t["side"] == "YES"
        entry_price = yes_entry if is_yes else 1 - yes_entry
        mkt_price = yes_mkt if is_yes else 1 - yes_mkt
        pnl_delta = (mkt_price - entry_price) + 0.0  # avoid -0.0 display
        rows.append({
            "time": t["created_at"][:16],
            "question": t["market_question"][:60],
            "url": t["market_url"] or None,
            "side": t["side"],
            "bet": t["amount_usd"],
            "edge": t["edge"],
            "model": t["claude_score"],
            "entry_price": entry_price,
            "mkt_price": mkt_price,
            "pnl_cents": pnl_delta * 100,
            "pnl_pct": (pnl_delta / entry_price * 100) if entry_price else 0.0,
            "status": t["status"],
        })
    return rows


def _build_polling_payload() -> dict:
    """Snapshot dashboard.state (its own scan loop) as JSON-serializable data."""
    if state.scanning:
        pipeline_status = "SCANNING"
    elif state.run_number > 0:
        pipeline_status = "ACTIVE"
    else:
        pipeline_status = "STARTING"

    signals_by_question = {sig["market"].question: sig for sig in state.latest_signals}
    top_markets = sorted(state.latest_markets, key=lambda m: m.volume, reverse=True)[:10]
    top_markets = sorted(top_markets, key=lambda m: m.yes_price, reverse=True)

    scanner_rows = []
    for m in top_markets:
        sig = signals_by_question.get(m.question)
        score = state.latest_scores.get(m.condition_id, {})
        confidence = sig["score"]["confidence"] if sig else score.get("confidence", 0.5)
        scanner_rows.append({
            "question": m.question[:60],
            "url": m.url or None,
            "volume": _format_volume(m.volume),
            "mkt_price": m.yes_price,
            "model_confidence": confidence,
            "edge": sig["score"]["edge"] if sig else score.get("edge", abs(confidence - m.yes_price)),
            "side": sig["trade"]["side"] if sig else None,
            "bet": sig["trade"]["amount"] if sig else None,
            "status": sig["trade"].get("status", "dry_run") if sig else None,
            "is_signal": sig is not None,
            "reason_label": REASON_LABELS.get(score.get("reason", ""), "no data"),
        })

    latest_headlines = [
        {
            "time": _format_ago(h["age_hours"]),
            "source": h["source"],
            "headline": h["headline"],
            "url": h.get("url"),
        }
        for h in sorted(state.latest_headlines, key=lambda h: h["age_hours"])[:10]
    ]

    return {
        "now": _now_str(),
        "mode": "DRY RUN" if config.DRY_RUN else "LIVE",
        "status": {
            "pipeline_status": pipeline_status,
            "openai_model": config.OPENAI_MODEL,
            "model_calls_today": classifier.model_calls_today(),
            "scan_cycle": state.run_number or None,
            "activity": state.scan_status,
            "markets_scanned": state.markets_scanned if state.run_number else None,
            "headlines_found": state.headlines_found if state.run_number else None,
            "signals": state.signals_found if state.run_number else None,
            "trades": state.trades_executed if state.run_number else None,
            "twitter_sources": _TWITTER_SOURCE_COUNT,
            "rss_sources": _RSS_SOURCE_COUNT,
            "edge_threshold": config.EDGE_THRESHOLD,
            "max_bet": config.MAX_BET_USD,
            "daily_limit": config.DAILY_LOSS_LIMIT_USD,
            "tweets_today": None,
            "tweets_cap": None,
            "rss_today": None,
        },
        "portfolio": _portfolio_snapshot(),
        "scanner_rows": scanner_rows,
        "trade_rows": _recent_trade_rows(),
        "coverage_gaps": _coverage_gap_rows(find_coverage_gaps(state.latest_markets)),
        "latest_headlines": latest_headlines,
    }


def _build_attached_payload(pipeline) -> dict:
    """Read-only snapshot of a live PipelineV2 (`watch`) — no independent
    scanning, since the pipeline itself already handles that."""
    stats = pipeline.stats
    tracked = pipeline.market_watcher.tracked_markets

    top_markets = sorted(tracked, key=lambda m: m.volume, reverse=True)[:10]
    top_markets = sorted(top_markets, key=lambda m: m.yes_price, reverse=True)

    scanner_rows = []
    for m in top_markets:
        score = pipeline.latest_scores.get(m.condition_id)
        confidence = score["confidence"] if score else 0.5
        edge = score["edge"] if score else 0.0
        side = score["side"] if score else None
        reason = score.get("reason", "") if score else ""
        scanner_rows.append({
            "question": m.question[:60],
            "url": m.url or None,
            "volume": _format_volume(m.volume),
            "mkt_price": m.yes_price,
            "model_confidence": confidence,
            "edge": edge,
            "side": side,
            "bet": None,
            "status": None,
            "is_signal": side is not None,
            "reason_label": REASON_LABELS.get(reason, "no data"),
        })

    recent_events = logger.get_recent_news_events(limit=10)
    now = datetime.now(timezone.utc)
    for e in recent_events:
        received = datetime.fromisoformat(e["received_at"])
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
        e["_received_dt"] = received
    recent_events.sort(key=lambda e: e["_received_dt"], reverse=True)
    latest_headlines = [
        {
            "time": _format_ago((now - e["_received_dt"]).total_seconds() / 3600),
            "source": e["source"],
            "headline": e["headline"],
            "url": e.get("url"),
        }
        for e in recent_events
    ]

    return {
        "now": _now_str(),
        "mode": "DRY RUN" if config.DRY_RUN else "LIVE",
        "status": {
            "pipeline_status": "ACTIVE" if pipeline.running else "STARTING",
            "openai_model": config.OPENAI_MODEL,
            "model_calls_today": classifier.model_calls_today(),
            "scan_cycle": None,
            "activity": (
                f"news:{stats['news_processed']}  matched:{stats['markets_matched']}"
            ),
            "markets_scanned": len(tracked),
            "headlines_found": stats["news_processed"],
            "signals": stats["signals_found"],
            "trades": stats["trades_executed"],
            "twitter_sources": _TWITTER_SOURCE_COUNT,
            "rss_sources": _RSS_SOURCE_COUNT,
            "edge_threshold": config.EDGE_THRESHOLD,
            "max_bet": config.MAX_BET_USD,
            "daily_limit": config.DAILY_LOSS_LIMIT_USD,
            "tweets_today": pipeline.news_aggregator.twitter.tweets_processed_today,
            "tweets_cap": pipeline.news_aggregator.twitter.daily_tweet_cap,
            "rss_today": pipeline.news_aggregator.rss_items_processed_today,
        },
        "portfolio": _portfolio_snapshot(),
        "scanner_rows": scanner_rows,
        "trade_rows": _recent_trade_rows(),
        "coverage_gaps": _coverage_gap_rows(pipeline.market_watcher.coverage_gaps),
        "latest_headlines": latest_headlines,
    }


def build_state_payload() -> dict:
    if _ACTIVE_PIPELINE is not None:
        return _build_attached_payload(_ACTIVE_PIPELINE)
    return _build_polling_payload()


class DashboardRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # keep stdout quiet; rely on the scan loop's own status

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            body = INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/state":
            body = json.dumps(build_state_payload()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


def _scan_loop(scan_interval: float, stop_event: threading.Event):
    while not stop_event.is_set():
        run_scan_cycle()
        stop_event.wait(scan_interval)


def run_web_dashboard(scan_interval: float = 60.0, host: str = "127.0.0.1", port: int | None = None):
    """Launch the web dashboard: background scan loop + HTTP server."""
    port = port if port is not None else config.DASHBOARD_PORT
    stop_event = threading.Event()
    scan_thread = threading.Thread(
        target=_scan_loop, args=(scan_interval, stop_event), daemon=True
    )
    scan_thread.start()
    _start_portfolio_thread(stop_event)

    server = ThreadingHTTPServer((host, port), DashboardRequestHandler)
    print(f"Web dashboard serving on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        server.shutdown()
        stats = logger.get_trade_stats()
        print(f"\nWeb dashboard stopped. {stats['total_trades']} signals logged across {state.run_number} cycles.")


def start_attached_dashboard(pipeline, host: str = "127.0.0.1", port: int | None = None) -> ThreadingHTTPServer:
    """Serve a read-only view of an already-running PipelineV2 (`watch`).

    Non-blocking: starts the HTTP server on a background thread and returns
    immediately. Does NOT run its own scan loop — the pipeline passed in is
    the only thing scanning/trading; this just mirrors its live state.
    """
    global _ACTIVE_PIPELINE
    _ACTIVE_PIPELINE = pipeline
    port = port if port is not None else config.DASHBOARD_PORT
    _start_portfolio_thread()

    server = ThreadingHTTPServer((host, port), DashboardRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Web dashboard serving on http://{host}:{port}")
    return server


if __name__ == "__main__":
    run_web_dashboard()
