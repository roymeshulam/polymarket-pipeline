# Repository Guidelines

## Project Structure & Module Organization

This repository is a flat Python application. `cli.py` is the command-line entry point, while `pipeline.py` coordinates the synchronous V1 and asynchronous V2 workflows. Core components are split by responsibility: `news_stream.py` and `scraper.py` ingest news, `matcher.py` and `classifier.py` evaluate relevance, `edge.py` sizes opportunities, and `executor.py` handles dry-run or live orders. `logger.py` persists activity to SQLite, and `dashboard.py`, `backtest.py`, and `calibrator.py` provide analysis tools. Configuration belongs in `config.py` and a local `.env` copied from `.env.example`.

There is currently no dedicated `tests/` or assets directory. Add automated tests under `tests/` as the suite grows, mirroring module names (for example, `tests/test_matcher.py`).

## Build, Test, and Development Commands

- `python -m venv .venv` creates an isolated environment.
- `pip install -r requirements.txt` installs runtime dependencies.
- `python cli.py verify` checks configuration, API credentials, and service connectivity.
- `python cli.py watch` starts the event-driven pipeline in dry-run mode.
- `python cli.py run --max 15 --hours 12` performs one synchronous scan.
- `python cli.py backtest --limit 50` evaluates the strategy on resolved markets.
- `python cli.py dashboard` opens the terminal dashboard.

On Unix-like systems, `bash setup.sh` automates environment creation and configuration.

## Coding Style & Naming Conventions

Follow PEP 8 with four-space indentation. Use `snake_case` for functions, variables, and modules; `PascalCase` for classes; and `UPPER_SNAKE_CASE` for configuration constants. Keep modules focused on one pipeline responsibility. Add type hints to public functions and concise docstrings to classes or non-obvious workflows. Preserve the existing async style: do not introduce blocking network or database work into event-loop paths.

No formatter or linter is configured. Keep imports grouped as standard library, third-party, then local modules.

## Testing Guidelines

No automated test framework or coverage threshold is currently committed. Before submitting changes, run `python cli.py verify` and the relevant dry-run or backtest command. New tests should use `pytest`, be named `test_*.py`, avoid live trading, and mock external APIs. Run them with `python -m pytest`.

## Commit & Pull Request Guidelines

Recent commits use short, imperative, sentence-style subjects, such as `Prepare repo for public distribution`; scoped prefixes like `V2:` are used when helpful. Keep each commit focused.

Pull requests should explain the behavior change, list verification commands, identify configuration changes, and link related issues. Include terminal output or screenshots for dashboard changes. Never commit `.env`, API keys, private keys, or generated `trades.db*` files.
