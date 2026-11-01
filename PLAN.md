# Plan & progress

> Updated at the end of every work session. A fresh session reads this to know exactly where things stand.
> Feature IDs (F1.4 etc.) refer to acceptance criteria in `docs/SPEC.md`.

## Current phase
**Phase 1: Walking skeleton**: built, tested, CI green. One check remains: the owner runs it locally against real Yahoo data (Yahoo is blocked from the cloud machine where it was built). After that, Phase 2.

## Phases
Each phase ends with a working app, passing tests, and the docs updated.

- [x] **Phase 1 — Walking skeleton** (code done 2026-11-01; awaiting owner's local run with real Yahoo data)
  - Project set up with `uv`, FastAPI, SQLite + migrations, ruff, pytest, GitHub Actions CI.
  - One page: type a ticker → shows last price, day change and fetched time (from Yahoo), saved in `quotes`.
  - Default user created at startup (F7.1). `.env` loading in place.
  - **Done when:** `uv run uvicorn app.main:app --reload` serves the page; a test for the quote flow passes using recorded sample data (no network); CI is green; F1.6 behaviour (source down → page still loads with error note) works for this page.

- [ ] **Phase 2 — Watchlists & stock tracking (F1)**
  - Ticker/company search, create/rename/delete lists, add/remove items, configurable limits, full stock row fields, Refresh button, stale handling.
  - Simple dashboard home page listing my watchlists.
  - **Done when:** F1.1–F1.7 pass.

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
1. Owner: merge the Phase 1 pull request, install `uv`, and run the app locally (steps in `README.md` → "Running it on your computer"). Try `AAPL`, `BRK.B` and a made-up ticker like `ZZZZQ`, and report what you see.
2. Phase 2: watchlists & stock tracking (F1.1–F1.7). Before starting, settle which Yahoo call supplies the full F1.4 row (day range, 52-week range, volume, market cap, next earnings).
3. Owner, before Phase 3: create a Gemini API key in Google AI Studio linked to the Google Cloud project that receives the $10/month credit, and set a $10 budget alert (step-by-step given then).

## Done (recent)
- 2026-11-01 Phase 1 walking skeleton: uv project, FastAPI + Jinja2 + HTMX quote page, SQLite tables `users`/`instruments`/`quotes` with the first Alembic migration, default user at startup (F7.1), Yahoo adapter behind `MarketData`, stale/error handling (F1.6) and "not found" (F1.7) for the quote page, 47 tests (no network), GitHub Actions CI (ruff + pytest) green.
- 2026-11-01 Kickoff: interviewed owner; wrote SPEC, ARCHITECTURE, PLAN, DECISIONS; filled in stack and commands in CLAUDE.md. No application code yet.

## Known issues / bugs
- The Yahoo adapter hasn't been tried against real Yahoo yet: the cloud build machine can't reach Yahoo. The error path *was* exercised live (Yahoo blocked → stale/error note shown correctly). Real-data check is "Next up" #1.
- The test sample `tests/fixtures/yahoo_history_AAPL_5d.json` is hand-made in yfinance's format with invented prices. Once the app runs somewhere with Yahoo access, replace it with a real recorded response.
- The quote page fetches from Yahoo on every lookup (no 15-minute cache yet). The refresh window and the Refresh button come in Phase 2 (F1.5).

## Handoff note
Phase 1 is complete on branch `claude/zealous-wright-5wqtbj` (CI green). First check whether the owner ran it locally against real Yahoo data, and fix anything they report before starting Phase 2.
- How the code is laid out: `app/main.py` has `create_app(engine, market_data)`, so tests inject a temporary DB and `FakeMarketData` (`tests/conftest.py`). The test DB is built by running the real Alembic migrations.
- New tables need a model in `app/db/models.py` plus `uv run alembic revision --autogenerate -m "..."`. Review the generated file, which is excluded from ruff.
- Dev-tool note: `httpx2` (Starlette's suggested replacement for `httpx` in tests) was *not* added because it wasn't vetted. A deprecation warning is filtered in `pyproject.toml` instead.
- Gemini access is settled ($10/month Cloud credit) and the covered-call formula is confirmed. Still open from planning: before Phase 8, check the X provider pricing; before Phase 9, confirm the macro indicator list.
