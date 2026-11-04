# Plan & progress

> Updated at the end of every work session. A fresh session reads this to know exactly where things stand.
> Feature IDs (F1.4 etc.) refer to acceptance criteria in `docs/SPEC.md`.

## Current phase
**Phase 4: Options chain + virtual portfolio**: built. Part A (options browser, F2) is merged and checked by the owner with real Yahoo data, after hours and during market hours. Part B (virtual portfolio, F3) is merged and checked by the owner (stock, cash-secured put, covered call and the return figures work; expiry can only be tested after Friday Nov 6). Follow-ups merged (poor man's covered calls, trade-date and after-close price fixes; owner confirmed the automatic prices). Options grouped per ticker and trading-day-only trade dates are merged and confirmed. SEC contact and Gemini key are set up (Gemini needed prepaid credit). On branch `claude/next-task-plan-5yfs0v`, waiting to be merged: reports no longer lose their period after a failed AI run, a plain-words 402 message, no more "-0.00". Next: **Phase 5** (report v2: YouTube, blogs, 7:30am schedule).

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

- [x] **Phase 3 — Report v1: news + SEC filings (F4 core)** (done 2026-11-03; owner confirmed locally with real news; SEC + Gemini real run pending the owner's keys)
  - Source plug-in framework (`sources/base.py`), Google News/Yahoo/press-release RSS, SEC EDGAR filings + Form 4.
  - Gemini adapter, per-item summaries + sentiment, report composition with citations, dedupe.
  - "Generate now" button, report history page.
  - **Done when:** F4.1 (developments, rumors, insider part of F4.8, sentiment, sources), F4.2, F4.3 (news + filings), F4.4, F4.6, F4.10–F4.14 pass. AI calls are mocked in tests. *(All have automated tests; see "Done (recent)".)*

- [ ] **Phase 4 — Options chain + virtual portfolio (F2, F3)**
  - Option chain browser, Black-Scholes greeks, DTE, add contracts to watchlists. *(Part A built and merged 2026-11-03; owner confirmed after hours, market-hours check pending.)*
  - Bought/Sold buttons with auto-filled prices, positions and P&L, annualized return for short options, spread linking, expiry handling. *(Part B built 2026-11-03: F2.5b and F3.1–F3.9 have automated tests, including the three F3.6 known answers; owner check pending.)*
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
1. **Owner, after merging** (README "Updating to a new version"; no database change):
   - **Portfolio layout:** options are grouped per ticker (A–Z, open by default), each header showing contracts and total P&L; your AMZN poor man's covered call appears as one line with both legs under it, inside AMZN.
   - **Trading days only:** on the trade form, pick a Saturday or a holiday (e.g. Nov 27, 2025): you should see why it's closed, and **Save trade** greys out. On a weekend, the form should open on Friday.
   - **After 4 PM ET**, refresh a watchlist: prices should be that day's close (not the previous day's), with the change labelled "today".
   - **Placeholder IVs:** during market hours, look at far out-of-the-money strikes on a later date (where bids are often 0). Do IVs of exactly 50%, 25%, 12.5%… still appear? If yes, we'll hide IV and greeks for strikes with no bid and no ask.
   - **Expiry (after Friday Nov 6, 4 PM ET):** record a contract expiring Friday before then; afterwards the Portfolio page should ask "Expired worthless" or "Assigned".
2. **Phase 5: Report v2** (YouTube + blogs + the weekday 7:30am schedule), after the owner's check. Propose a plan first; the YouTube channel and blog lists come from the owner (`config/sources.yaml`).
3. Owner, after merging the report-period fix: generate one report for a ticker with recent news (e.g. AMZN; its next report reaches back to the last report with AI, up to 7 days). Check that AI summaries appear, the citation numbers jump to the right sources, and an insider trade matches the SEC's own Form 4 page.
4. Owner: after seeing which outlets show up, decide whether to switch on `trusted_outlets_only` in `config/sources.yaml`.

## Done (recent)
- 2026-11-04 Owner set up the SEC contact and Gemini key. SEC filings work (AMZN Form 4 insider sales listed). Gemini first failed with 402 (prepaid credit needed; owner added credit), then worked, but the next report covered only 7 minutes because the failed run counted as a report. Fixed, in two commits: a report's period now starts where the last report **with AI** ended (owner's choice); the 402 is explained in plain words; "-0.00" no longer appears; README "Setting up reports" now includes adding prepaid credit. Tests: 506 pass.
- 2026-11-04 Owner confirmed the per-ticker options layout and trading-day-only trade dates work as intended (PR #12 merged).
- 2026-11-04 Owner confirmed the missing price was a weekend (Nov 30, 2025), not a data gap. At the owner's request, trades can only be dated on trading days: weekends and NYSE holidays (worked out in `app/market_calendar.py`, one-off closures in `config/settings.yaml`) show why they're closed and switch Save off; the form opens on the latest trading day. Tests: 495 pass.
- 2026-11-04 Owner's feedback on the follow-up: automatic trade prices work; poor man's covered call figures correct (AMZN 290/230: 31.11% / 53.92%). Changed at the owner's request: **options grouped per ticker** on the Portfolio page (collapsible sections A–Z, open by default, header with contract count and total P&L; linked pairs as one line with both legs underneath) instead of separate spread / poor-man's-covered-call tables. Also: the trade form now says when a date is a weekend (the owner's "no price before Dec 1, 2025" was Sun Nov 30). Tests: 474 pass.
- 2026-11-04 Follow-up to the owner's portfolio check, in three commits (branch `claude/next-task-plan-5yfs0v`):
  1. **Trade-date bug fixed:** changing the date and pressing Save kept the price filled in for today (a trade dated Oct 1 was saved at Nov 3's 245.63). The price now refreshes when the date changes, and Save refuses a filled-in price that belongs to another date.
  2. **After-close price bug fixed:** at 8 PM, AMZN showed Monday's close (245.63) instead of Tuesday's (~251), because Yahoo's daily history lacks today's row for a while after 4 PM. The app now fills that day from Yahoo's latest regular-market price (after-hours trades excluded).
  3. **Poor man's covered calls (new SPEC F3.10):** link a LEAP call bought and a shorter-dated call sold; capital at risk = what the LEAP cost × 100 × contracts (owner's choice); SPEC test case 60.83% checked end to end. The sold call no longer shows as uncovered. A link whose leg closes simply ends, so the next sold call can be linked to the same LEAP.
  Tests: 471 pass (up from 448), all offline. No new libraries, no database change.
- 2026-11-03 Owner checked Phase 4 part B with real data: stock purchase, cash-secured put, covered call and the annualized returns (8.03% / 16.59% on an AMZN covered call; 78.84% / 81.16% on an NBIS put) all correct. Found: the trade-date bug, the after-close price bug, and the wish to link poor man's covered calls (all handled 2026-11-04).
- 2026-11-03 Phase 4 part B, virtual portfolio (F3, F2.5b), in four commits, with the owner's confirmed defaults (average cost, whole units, no fees, short puts cash-secured, shares cover the oldest calls, delete-not-edit, spread rules, assignment at the strike, no new libraries):
  1. `options_math.py`: P&L, premium, capital at risk (cash-secured put, covered call, credit spread) and annualized return, with the SPEC F3.6 answers (24.83%, 16.22%, 202.78%). Also fixed "-0.000" in the greeks (seen in the owner's screenshots).
  2. Recording trades: `transactions` table; Bought/Sold form with date, quantity and an automatic price you can change (today's mid/last, or a past day's close via the new `get_daily_close`), plus the stock price at the time for options; Bought/Sold links on chain rows, watchlist rows and the quote card; **Portfolio** in the menu with the trade list and Delete (F3.1, F3.2, F2.5b).
  3. Portfolio page: `services/positions.py` (average-cost replay, known-answer tests); stocks and options with current price, P&L $ and %, DTE/IV/delta; cash-secured puts, covered calls (shares allocated oldest first) and "uncovered — no return figure"; premium, capital at risk, locked-in and remaining returns; closed positions with realized P&L; "expiring today" / "expired" (F3.3, F3.4, F3.5, F3.8).
  4. `spreads` table: link/unlink a credit spread, shown and calculated as one position (SPEC F3.6 spread case checked end to end); expired positions ask "Expired worthless" or "Assigned / Exercised" (F3.7, F3.9). Tables fit a laptop screen.
  Tests: 448 pass (up from 361), all offline. Both database changes checked on a copy with Phase 4A data (rows kept; undo works). No new libraries.
- 2026-11-03 Owner checked the AMZN Nov 4 calls during market hours against Yahoo Finance's own options page: bid, ask, last, volume, open interest and the in-the-money shading match exactly. IV reads about 1% lower than Yahoo's page (e.g. 242.0% vs 245.12%). We show Yahoo's own IV field unchanged; with 1 day left the same price implies a higher IV as the day goes on, so the gap fits Yahoo's page being recalculated about half an hour after our download. Not a bug; Refresh fetches Yahoo's latest.
- 2026-11-03 Owner confirmed Phase 4 part A after hours with real Yahoo data (SOFI chain, a put added to a watchlist). Seen in the screenshots: placeholder IVs for strikes with no bid/ask (see Known issues) and "-0.000" greeks (fixed in part B).
- 2026-11-03 Phase 4 part A, options browser (F2), in four commits, with the owner's confirmed defaults (no dividends, T-bill rate with a 4.0% fallback, 15-minute reuse, theta per day, vega per IV point, no new libraries):
  1. `app/services/options_math.py`: Black-Scholes price and greeks, DTE, expiry at 4 PM ET, mid, in the money, nearest strike. Known answers: the SPEC F2.3 case (call 10.4506, delta 0.6368; put 5.5735) and two textbook examples (Hull), plus put-call parity (F2.3).
  2. Yahoo adapter: expiration dates, option chains (bid, ask, last, volume, open interest, IV, stock price) and the 13-week T-bill rate (`^IRX`), behind the `MarketData` interface; hand-made sample `tests/fixtures/yahoo_options_AAPL.json`.
  3. Option chain page (`/options/{ticker}`, **Options** in the menu, links on watchlist rows and the quote card): pick a date, Calls/Puts, Refresh; nearest strike outlined, in-the-money rows shaded; every strike stored in a new `option_quotes` table, stale/missing handling like stocks (F2.1, F2.2, F2.4, F1.5, F1.6). Settings: `options:` in `config/settings.yaml`.
  4. Contracts in watchlists: Add from the chain page into a chosen list (F2.5a); an "Option contracts" table with the F2.2 fields plus the stock price (F2.6); expired contracts marked and not refreshed (F2.7). Contracts count toward the items limit; reports skip them. The home page now counts "items". Also fixed: a wide table could make the whole page scroll sideways.
  Tests: 361 pass (up from 263), all offline. The two database changes were checked on a copy holding Phase 3 data (rows kept; undo works). No new libraries.
- 2026-11-03 Owner ran Phase 3 locally (no keys yet): reports for two watchlists (8 tickers) built, news items collected, SEC and AI correctly listed as missing. Fixed from those screenshots: with SEC filings missing, the insider section said "no open-market buys or sales" / "none" as if it were a fact; it now says the data isn't available. Likewise the sentiment section says "AI ratings are off" instead of "no items were rated". Tests in `tests/test_web_reports.py` and `tests/test_reports.py`.
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
- **Placeholder IVs after hours** (owner's SOFI screenshot, 2026-11-03): strikes with no bid and no ask show IVs of exactly 50%, 25%, 12.5%, 6.3%, 3.1% (Yahoo's dummy values), and a few with only an ask show 242% or 338%. The greeks from these are meaningless. The 1% minimum-IV filter doesn't catch them. Decide after the market-hours check: likely hide IV and greeks when a strike has no bid and no ask.
- Past-date prices for option contracts come from Yahoo's daily history for the contract, which is often missing, so the form usually asks you to type the price. Stocks' past closes are reliable.
- A stock's "today" price on the trade form is its last price (we don't store stock bids/asks).
- If a poor man's covered call's sold call is assigned, "Assigned" records 100 shares sold at the strike, which shows as a short stock position (you'd normally exercise or sell the LEAP; record that as a trade).
- Expired contracts can't be recorded after the fact (Yahoo no longer lists them), so expiry can only be tested with a contract recorded before it expires.
- The portfolio doesn't track cash: every short put outside a spread is treated as cash-secured. Assignment is all-or-nothing (no partial assignment). Trades can't be edited, only deleted and re-entered.
- Positions are recalculated from all trades on every portfolio page view. Fine for hundreds of trades; revisit if it ever feels slow.
- **Phase 4 option samples are hand-made** (`tests/fixtures/yahoo_options_AAPL.json`, invented prices in yfinance's format) because the cloud build machine can't reach Yahoo. The owner's first real run is the real test of the chain parsing and the `^IRX` rate.
- Greeks are Black-Scholes without dividends, so they'll differ a little from a broker's (most for high-dividend stocks and for deep in-the-money puts, which can be exercised early). `^IRX` is a T-bill discount yield used as-is; the difference from a true continuous rate is tiny.
- Outside market hours Yahoo often shows bids/asks of 0, so mid shows "—" and IV may be a placeholder (shown as "—", no greeks). Expected.
- A watchlist refreshes its option contracts one chain at a time (one Yahoo request per stock + expiration date). Fine for a few contracts; if lists hold many contracts across many dates, refresh could get slow.
- The chain page asks Yahoo for the expiration-date list on every visit (not reused for 15 minutes like prices). It's a small request; cache it if Yahoo starts rate-limiting.
- The risk-free rate is remembered only while the app runs; after a restart it's fetched again on the first chain page.
- Without `SEC_USER_AGENT`, the company name isn't known, so Google News is searched by ticker only (`"MU stock"`), which finds fewer and less relevant stories. Fixed by setting up `.env`.
- GOOG and GOOGL in the same list are researched twice (same company, same news). Harmless, but doubles the AI cost for that company. Could merge share classes later if it matters.
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
Phase 4 is merged into `main`; the follow-ups (poor man's covered calls, trade-date and after-close price fixes) are merged; the per-ticker layout and trading-day-only dates are merged; the report-period / 402 / "-0.00" fixes are on branch `claude/next-task-plan-5yfs0v` (506 tests green), not merged yet. Both parts are checked by the owner with real data. Next session: if the owner reports problems from the market-hours check (including the placeholder-IV question in Known issues) or from their first run with SEC/Gemini keys, fix those first (prompt #3); otherwise plan Phase 5. After each merge, remind the owner to update their folder (README, "Updating to a new version").
- Portfolio code map: `app/services/positions.py` (pure: `build_positions()` replays trades), `app/services/portfolio.py` (`resolve_target()`, `suggest_price()`, `record_trade()`, `portfolio_view()`, `link_spread()`, `settle_expired()`), `app/web/portfolio_routes.py`, templates `portfolio.html` / `trade_form.html`. Formulas in `options_math.py`. Option prices for pages come from `options.contract_quotes()` (shared with watchlists).
- Portfolio tests: `tests/test_positions.py` (pure), `test_web_trades.py`, `test_web_portfolio.py`, `test_web_spreads_expiry.py`. They use `options_clock` and open the sample chain first (`client.get(CHAIN_URL)`), as the owner would. `FakeMarketData.get_daily_close` gives the stock sample's closes; set `option_closes[(symbol, date)]` for options.
- Options code map: `app/services/options_math.py` (pure formulas), `app/services/options.py` (`option_chain()` for the chain page, `contract_rows()` for watchlists, `RiskFreeRate` kept on `app.state.risk_free_rate`), `app/web/option_routes.py`, templates `option_chain.html` / `options_start.html`, contracts table in `watchlist.html`. `watchlists.add_contract()` adds a contract; `watchlist_rows()` returns stocks only and `watchlist_contracts()` the contracts.
- Option tests use the `options_clock` fixture (frozen at Tue Nov 3, 2026, 10:00 AM ET; move it with `options_clock.now = ...`). `FakeMarketData` serves the option sample for any ticker (contract symbols re-labelled), with `no_options`, `rate_down`, `chain_calls`, `expiration_calls` and `rate_calls`.
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
