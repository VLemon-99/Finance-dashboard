# Specification

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

## Features
Each feature lists acceptance criteria: concrete, checkable statements. IDs (e.g. F3.4) are
referenced from `PLAN.md` and from tests.

### F1 — Watchlists and stock tracking
Create named lists of stocks/ETFs (and, from F2, option contracts) and see key numbers at a glance.

Acceptance criteria:
- [ ] F1.1 I can search by ticker or company name (e.g. "amazon" or "AMZN") and see matching US-listed stocks/ETFs.
- [ ] F1.2 I can create, rename and delete watchlists, and add/remove items, without typing any price data.
- [ ] F1.3 Default limits are 30 watchlists and 25 items per list; both come from configuration and can be raised without code changes. Exceeding a limit shows a clear message and nothing is saved.
- [ ] F1.4 Each stock row shows: last price, change $ and % on the day, day range, 52-week range, volume, market cap, next earnings date, and the time the data was fetched.
- [ ] F1.5 A "Refresh" button re-fetches prices; data up to 15 minutes old is acceptable and the fetch time is always visible.
- [ ] F1.6 If the data source fails, the page still loads, shows the last stored values marked "stale", and shows an error note instead of crashing.
- [ ] F1.7 An unknown ticker shows "not found" and is not added.

### F2 — Options chain browser
Find option contracts without typing them in.

Acceptance criteria:
- [ ] F2.1 From a ticker I can open its option chain, pick an expiration date, and switch between calls and puts.
- [ ] F2.2 Each strike row shows: bid, ask, mid, last, volume, open interest, IV, delta, gamma, theta, vega, and DTE.
- [ ] F2.3 Greeks are calculated by the app with the Black-Scholes model from the stock price, strike, DTE, IV and a risk-free rate. Test case: stock 100, strike 100, 1 year, rate 5%, IV 20%, no dividend → call price 10.4506 and call delta 0.6368 (±0.0001).
- [ ] F2.4 The strike closest to the current stock price is highlighted, and the in-the-money strikes look different from the out-of-the-money ones.
- [ ] F2.5 From any row I can (a) add the contract to a watchlist, or (b) record it in the virtual portfolio (F3).
- [ ] F2.6 Option contracts in a watchlist show the same fields as F2.2, plus the underlying price.
- [ ] F2.7 Expired contracts are marked "expired" and are no longer refreshed.

### F3 — Virtual portfolio and short-option returns
Record buys and sells with a click, then track performance and the return on short options.

Acceptance criteria:
- [ ] F3.1 On a stock or contract I can press **Bought** or **Sold**, choose a date (default today) and quantity, and the trade is recorded. **Sold** with no existing position opens a short position.
- [ ] F3.2 The trade price is filled in automatically: for today, the current mid (or last if there's no bid/ask); for a past date, that day's closing price if the data source has it. I can override the price before saving. If no price is available, I'm asked to enter one.
- [ ] F3.3 The portfolio lists open positions with: quantity, average entry price, current price, unrealized P&L ($ and %), and for options DTE, IV and delta. Closed positions show realized P&L.
- [ ] F3.4 For short options the app shows **annualized return = premium ÷ capital at risk × 365 ÷ DTE**, twice: using DTE at entry ("locked-in") and DTE remaining today ("remaining").
- [ ] F3.5 Capital at risk:
  - cash-secured put = strike × 100 × contracts − premium received
  - covered call = underlying price at the time of sale × 100 × contracts, not my cost basis (confirmed by owner 2026-11-01). The call counts as covered only if the portfolio holds ≥ 100 shares per contract.
  - credit spread = (difference between strikes − net credit) × 100 × contracts
- [ ] F3.6 Test cases (premium per share × 100 × contracts = premium received):
  - CSP: strike 100, premium 2.00, 1 contract, 30 DTE → capital 9,800, annualized **24.83%**
  - Covered call: stock 150 at sale, premium 3.00, 1 contract, 45 DTE → capital 15,000, annualized **16.22%**
  - Put credit spread: strikes 100/95, net credit 1.00, 1 contract, 45 DTE → capital 400, annualized **202.78%**
- [ ] F3.7 I can link two option legs (same underlying, type and expiration) as a credit spread; the spread is then shown and calculated as one position.
- [ ] F3.8 A short call that isn't covered (not enough shares) shows "uncovered — no return figure" instead of a number.
- [ ] F3.9 When DTE reaches 0 the position is flagged "expiring today". After expiration it's marked expired, and I can confirm whether it expired worthless or was assigned.
- [ ] F3.10 I can link a call I bought (a long-dated "LEAP") and a call I sold on the same stock as a **poor man's covered call**: the bought call expires after the sold call, the sold call's strike is above the bought call's, and both have the same number of contracts. It's shown and calculated as one position, and the sold call needs no shares to count as covered. Capital at risk = price paid for the bought call × 100 × contracts (confirmed by owner 2026-11-04); premium = the sold call's premium; DTE = the sold call's. Test case: bought call 60.00, sold call 3.00, 1 contract, 30 DTE → capital 6,000, annualized **60.83%**. (Added 2026-11-04 at the owner's request.)

### F4 — Daily research report
One digest per ticker, about one page each, with a short summary of the whole watchlist on top.

Acceptance criteria:
- [ ] F4.1 Each ticker's report has these sections: Major developments · Rumors & speculation (each labelled "unconfirmed") · Institutional & insider activity · Valuation snapshot · Sentiment · Sources.
- [ ] F4.2 Every statement in the report links to at least one source item (title, outlet, date, URL). Nothing appears without a source.
- [ ] F4.3 Sources by phase: news (Yahoo Finance, Google News RSS, company press releases) and SEC filings (8-K, 10-Q, 10-K, Form 4, 13D/G) first; then YouTube and blogs; then X and Reddit.
- [ ] F4.4 Only sources on my trusted list (`config/sources.yaml`: outlets, blogs, YouTube channels, X accounts, subreddits) are used, except for SEC filings and the main news feeds. I can edit the list without code changes.
- [ ] F4.5 YouTube: recent videos (default: last 7 days) from trusted channels that mention the ticker or company in the title or description are summarized from the video itself in 3–5 bullet points, linked with a timestamp where possible.
- [ ] F4.6 Sentiment: each source item is rated bullish / neutral / bearish with a one-line reason. The ticker gets an overall score from −100 to +100, weighted by source type (default weights: filings & news 1.0, blogs 0.8, YouTube 0.7, X 0.5, Reddit 0.3, set in config). The report shows the score, the counts by rating and the weights used.
- [ ] F4.7 Valuation snapshot: trailing and forward P/E, EV/EBITDA, P/S, and how each compares with the stock's own multi-year range (as much history as the source provides) and with a peer list I set per ticker; plus the analyst target (low / average / high) and the % gap to the current price.
- [ ] F4.8 Institutional & insider: insider buys and sells from Form 4 in the last 90 days; latest top institutional holders and the change in their stakes; new 13D/13G filings; unusual options activity (contracts where today's volume > 2× open interest and volume ≥ 500; thresholds set in config).
- [ ] F4.9 Reports run automatically on weekdays at 7:30am US Eastern while the app is running. A "Generate now" button runs one for a single ticker or the whole watchlist.
- [ ] F4.10 Past reports are kept and browsable by date and ticker.
- [ ] F4.11 If a source fails or hits its limit, the report is still produced and says which sources were missing.
- [ ] F4.12 The same story from several outlets appears once, with all of its sources listed.
- [ ] F4.13 Only public content (articles, filings, transcripts, video links) is sent to the AI service. My portfolio and watchlist positions are never sent.
- [ ] F4.14 Each report shows when it was generated and the date range it covers (default: since the previous report, max 7 days).

### F5 — Market events calendar
Upcoming events that can move my stocks or the market.

Acceptance criteria:
- [ ] F5.1 Shows earnings dates (with before-open / after-close timing when known) for every ticker in my watchlists.
- [ ] F5.2 Shows the main US macro events: FOMC rate decisions (and minutes), CPI, PCE, the jobs report (employment situation), GDP; each tagged high / medium impact.
- [ ] F5.3 Has a week view and a list view. The default list shows the next 14 days.
- [ ] F5.4 I can filter by "my tickers only" and by "high impact only".
- [ ] F5.5 Each event shows the date, time (US Eastern), and source. Past events drop off the default view.
- [ ] F5.6 The dashboard home page shows the next 3 high-impact events.

### F6 — Macro dashboard (later; still being brainstormed)
A panel of broad market indicators. Exact list to be confirmed before this phase starts.

Acceptance criteria (initial; refine before building):
- [ ] F6.1 Shows the latest value, the change since last month, and a 1–5 year chart for: yield-curve spreads (10y–2y, 10y–3m), CPI and core PCE inflation, money market fund assets, equity risk premium (S&P 500 earnings yield minus 10y Treasury yield), and S&P 500 forward P/E.
- [ ] F6.2 Every indicator shows its source and the date of its last update.
- [ ] F6.3 Indicators can be added through configuration (FRED series ID + label) without code changes.

### F7 — Accounts & hosting (later)
Acceptance criteria (initial):
- [ ] F7.1 From the first version, every stored watchlist, trade and report belongs to a user, so accounts can be added without redesigning the data.
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

## Constraints
- Runs on my own computer first (Mac or Windows, opened in a web browser), and is built so it can move to the cloud later.
- Ongoing cost: market data must be free to start (Yahoo Finance via an unofficial library). AI usage should stay within the $10/month Google Cloud credit that comes with the owner's Gemini Pro subscription. At most about $5/month extra for YouTube/X aggregation.
- Starts with one command once installed.
- A full morning report for 25 tickers finishes in under 10 minutes.
- Secrets (API keys) live only in `.env`, never in code or git.
- Code stays simple and conventional enough that a hired developer can review and extend it.
- Every finance calculation (greeks, DTE, returns, P&L, sentiment score) has automated tests with known answers.

## Open questions
- **X/Twitter source** (needed by the X phase): pick between X's official pay-per-use API and a third-party read API, within about $5/month and X's terms. Check current prices then.
- **Macro indicators (F6):** final list, especially retail trading volume, fund inflows/outflows and overall sentiment measures. Their free data sources need confirming.
- **Report delivery:** in the app only for now. Is email or PDF wanted later?
