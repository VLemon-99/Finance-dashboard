# Architecture

> The bird's-eye view. Keep this short and current — it's what a fresh agent or reviewer reads to understand the whole system.
> Status: **planned**. No application code exists yet; this describes the target design.

## Overview
A single Python web app runs on my computer and opens in the browser. Pages are built on
the server (FastAPI + HTML templates, with HTMX for small interactive updates), so there is no
separate frontend project. The app gets market data from Yahoo Finance and research content
from feeds like SEC EDGAR, news RSS and YouTube, saves everything in one SQLite database file,
and uses the Gemini API to summarize public content into reports. A built-in scheduler runs the
morning report. Each outside service sits behind a small "adapter", so one can be swapped
(e.g. Yahoo → a paid data feed, Gemini → another AI) without touching the rest.

```
 Browser (localhost:8000)
     │  HTML pages + HTMX requests
     ▼
 ┌──────────────────────── app (Python, FastAPI) ────────────────────────┐
 │  web/        routes + Jinja2 templates (pages, partials)              │
 │     │                                                                  │
 │  services/   watchlists · portfolio · options_math · reports ·         │
 │              calendar · macro          (business rules, no I/O details)│
 │     │                  │                         │                     │
 │  providers/            sources/                  ai/                   │
 │  MarketData            Source plug-ins:          LLM interface         │
 │  (yahoo.py)            news_rss · edgar ·        (gemini.py)           │
 │  Calendar data         youtube · blogs ·                               │
 │  (fred.py, fomc)       x · reddit                                      │
 │     │                                                                  │
 │  db/  SQLModel models + Alembic migrations ──► data/app.db (SQLite)    │
 │  scheduler.py  APScheduler: weekday 7:30am ET report job               │
 └────────────────────────────────────────────────────────────────────────┘
```

## Components
| Component | What it does | Where it lives |
|---|---|---|
| Web UI | Pages for dashboard, watchlists, option chain, portfolio, reports, calendar, macro. Server-rendered HTML plus HTMX | `app/web/` (routes), `app/web/templates/` |
| Services | Business rules: watchlist limits, recording trades, positions & P&L, report building, calendar merging | `app/services/` |
| Options math | Black-Scholes price & greeks, DTE, capital at risk, annualized return. Pure functions, heavily tested | `app/services/options_math.py` |
| Market data provider | Quotes, search, option chains (with IV), price history, earnings dates, holders, valuation fields. Yahoo via `yfinance` to start | `app/providers/market_data.py` (interface), `app/providers/yahoo.py` |
| Source plug-ins | Each fetches content for a ticker since a date and returns standard "source items" (title, outlet, URL, date, text or link) | `app/sources/` (`base.py`, `news_rss.py`, `edgar.py`, `youtube.py`, `blogs.py`, `x.py`, `reddit.py`) |
| AI adapter | Summarize items, rate sentiment, write report sections. Gemini to start, swappable | `app/ai/llm.py` (interface), `app/ai/gemini.py` |
| Report pipeline | Collect → dedupe → trusted-list filter → summarize → weighted sentiment → compose → store | `app/services/reports.py` |
| Calendar data | Earnings from market data provider; macro release dates from FRED; FOMC dates from the Fed's published schedule | `app/providers/fred.py`, `config/fomc_dates.yaml` |
| Scheduler | Runs the weekday 7:30am ET report while the app is running | `app/scheduler.py` |
| Database | All stored data; one file locally, Postgres-compatible design | `data/app.db` (git-ignored), `app/db/` |
| Configuration | Trusted sources, sentiment weights, limits, thresholds | `config/*.yaml`, `.env` for secrets |

## Data model (main tables)
Every user-owned table has a `user_id` column from day one (one default user for now).
- `users` — id, email (later: password hash).
- `instruments` — one row per stock/ETF or option contract: kind (`stock` | `option`), symbol,
  underlying, option type (call/put), strike, expiration, OCC symbol.
- `quotes` — latest cached market data per instrument + fetched_at (for "stale" display).
- `watchlists`, `watchlist_items` — user's named lists → instruments.
- `transactions` — user, instrument, side (bought/sold), quantity, price, trade date, price source (auto/manual).
- `spreads` — links two option transactions into one credit spread position.
- Positions & P&L are **calculated** from transactions (not stored separately) to avoid drift.
- `source_items` — fetched content: source type, outlet, URL (unique), published_at, ticker(s), text/summary, sentiment, reason.
- `reports` — user, ticker (or "watchlist summary"), generated_at, period covered, markdown body, structured JSON, missing sources.
- `events` — calendar: date/time (stored UTC, shown US Eastern), type, ticker (optional), impact, source.

## How data flows
**Watchlist / options page**
1. I open a watchlist; the page asks the services layer for its items.
2. Services return cached quotes; if they're older than the refresh window (or I click Refresh), the Yahoo provider fetches new ones.
3. For options, `options_math` computes greeks and DTE from the cached chain data; the template renders the table.

**Recording a trade**
1. I click Bought/Sold on a row → a small form with date (today), quantity and an auto-filled price (current mid, or that day's close).
2. On save, the trade is validated and stored in `transactions`; positions, P&L and annualized returns are recalculated on display.

**Morning report** (scheduler at 7:30am ET, or "Generate now")
1. For each ticker in my watchlists, each enabled source plug-in fetches new items since the last report (max 7 days).
2. Items are de-duplicated (same URL, or near-identical headlines merged into one story) and filtered by the trusted list.
3. The AI adapter summarizes each new item and rates its sentiment (YouTube videos are summarized from the video link).
4. Services compute the weighted sentiment score, valuation snapshot, insider and holder changes, and unusual options activity. These are numbers computed in code, not by the AI.
5. The AI adapter writes the report sections from the item summaries and numbers. Every claim must cite item IDs, and claims without a citation are dropped.
6. The report (markdown + JSON) is saved. Any source that failed is listed in the report.

**Rules that apply everywhere**
- Each provider/source has its own cache, rate limit and timeout. A failure in one never breaks a page or a report (F1.6, F4.11).
- Only public content goes to the AI. Portfolio and watchlist data are never sent (F4.13).
- Dates are stored in UTC and displayed in US Eastern time.

## External services
- **Yahoo Finance** via `yfinance` (free, unofficial, no key) for quotes, option chains, IV, history, earnings dates, holders, analyst targets. It can break or rate-limit, which is why it sits behind the `MarketData` interface.
- **SEC EDGAR** (free, no key; must send a contact User-Agent) for filings and Form 4. Set as `SEC_USER_AGENT` in `.env`.
- **Google News RSS / company press-release RSS / blog RSS** (free) via `feedparser`.
- **YouTube channel RSS feeds** (free, no key) to list new videos from trusted channels.
- **Gemini API** (Google AI Studio key `GEMINI_API_KEY`) for summaries, sentiment and video understanding. Free tier first.
- **FRED API** (free key `FRED_API_KEY`) for macro release dates and, later, macro series.
- **Later:** X/Twitter (provider to be decided, ≤ ~$5/month, key in `.env`); Reddit API (free personal-use app credentials).

## Folder map
```
app/
  main.py          starts the web app (FastAPI) and the scheduler
  web/             routes and templates/ (HTML pages + HTMX partials)
  services/        business rules (watchlists, portfolio, options_math, reports, calendar, macro)
  providers/       market data + calendar data adapters (yahoo, fred)
  sources/         research content plug-ins (news_rss, edgar, youtube, blogs, x, reddit)
  ai/              LLM interface + Gemini adapter, prompt templates
  db/              database models and migrations (Alembic)
  scheduler.py     timed jobs
config/            sources.yaml (trusted list), settings.yaml (limits, weights, thresholds), fomc_dates.yaml
data/              app.db and caches (git-ignored)
tests/             automated tests (unit tests for math; recorded sample responses for providers)
docs/              spec, architecture, decisions, prompts
```
