# Plan & progress

> Updated at the end of every work session. A fresh session reads this to know exactly where things stand.
> Feature IDs (F1.4 etc.) refer to acceptance criteria in `docs/SPEC.md`.

## Current phase
**Phase 3: Report v1 (news + SEC filings)**: built on branch `claude/next-task-plan-z90duy`, all automated tests pass. Waiting for the owner to set up `.env` (SEC contact + Gemini key) and try it with real data.

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

- [ ] **Phase 3 — Report v1: news + SEC filings (F4 core)** (built 2026-11-02; waiting for the owner's check with real data)
  - Source plug-in framework (`sources/base.py`), Google News/Yahoo/press-release RSS, SEC EDGAR filings + Form 4.
  - Gemini adapter, per-item summaries + sentiment, report composition with citations, dedupe.
  - "Generate now" button, report history page.
  - **Done when:** F4.1 (developments, rumors, insider part of F4.8, sentiment, sources), F4.2, F4.3 (news + filings), F4.4, F4.6, F4.10–F4.14 pass. AI calls are mocked in tests. *(All have automated tests; see "Done (recent)".)*

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
1. Owner: merge the Phase 3 pull request, update your folder (README, "Updating to a new version"; this includes `uv sync` for the new libraries and `uv run alembic upgrade head` for the new tables), then follow README "Setting up reports" (SEC contact, Gemini key, $10 budget alert).
2. Owner: generate a report for one ticker, then for a watchlist. Check: real headlines and filings appear; the citation numbers jump to the right sources; insider trades look plausible against the SEC's own Form 4 pages; the "Missing" box is empty (or explains itself). Tell Claude anything that looks off.
3. Decide whether to switch on `trusted_outlets_only` in `config/sources.yaml` after seeing which outlets show up.
4. Then Phase 4: options chain + virtual portfolio.

## Done (recent)
- 2026-11-02 Fixed: after merging Phase 3 and running the four update commands, the browser showed "localhost refused to connect". The app wasn't broken (the same steps start cleanly here, with a Phase 2 database and the example `.env`); the README's update steps start with "stop the app" but never gave the command to start it again. The start command is now the last step, with what to look for (`Application startup complete.`). Test: `tests/test_readme.py`.
- 2026-11-02 Phase 3 report v1, in five commits:
  1. Source plug-ins (`app/sources/`): Google News and Yahoo Finance RSS, SEC EDGAR (8-K with press-release exhibit, 10-Q, 10-K, Form 4, 13D/13G); Form 4 buys/sells read in code; `config/sources.yaml` (F4.3, F4.4).
  2. `source_items` table; each URL stored once per ticker; same-headline stories merged; failing sources listed, failed downloads retried next run (F4.11, F4.12).
  3. Gemini adapter behind `app/ai/llm.py`, which only accepts public-content records; batched summaries + bullish/neutral/bearish + rumor flag; plain errors for limit reached / bad key / unknown model (F4.6, F4.13).
  4. Report building: period since the previous report (max 7 days), AI statements kept only when properly cited, sentiment score and insider totals in code with known-answer tests, watchlist summary built in code (F4.1, F4.2, F4.6, F4.8 insider part, F4.14).
  5. Pages: "Generate now" for a watchlist or one ticker (background, with progress), run page with summary on top, single report page, history by ticker and date; Reports in the menu (F4.9 button, F4.10). The app now refuses to start on an out-of-date database and says which command to run.
  Tests: 257 pass (up from 133), all offline. New libraries: feedparser, google-genai, httpx (see DECISIONS).
- 2026-11-01 Fixed: after updating, the browser kept Phase 1's saved stylesheet, so Phase 2 pages showed with the old look (narrow, unstyled lists and buttons). Pages now link to `style.css` and `htmx.min.js` with a version code taken from the file's content, so an updated file always gets a new address. Test in `tests/test_assets.py`. Also confirmed from the owner's screenshots: GOOG/GOOGL market cap shows the whole company (~$3.4T), not one share class.
- 2026-11-01 Owner confirmed Phase 2 locally with real Yahoo data. First try showed the old Phase 1 page: the merge was on GitHub but the owner's folder hadn't been updated with `git pull`. Added "Updating to a new version" steps to the README.
- 2026-11-01 Phase 2 watchlists & stock tracking: `config/settings.yaml` (limits 30 lists / 25 stocks, 15-minute refresh window); Yahoo adapter returns the full F1.4 row and searches US-listed stocks/ETFs; `watchlists` + `watchlist_items` tables (with `user_id`) and new `quotes` columns in a second migration (checked to keep existing Phase 1 data); watchlist rules (limits checked before any fetch/save, unknown tickers not added, per-user ownership); parallel refresh with per-row stale/missing handling; home page lists watchlists; watchlist page with search + one-click Add, Refresh, rename, delete; quote lookup moved to `/lookup` and now uses the refresh window. Tests: 133 pass (up from 47), all offline. New dependencies: PyYAML, python-multipart (see DECISIONS).
- 2026-11-01 Owner ran Phase 1 locally against real Yahoo: `AMZN` (price + day change), `BRK.A` (shown as `BRK-A`) and `ZZZZQ` ("not found") all behaved as expected.
- 2026-11-01 Phase 1 walking skeleton: uv project, FastAPI + Jinja2 + HTMX quote page, SQLite tables `users`/`instruments`/`quotes` with the first Alembic migration, default user at startup (F7.1), Yahoo adapter behind `MarketData`, stale/error handling (F1.6) and "not found" (F1.7) for the quote page, 47 tests (no network), GitHub Actions CI (ruff + pytest) green.
- 2026-11-01 Kickoff: interviewed owner; wrote SPEC, ARCHITECTURE, PLAN, DECISIONS; filled in stack and commands in CLAUDE.md. No application code yet.

## Known issues / bugs
- **Phase 3 test samples are hand-made** (the cloud build machine can't reach Google News, Yahoo or the SEC). They follow each site's published format, but the first real run on the owner's computer is the real test. Things to watch: Google News outlet names, Yahoo's feed still existing, and SEC filing times (we assume EDGAR's "acceptanceDateTime" is US Eastern clock time even though it's labelled UTC).
- News summaries are based on headlines (plus Yahoo's one-line snippet), because we don't download article pages. Filing summaries use the full filing text.
- Same-story merging only catches near-identical headlines. The same event in different words (e.g. "results beat estimates" vs. "record revenue") shows as two stories, and is counted twice in the sentiment score.
- Google News links go through a Google redirect address, not straight to the outlet.
- Form 4s are listed in the insider section but not rated for sentiment.
- `gemini-flash-latest` follows Google's newest Flash model, so summaries may change style when Google updates it. Pin a specific model in `config/settings.yaml` if that becomes a problem.
- Stopping the app during a report run marks that run "didn't finish" at the next start; the items it already collected are kept and the next run picks up from there.
- Two older tests (`test_f7_1_default_user_created_once_at_startup`, `test_startup_explains_missing_database_setup`) build the real report services, which reads `.env`. They make no network calls.
- The test samples (`tests/fixtures/yahoo_history_AAPL_1y.json`, `yahoo_search_amazon.json`) are hand-made in yfinance's format with invented prices. The cloud build machine can't reach Yahoo, so real recorded samples would have to be captured on the owner's computer.
- Next earnings shows "—" when Yahoo hasn't published the next date yet (seen for GOOG/GOOGL right after their October report). Expected, not a bug.
- The owner couldn't fully check the Refresh button because the market was closed. Worth a quick look during market hours: prices and fetch times should change after clicking it.
- Each stock refresh makes three Yahoo requests (prices, share count, calendar). If Yahoo starts rate-limiting, cache market cap and earnings for a day.
- For the Phase 10 security review: the forms have no protection against another website submitting them in the background ("cross-site request forgery"). It's low risk while the app only runs on your own computer, but it must be fixed before it goes online.
- After a failed add/rename, the browser's address bar shows the form's address (e.g. `/watchlists/1/items`). Reloading that page asks to resend the form. Harmless; clicking the list's name or "All watchlists" gets back to normal.
- *(Fixed in Phase 2: the day change was always labelled "today", and the quote page fetched from Yahoo on every lookup.)*

## Handoff note
Phase 3 is built on `claude/next-task-plan-z90duy` (five commits plus docs), all tests green, not yet tried against the real sites. Next session: first ask the owner how the real-data check went and fix what they report (prompt #3), then start Phase 4 with prompt #2. After each merge, remind the owner to update their folder (README, "Updating to a new version").
- Report code map: `app/sources/` (plug-ins; `registry.py` builds them from `config/sources.yaml` + `.env`), `app/services/collect.py` → `summarize.py` → `reports.py` (+ `sentiment.py`, `insider.py`), `app/ai/` (interface + Gemini), pages in `app/web/report_routes.py` and `templates/_ticker_report.html`.
- `create_app(..., report_services=, report_runner=)`: tests pass `ReportServices` wired to `FakeFetch` (fake internet, recorded samples in `tests/fixtures/sources/`) and `FakeLLM`, plus a `RecordingRunner` that runs reports immediately (or holds them, to test the progress page).
- The `sources_now` fixture freezes "now" at 2026-11-02 12:00 UTC for the source and report modules, so the sample dates fall inside the report window.
- A new source = a class with `name`, `label`, `source_type`, `fetch()`, `enrich()` (see `app/sources/base.py`), added in `registry.py`, with a weight in `config/settings.yaml` → `reports.sentiment_weights`.
- Limits and timings are in `config/settings.yaml` (loaded by `app/config.py`, passed to `create_app(..., app_config=...)`). Tests use small limits (3 lists, 2 stocks) from the `app_config` fixture.
- `FakeMarketData` in `tests/conftest.py` can fail for everything (`mode`) or for chosen tickers (`not_found`, `down_for`), and records `calls` and `searches`.
- How the code is laid out: `app/main.py` has `create_app(engine, market_data)`, so tests inject a temporary DB and `FakeMarketData` (`tests/conftest.py`). The test DB is built by running the real Alembic migrations.
- New tables need a model in `app/db/models.py` plus `uv run alembic revision --autogenerate -m "..."`. Review the generated file, which is excluded from ruff.
- Dev-tool note: `httpx2` (Starlette's suggested replacement for `httpx` in tests) was *not* added because it wasn't vetted. A deprecation warning is filtered in `pyproject.toml` instead.
- Gemini access is settled ($10/month Cloud credit) and the covered-call formula is confirmed. Still open from planning: before Phase 8, check the X provider pricing; before Phase 9, confirm the macro indicator list.
