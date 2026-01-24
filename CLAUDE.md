# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 0. Token-efficient operating rules (MUST)

1. **Read the minimum necessary files** to complete the task (typically 1–3). Do not scan the entire repository by default.
2. **Do not open large/irrelevant artifacts** (images, gifs, logs, databases, exported datasets). In this repo that usually means:
   - `sources/` (screenshots, gifs, images)
   - `docs/full-guide.md` (long-form guide)
   - any `*.db`, `*.sqlite*`, `*.log`, `*.csv`, `*.parquet`, `*.jsonl`
3. Prefer **small, patch-style changes** over broad refactors unless explicitly requested.
4. When responding, default to:
   - a short plan/root cause,
   - a minimal diff (or only changed functions),
   - one concrete command to validate.

## 1. Project overview

**A股智能分析系统 (A-Share Intelligent Analysis System)** — an AI-powered Chinese stock analysis system that generates daily “decision dashboards” with buy/sell signals and pushes them via multiple notification channels. It is designed to support low-cost scheduled execution (e.g., GitHub Actions) and local runs.

Core pipeline (conceptual):

1. Load configuration from environment (`.env`) via `config.py`.
2. Fetch market data via a multi-provider fallback chain (`data_provider/`).
3. Perform technical analysis (`stock_analyzer.py`) and market review (`market_analyzer.py`).
4. (Optional) Fetch news via search providers (`search_service.py`).
5. Generate LLM-based decision summary (`analyzer.py`).
6. Persist daily results to SQLite (`storage.py`).
7. Push notifications (`notification.py`).

## 2. Repository structure (high-signal files)

Use this map to avoid unnecessary file reads.

- `main.py` — entry point and orchestrator (CLI flags, parallelism, end-to-end flow).
- `config.py` — configuration manager; loads settings from `.env` (single source of truth).
- `analyzer.py` — LLM analysis (Gemini primary; OpenAI-compatible fallback) and `AnalysisResult` output.
- `stock_analyzer.py` — technical analysis logic (signals/levels/indicators).
- `market_analyzer.py` — market review (indices/sectors/funds, etc.).
- `storage.py` — SQLAlchemy ORM over SQLite; stores OHLC + indicators/results.
- `notification.py` — message composition + multi-channel delivery.
- `search_service.py` — news/search integration and key rotation/load balancing.
- `scheduler.py` — continuous scheduling mode.
- `webui.py` — Web UI for stock list management.
- `feishu_doc.py` — Feishu document/report helper (if used by current notification/report flow).

Low-signal for most coding tasks (avoid opening unless needed):
- `sources/` — media assets (screenshots/gifs).
- `docs/full-guide.md` — long guide.
- `CHANGELOG.md`, `LICENSE`.

## 3. Common commands

```bash
# Run full analysis
python main.py

# Debug mode (verbose logging)
python main.py --debug

# Dry run (fetch data only, no AI analysis)
python main.py --dry-run

# Market review only
python main.py --market-review

# Stock analysis only (no market review)
python main.py --no-market-review

# Scheduler mode (continuous cron-like operation)
python main.py --schedule

# Launch WebUI for stock list management
python main.py --webui

# Test configuration and integrations
python test_env.py
python test_env.py --llm      # Test AI model
python test_env.py --fetch    # Test data fetching
python test_env.py --notify   # Test notifications
python test_env.py --db       # View database

# Code quality
pip install black flake8 isort bandit
black .
isort .
flake8 . --select=E9,F63,F7,F82
bandit -r . -x ./test_*.py

# Docker
docker-compose up -d
docker-compose logs -f
```

## 4. Architecture

### 4.1 Core modules

- **main.py** — orchestrates the analysis pipeline; uses `ThreadPoolExecutor` for parallel per-stock processing.
- **config.py** — configuration manager; loads `.env` using `python-dotenv`.
- **analyzer.py** — AI analysis using Gemini (primary) or OpenAI-compatible APIs (fallback).
- **storage.py** — SQLAlchemy ORM layer with SQLite. `StockDaily` stores OHLC + technical indicators.
- **notification.py** — multi-channel delivery (WeChat, Feishu, Telegram, Email, Pushover, custom webhooks).
- **market_analyzer.py** — daily market review (indices, sector analysis, north-bound funds).
- **stock_analyzer.py** — technical analysis (e.g., MA crossovers, volume ratio, divergence rate, support/resistance).
- **search_service.py** — news search via Tavily/Bocha/SerpAPI with load balancing across multiple API keys.
- **scheduler.py** — task scheduling for continuous deployment.

### 4.2 Data provider layer (`data_provider/`)

Strategy pattern with automatic fallback chain:

1. **EfinanceFetcher** (Priority 0) — Eastern Money
2. **AkshareFetcher** (Priority 1) — AkShare API
3. **TushareFetcher** (Priority 2) — Tushare Pro
4. **BaostockFetcher** (Priority 3) — Baostock
5. **YfinanceFetcher** (Priority 4) — Yahoo Finance

`DataFetcherManager` handles source selection and basic anti-blocking (e.g., random delays, User-Agent rotation).

### 4.3 Execution flow

```text
main.py → setup_logging → get_config → get_db → DataFetcherManager
  ↓
  for each stock:
    fetch_data → search_news → GeminiAnalyzer.analyze → StockTrendAnalyzer → save to DB → notify (if single mode)
  ↓
  MarketAnalyzer (if enabled) → generate market review
  ↓
  NotificationService.send_daily_report (if batch mode)
```

## 5. Key design patterns

- **Singleton**: Config, DatabaseManager
- **Strategy**: data fetchers with auto-fallback
- **Retry with exponential backoff**: `tenacity` for API calls
- **ThreadPoolExecutor**: parallel stock analysis (default 3 workers to prevent IP blocking)

## 6. Configuration

All settings are loaded from `.env` (see `.env.example` if present).

Key variables (common):
- `GEMINI_API_KEY` or `OPENAI_API_KEY` (one required)
- `STOCK_LIST` (comma-separated stock codes, e.g., `600519,300750`)
- at least one notification channel credential (e.g., Feishu webhook)

Notes for scheduled/CI execution:
- This repo is designed to be runnable in non-interactive environments (e.g., GitHub Actions) by providing env vars via CI secrets.
- Do **not** log secrets.
- Keep concurrency conservative unless explicitly requested.

## 7. Commit convention

Uses [Conventional Commits](https://www.conventionalcommits.org/):
- `feat:` new feature
- `fix:` bug fix
- `docs:` documentation
- `refactor:` code restructuring
- `perf:` performance improvement

## 8. Code style

- Python 3.10+
- Black formatter (line-length=120)
- isort for imports (profile="black")
- PEP 8 with max-line-length=120

## 9. Task-to-file guidance (to reduce context)

When asked to work on a specific area, start by opening only the relevant files:

- **End-to-end pipeline / CLI behavior** → `main.py`, then `config.py`
- **Data fetching issues / fallback** → `data_provider/base.py` + the specific `*_fetcher.py`
- **Indicators / signals** → `stock_analyzer.py` (and `storage.py` only if persistence changes are required)
- **Market review** → `market_analyzer.py`
- **LLM prompt/output fields** → `analyzer.py`
- **News search** → `search_service.py`
- **Notification formatting/delivery** → `notification.py` (+ `feishu_doc.py` if Feishu doc output is involved)
- **Scheduler behavior** → `scheduler.py`
- **Environment/integration diagnosis** → `test_env.py`
