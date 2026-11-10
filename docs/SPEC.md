# Specification (product requirements)

> This is the document the loops build from. Every acceptance criterion must be checkable by an automated test — if it can't be tested, rewrite it until it can. Vague criteria produce confident garbage.
> `[x]` = works in the code today and has automated tests (checked 2026-11-10). IDs (e.g. F3.4) are referenced from `PLAN.md`, `ralph/tasks/` and tests.

## Goal
A personal research dashboard that saves time on stock and options research and trade
decisions. It gathers information about the tickers I follow (news, SEC filings, blogs,
YouTube, X/Twitter, Reddit), digests it into a short daily report, tracks the stocks and
option contracts I'm watching or (virtually) holding, and shows upcoming market-moving
events. It is **not** linked to any brokerage and never places trades.

## Users
- **The owner (single user, for now):** an active investor who trades stocks and sells/buys
  options. Wants to stop sifting manually through news, X and YouTube every day, and wants
  one place to see prices, greeks, and the return on short-option trades.
- **Later:** possibly other people with their own accounts. Not built yet, but the data
  model must not make this hard (see F7 and `docs/DECISIONS.md`).

## Glossary (plain language)
- **Ticker** — the short code for a stock or ETF, e.g. `AMZN`.
- **Option contract** — the right to buy (call) or sell (put) 100 shares at a set price
  (**strike**) until a set date (**expiration**). Identified by a standard code (OCC symbol),
  e.g. `AMZN261218C00200000`.
- **DTE (days to expiration)** — calendar days from a given date until expiration.
- **IV (implied volatility)** — how much movement the option's price implies, as a yearly %.
- **Greeks** — delta, gamma, theta, vega, rho: how the option's price reacts to stock price,
  time, volatility and interest-rate changes.
- **Short option** — an option you sold (you collected the **premium**).
- **CSP (cash-secured put)** — a short put backed by enough cash to buy the shares.
- **Covered call** — a short call backed by 100 shares you own per contract.
- **Credit spread** — a short option plus a further-out long option of the same type and
  expiration, sold for a net credit.
- **Capital at risk** — the money tied up or at stake in a short-option trade (formulas in F3).
- **Virtual portfolio** — positions recorded in this app for performance tracking only.
- **Watchlist** — a named list of stocks and/or option contracts to follow.

## How we verify
- Automated tests: `pytest` (`uv run pytest`) for the formulas, services and every page. Pages are checked through FastAPI's test client (the real app with a temporary database built by the real migrations), not a browser. Outside services are replaced by recorded or hand-made sample responses in `tests/fixtures/` and fakes in `tests/conftest.py`; the AI is always a fake. Tests never use the network.
- Code style: `uv run ruff check .` and `uv run ruff format --check .`. CI (GitHub Actions) runs the style checks and the tests on every push and pull request.
- Not automated (done by hand): layout checks with screenshots (laptop and phone width), and the owner's checks against real data (see `PLAN.md`).
- A feature is done when every acceptance criterion below has a passing test.

## Features
Each feature has an ID (F0, F1…) and each criterion an ID (F1.1, F1.2…). Stories in `ralph/tasks/` refer back to these IDs.

### F0 — Walking skeleton
The project as it runs today (Phases 1–9 built): the app starts with one command, sets up its database with one command, and its tests and style checks run with one command each.

Acceptance criteria:
- [x] F0.1 Running `uv run uvicorn app.main:app --reload` serves the home page at http://localhost:8000.
- [x] F0.2 `uv run alembic upgrade head` creates or updates the database (`data/app.db`); the app refuses to start on a missing or out-of-date database and names the command to run.
- [x] F0.3 The test suite runs with one command (`uv run pytest`) and passes, without network access.
- [x] F0.4 `uv run ruff check .` and `uv run ruff format --check .` pass, and CI runs them and the tests on every push and pull request.

### F1 — Watchlists and stock tracking
Create named lists of stocks/ETFs (and, from F2, option contracts) and see key numbers at a glance.

Acceptance criteria:
- [x] F1.1 I can search by ticker or company name (e.g. "amazon" or "AMZN") and see matching US-listed stocks/ETFs.
- [x] F1.2 I can create, rename and delete watchlists, and add/remove items, without typing any price data.
- [x] F1.3 Default limits are 30 watchlists and 25 items per list; both come from configuration and can be raised without code changes. Exceeding a limit shows a clear message and nothing is saved.
- [x] F1.4 Each stock row shows: last price, change $ and % on the day, day range, 52-week range, volume, market cap, next earnings date, and the time the data was fetched.
- [x] F1.5 A "Refresh" button re-fetches prices. Otherwise stored prices up to 15 minutes old (the refresh window, set in configuration) are reused and older ones are fetched again. The fetch time is always visible.
- [x] F1.6 If the data source fails, the page still loads, shows the last stored values marked "stale", and shows an error note instead of crashing.
- [x] F1.7 An unknown ticker shows "not found" and is not added.

### F2 — Options chain browser
Find option contracts without typing them in.

Acceptance criteria:
- [x] F2.1 From a ticker I can open its option chain, pick an expiration date, and switch between calls and puts.
- [x] F2.2 Each strike row shows: bid, ask, mid, last, volume, open interest, IV, delta, gamma, theta, vega, and DTE.
- [x] F2.3 Greeks are calculated by the app with the Black-Scholes model from the stock price, strike, DTE, IV and a risk-free rate. Test case: stock 100, strike 100, 1 year, rate 5%, IV 20%, no dividend → call price 10.4506 and call delta 0.6368 (±0.0001).
- [x] F2.4 The strike closest to the current stock price is highlighted, and the in-the-money rows are marked differently (their own style) from the out-of-the-money rows.
- [x] F2.5 From any row I can (a) add the contract to a watchlist, or (b) record it in the virtual portfolio (F3).
- [x] F2.6 Option contracts in a watchlist show the same fields as F2.2, plus the underlying price.
- [x] F2.7 Expired contracts are marked "expired" and are no longer refreshed.

### F3 — Virtual portfolio and short-option returns
Record buys and sells with a click, then track performance and the return on short options.

Acceptance criteria:
- [x] F3.1 On a stock or contract I can press **Bought** or **Sold**, choose a date (default today) and quantity, and the trade is recorded. **Sold** with no existing position opens a short position.
- [x] F3.2 The trade price is filled in automatically: for today, the current mid (or last if there's no bid/ask); for a past date, that day's closing price if the data source has it. I can override the price before saving. If no price is available, I'm asked to enter one.
- [x] F3.3 The portfolio lists open positions with: quantity, average entry price, current price, unrealized P&L ($ and %), and for options DTE, IV and delta. Closed positions show realized P&L.
- [x] F3.4 For short options the app shows **annualized return = premium ÷ capital at risk × 365 ÷ DTE**, twice: using DTE at entry ("locked-in") and DTE remaining today ("remaining").
- [x] F3.5 Capital at risk:
  - cash-secured put = strike × 100 × contracts − premium received
  - covered call = underlying price at the time of sale × 100 × contracts, not my cost basis (confirmed by owner 2026-11-01). The call counts as covered only if the portfolio holds ≥ 100 shares per contract.
  - credit spread = (difference between strikes − net credit) × 100 × contracts
- [x] F3.6 Test cases (premium per share × 100 × contracts = premium received):
  - CSP: strike 100, premium 2.00, 1 contract, 30 DTE → capital 9,800, annualized **24.83%**
  - Covered call: stock 150 at sale, premium 3.00, 1 contract, 45 DTE → capital 15,000, annualized **16.22%**
  - Put credit spread: strikes 100/95, net credit 1.00, 1 contract, 45 DTE → capital 400, annualized **202.78%**
- [x] F3.7 I can link two option legs (same underlying, type and expiration) as a credit spread; the spread is then shown and calculated as one position.
- [x] F3.8 A short call that isn't covered (not enough shares) shows "uncovered — no return figure" instead of a number.
- [x] F3.9 When DTE reaches 0 the position is flagged "expiring today". After expiration it's marked expired, and I can confirm whether it expired worthless or was assigned.
- [x] F3.10 I can link a call I bought (a long-dated "LEAP") and a call I sold on the same stock as a **poor man's covered call**: the bought call expires after the sold call, the sold call's strike is above the bought call's, and both have the same number of contracts. It's shown and calculated as one position, and the sold call needs no shares to count as covered. Capital at risk = price paid for the bought call × 100 × contracts (confirmed by owner 2026-11-04); premium = the sold call's premium; DTE = the sold call's. Test case: bought call 60.00, sold call 3.00, 1 contract, 30 DTE → capital 6,000, annualized **60.83%**. (Added 2026-11-04 at the owner's request.)

### F4 — Daily research report
One digest per ticker, about one page each, with a short summary of the whole watchlist on top.

Acceptance criteria:
- [x] F4.1 Each ticker's report has these sections: Major developments · Rumors & speculation (each labelled "unconfirmed") · Institutional & insider activity · Valuation snapshot · Sentiment · Sources.
- [x] F4.2 Every statement in the report links to at least one source item (title, outlet, date, URL). Nothing appears without a source.
- [x] F4.3 Sources by phase: news (Yahoo Finance, Google News RSS, company press releases) and SEC filings (8-K, 10-Q, 10-K, Form 4, 13D/G) first; then YouTube and blogs; then X and Reddit. *(2026-11-05: Reddit is parked because Reddit closed free access for personal projects; see DECISIONS.)* *(2026-11-10: Reddit moved to "Out of scope" by the owner; everything else is built and tested.)*
- [x] F4.4 Only sources on my trusted list (`config/sources.yaml`: outlets, blogs, YouTube channels, X accounts, subreddits; X accounts are in `config/x.yaml`) are used, except for SEC filings and the main news feeds. I can edit the list without code changes. *(Subreddits: out of scope with Reddit.)*
- [x] F4.5 YouTube: recent videos (default: last 7 days) from trusted channels that mention the ticker or company in the title or description are summarized from the video itself in 3–5 bullet points, linked with a timestamp where possible.
- [x] F4.6 Sentiment: each source item is rated bullish / neutral / bearish with a one-line reason. The ticker gets an overall score from −100 to +100, weighted by source type (default weights: filings & news 1.0, blogs 0.8, YouTube 0.7, X 0.5, Reddit 0.3, set in config). The report shows the score, the counts by rating and the weights used.
- [x] F4.7 Valuation snapshot: trailing and forward P/E, EV/EBITDA, P/S, and how each compares with the stock's own history (its past fiscal years, as much as the source provides), with peers picked automatically (its industry's largest companies; owner 2026-11-04: no hand-made lists), with its sector's largest companies, and with Damodaran's industry average; each comparison is a median, shown as % above/below. Per-ticker overrides (sector, industry, own peer list, Damodaran industry) live in a config file (AMZN is compared with Technology, owner 2026-11-04). Plus the analyst target (low / average / high) and the % gap to the current price.
- [x] F4.8 Institutional & insider: insider buys and sells from Form 4 in the last 90 days; latest top institutional holders and the change in their stakes; the funds I follow (a list in config) that hold the stock, from their latest two quarterly 13F reports: new / added / reduced / unchanged / sold, and the share of the fund's portfolio (owner 2026-11-04); new 13D/13G filings; unusual options activity (contracts where today's volume > 2× open interest and volume ≥ 500, in expirations of the next 60 days, at most 6 dates; thresholds set in config).
- [x] F4.9 Reports can run automatically on weekdays at 7:30am US Eastern while the app is running (a setting; off by default, owner 2026-11-04). A "Generate now" button runs one for a single ticker or the whole watchlist.
- [x] F4.10 Past reports are kept and browsable by date and ticker.
- [x] F4.11 If a source fails or hits its limit, the report is still produced and says which sources were missing.
- [x] F4.12 The same story from several outlets, recognised by near-identical headlines, appears once, with all of its sources listed. *(Narrowed by the owner 2026-11-10: the same event told in different words may show as two stories.)*
- [x] F4.13 Only public content (articles, filings, transcripts, video links) is sent to the AI service. My portfolio and watchlist positions are never sent.
- [x] F4.14 Each report shows when it was generated and the date range it covers (default: the last 7 days, owner 2026-11-04; items already summarized earlier are reused, not sent to the AI again).

### F5 — Market events calendar
Upcoming events that can move my stocks or the market.

Acceptance criteria:
- [x] F5.1 Shows earnings dates (with before-open / after-close timing when known) for every ticker in my watchlists and every stock I hold (or hold options on) in the virtual portfolio (owner 2026-11-04).
- [x] F5.2 Shows the main US macro events: FOMC rate decisions (and minutes), CPI, PCE, the jobs report (employment situation), GDP; each tagged high / medium impact.
- [x] F5.3 Has a week view and a list view. The default list shows the next 14 days.
- [x] F5.4 I can filter by "my tickers only" (watchlists + portfolio; economic events are hidden) and by "high impact only".
- [x] F5.5 Each event shows the date, time (US Eastern), and source. Past events drop off the default view.
- [x] F5.6 The dashboard home page shows the next 3 high-impact events.

### F6 — Macro dashboard
A page of broad market indicators. List confirmed with the owner 2026-11-08 (DECISIONS 2026-11-08); the cards live in `config/macro.yaml`.

Acceptance criteria:
- [x] F6.1 Shows the latest value, the change since about a month earlier (the date compared with is shown), and a chart switchable between 1 and 5 years for: yield-curve spreads (10y–2y, 10y–3m), CPI, core CPI and core PCE inflation (change from a year earlier), money market fund assets (ICI's weekly total), equity risk premium (S&P 500 earnings yield, from SPY's trailing P/E, minus the 10y Treasury yield), S&P 500 forward P/E (estimated from the 50 largest US companies), VIX and University of Michigan consumer sentiment. Extras shipped in the same file: Fed funds rate, 10-year expected inflation, unemployment rate, high-yield bond spread, and the Fed's quarterly money market fund total. Figures with no free history (ICI weekly, equity risk premium, forward P/E) build their charts from one saved reading per week or trading day, starting with the first visit.
- [x] F6.2 Every indicator shows its source and the date of its last update; a source that fails shows its cards as "stale" (with the reason and the saved figures) or "missing", and never breaks the page.
- [x] F6.3 Indicators can be added through configuration (FRED series ID + label) without code changes.
- Not included (owner 2026-11-08): retail trading volume (no free source), fund inflows/outflows (ICI publishes them but not on FRED; maybe later), AAII investor survey (download needs a paid membership), CNN Fear & Greed (no official access).

### F7 — Accounts & hosting (later)
Acceptance criteria (initial):
- [x] F7.1 From the first version, every stored watchlist, trade and report belongs to a user, so accounts can be added without redesigning the data.
- [ ] F7.2 (Later phase) I can log in with email and password; each user only sees their own data.
- [ ] F7.3 (Later phase) The app can be deployed online and reached securely (HTTPS) from my phone.

## Out of scope (for now)
- Linking to a brokerage account, importing statements, or placing / executing trades.
- Buy/sell recommendations or automated trade signals. The app summarizes; I decide.
- Real-time streaming quotes (data up to 15 minutes old is fine).
- Non-US markets, crypto, futures, forex.
- Paid "options flow" / dark-pool data feeds.
- Push, SMS or email alerts (may come later).
- Return figures for uncovered (naked) short calls; strategies with 3+ legs (iron condors, butterflies) and the put version of the poor man's covered call, until requested.
- Backtesting, tax lots, tax reporting.
- Native mobile app (a phone-friendly web page comes with hosting, F7).
- Multiple users / login (until F7's phase).
- A full adjustable DCF valuation model (after F4.7 is done).
- Scraping X/Twitter in breach of its terms of service.
- Reddit as a report source (owner 2026-11-10; parked since 2026-11-05 because Reddit closed free access for personal projects). Its sentiment weight (0.3) stays in the settings; revisit if Reddit offers personal-use access again.

## Constraints
- Runs on my own computer first (Mac or Windows, opened in a web browser), and is built so it can move to the cloud later.
- Ongoing cost: market data must be free to start (Yahoo Finance via an unofficial library). AI usage should stay within the $10/month Google Cloud credit that comes with the owner's Gemini Pro subscription. At most about $5/month extra for YouTube/X aggregation.
- Starts with one command once installed.
- A full morning report for 25 tickers finishes in under 10 minutes.
- Secrets (API keys) live only in `.env`, never in code or git.
- Code stays simple and conventional enough that a hired developer can review and extend it.
- Every finance calculation (greeks, DTE, returns, P&L, sentiment score) has automated tests with known answers.

## Open questions
- ~~**X/Twitter source**~~ settled 2026-11-05: X's official pay-per-use API, $5/month (DECISIONS 2026-11-05).
- ~~**Macro indicators (F6):**~~ settled 2026-11-08 (DECISIONS 2026-11-08; F6 above).
- **Report delivery:** in the app only for now. Is email or PDF wanted later?
