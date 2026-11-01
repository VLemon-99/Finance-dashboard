# Decision log

> One entry per meaningful choice, newest at the top. This stops future sessions from quietly undoing past decisions.

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
- **Revisit if:** Phase 2 needs intraday fields Yahoo's daily bars don't give (day range, volume and market cap may need `fast_info`).

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
