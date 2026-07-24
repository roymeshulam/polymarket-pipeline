# Israel Event Intelligence for Polymarket

A Hebrew-oriented event pipeline that monitors reviewed Israeli sources, maps
reports to the exact rules of active Polymarket markets, and remains in dry-run
mode unless strict source and wallet safeguards are explicitly enabled.

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

## Important behavior

- Ynet, Israel Hayom, and Walla RSS feeds are enabled as independent discovery
  sources.
- Both publishers require independent confirmation and are not authorized for
  live trading or aviation-market routing by default.
- Optional X, Telegram, and specialist-source templates remain available in
  `sources.example.json`; `sources.json` contains enabled sources only.
- Every source has its own maximum actionable age and relevance score.
- Topical overlap never becomes a signal.
- Probability evidence can be studied in dry-run mode but cannot trigger a live
  order.
- Live orders require direct resolution evidence, source-level live permission,
  enough independent confirmations, and an environment allowlist.
- The legacy `py-clob-client` has been replaced by
  `py-clob-client-v2==1.1.0`.

## Setup

Python 3.10 or newer is recommended.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python cli.py verify
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python cli.py verify
```

Set `OPENAI_API_KEY` in `.env`. RSS works without an additional API key. Set
`TWITTER_BEARER_TOKEN` or `TELEGRAM_BOT_TOKEN` only when the corresponding
profiles are enabled.

### Automated deployment

Pushes to `main` run `.github/workflows/deploy.yml`, which connects to the
production server, pulls the repository configured as the `WorkingDirectory`
of `polymarket-watch.service`, and restarts the user service installed at
`/home/meshulro/.config/systemd/user/polymarket-watch.service`.

Configure these secrets in the GitHub `production` environment:

- `DEPLOY_HOST`
- `DEPLOY_USERNAME`
- `DEPLOY_PASSWORD`
- `DEPLOY_KNOWN_HOSTS`

The workflow can also be started manually with **Run workflow**.

## Configure news sources

Edit `sources.json`, or copy `sources.example.json` to a private file and set:

```text
SOURCE_CONFIG_PATH=C:\path\to\my-sources.json
```

Each source has an independent policy:

```json
{
  "id": "reviewed_reporter",
  "kind": "twitter",
  "name": "Reviewed reporter",
  "independence_group": "reporter_name",
  "enabled": true,
  "language": "he",
  "query": "from:ACCOUNT_NAME -is:retweet",
  "max_age_seconds": 120,
  "poll_interval_seconds": 60,
  "relevance": 0.9,
  "trust_tier": 2,
  "min_confirmations": 2,
  "allow_live": false,
  "topics": ["israel", "security"]
}
```

Policy fields:

| Field | Meaning |
|---|---|
| `id` | Stable ID used in logs and the live allowlist |
| `kind` | `rss`, `twitter`, or `telegram` |
| `independence_group` | Shared origin used for corroboration counting |
| `max_age_seconds` | Source-specific validity window |
| `poll_interval_seconds` | RSS polling cadence; retained as source metadata elsewhere |
| `relevance` | Editorial relevance from `0.0` to `1.0` |
| `trust_tier` | `1` official through `5` unverified |
| `min_confirmations` | Independent source IDs required |
| `allow_live` | Source-level live permission; keep false while evaluating |
| `topics` | Enforced source capability tags used for market routing |

An enabled Telegram source needs its numeric `channel_id`. A Telegram bot only
receives channel posts when it has been added to that channel with suitable
permissions.

The X adapter creates one tagged Filtered Stream rule per enabled X profile.
Review query volume carefully because X charges for posts delivered.

Active market discovery uses Gamma search instead of scanning unrelated
top-volume markets. Customize the comma-separated search set when needed:

```text
MARKET_SEARCH_QUERIES=Israel,Netanyahu,Gaza,Hamas,Hezbollah,Iran
```

Matching is fail-closed: a report and market must share both a named entity and
a resolution predicate. Specialist domains add another source capability gate.
For example, an airspace market is considered only for profiles explicitly tagged
with `"aviation"`. Add a reviewed aviation profile from `sources.example.json`
when one is available; the general news feeds are intentionally not tagged for
aviation.

Events that do not meet their source's `min_confirmations` policy are suppressed,
not merely labeled. With the default Ynet-only configuration, this means the
pipeline can ingest headlines for inspection but will produce no classified
signals until an independent source corroborates an event.

## Run

```bash
python cli.py watch
python cli.py run --max 15 --hours 1
python cli.py scrape --hours 1
python cli.py markets --max 100
python cli.py dashboard
python cli.py backtest --limit 50
```

`watch` is the recommended continuous path. `run` performs one synchronous pass
through the same event-level matcher, classifier, source-policy, corroboration,
and edge controls. Neither path infers a NO signal from the absence of a relevant
headline.

## Live trading

Dry-run is the default. Do not enable live mode until shadow testing demonstrates
an edge after spread, fees, slippage, false reports, and corrections.

The CLOB V2 SDK derives API credentials from:

```text
POLYMARKET_PRIVATE_KEY=
POLYMARKET_FUNDER_ADDRESS=
POLYMARKET_SIGNATURE_TYPE=
LIVE_TRADING_ACK=
```

To authorize a reviewed source for live evaluation, both controls must agree:

1. Set `"allow_live": true` on a trust-tier 1 or 2 source profile.
2. Add the exact source ID to `LIVE_SOURCE_ALLOWLIST`.

The executor additionally requires:

- direct `resolution_evidence`;
- the source's independent-confirmation threshold;
- the source's own freshness window;
- a fresh executable price within the slippage ceiling;
- bet and exposure limits;
- an interactive funder-address confirmation.

Then, and only then:

```bash
python cli.py watch --live
```

Some newer deposit-wallet/signature flows have had SDK or server-side
compatibility limitations. Verify your exact wallet flow with a minimal-size
order and current Polymarket documentation before relying on unattended
execution.

## Architecture

```text
source_config.py    Typed per-source policy loading and validation
sources.json        Reviewed source registry (no secrets)
news_stream.py      RSS, X and Telegram adapters; freshness and corroboration
matcher.py          Hebrew normalization and canonical concept matching
classifier.py       Resolution-aware Hebrew/English classification
markets.py          Gamma discovery plus resolution metadata and token IDs
market_watcher.py   Live order-book price updates
edge.py             Fair-probability edge calculation
executor.py         Fail-closed CLOB V2 execution
pipeline.py         Async orchestration
logger.py           SQLite audit and calibration data
```

## Evaluation

Measure the system in shadow mode by source and event type:

- time from original publication to receipt;
- time to first market-price movement;
- event-to-market precision;
- direct-resolution-evidence precision;
- correction and retraction losses;
- P&L after spread, fees, and slippage;
- performance at 30 seconds, 2 minutes, 10 minutes, and 1 hour.

Language familiarity is a hypothesis for an information advantage, not proof of
one. Never trade money you cannot afford to lose.
