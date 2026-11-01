# Plan & progress

> Updated at the end of every work session. A fresh session reads this to know exactly where things stand.
> Feature IDs (F1.4 etc.) refer to acceptance criteria in `docs/SPEC.md`.

## Current phase
**Phase 2: Watchlists & stock tracking**: done. Merged, and confirmed by the owner on their computer with real Yahoo data. Next: Phase 3.

## Phases
Each phase ends with a working app, passing tests, and the docs updated.

- [x] **Phase 1 — Walking skeleton** (done 2026-11-01; owner confirmed it locally with real Yahoo data)
  - Project set up with `uv`, FastAPI, SQLite + migrations, ruff, pytest, GitHub Actions CI.
  - One page: type a ticker → shows last price, day change and fetched time (from Yahoo), saved in `quotes`.
  - Default user created at startup (F7.1). `.env` loading in place.
  - **Done when:** `uv run uvicorn app.main:app --reload` serves the page; a test for the quote flow passes using recorded sample data (no network); CI is green; F1.6 behaviour (source down → page still loads with error note) works for this page.

- [x] **Phase 2 — Watchlists & stock tracking (F1)** (done 2026-11-01; owner confirmed it locally with real Yahoo data)
  - Ticker/company search, create/rename/delete lists, add/remove items, configurable limits, full stock row fields, Refresh button, stale handling.
  - Simple dashboard home page listing my watchlists.
  - **Done when:** F1.1–F1.7 pass. *(All have automated tests. See "Done (recent)".)*

- [ ] **Phase 3 — Report v1: news + SEC filings (F4 core)**
  - Source plug-in framework (`sources/base.py`), Google News/Yahoo/press-release RSS, SEC EDGAR filings + Form 4.
  - Gemini adapter, per-item summaries + sentiment, report composition with citations, dedupe.
  - "Generate now" button, report history page.
  - **Done when:** F4.1 (developments, rumors, insider part of F4.8, sentiment, sources), F4.2, F4.3 (news + filings), F4.4, F4.6, F4.10–F4.14 pass. AI calls are mocked in tests.

- [ ] **Phase 4 — Options chain + virtual portfolio (F2, F3)**
  - Option chain browser, Black-Scholes greeks, DTE, add contracts to watchlists.
  - Bought/Sold buttons with auto-filled prices, positions and P&L, annualized return for short options, spread linking, expiry handling.
  - **Done when:** F2.1–F2.7 and F3.1–F3.9 pass, including the F2.3 and F3.6 numeric test cases.

- [ ] **Phase 5 — Report v2: YouTube + blogs + morning schedule (F4)**
  - YouTube plug-in (trusted channels' RSS feeds → Gemini summarizes the video), blog RSS plug-in, starter `config/sources.yaml` for me to edit.
  - Weekday 7:30am ET scheduler.
  - **Done when:** F4.5 and F4.9 pass; a 25-ticker run completes in < 10 minutes.

- [ ] **Phase 6 — Calendar (F5)**
  - Watchlist earnings dates, macro releases from FRED, FOMC dates file, week/list views, filters, top-3 on home page.
  - **Done when:** F5.1–F5.6 pass.

- [ ] **Phase 7 — Report v3: valuation + institutional + unusual options (F4.7, F4.8)**
  - Multiples vs. own history and per-ticker peer list, analyst targets, top holders and changes, 13D/G, unusual options volume.
  - **Done when:** F4.7 and F4.8 pass in full.

- [ ] **Phase 8 — X/Twitter + Reddit (F4.3, F4.6)**
  - Settle the X provider (official pay-per-use vs. third-party API; ≤ ~$5/month; within X's terms) and log it in DECISIONS. Add trusted accounts + `$TICKER` search, a hard monthly cap, and an on/off switch. Reddit at low weight.
  - **Done when:** both sources appear in reports with their weights, the cap is enforced (tested), and turning a source off removes it cleanly.

- [ ] **Phase 9 — Macro dashboard (F6)**
  - Confirm the indicator list with me first, then FRED-based panels with charts.
  - **Done when:** F6.1–F6.3 pass.

- [ ] **Phase 10 — Polish, security review, accounts, deploy (F7)**
  - Security review (prompt #7), login (F7.2), move to Postgres if needed, deploy with HTTPS (F7.3), phone-friendly layout.
  - **Done when:** F7.1–F7.3 pass and the security review has no open high-severity findings.

## Next up
1. Phase 3: Report v1 (news + SEC filings).
2. Owner, before Phase 3: create a Gemini API key in Google AI Studio linked to the Google Cloud project that receives the $10/month credit, and set a $10 budget alert (step-by-step given then).

## Done (recent)
- 2026-11-01 Owner confirmed Phase 2 locally with real Yahoo data. First try showed the old Phase 1 page: the merge was on GitHub but the owner's folder hadn't been updated with `git pull`. Added "Updating to a new version" steps to the README.
- 2026-11-01 Phase 2 watchlists & stock tracking: `config/settings.yaml` (limits 30 lists / 25 stocks, 15-minute refresh window); Yahoo adapter returns the full F1.4 row and searches US-listed stocks/ETFs; `watchlists` + `watchlist_items` tables (with `user_id`) and new `quotes` columns in a second migration (checked to keep existing Phase 1 data); watchlist rules (limits checked before any fetch/save, unknown tickers not added, per-user ownership); parallel refresh with per-row stale/missing handling; home page lists watchlists; watchlist page with search + one-click Add, Refresh, rename, delete; quote lookup moved to `/lookup` and now uses the refresh window. Tests: 133 pass (up from 47), all offline. New dependencies: PyYAML, python-multipart (see DECISIONS).
- 2026-11-01 Owner ran Phase 1 locally against real Yahoo: `AMZN` (price + day change), `BRK.A` (shown as `BRK-A`) and `ZZZZQ` ("not found") all behaved as expected.
- 2026-11-01 Phase 1 walking skeleton: uv project, FastAPI + Jinja2 + HTMX quote page, SQLite tables `users`/`instruments`/`quotes` with the first Alembic migration, default user at startup (F7.1), Yahoo adapter behind `MarketData`, stale/error handling (F1.6) and "not found" (F1.7) for the quote page, 47 tests (no network), GitHub Actions CI (ruff + pytest) green.
- 2026-11-01 Kickoff: interviewed owner; wrote SPEC, ARCHITECTURE, PLAN, DECISIONS; filled in stack and commands in CLAUDE.md. No application code yet.

## Known issues / bugs
- The test samples (`tests/fixtures/yahoo_history_AAPL_1y.json`, `yahoo_search_amazon.json`) are hand-made in yfinance's format with invented prices. The cloud build machine can't reach Yahoo, so real recorded samples would have to be captured on the owner's computer.
- Market cap = Yahoo's share count × price. For companies with several share classes (e.g. GOOGL/GOOG), Yahoo's share count may cover one class only, so market cap could read low. Worth a spot-check against Yahoo's website.
- Each stock refresh makes three Yahoo requests (prices, share count, calendar). If Yahoo starts rate-limiting, cache market cap and earnings for a day.
- For the Phase 10 security review: the forms have no protection against another website submitting them in the background ("cross-site request forgery"). It's low risk while the app only runs on your own computer, but it must be fixed before it goes online.
- After a failed add/rename, the browser's address bar shows the form's address (e.g. `/watchlists/1/items`). Reloading that page asks to resend the form. Harmless; clicking the list's name or "All watchlists" gets back to normal.
- *(Fixed in Phase 2: the day change was always labelled "today", and the quote page fetched from Yahoo on every lookup.)*

## Handoff note
Phase 2 is complete, merged into `main`, and confirmed by the owner with real Yahoo data. Start Phase 3 with prompt #2 in `docs/PROMPTS.md`. After each merge, remind the owner to update their folder (README, "Updating to a new version").
- Limits and timings are in `config/settings.yaml` (loaded by `app/config.py`, passed to `create_app(..., app_config=...)`). Tests use small limits (3 lists, 2 stocks) from the `app_config` fixture.
- `FakeMarketData` in `tests/conftest.py` can fail for everything (`mode`) or for chosen tickers (`not_found`, `down_for`), and records `calls` and `searches`.
- How the code is laid out: `app/main.py` has `create_app(engine, market_data)`, so tests inject a temporary DB and `FakeMarketData` (`tests/conftest.py`). The test DB is built by running the real Alembic migrations.
- New tables need a model in `app/db/models.py` plus `uv run alembic revision --autogenerate -m "..."`. Review the generated file, which is excluded from ruff.
- Dev-tool note: `httpx2` (Starlette's suggested replacement for `httpx` in tests) was *not* added because it wasn't vetted. A deprecation warning is filtered in `pyproject.toml` instead.
- Gemini access is settled ($10/month Cloud credit) and the covered-call formula is confirmed. Still open from planning: before Phase 8, check the X provider pricing; before Phase 9, confirm the macro indicator list.
