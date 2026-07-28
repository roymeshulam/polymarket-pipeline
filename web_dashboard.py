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

import config
import logger
from dashboard import state, run_scan_cycle
from edge import REASON_LABELS

# Set by start_attached_dashboard() when serving alongside a live PipelineV2
# (i.e. `cli.py watch`). While set, the server reads that pipeline's in-memory
# stats instead of running its own scan loop — running a second independent
# scan+trade loop concurrently with `watch` would double-execute real trades.
_ACTIVE_PIPELINE = None

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Polymarket Pipeline</title>
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20viewBox='0%200%2032%2032'%3E%3Crect%20width='32'%20height='32'%20rx='6'%20fill='%230a0e0a'%20stroke='%234ade80'%20stroke-width='2'/%3E%3Ctext%20x='16'%20y='23'%20font-family='Consolas,monospace'%20font-size='17'%20font-weight='bold'%20fill='%234ade80'%20text-anchor='middle'%3EP%3C/text%3E%3C/svg%3E">
<style>
  :root {
    color-scheme: dark;
    --bg: #0a0e0a; --panel: #10160f; --border: #1e3a1e;
    --accent: #4ade80; --warn: #facc15; --loss: #f87171;
    --muted: #6b7280; --text: #d1fae5;
  }
  * { box-sizing: border-box; }
  html, body { max-width: 100%; overflow-x: hidden; }
  body {
    background: var(--bg); color: var(--text);
    font-family: "Cascadia Code", "Consolas", ui-monospace, monospace;
    font-size: 13px; margin: 0; padding: 12px;
  }
  header {
    display: flex; justify-content: space-between; align-items: center; gap: 10px;
    border: 2px solid var(--accent); padding: 8px 14px; margin-bottom: 10px;
  }
  header h1 { font-size: 15px; color: var(--accent); margin: 0; letter-spacing: 1px; flex-shrink: 0; }
  header .sub { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  header .sub:last-child { flex-shrink: 0; }
  .grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 2fr); gap: 10px; min-width: 0; }
  .col { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
  .panel {
    border: 1px solid var(--border); border-radius: 6px; padding: 10px 12px;
    background: var(--panel); min-width: 0; max-width: 100%; overflow: hidden;
  }
  .panel h2 {
    font-size: 12px; color: var(--accent); margin: 0 0 8px 0;
    text-transform: uppercase; letter-spacing: 1px;
  }
  .row { display: flex; justify-content: space-between; padding: 2px 0; }
  .row .label { color: var(--muted); }
  .table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; max-width: 100%; }
  table { width: 100%; border-collapse: collapse; }
  th, td { text-align: left; padding: 3px 6px; white-space: nowrap; }
  th { color: var(--muted); font-weight: normal; border-bottom: 1px solid var(--border); }
  td.num, th.num { text-align: right; }
  td.center, th.center { text-align: center; }
  .dim { color: var(--muted); }
  .warn { color: var(--warn); }
  .win { color: var(--accent); }
  .loss { color: var(--loss); }
  .side-yes { color: var(--accent); }
  .side-no { color: #e879f9; }
  .mkt-link { color: inherit; text-decoration: none; border-bottom: 1px dotted var(--muted); }
  .mkt-link:hover { color: var(--accent); border-bottom-color: var(--accent); }
  footer {
    border: 2px solid var(--accent); padding: 6px 14px; margin-top: 10px;
    display: flex; justify-content: space-between; gap: 10px; color: var(--muted);
  }
  footer #headline {
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0;
  }
  footer #mode { flex-shrink: 0; }
  @media (max-width: 900px) { .grid { grid-template-columns: 1fr; } }
  @media (max-width: 640px) {
    body { font-size: 12px; padding: 8px; }
    header { padding: 6px 10px; }
    header .sub:first-of-type { display: none; }
    .panel { padding: 9px 10px; }
    .row { gap: 12px; }
    .row > :last-child { min-width: 0; text-align: right; overflow-wrap: anywhere; }
    .table-scroll { overflow: visible; }
    #scanner table, #scanner tbody, #trades table, #trades tbody { display: block; width: 100%; }
    #scanner tr:first-child, #trades tr:first-child { display: none; }
    #scanner tr:not(:first-child), #trades tr:not(:first-child) {
      display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 3px 14px; width: 100%; padding: 7px 0;
      border-bottom: 1px solid var(--border);
    }
    #scanner tr:last-child, #trades tr:last-child { border-bottom: 0; }
    #scanner td, #trades td {
      display: flex; justify-content: space-between; gap: 8px;
      min-width: 0; padding: 2px 0; white-space: normal; text-align: right;
    }
    #scanner td::before, #trades td::before {
      content: attr(data-label); color: var(--muted); text-align: left;
    }
    #scanner td:first-child, #trades td:nth-child(2) {
      grid-column: 1 / -1; padding-bottom: 5px; margin-bottom: 2px;
      border-bottom: 1px dotted var(--border); overflow-wrap: anywhere;
    }
    #scanner td.empty, #trades td.empty {
      grid-column: 1 / -1; display: block; text-align: left; border: 0;
    }
    #scanner td.empty::before, #trades td.empty::before { content: none; }
  }
</style>
</head>
<body>
  <header>
    <h1>POLYMARKET PIPELINE</h1>
    <span class="sub">EVENT MATCHER + RESOLUTION CLASSIFIER + GUARDED TRADER</span>
    <span class="sub" id="clock">—</span>
  </header>
  <div class="grid">
    <div class="col">
      <div class="panel"><h2>Pipeline Status</h2><div id="status"></div></div>
      <div class="panel"><h2>Performance</h2><div id="performance"></div></div>
    </div>
    <div class="col">
      <div class="panel"><h2>Market Scanner</h2><div id="scanner"></div></div>
      <div class="panel"><h2>Trade Log</h2><div id="trades"></div></div>
    </div>
  </div>
  <footer>
    <span id="headline">—</span>
    <span id="mode">—</span>
  </footer>

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
function render(data) {
  document.getElementById("clock").textContent = data.now;
  document.getElementById("mode").textContent =
    data.mode + "  |  Signals: " + data.performance.total_signals;

  const s = data.status;
  document.getElementById("status").innerHTML = `
    <div class="row"><span class="label">Pipeline</span><span class="${s.pipeline_status === 'SCANNING' ? 'warn' : 'win'}">${s.pipeline_status}</span></div>
    <div class="row"><span class="label">Scan Cycle</span><span>${s.scan_cycle ?? "—"}</span></div>
    <div class="row"><span class="label">Activity</span><span class="dim">${s.activity}</span></div>
    <div class="row"><span class="label">Markets Scanned</span><span>${s.markets_scanned ?? "—"}</span></div>
    <div class="row"><span class="label">Headlines Found</span><span>${s.headlines_found ?? "—"}</span></div>
    <div class="row"><span class="label">Tweets Today</span><span>${s.tweets_today ?? "—"}${s.tweets_cap ? ` / ${s.tweets_cap}` : ""}</span></div>
    <div class="row"><span class="label">Signals / Trades</span><span>${s.signals ?? "—"} / ${s.trades ?? "—"}</span></div>
    <div class="row">&nbsp;</div>
    <div class="row"><span class="label">Edge Threshold</span><span>&gt;= ${(s.edge_threshold * 100).toFixed(0)}%</span></div>
    <div class="row"><span class="label">Max Bet</span><span>$${s.max_bet.toFixed(2)}</span></div>
    <div class="row"><span class="label">Daily Limit</span><span>$${s.daily_limit.toFixed(2)}</span></div>
    <div class="row"><span class="label">Mode</span><span class="${data.mode === 'LIVE' ? 'win' : 'warn'}">${data.mode}</span></div>
  `;

  const p = data.performance;
  document.getElementById("performance").innerHTML = `
    <div class="row"><span class="label">Total Signals</span><span class="win">${p.total_signals}</span></div>
    <div class="row"><span class="label">Dry Runs</span><span class="warn">${p.dry_runs}</span></div>
    <div class="row"><span class="label">Executed</span><span class="win">${p.executed}</span></div>
    ${p.errors ? `<div class="row"><span class="label">Errors</span><span class="loss">${p.errors}</span></div>` : ""}
    <div class="row">&nbsp;</div>
    <div class="row"><span class="label">Daily Exposure</span><span class="win">$${p.daily_exposure.toFixed(2)}</span></div>
    <div class="row"><span class="label">Total Wagered</span><span class="win">$${p.total_wagered.toFixed(2)}</span></div>
    <div class="row"><span class="label">Avg Edge</span><span class="win">${p.avg_edge.toFixed(1)}%</span></div>
    ${p.best_edge !== null ? `<div class="row"><span class="label">Best Edge</span><span class="win">${(p.best_edge * 100).toFixed(1)}%</span></div>` : ""}
    <div class="row">&nbsp;</div>
    <div class="row"><span class="label">Classified (24h)</span><span class="win">${p.classified_24h}</span></div>
    ${p.top_rejection_reasons.map(r => `<div class="row"><span class="label dim">&nbsp;&nbsp;${r.label}</span><span class="dim">${r.count}</span></div>`).join("")}
  `;

  let scannerRows = "";
  if (!data.scanner_rows.length) {
    scannerRows = `<tr><td class="dim empty" colspan="7">Waiting for first scan...</td></tr>`;
  } else {
    scannerRows = data.scanner_rows.map(r => `
      <tr class="${r.is_signal ? '' : 'dim'}">
        <td data-label="Market">${marketCell(r.question, r.url)}</td>
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
      <tr><th>Market</th><th class="num">Mkt$</th><th class="num">Model</th><th class="num">Edge</th><th class="center">Side</th><th class="num">Bet</th><th class="center">Status</th></tr>
      ${scannerRows}
    </table>
    </div>`;

  let tradeRows = "";
  if (!data.trade_rows.length) {
    tradeRows = `<tr><td class="dim empty" colspan="8">No trades yet — pipeline scanning...</td></tr>`;
  } else {
    tradeRows = data.trade_rows.map(t => `
      <tr>
        <td data-label="Time" class="dim">${t.time}</td>
        <td data-label="Market">${t.question}</td>
        <td data-label="Side" class="center ${t.side === 'YES' ? 'side-yes' : 'side-no'}">${t.side}</td>
        <td data-label="Bet" class="num">$${t.bet.toFixed(2)}</td>
        <td data-label="Edge" class="num">${(t.edge * 100).toFixed(0)}%</td>
        <td data-label="Model" class="num">${t.model.toFixed(2)}</td>
        <td data-label="Mkt$" class="num">${t.mkt_price.toFixed(2)}</td>
        <td data-label="Status" class="center ${statusClass(t.status)}">${statusLabel(t.status)}</td>
      </tr>`).join("");
  }
  document.getElementById("trades").innerHTML = `
    <div class="table-scroll">
    <table>
      <tr><th>Time</th><th>Market</th><th class="center">Side</th><th class="num">Bet</th><th class="num">Edge</th><th class="num">Model</th><th class="num">Mkt$</th><th class="center">Status</th></tr>
      ${tradeRows}
    </table>
    </div>`;

  document.getElementById("headline").textContent = data.latest_headline
    ? `> ${data.latest_headline.source}: ${data.latest_headline.headline}`
    : "Waiting for news feed...";
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


def _performance_snapshot() -> dict:
    """Trade performance summary — sourced from trades.db, so it's identical
    regardless of whether dashboard.py's polling loop or the live PipelineV2
    (`watch`) produced the trades."""
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


def _recent_trade_rows(limit: int = 10) -> list[dict]:
    trades = logger.get_recent_trades(limit=limit)
    return [{
        "time": t["created_at"][:16],
        "question": t["market_question"][:60],
        "side": t["side"],
        "bet": t["amount_usd"],
        "edge": t["edge"],
        "model": t["claude_score"],
        "mkt_price": t["market_price"],
        "status": t["status"],
    } for t in trades]


def _build_polling_payload() -> dict:
    """Snapshot dashboard.state (its own scan loop) as JSON-serializable data."""
    if state.scanning:
        pipeline_status = "SCANNING"
    elif state.run_number > 0:
        pipeline_status = "ACTIVE"
    else:
        pipeline_status = "STARTING"

    signal_questions = {sig["market"].question for sig in state.latest_signals}

    scanner_rows = []
    for sig in state.latest_signals[:5]:
        m, s, t = sig["market"], sig["score"], sig["trade"]
        scanner_rows.append({
            "question": m.question[:60],
            "url": m.url or None,
            "mkt_price": m.yes_price,
            "model_confidence": s["confidence"],
            "edge": s["edge"],
            "side": t["side"],
            "bet": t["amount"],
            "status": t.get("status", "dry_run"),
            "is_signal": True,
        })
    for m in state.latest_markets:
        if m.question in signal_questions or len(scanner_rows) >= 8:
            continue
        score = state.latest_scores.get(m.condition_id, {})
        confidence = score.get("confidence", 0.5)
        scanner_rows.append({
            "question": m.question[:60],
            "url": m.url or None,
            "mkt_price": m.yes_price,
            "model_confidence": confidence,
            "edge": score.get("edge", abs(confidence - m.yes_price)),
            "side": None,
            "bet": None,
            "status": None,
            "is_signal": False,
            "reason_label": REASON_LABELS.get(score.get("reason", ""), "no data"),
        })

    latest_headline = state.latest_headlines[0] if state.latest_headlines else None

    return {
        "now": _now_str(),
        "mode": "DRY RUN" if config.DRY_RUN else "LIVE",
        "status": {
            "pipeline_status": pipeline_status,
            "scan_cycle": state.run_number or None,
            "activity": state.scan_status,
            "markets_scanned": state.markets_scanned if state.run_number else None,
            "headlines_found": state.headlines_found if state.run_number else None,
            "signals": state.signals_found if state.run_number else None,
            "trades": state.trades_executed if state.run_number else None,
            "edge_threshold": config.EDGE_THRESHOLD,
            "max_bet": config.MAX_BET_USD,
            "daily_limit": config.DAILY_LOSS_LIMIT_USD,
            "tweets_today": None,
            "tweets_cap": None,
        },
        "performance": _performance_snapshot(),
        "scanner_rows": scanner_rows,
        "trade_rows": _recent_trade_rows(),
        "latest_headline": latest_headline,
    }


def _build_attached_payload(pipeline) -> dict:
    """Read-only snapshot of a live PipelineV2 (`watch`) — no independent
    scanning, since the pipeline itself already handles that."""
    stats = pipeline.stats
    tracked = pipeline.market_watcher.tracked_markets

    scanner_rows = []
    for m in tracked[:8]:
        score = pipeline.latest_scores.get(m.condition_id)
        confidence = score["confidence"] if score else 0.5
        edge = score["edge"] if score else 0.0
        side = score["side"] if score else None
        reason = score.get("reason", "") if score else ""
        scanner_rows.append({
            "question": m.question[:60],
            "url": m.url or None,
            "mkt_price": m.yes_price,
            "model_confidence": confidence,
            "edge": edge,
            "side": side,
            "bet": None,
            "status": None,
            "is_signal": side is not None,
            "reason_label": REASON_LABELS.get(reason, "no data"),
        })

    recent_events = logger.get_recent_news_events(limit=1)
    latest_headline = (
        {"source": recent_events[0]["source"], "headline": recent_events[0]["headline"]}
        if recent_events else None
    )

    return {
        "now": _now_str(),
        "mode": "DRY RUN" if config.DRY_RUN else "LIVE",
        "status": {
            "pipeline_status": "ACTIVE" if pipeline.running else "STARTING",
            "scan_cycle": None,
            "activity": (
                f"news:{stats['news_processed']}  matched:{stats['markets_matched']}"
            ),
            "markets_scanned": len(tracked),
            "headlines_found": stats["news_processed"],
            "signals": stats["signals_found"],
            "trades": stats["trades_executed"],
            "edge_threshold": config.EDGE_THRESHOLD,
            "max_bet": config.MAX_BET_USD,
            "daily_limit": config.DAILY_LOSS_LIMIT_USD,
            "tweets_today": pipeline.news_aggregator.twitter.tweets_processed_today,
            "tweets_cap": pipeline.news_aggregator.twitter.daily_tweet_cap,
        },
        "performance": _performance_snapshot(),
        "scanner_rows": scanner_rows,
        "trade_rows": _recent_trade_rows(),
        "latest_headline": latest_headline,
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

    server = ThreadingHTTPServer((host, port), DashboardRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Web dashboard serving on http://{host}:{port}")
    return server


if __name__ == "__main__":
    run_web_dashboard()
