# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Hebrew-oriented event-intelligence pipeline: it monitors reviewed Israeli news sources (RSS, X/Twitter, Telegram), matches reports to the exact resolution rules of active Polymarket markets, and either logs a dry-run signal or places a fail-closed CLOB V2 order. Live trading is gated behind multiple independent safeguards and is off by default.

```text
Hebrew RSS / reviewed X accounts / reviewed Telegram channels
        ↓
Per-source freshness, trust, relevance, and confirmation policy
        ↓
Hebrew normalization + canonical entity/predicate extraction
        ↓
Entity + resolution-predicate match with source-topic routing
        ↓
Resolution evidence / probability evidence / topical / irrelevant
        ↓
Fair-probability comparison → dry-run signal or guarded CLOB V2 order
```

## Commands

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                # Windows: Copy-Item .env.example .env
python cli.py verify                # checks env, deps, API keys, source config
```

Running the pipeline:

```bash
python cli.py watch                 # V2: async event-driven pipeline (recommended, runs indefinitely)
python cli.py watch --live          # same, with live trading enabled
python cli.py run --max 15 --hours 1   # one synchronous scan through the same matcher/classifier/edge logic
python cli.py scrape --hours 1      # news scraper only
python cli.py markets --max 100     # browse active Polymarket markets
python cli.py niche                 # browse volume-filtered niche markets
python cli.py dashboard             # live terminal dashboard
python cli.py backtest --limit 50   # backtest against resolved markets
python cli.py calibrate             # classification accuracy report
python cli.py trades / stats        # trade log / performance stats
```

Tests:

```bash
python -m pytest                    # run full suite
python -m pytest tests/test_matcher.py           # (mirror module names, e.g. tests/test_hebrew_matcher.py)
python -m pytest tests/test_executor_v2.py -k some_test_name
```

There is no formatter/linter configured. Group imports as: standard library, third-party, then local modules. PEP 8, four-space indent, `snake_case`/`PascalCase`/`UPPER_SNAKE_CASE` as usual.

## Architecture

Flat, single-package Python app — no subpackages. Each module owns one pipeline responsibility:

| Module | Responsibility |
| --- | --- |
| `cli.py` | argparse entry point; all `python cli.py <command>` subcommands live here |
| `pipeline.py` | Orchestration: `PipelineV2` (async, event-driven, runs forever) and `run_pipeline()` (sync, one-shot scan). Both follow news → match → classify → edge → execute |
| `source_config.py` | `SourceProfile` dataclass + validation for `sources.json`; every source has its own freshness, trust, relevance, and confirmation policy |
| `news_stream.py` | `NewsAggregator` — RSS/X/Telegram adapters, freshness filtering, independent-source corroboration counting |
| `scraper.py` | Synchronous RSS-only scraping used by `cli.py run`/`scrape` |
| `matcher.py` | Hebrew normalization + canonical entity/predicate extraction; matches headlines to markets (fail-closed: requires shared entity AND resolution predicate) |
| `classifier.py` | Resolution-aware Hebrew/English classification (`resolution_evidence` / `probability_evidence` / topical / irrelevant), sync and async variants |
| `markets.py` | Gamma API market discovery, resolution metadata, token IDs |
| `market_watcher.py` | Live order-book price updates via Polymarket websocket |
| `edge.py` | `Signal` dataclass + fair-probability edge calculation (`detect_edge_v2`) |
| `executor.py` | Fail-closed CLOB V2 execution — see "Live trading safeguards" below |
| `logger.py` | SQLite (`trades.db`) persistence: trades, outcomes, pipeline runs, news events, calibration, exposure reservations, per-market trade locks |
| `telegram_alerts.py` | Trade alert notifications |
| `dashboard.py`, `backtest.py`, `calibrator.py` | Analysis/monitoring tools built on `logger.py` data |
| `config.py` | All env-derived settings; loads `SOURCE_PROFILES` from `sources.json` at import time |

### Source policy model (`sources.json` / `source_config.py`)

Every news source is an independently configured `SourceProfile`: `kind` (rss/twitter/telegram), `independence_group` (for corroboration counting), `max_age_seconds`, `relevance` (0-1), `trust_tier` (1 official → 5 unverified), `min_confirmations`, `allow_live`, and `topics` (capability tags used for market routing, e.g. an airspace market only matches sources tagged `"aviation"`). `sources.example.json` holds optional/disabled templates; `sources.json` holds only what's actually enabled. Events that don't meet their source's `min_confirmations` are suppressed outright, not just labeled — the pipeline can ingest and log a headline while producing zero signals until independently corroborated.

### Matching is fail-closed

A report and a market must share both a named entity and a resolution predicate (`matcher.py`). Topical overlap alone never becomes a signal. Specialist domains add a further source-capability gate via `topics`.

### Live trading safeguards (`executor.py`)

Dry-run (`config.DRY_RUN = True`) is the default and skips all of this. Enabling live trading (`python cli.py watch --live`) requires, in order:

1. `validate_live_configuration()` — private key/address format, signature type, `LIVE_TRADING_ACK` exactly matching the funder address, and every ID in `LIVE_SOURCE_ALLOWLIST` must map to an enabled, `allow_live` source profile.
2. An interactive terminal confirmation where the operator types the full funder address (`cli.py::_confirm_live_trading`).
3. Per-trade: source must be trust-tier 1-2, above `MIN_SOURCE_RELEVANCE`, within its own `max_age_seconds`, meeting `min_confirmations`, and classified as `resolution_evidence` (never `probability_evidence`).
4. A fresh executable price within `MAX_SLIPPAGE_BPS` of the signal's reference price.
5. `logger.py`-backed per-market trade locks (`reserve_market_trade`/`reclaim_market_trade`) so only one live order can be in flight per market at a time, reconciled against Polymarket's own open orders/positions API before stealing a stale lock.
6. Exposure/loss-limit reservations (`MAX_BET_USD <= MAX_OPEN_EXPOSURE_USD <= DAILY_LOSS_LIMIT_USD`).

When changing anything in `executor.py`, preserve this fail-closed ordering — a bug here risks real money.

### Async style

`pipeline.py`'s `PipelineV2` and the news/market-watcher adapters are asyncio-based and run concurrently via `asyncio.gather`. Don't introduce blocking network or database work into event-loop paths; sync-only code (`logger.py`'s sqlite3 calls, `executor.py`'s live order path) is bridged into async via `run_in_executor`.

## Configuration

All settings load from `.env` via `config.py` at import time (see `.env.example` for the full list). Key ones: `OPENAI_API_KEY`/`OPENAI_MODEL` (classification), `DRY_RUN`, `MAX_BET_USD`/`DAILY_LOSS_LIMIT_USD`/`MAX_OPEN_EXPOSURE_USD`/`MAX_SLIPPAGE_BPS`, `MARKET_SEARCH_QUERIES` (Gamma search terms, not a top-volume scan), `SOURCE_CONFIG_PATH` (defaults to `sources.json`, can point to a private file elsewhere). `LIVE_SOURCE_ALLOWLIST` and each source's `allow_live` flag must both be set for a source to be eligible for live orders.

## Deployment

Pushes to `main` trigger `.github/workflows/deploy.yml`, which SSHes into the production host, `git pull --ff-only`s the working directory of the `polymarket-watch.service` user systemd unit, and restarts it. Required GitHub `production` environment secrets: `DEPLOY_HOST`, `DEPLOY_USERNAME`, `DEPLOY_PASSWORD`, `DEPLOY_KNOWN_HOSTS`. Never commit `.env`, private keys, or `trades.db*`.

## Notes

- `py-clob-client-v2` (not the legacy `py-clob-client`) is the CLOB SDK.
- Windows terminals need UTF-8 stream reconfiguration for Hebrew output — see `cli.py::_configure_utf8_streams`; keep this if touching CLI startup.
- Language familiarity (Hebrew sources) is a hypothesis for information advantage, not proven edge — evaluate via `backtest.py`/`calibrator.py` before trusting signals.
