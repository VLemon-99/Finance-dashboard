# Architecture

> The bird's-eye view. Keep this short and current — it's what a fresh agent or reviewer reads to understand the whole system.
> Status: **Phase 1 built** (quote page, `users`/`instruments`/`quotes` tables, Yahoo adapter, CI). Everything else below is the target design.

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
| Database | All stored data; one file locally, Postgres-compatible design. Tables are created by Alembic migrations (`uv run alembic upgrade head`), never automatically at startup | `data/app.db` (git-ignored), `app/db/models.py`, `app/db/migrations/` |
| Configuration | Trusted sources, sentiment weights, limits, thresholds | `config/*.yaml`, `.env` for secrets |

## Data model (main tables)
Every user-owned table has a `user_id` column from day one (one default user for now).
- `users` — id, email (later: password hash).
- `instruments` — one row per stock/ETF or option contract: kind (`stock` | `option`), symbol,
  underlying, option type (call/put), strike, expiration, OCC symbol.
- `quotes` — latest cached market data per instrument: price, previous close, trading-session date, day range, 52-week range, volume, market cap, next earnings date, fetched_at (for "stale" display and the 15-minute refresh window). Shared market data, so no `user_id`.
- `watchlists` (user_id, name; names unique per user, ignoring upper/lower case) and `watchlist_items` (user_id, watchlist, instrument; a stock appears once per list but can be in several lists). Deleting a list deletes its items, not the stored prices.
- `transactions` — user, instrument, side (bought/sold), quantity, price, trade date, price source (auto/manual).
- `spreads` — links two option transactions into one credit spread position.
- Positions & P&L are **calculated** from transactions (not stored separately) to avoid drift.
- `source_items` — fetched content: source type, outlet, URL (unique), published_at, ticker(s), text/summary, sentiment, reason.
- `reports` — user, ticker (or "watchlist summary"), generated_at, period covered, markdown body, structured JSON, missing sources.
- `events` — calendar: date/time (stored UTC, shown US Eastern), type, ticker (optional), impact, source.

## How data flows
**Watchlist page** (Phase 2, `/watchlists/{id}`; the home page `/` lists the watchlists)
1. I open a watchlist; `services/watchlists.py` loads its items (only the current user's lists are reachable).
2. `services/quotes.py` reuses stored quotes younger than the refresh window (`config/settings.yaml`, 15 minutes). Older ones, or all of them when I click **Refresh prices** (`?refresh=1`), are fetched from the `MarketData` provider, a few at a time in parallel (`max_parallel_fetches`). Only the outside calls run in parallel; all database work stays on one thread.
3. Each stock that fails keeps its last stored values, marked "stale" (or "missing" if never fetched), with a note. The other rows are unaffected (F1.6).
4. **Adding a stock:** search by ticker or company name (`services/search.py` → provider search, US-listed stocks/ETFs only), then click Add, or type a ticker. The limit is checked first (nothing fetched or saved if full). The ticker is then checked with the provider: unknown → "not found", not added (F1.7); source down and never seen before → not added, try again later.
5. Changes (create, rename, delete, add, remove) are plain HTML form posts followed by a redirect, so everything works without JavaScript. HTMX only swaps in search results.
6. Later, for options (Phase 4): `options_math` computes greeks and DTE from the cached chain data.

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

**Single-ticker quote lookup** (`/lookup`; was `/` in Phase 1)
1. I type a ticker. `app/tickers.py` checks it looks like a US ticker (`BRK.B` → `BRK-B`); anything else is rejected before any outside call.
2. A stored quote younger than the refresh window is reused. Otherwise the `MarketData` provider (Yahoo) returns the full stock row (see "Where the stock row comes from" below). The quote is saved in `quotes` (one row per instrument, overwritten) and shown with the day change, labelled with its trading day ("today" or e.g. "on Fri, Oct 30"), and the fetch time in US Eastern.
3. If the source fails, the stored row is shown marked "stale" with a note. If the source says "no such ticker" and we have never stored it, the page says "not found" and saves nothing. If we *have* stored it before, it's treated as a source problem (stale), because yfinance sometimes reports "no data" when Yahoo is struggling.
4. With JavaScript on, HTMX swaps just the result box (`/lookup/result`); without it, the form reloads the page (`/lookup?ticker=`).

**Where the stock row comes from** (Yahoo adapter, `app/providers/yahoo.py`)
- One year of daily price bars → last price, previous close, day range and volume (latest bar), 52-week range (highest high / lowest low over the year), and the trading session's date.
- Yahoo's share count × last price → market cap (blank for most ETFs).
- Yahoo's company calendar → next earnings date (the earliest date that is today or later).
- Market cap and earnings are extras: if either call fails, the rest of the row is still shown.

**Rules that apply everywhere**
- Each provider/source has its own cache, rate limit and timeout. A failure in one never breaks a page or a report (F1.6, F4.11).
- Only public content goes to the AI. Portfolio and watchlist data are never sent (F4.13).
- Dates are stored in UTC and displayed in US Eastern time.

## External services
- **Yahoo Finance** via `yfinance` (free, unofficial, no key) for quotes, option chains, IV, history, earnings dates, holders, analyst targets. It can break or rate-limit, which is why it sits behind the `MarketData` interface.
- **SEC EDGAR** (free, no key; must send a contact User-Agent) for filings and Form 4. Set as `SEC_USER_AGENT` in `.env`.
- **Google News RSS / company press-release RSS / blog RSS** (free) via `feedparser`.
- **YouTube channel RSS feeds** (free, no key) to list new videos from trusted channels.
- **Gemini API** (Google AI Studio key `GEMINI_API_KEY`) for summaries, sentiment and video understanding. Billed to the $10/month Google Cloud credit included with the owner's Gemini Pro subscription.
- **FRED API** (free key `FRED_API_KEY`) for macro release dates and, later, macro series.
- **Later:** X/Twitter (provider to be decided, ≤ ~$5/month, key in `.env`); Reddit API (free personal-use app credentials).

## Folder map
```
app/
  main.py          create_app(): builds the web app (tests pass in a temp DB + fake data source); later starts the scheduler
  config.py        settings from environment / .env, plus loading/checking config/settings.yaml
  tickers.py       ticker format rules (shared by services and adapters)
  timeutils.py     UTC storage ↔ US Eastern display
  web/             routes, formatting.py (number/date display), templates/ (HTML pages + HTMX partials), static/ (CSS, vendored htmx.min.js)
  services/        business rules (watchlists, portfolio, options_math, reports, calendar, macro)
  providers/       market data + calendar data adapters (yahoo, fred)
  sources/         research content plug-ins (news_rss, edgar, youtube, blogs, x, reddit)
  ai/              LLM interface + Gemini adapter, prompt templates
  db/              models.py, session.py, migrations/ (Alembic; alembic.ini is in the project root)
  scheduler.py     timed jobs
config/            settings.yaml (limits, refresh window; later weights, thresholds), later sources.yaml (trusted list) and fomc_dates.yaml
data/              app.db and caches (git-ignored)
tests/             automated tests; fixtures/ holds sample provider responses; conftest.py has the fake data source
docs/              spec, architecture, decisions, prompts
```
