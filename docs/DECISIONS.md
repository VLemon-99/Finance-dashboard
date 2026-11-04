# Decision log

> One entry per meaningful choice, newest at the top. This stops future sessions from quietly undoing past decisions.

## 2026-11-04 — Trades only on trading days
- **Decision** (owner's request): trades can't be dated on a weekend or a US stock market holiday. Browsers' date pickers can't grey out single days, so picking a closed day shows why ("Nov 27, 2025 was a market holiday (Thanksgiving Day)") and switches **Save trade** off; the server refuses such dates too. The form opens on the latest trading day, and on a weekend or holiday that day's prices count as current (Friday's closing mid on a Saturday).
- **How:** `app/market_calendar.py` works out the regular NYSE holidays (New Year's, MLK Day, Washington's Birthday, Good Friday, Memorial Day, Juneteenth from 2022, Independence Day, Labor Day, Thanksgiving, Christmas, with the weekend "observed" rules; New Year's on a Saturday isn't moved to Friday). Rare one-off closures (Sept 11, 2001, presidential funerals, Hurricane Sandy, Jan 9, 2025) are listed in `config/settings.yaml` → `market_data.extra_closed_days`, where new ones can be added. Half-day sessions count as trading days. Option expirations are left alone (Yahoo gives the real dates).
- **Alternatives considered:** a calendar library such as `pandas_market_calendars` (a new dependency for rules that fit in a short, tested file); a drop-down of trading days instead of a date field (thousands of entries back to 2000).
- **Revisit if:** the NYSE adds a holiday or the app covers other markets.

## 2026-11-04 — Portfolio options grouped per ticker
- **Decision** (owner's choices from four options each): every open option position sits in a **collapsible section per stock**, tickers A–Z, **open by default**. The section header shows the ticker, the number of contracts and the **total P&L** (sum of the positions' P&L, calculated in code; "—" if any price is missing). **Linked pairs** (credit spreads and poor man's covered calls) are one shaded line with the combined figures and an Unlink button, with **both legs listed underneath**. Stocks keep their own table above. This replaces the separate "Credit spreads" and "Poor man's covered calls" tables (Phase 4 part B entry).
- **Why:** The owner found a separate table per strategy hard to follow; grouping by stock keeps everything about one company together, as broker apps do.
- **How:** HTML `<details>` sections (no JavaScript), one table per ticker with fixed column widths so the columns line up across sections.
- **Alternatives considered:** grouping only the linked pairs; grouping stocks and options together per ticker; starting closed or remembering open/closed (owner chose open).
- **Revisit if:** the page gets long with many tickers (then consider starting closed or remembering the choice).

## 2026-11-04 — Poor man's covered calls; trade-date and after-close price fixes
- **Decision:**
  - **Poor man's covered calls (new SPEC F3.10, requested by the owner).** A call bought (the LEAP) and a call sold on the same stock can be linked when the bought call expires later, the sold call's strike is higher, and both hold the same number of contracts. **Capital at risk = price paid for the LEAP × 100 × contracts** (owner's choice of three: what was paid, its market value when the call was sold, or its cost minus the premium). Premium = the sold call's; days to expiration = the sold call's; P&L % is of the capital at risk. The sold call needs no shares to count as covered. Calls only; the put version waits until requested.
  - **One Link button for both kinds.** The app tells them apart by the expiration dates (same date → credit spread; bought call later → poor man's covered call). They're stored in the same `spreads` table (no database change).
  - **A link whose leg is closed simply ends, with no note**, because that's the normal end of a poor man's covered call's short call (or a spread leg closed early). The next sold call can then be linked to the same LEAP. Links whose legs are both open but no longer match still show a note.
  - **The trade form's price always belongs to the date shown.** Changing the date refreshes the price (HTMX). Save refuses a filled-in price that was for another date and shows that day's price to check. (Bug: a trade dated Oct 1 was saved at the Nov 3 price.)
  - **After the close, a missing "today" row is filled from Yahoo's latest regular-market price.** Yahoo's daily history can lack today's row for a while after 4 PM, so the app showed the previous day's close as the latest price. The price history's metadata carries the latest regular-session price and time (after-hours trades excluded); it's used when it's newer than the last row.
- **Why:** The owner trades poor man's covered calls and wanted their returns tracked like covered calls; capital = what the LEAP cost is exact and matches common calculators. The two fixes come from the owner's tests with real data.
- **Alternatives considered:** a separate "Link poor man's covered call" form (more buttons for the same action); a JavaScript-free date check only (works, but the owner expected the price to update when the date changes); always using the metadata price instead of the history (the history also gives the day range, volume and 52-week range).
- **Revisit if:** the owner wants the put version, LEAPs covering more contracts than are sold, or Yahoo's metadata turns out to be unreliable.

## 2026-11-03 — Phase 4 part B: virtual portfolio choices
- **Decision** (items 1–9 were confirmed by the owner before coding):
  1. **Average cost.** A position's entry price is the average of the trades that opened it. Trades the other way realize P&L against that average. Going past zero closes the position and opens the opposite one with the rest. A position that returns to zero is "closed"; a later trade starts a new one.
  2. **Whole shares and contracts, no fees or commissions.**
  3. **Short puts outside a spread count as cash-secured.** The app doesn't track cash, so it can't check.
  4. **Covered calls:** shares held (long stock positions in the portfolio) cover short calls oldest first, 100 shares per contract. A call position is covered only if all its contracts are; otherwise "uncovered — no return figure" (F3.8). Calls inside a credit spread don't use shares. Short stock never covers.
  5. **Return formula inputs:** premium = average sale price × 100 × contracts; "DTE at entry" counts from the first sale (for a spread, from the day its second leg was opened); on expiration day (0 DTE) the "remaining" return shows "—" because the formula would divide by zero.
  6. **Assignment / exercise** closes the option at $0 (the premium stays as its profit or loss) and records 100 shares per contract bought or sold at the strike on the expiration date: short put assigned or long call exercised → shares bought; short call assigned or long put exercised → shares sold. "Expired worthless" closes the option at $0. All or nothing: partial assignment isn't offered.
  7. **No trade editing:** delete a wrong trade and enter it again.
  8. **Credit spreads link two open positions** (one sold, one bought): same stock, both calls or both puts, same expiration, same number of contracts, opened for a net credit, with the bought leg further out of the money. The link is to the contracts, not the trades; if the legs stop matching (e.g. one is partly closed) they're shown separately with a note, and linking those contracts again replaces the old link.
  9. **No new libraries.**
  - **The automatic price (F3.2):** for an option today, its mid (or last trade if there's no bid/ask), from the stored chain, refreshed with the usual 15-minute rule; for a stock today, its last price (we don't store stock bids/asks); for a past date, that day's closing price from Yahoo (`get_daily_close`), for the stock or for the option contract itself. If Yahoo has no price, the form asks for one. A price that matches the suggestion is stored as "auto", otherwise "manual" (shown as "typed in").
  - **Option trades record the stock price at the time** (auto-filled the same way). It's required only when selling a call, since only the covered-call formula uses it.
  - **Options can only be traded after their chain has been opened**, so the contract's details come from Yahoo, not from typing.
  - **Bought/Sold buttons** are on option chain rows, watchlist rows (stocks and contracts), the quote card and the Portfolio page.
  - **P&L % is relative to the entry value** (average entry × quantity × 100 for options); for a spread, relative to the premium received.
  - **The portfolio page groups positions** into Stocks, Options, Credit spreads, Closed positions and the trade list, with "Expired: how did these end?" on top when needed. There's no grand-total row (not in the SPEC).
- **Why:** The simplest rules that meet F3.1–F3.9 with the known answers in F3.6, keep every number in tested Python code, and stay explainable on the page.
- **Alternatives considered:** first-in-first-out lots (more bookkeeping, mainly matters for taxes, which are out of scope); tracking cash so puts could be checked as cash-secured (more data entry for little gain now); storing spreads as a link between trades (breaks when trades are deleted or added); adjusting the share cost by the premium on assignment (brokers differ; keeping the premium on the option keeps both P&L lines honest).
- **Revisit if:** the owner wants partial assignment, fees, cash tracking, totals, or editing trades; or pages get slow with many trades.

## 2026-11-03 — Phase 4 part A: option chain choices
- **Decision** (the first five were confirmed by the owner before coding):
  - **Black-Scholes without dividends**, as in the SPEC F2.3 test case. Time is counted in calendar days over a 365-day year, up to **4:00 PM US Eastern on the expiration date**.
  - **Theta is per calendar day; vega is per 1 percentage point of IV** (e.g. 20% → 21%), as most broker screens show them.
  - **Risk-free rate = Yahoo's 13-week T-bill yield (`^IRX`)**, taken as it is. It's remembered in memory for the refresh window (15 minutes). If it can't be fetched, the last good value is used; if there's none, the fallback rate in `config/settings.yaml` (4.0%) is used and the page says so.
  - **Option prices are reused for 15 minutes like stocks**, and Refresh always fetches. Every strike of a fetched chain is stored in a new `option_quotes` table, so the chain page and watchlists share the same prices, and stored prices can be shown "stale" when Yahoo fails (F1.6).
  - **No new libraries**: the normal distribution comes from Python's built-in `math.erfc`.
  - **Greeks, mid and DTE are calculated when shown**, not stored. The greeks use the stock price Yahoo sends with the option chain (so stock and option prices are from the same moment); the stored stock quote is used only if the chain comes without one.
  - **Yahoo's placeholder IV is ignored**: IVs below 1% (`min_implied_volatility_percent` in `config/settings.yaml`) are shown as "—", with no greeks. Yahoo reports about 0.001% for strikes that barely trade.
  - **Mid** is shown only when there's a bid above zero and the ask isn't below it.
  - **"In the money"**: calls with strike below the stock price, puts with strike above it. The **nearest strike** is the one closest to the stock price (the lower one on a tie).
  - **Expired** = from 4:00 PM Eastern on the expiration date. Expired dates aren't offered on the chain page; expired contracts in a watchlist are marked "expired", keep their last prices, show no greeks and aren't refreshed (F2.7).
  - **Contracts in watchlists** are rows in `instruments` with kind `option` and their OCC-style symbol. They can only be added from a chain we have stored prices for (the symbol is checked against a format rule and our stored chain), and they **count toward the 25-items-per-list limit** (SPEC F1.3 says "items"). A watchlist refreshes its contracts through their chains, one fetch per stock + expiration date. The **expiration-date list is fetched on every chain page visit** (it's small); if that fails, the dates with stored prices are offered.
  - **Reports research a watchlist's stocks only**; option contracts are skipped.
- **Why:** Meets F2.1–F2.4, F2.5a, F2.6 and F2.7 with the fewest moving parts, keeps every finance number in tested Python code, and reuses the stock rules (refresh window, stale/missing) the owner already knows.
- **Alternatives considered:** dividends in the formula (needs a dividend-yield feed; small effect for most stocks); a binomial model for American-style options (more complex; for calls on non-dividend stocks it gives the same answer); `scipy` for the normal distribution (a large new library for one function); refreshing watchlist contracts via each contract's own Yahoo ticker (no IV there); storing the calculated greeks (they'd go out of date as time passes); FRED for the rate (needs a key the owner hasn't set up yet).
- **Revisit if:** the greeks differ noticeably from the owner's broker (likely causes: dividends, or the broker's own IV), Yahoo starts rate-limiting the chain or date requests, or watchlists hold many contracts across many dates (refreshes are one chain at a time).

## 2026-11-02 — Phase 3: how the report is built
- **Decision:**
  - **Press releases come through Google News** (Business Wire, PR Newswire and GlobeNewswire are outlets there), not each company's own feed (owner agreed).
  - **News is stored as headline + feed snippet.** We don't download the article pages: many are paywalled, and Google News links are redirects. So news summaries are only as rich as the headline. Filings are different: we download the full text (8-Ks with their press-release exhibit 99), cut to 150,000 characters.
  - **The trusted-outlets list exists but is off by default** (`trusted_outlets_only: false` in `config/sources.yaml`), because SPEC F4.4 says the main news feeds are used as they are. Switch it on to keep only listed outlets.
  - **Google News search text is a config setting**: `"{company}" OR "{ticker} stock"`, with the company name taken from the SEC's ticker list ("Apple Inc." → "Apple").
  - **Same story = near-identical headline** (similarity ≥ 0.85, published within 72 hours), using Python's built-in text comparison. Filings are never merged.
  - **One AI summary and rating per story**, not per outlet; the sentiment counts and score count each story once.
  - **Form 4s aren't sent to the AI or rated.** Their buys and sells are read from the filing's data in code. Only open-market buys (code P) and sales (code S) count; grants, option exercises, tax withholding and gifts are counted separately.
  - **Sentiment score** = 100 × Σ(weight × rating) / Σ(weight), ratings +1/0/−1, rounded to a whole number (halves away from zero); no rated stories → no score instead of 0.
  - **The watchlist summary on top is built in code**, from each ticker's score and first development. Each AI call is about one ticker, so the list of tickers in a watchlist never reaches the AI (F4.13).
  - **The AI must cite.** It gets numbered stories and returns statements with story numbers. Statements citing nothing, or numbers we didn't send, are dropped. A development must cite a confirmed story and a rumor an unconfirmed one.
  - **Without a Gemini key, or when the AI fails, the report is still made**: it lists every source item (headline, outlet, date, link) and says the AI part is missing.
  - **Reports are stored as structured data (JSON)**, not markdown, and drawn by the page template.
  - **Model: `gemini-flash-latest`**, Google's name that always points at the current Flash model; changeable in `config/settings.yaml`. Items are summarized in batches (up to 20 per request) to keep requests few.
  - **"Generate now" runs in a background thread** with one run at a time; the page shows progress and reloads when done. A run cut off by stopping the app is marked "didn't finish" at the next start.
  - **The app checks the database is up to date at startup** and says to run `uv run alembic upgrade head` if not.
  - **New libraries:** `feedparser` (reads news feeds), `google-genai` (Gemini), `httpx` (web requests; it was already installed for tests, now a regular dependency).
- **Why:** Simplest way to meet F4.1–F4.4, F4.6, F4.10–F4.14 while keeping the hard rules: numbers in code, every claim cited, no personal data to the AI, one failing source never breaks a report.
- **Alternatives considered:** each company's own press-release feed (many have none, and URLs differ per company); fetching article pages (paywalls, fragile, terms-of-service risk); a text-similarity library for duplicates (new dependency for a small gain); letting the AI write the watchlist summary (would send the watchlist's tickers together); per-item AI calls (many more requests for headline-sized items).
- **Revisit if:** news summaries feel too thin (consider a news API with article text), duplicates slip through with different wording, Google or Yahoo change their feeds, or the AI bill nears the $10 credit.

## 2026-11-01 — Static files are linked with a version code
- **Decision:** Pages link to `style.css` and `htmx.min.js` as e.g. `/static/style.css?v=3f2a9c1e04b7`, where the code comes from the file's content (`app/web/assets.py`).
- **Why:** After the Phase 2 update, the owner's browser kept using its saved copy of Phase 1's `style.css`, because the address hadn't changed and the app didn't say how long a copy may be kept. A content-based code changes exactly when the file changes, so browsers can still keep unchanged files but always fetch updated ones.
- **Alternatives considered:** telling browsers never to keep a copy (slower every page load); asking the owner to hard-refresh after every update (easy to forget).
- **Revisit if:** we add a build step or a CDN that versions files on its own.

## 2026-11-01 — Phase 2: where the stock row comes from, and other watchlist choices
- **Decision:**
  - **Stock row from three Yahoo calls per ticker.** One year of daily price bars gives price, previous close, day range, volume, 52-week range and the trading day. Yahoo's share count × price gives market cap. Yahoo's company calendar gives the next earnings date. This replaces Phase 1's 5-day request. Market cap and earnings are optional: if they fail, the row shows "—" for them and everything else still appears.
  - **Market cap is calculated by us** (share count × last price), like the other finance numbers.
  - **Prices younger than 15 minutes are reused** everywhere (watchlists and the quote lookup). The Refresh button always fetches. The window lives in `config/settings.yaml`.
  - **A watchlist's stocks are fetched 4 at a time** (`max_parallel_fetches` in `config/settings.yaml`), so a 25-stock refresh takes seconds, not a minute. Only the Yahoo calls run in parallel; the database is used from one thread.
  - **Search shows only US exchange-listed stocks and ETFs** (NASDAQ, NYSE, NYSE American, NYSE Arca, Cboe). OTC/pink-sheet, foreign, fund and option results are dropped.
  - **A ticker is checked with Yahoo before it's added.** Unknown → "not found", not added. If Yahoo is down and we've never seen the ticker, it isn't added either ("try again later"), because we can't tell a typo from a real stock.
  - **List limits are checked before anything is fetched or saved.**
  - **List names are unique per user, ignoring upper/lower case** ("Tech" and "tech" can't both exist).
  - **`config/settings.yaml` is read with PyYAML** (new direct dependency; it was already installed as part of the web server). Misspelled setting names stop the app with a message instead of being silently ignored.
  - **Forms use `python-multipart`** (new dependency): the small library FastAPI requires to read submitted HTML forms.
  - **Changes are plain form posts followed by a redirect.** Pages work without JavaScript; HTMX only swaps in search results and the quote lookup result.
  - **The home page (`/`) is the watchlists dashboard.** The Phase 1 quote page moved to `/lookup` (owner agreed).
- **Why:** These are the fewest calls that cover every F1.4 field. Yahoo's quick "fast info" summary would download the same year of prices again just to get the ranges, and it has no earnings date. The full `info` call is slow and rate-limited more often. The other choices keep pages working when Yahoo is down and follow the "simplest thing that meets the spec" rule.
- **Alternatives considered:** Yahoo's `info` call for everything (one call, but slower and the first to be rate-limited); caching market cap and earnings for a day (fewer calls, but more code. Revisit if Yahoo rate-limits us); parsing forms by hand instead of adding `python-multipart` (non-standard, easy to get wrong).
- **Revisit if:** Yahoo starts rate-limiting refreshes, market caps look wrong for companies with several share classes (Yahoo's share count may cover only one class, e.g. GOOGL), or refreshes feel slow.

## 2026-11-01 — Phase 1 build choices (small ones, recorded so they aren't undone by accident)
- **Decision:**
  - **HTMX is saved in the repo** (`app/web/static/htmx.min.js`, version 2.0.4, checked against the npm registry's published fingerprint), not loaded from a website.
  - **Settings come from `pydantic-settings`**, which reads `.env`. It's a small library from the same people as FastAPI's validation library.
  - **`instruments` and `quotes` have no `user_id`.** A stock price is a shared fact about the market, not something a user owns. Watchlists, trades and reports will have `user_id`.
  - **Quotes come from Yahoo's last 5 daily price bars.** The latest close (today's price during market hours) is the price; the one before it is the previous close used for the day change.
  - **"Not found" vs. "Yahoo is down":** yfinance is told to raise errors instead of hiding them. If it says "no such ticker" for a ticker we've stored before, we treat it as a source problem and show the stored price as stale.
  - **Migrations are run by you (`uv run alembic upgrade head`), not automatically.** If you forget, the app stops at startup with a message telling you the command.
  - **`tzdata` is installed on Windows only**, so US Eastern times display correctly there too. Mac and Linux already have this data.
- **Why:** These are the simplest choices that work offline, keep tests off the network, and follow the "one failing source never breaks a page" rule.
- **Alternatives considered:** loading HTMX from a CDN (needs internet, could change under us); Yahoo's `info` call for quotes (slower and rate-limited more often); creating tables automatically at startup (hides migration mistakes).
- **Revisit if:** Phase 2 needs intraday fields Yahoo's daily bars don't give (day range, volume and market cap may need `fast_info`). *(Revisited in Phase 2: see the entry above. Quotes now use a year of daily bars plus share count and calendar.)*

## 2026-11-01 — X/Twitter: no scraping; pluggable, capped, decided later (OPEN)
- **Decision:** Don't scrape X. Build X as an optional source plug-in in Phase 8. It will use either X's official pay-per-use API or a low-cost third-party read API, look only at trusted accounts plus `$TICKER` searches, and have a hard spending cap (about $5/month) and an on/off switch. The specific provider is chosen at that phase.
- **Why:** Scraping breaks X's terms at any volume. The free tools need a logged-in account that can be banned, and they break often. Prices and options change quickly, so it's better to decide when we build it.
- **Alternatives considered:** Low-volume scraping with a throwaway account (fragile, against the terms); dropping X (but X is one of the owner's biggest time sinks); paying hundreds of dollars a month for official API tiers (over budget).
- **Revisit if:** X pricing changes, a cheap compliant option appears, or the owner accepts a higher budget.

## 2026-11-01 — YouTube via channel RSS feeds + Gemini video summaries
- **Decision:** Find new videos through each trusted channel's free RSS feed. Have Gemini summarize the video from its YouTube link. Only channels on the trusted list count; that's how we filter for reputable, non-AI content.
- **Why:** Costs nothing and needs no YouTube API key. Gemini can understand public YouTube videos directly, so we don't depend on fragile transcript scrapers. This means the owner's second biggest time sink can come early (Phase 5).
- **Alternatives considered:** YouTube Data API search (needs a key and has a quota; search results are noisy); transcript-scraping libraries (unofficial, often blocked).
- **Revisit if:** Gemini's free tier limits video summaries, or a channel has no RSS feed.

## 2026-11-01 — AI summaries: Gemini API paid tier, paid for by the subscription's Cloud credit, behind a swappable adapter; public data only
- **Decision:** Use the Gemini API through the `google-genai` library, wrapped in our own small `LLM` interface. Use an API key from Google AI Studio linked to the owner's Google Cloud project, so usage is billed to the $10/month Google Cloud credit that comes with the Gemini Pro subscription (unused credit carries over and expires after 12 months). Default to a "Flash" model. Set a budget alert in Google Cloud at $10/month. Send only public content (articles, filings, video links), never portfolio or watchlist data.
- **Why:** The owner already pays for this credit, Gemini handles YouTube videos natively, and $10/month should comfortably cover a 25-ticker daily report on a Flash model. Billed (paid-tier) usage also gets higher rate limits than the free tier. Under Google's terms at the time of writing, paid-tier inputs aren't used to improve their models. The adapter means switching providers later is a one-file change.
- **Alternatives considered:** Gemini free tier (no billing setup, but lower limits, and inputs may be used for training; kept as a fallback); Claude or OpenAI APIs (similar cost and quality, but no existing credit and no native YouTube input).
- **Revisit if:** monthly usage gets close to the $10 credit, quality is poor, or the subscription changes.

## 2026-11-01 — Market data: Yahoo Finance via `yfinance`, behind a provider interface; greeks calculated by us
- **Decision:** Get quotes, option chains (with IV), history, earnings dates, holders and analyst data from `yfinance`. Calculate the greeks ourselves with the Black-Scholes formula, using Yahoo's IV and a risk-free rate from 13-week T-bills (`^IRX`) or FRED.
- **Why:** Free, and it covers almost everything needed. Yahoo doesn't provide greeks, but the formula is standard and easy to test against known answers. All Yahoo calls go through one `MarketData` interface, so a paid feed (e.g. Polygon/Tradier) can replace it without touching the rest of the app.
- **Alternatives considered:** Paid data APIs ($30–$100/month, over the free-to-start budget); brokerage APIs (out of scope: no brokerage link).
- **Revisit if:** `yfinance` breaks often, the data is wrong, or we need reliable historical option prices (Yahoo's are patchy, so F3.2 allows a manual price override).

## 2026-11-01 — Tech stack: Python + FastAPI + server-rendered HTML (Jinja2 + HTMX) + SQLite
- **Decision:**
  - Python 3.12 for everything.
  - FastAPI as the web framework, with Jinja2 HTML templates and HTMX for small interactive updates. Chart.js for charts.
  - SQLite through SQLModel (built on SQLAlchemy), with Alembic for database changes.
  - APScheduler for the morning job.
  - Tooling: `uv` (installs and runs everything), `ruff` (code style), `pytest` (tests), GitHub Actions (runs the tests on every push).
- **Why (plain language):**
  - Python is where the finance and data tools live (`yfinance`, maths, AI libraries), and it's the most readable choice for an owner who reads some code.
  - With a single language and no separate frontend project, there's less to learn, install and break.
  - FastAPI is popular and well documented, so a hired developer will know it, and adding login later is standard.
  - SQLite is just a file on disk, so there's no database server to run. SQLAlchemy lets us move to Postgres for cloud hosting without rewriting the code.
  - `uv` makes setup one or two commands.
- **Alternatives considered:**
  - Streamlit: fastest to prototype, but awkward for login, background jobs and custom layouts. Projects often get rewritten when they grow.
  - Next.js/React + Python API: a second language and a build step, more than this project needs.
  - Desktop app: harder to move to the cloud later.
- **Revisit if:** the UI needs heavy interactivity (e.g. live-updating charts everywhere), or we move to multi-user hosting at scale.

## 2026-11-01 — Local-first, single user, but `user_id` on everything
- **Decision:** Run on the owner's computer, opened in a browser, with no login. Every stored record still belongs to a user (one default user for now).
- **Why:** This is the simplest way to start. Tagging data with a user from day one means adding accounts and cloud hosting later (Phase 10) won't require redesigning the database.
- **Alternatives considered:** Hosting with login from day one (more cost and security work before the app proves useful).
- **Revisit if:** the owner needs phone access sooner. If so, Phase 10 moves earlier.

## 2026-11-01 — Positions are calculated from trades, not stored
- **Decision:** Store only the Bought/Sold transactions. Holdings, P&L and annualized returns are calculated from them each time they're displayed.
- **Why:** There's one source of truth, so stored totals can't drift out of sync with the trades. It's also easy to fix a wrong trade.
- **Alternatives considered:** Storing a running positions table (faster at huge scale, which we don't have).
- **Revisit if:** pages get slow with many transactions.
