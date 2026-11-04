# Architecture

> The bird's-eye view. Keep this short and current — it's what a fresh agent or reviewer reads to understand the whole system.
> Status: **Phases 1–6 built**: quote lookup, watchlists, research reports from news, SEC filings, trusted blogs and YouTube channels (Gemini summaries and video watching, "Generate now", history, an optional weekday schedule that's off by default), the option chain browser (greeks calculated by us, contracts in watchlists) and the virtual portfolio (trades, positions, P&L, short-option returns, credit spreads, expiry outcomes). Phase 6 added the market events calendar (earnings, economic releases, Fed meetings; list and week views; next 3 high-impact events on the home page). Macro and X/Reddit are still the target design.

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
 │  scheduler.py  APScheduler: weekday 7:30am ET report job (off by default)│
 └────────────────────────────────────────────────────────────────────────┘
```

## Components
| Component | What it does | Where it lives |
|---|---|---|
| Web UI | Pages for dashboard, watchlists, option chain, portfolio, reports, calendar, macro. Server-rendered HTML plus HTMX | `app/web/` (routes), `app/web/templates/` |
| Services | Business rules: watchlist limits, recording trades, positions & P&L, report building, calendar merging | `app/services/` |
| Options math | Black-Scholes price & greeks, DTE, capital at risk, annualized return. Pure functions, heavily tested | `app/services/options_math.py` |
| Market data provider | Quotes, search, option chains (with IV), price history, earnings dates, holders, valuation fields. Yahoo via `yfinance` to start | `app/providers/market_data.py` (interface), `app/providers/yahoo.py` |
| Source plug-ins | Each fetches content for a ticker since a date and returns standard "source items" (title, outlet, URL, date, text). `enrich()` downloads extras (filing text, Form 4 trades) for new items only. Built from `config/sources.yaml` by `registry.py`; downloads go through `http.py`. Each trusted blog and YouTube channel is its own source; its feed is downloaded once per run (`feeds.py`) and posts/videos are kept when they mention the ticker or company (`mentions.py`) | `app/sources/` (built: `base.py`, `http.py`, `news_rss.py`, `edgar.py`, `blogs.py`, `youtube.py`, `feeds.py`, `mentions.py`, `registry.py`; later `x.py`, `reddit.py`) |
| AI adapter | Summarize items, rate sentiment, write report sections, watch public YouTube videos (3–5 timed points). Accepts only public-content records. Gemini to start, swappable | `app/ai/llm.py` (interface), `app/ai/gemini.py` (prompts live here too) |
| Report pipeline | Collect + dedupe → watch new videos (once each, capped per run) → summarize → compose (cited statements only) → sentiment score + insider totals in code → store | `app/services/collect.py`, `videos.py`, `summarize.py`, `reports.py`, `sentiment.py`, `insider.py`; pages in `app/web/report_routes.py` |
| Calendar data | Earnings (date, time, before the open / after the close) from Yahoo's earnings calendar, all my tickers in one request (`MarketData.get_earnings`); release dates for CPI, jobs report, PCE and GDP from FRED's release calendar (release times and impact in `config/settings.yaml`); Fed decisions and minutes from a dates file | `app/providers/yahoo.py`, `app/providers/fred.py`, `config/fomc_dates.yaml`; merged in `app/services/calendar.py`, page in `app/web/calendar_routes.py` |
| Scheduler | Runs every watchlist's report on weekdays at 7:30am ET while the app is running. Off unless `schedule.enabled` is true in `config/settings.yaml` | `app/scheduler.py` |
| Database | All stored data; one file locally, Postgres-compatible design. Tables are created by Alembic migrations (`uv run alembic upgrade head`), never automatically at startup | `data/app.db` (git-ignored), `app/db/models.py`, `app/db/migrations/` |
| Configuration | Trusted sources, sentiment weights, limits, thresholds | `config/*.yaml`, `.env` for secrets |

## Data model (main tables)
Every user-owned table has a `user_id` column from day one (one default user for now).
- `users` — id, email (later: password hash).
- `instruments` — one row per stock/ETF or option contract: kind (`stock` | `option`), symbol (for options the OCC-style contract symbol, e.g. `AAPL261120C00260000`), and for options: underlying, option type (call/put), strike, expiration.
- `option_quotes` — latest stored prices per option contract, for every strike of every chain fetched: contract symbol, underlying, expiration, call/put, strike, bid, ask, last, volume, open interest, IV, the stock price sent with the chain, fetched_at. Shared market data, so no `user_id`. Greeks, mid and DTE are not stored; they're calculated when shown.
- `quotes` — latest cached market data per stock: price, previous close, trading-session date, day range, 52-week range, volume, market cap, next earnings date, fetched_at (for "stale" display and the 15-minute refresh window). Shared market data, so no `user_id`.
- `watchlists` (user_id, name; names unique per user, ignoring upper/lower case) and `watchlist_items` (user_id, watchlist, instrument; a stock appears once per list but can be in several lists). Deleting a list deletes its items, not the stored prices.
- `transactions` — user, instrument, side (bought/sold), quantity (shares or contracts), price per share, trade date, price source (auto/manual), for options the stock price at the time, and a reason (`trade`, or how an option ended: `expired` / `assigned` / `exercised`).
- `spreads` — user, the option instrument sold and the one bought, linked as one position: a credit spread (same expiration) or a poor man's covered call (the bought call expires later). Links instruments, not trades; a link only shows while both legs are open and still match.
- Positions & P&L are **calculated** from transactions (not stored separately) to avoid drift: `services/positions.py` replays the trades with the average-cost method.
- `source_items` — public content, so no `user_id`. One row per ticker + URL: source, source type (news/filing/blog/youtube), outlet, title, URL, published_at, SEC form, text, structured data (Form 4 trades; for videos the video id, description and the AI's kept answer: points with times, or why it couldn't be watched), `story_id` (the first item of its story; near-identical headlines share one), and the AI's summary, sentiment, reason, rumor flag.
- `report_runs` — user, label (watchlist name or ticker), watchlist id (no database link, so deleting a list keeps its reports), status/progress, started/finished, the watchlist summary (JSON).
- `reports` — user, run, ticker, generated_at, period covered, the report as structured JSON (statements with citations, insider section, sentiment, sources), missing sources.
- `calendar_events` — fetched calendar events, shared market facts (no `user_id`): kind (earnings/macro), source key (`earnings:AAPL`, `fred:10`), ticker, title, US Eastern day, start time (UTC, empty if unknown), earnings timing, source and link. Each refresh replaces all rows of its source key. Fed meetings aren't stored (they're read from `config/fomc_dates.yaml`); impact and release times are applied from the settings when shown.
- `calendar_fetches` — when each source key was last checked and whether it worked (for once-a-day refreshes, retry after 15 minutes, and stale/missing notes).

## How data flows
**Watchlist page** (Phase 2, `/watchlists/{id}`; the home page `/` lists the watchlists)
1. I open a watchlist; `services/watchlists.py` loads its items (only the current user's lists are reachable).
2. `services/quotes.py` reuses stored quotes younger than the refresh window (`config/settings.yaml`, 15 minutes). Older ones, or all of them when I click **Refresh prices** (`?refresh=1`), are fetched from the `MarketData` provider, a few at a time in parallel (`max_parallel_fetches`). Only the outside calls run in parallel; all database work stays on one thread.
3. Each stock that fails keeps its last stored values, marked "stale" (or "missing" if never fetched), with a note. The other rows are unaffected (F1.6).
4. **Adding a stock:** search by ticker or company name (`services/search.py` → provider search, US-listed stocks/ETFs only), then click Add, or type a ticker. The limit is checked first (nothing fetched or saved if full). The ticker is then checked with the provider: unknown → "not found", not added (F1.7); source down and never seen before → not added, try again later.
5. Changes (create, rename, delete, add, remove) are plain HTML form posts followed by a redirect, so everything works without JavaScript. HTMX only swaps in search results.
6. Option contracts in the list (Phase 4) are refreshed through their option chain (one fetch per stock + expiration date) and shown in their own table with the chain fields plus the stock price. Expired contracts keep their last prices and aren't refreshed.

**Option chain page** (Phase 4, `/options/{ticker}?expiration=…&type=call|put`)
1. `services/options.py` asks the provider for the expiration dates (falling back to the dates we have stored prices for if it fails) and drops expired ones; the earliest is shown unless I pick another.
2. The chain for that date is reused from `option_quotes` if younger than the refresh window; otherwise (or on **Refresh prices**) it's fetched and every strike is stored. If the fetch fails, the stored chain is shown "stale" (F1.6).
3. The risk-free rate is the 13-week T-bill yield (`^IRX`), remembered in memory for the refresh window; if it can't be fetched, the fallback rate from `config/settings.yaml` is used and the page says so.
4. `services/options_math.py` (pure functions) adds mid, DTE, greeks (Black-Scholes, from the stock price sent with the chain, the IV and the rate), in/out of the money and the nearest strike.
5. **Add** on a row puts that contract into the chosen watchlist (`services/watchlists.add_contract`): only contracts we have stored prices for, not expired, within the list's item limit.

**Recording a trade** (Phase 4, `/portfolio/trade?symbol=…&side=bought|sold`)
1. I click Bought/Sold on a watchlist row, an option chain row, the quote card or the Portfolio page → a form with date (today), quantity and an auto-filled price (refreshed when the date changes; Save refuses a filled-in price that belongs to another date) (`services/portfolio.suggest_price`): today = the option's mid (or last), or the stock's last price; a past date = that day's close from the provider (`get_daily_close`). If there's none, it asks. Option trades also fill in the stock price at the time.
2. On save, the trade is validated and stored in `transactions`. Options can only be traded once their chain has been seen (their prices are stored).

**Portfolio page** (`/portfolio`)
1. `services/portfolio.portfolio_view` loads the user's trades; `services/positions.build_positions` (pure functions) turns them into open and closed positions.
2. Current prices: stocks through `services/quotes.py`, options through `services/options.contract_quotes` (same refresh window, stale/missing and expiry rules as watchlists).
3. Option positions are grouped per stock (`group_by_ticker`: collapsible sections A–Z with contract count and total P&L). Linked credit spreads and poor man's covered calls that still match are shown as one line each, with their legs underneath. Short calls outside spreads are covered by long shares, oldest first, 100 per contract; short puts outside spreads count as cash-secured.
4. `options_math` computes P&L, premium, capital at risk and the locked-in / remaining annualized returns. Uncovered calls get no return figure.
5. Expired option positions ask for their outcome; the answer is stored as closing trades (and a share trade at the strike for assignment / exercise).

**Research report** ("Generate now" on the Reports page or a watchlist; or the optional weekday schedule, `app/scheduler.py`, one run per watchlist)
1. `POST /reports/generate` creates a `report_runs` row and starts `execute_run` in a background thread (one run at a time). The run page polls `/reports/runs/{id}/progress` every 3 seconds (it shows the current step) and reloads when done.
2. Per ticker, the period is the last 7 days (`reports.max_days` in `config/settings.yaml`), however recent the previous report. Stored items aren't downloaded again and already-summarized items aren't sent to the AI again, so only new items cost anything (plus the report's written statements).
3. `services/collect.py` asks each source plug-in for items since then (SEC Form 4s go back 90 days for the insider section). New items are enriched (filing text, Form 4 trades) and stored; near-identical news headlines get the same `story_id`. A failing source is noted as missing; the rest carry on. A whole run collects for **every** ticker first.
4. `services/videos.py` has the AI watch the run's new YouTube videos: newest first, at most `ai.max_videos_per_run` (10), 3 at a time, the first 60 minutes at low resolution. Each answer (3–5 points with times) is kept in the item's `data`, and reused for the same video under another ticker or in a later run, so no video is watched twice. Videos over the limit wait for the next run (still within its 7 days). The video's points become its text for the next step.
5. `services/summarize.py` sends each new story's first item (not Form 4s, not videos still waiting to be watched) to the AI in batches; the answers (summary, bullish/neutral/bearish, reason, rumor flag) are saved on the item. Items the AI says aren't about the ticker are hidden.
6. `services/reports.py` asks the AI for "Major developments" and "Rumors & speculation" statements citing story numbers, and drops any statement that doesn't cite properly. The sentiment score (`sentiment.py`) and insider totals (`insider.py`) are calculated in code.
7. The report is saved as JSON with its period and missing-source list. When all tickers are done, a short summary (score + top development per ticker) is built in code and put on top. Valuation and institutional holders show "coming in a later phase".
8. Pages: `/reports/runs/{id}` (summary + every ticker), `/reports/{id}` (one ticker), `/reports?ticker=&day=` (history).

**Calendar page** (`/calendar`) and the home page's "Coming up" box
1. `services/calendar.my_tickers`: every stock in my watchlists (and behind their option contracts) plus what I hold in the portfolio.
2. `refresh_if_due`: sources not checked for 24 hours (15 minutes after a failure) are fetched: one Yahoo earnings-calendar request for all due tickers (7 days back to 90 ahead), and each FRED release (CPI, jobs, PCE, GDP). New dates replace the stored ones per source; a failure keeps the old ones and is recorded.
3. `load_events` merges stored earnings (tickers that are still mine), stored releases (name, time and impact from the settings), the earnings date saved with watchlist prices for tickers Yahoo's calendar has nothing for, and Fed decisions/minutes from `config/fomc_dates.yaml` (minutes default to three weeks later).
4. The list view shows today plus 13 days without past events; the week view shows Monday to Sunday with every day listed. Filters: my tickers only (economic events hidden), high impact only. Notes list sources that are down or not set up, and a Fed dates file with no future meeting.
5. The home page shows the next 3 high-impact events (it refreshes the same way).

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
- **SEC EDGAR** (free, no key; must send a contact User-Agent) for filings and Form 4. Set as `SEC_USER_AGENT` in `.env`. Requests are spaced 0.15 s apart (the SEC allows 10 a second). Uses the ticker list (`company_tickers.json`, refreshed daily), each company's `submissions` file, and the filing documents.
- **Google News search RSS** (press releases arrive here too) and **Yahoo Finance headline RSS** (free, unofficial) via `feedparser`; trusted blogs' RSS/Atom feeds the same way.
- **YouTube channel feeds** (`https://www.youtube.com/feeds/videos.xml?channel_id=…`, free, no key) to list new videos from trusted channels. Gemini fetches the videos themselves from their public links.
- **Gemini API** (Google AI Studio key `GEMINI_API_KEY`) for summaries, sentiment and video understanding. Billed to the $10/month Google Cloud credit included with the owner's Gemini Pro subscription.
- **FRED API** (free key `FRED_API_KEY`) for macro release dates (`fred/release/dates`, including scheduled future dates) and, later, macro series. The key travels in the address, so addresses are never logged.
- **Later:** X/Twitter (provider to be decided, ≤ ~$5/month, key in `.env`); Reddit API (free personal-use app credentials).

## Folder map
```
app/
  main.py          create_app(): builds the web app (tests pass in a temp DB + fake data source); starts the scheduler if switched on
  config.py        settings from environment / .env, plus loading/checking config/settings.yaml
  tickers.py       ticker format rules (shared by services and adapters)
  timeutils.py     UTC storage ↔ US Eastern display
  market_calendar.py  US trading days: weekends + NYSE holidays (one-off closures from config/settings.yaml)
  web/             routes (routes.py: watchlists + lookup; option_routes.py; portfolio_routes.py; report_routes.py), formatting.py (number/date display), assets.py (version codes on CSS/JS links), templates/ (HTML pages + HTMX partials), static/ (CSS, vendored htmx.min.js)
  services/        business rules: watchlists, quotes, search, options (chains, contract prices, risk-free rate), options_math (pure formulas), portfolio (trades, prices, portfolio page, spreads, expiry), positions (pure: positions from trades), collect, videos (watching YouTube videos once each), summarize, reports, sentiment, insider, calendar (merging events, filters, views) (later macro)
  providers/       market data + calendar data adapters (yahoo: also the earnings calendar; fred: release dates)
  sources/         research content plug-ins: base.py (interface), http.py (downloads), feeds.py (one download per run), mentions.py (does a post mention the ticker?), news_rss.py, edgar.py, blogs.py, youtube.py, registry.py; later x, reddit
  ai/              LLM interface (llm.py) + Gemini adapter with its prompts (gemini.py)
  db/              models.py, session.py, migrations/ (Alembic; alembic.ini is in the project root)
  scheduler.py     the optional weekday report job (APScheduler)
config/            settings.yaml (limits, refresh window, options fallback rate + minimum IV, report settings, sentiment weights, AI model, video limits, schedule), sources.yaml (sources on/off, Google News search, trusted outlets, SEC forms, blogs, YouTube channels, also_known_as names), fomc_dates.yaml (Fed meeting dates)
data/              app.db and caches (git-ignored)
tests/             automated tests; fixtures/ holds sample responses (fixtures/sources/ for feeds and SEC files); conftest.py has the fake market data, fake internet (FakeFetch) and fake AI (FakeLLM)
docs/              spec, architecture, decisions, prompts
```
