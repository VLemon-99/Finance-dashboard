# Decision log

> One entry per meaningful choice, newest at the top. This stops future sessions and loop rounds from quietly undoing past decisions. Loop rounds don't edit this file — they record proposals in their progress log, and an interactive session adds them here after review.

## 2026-11-13 — UI redesign (improvement loop run 2): phone-first frame, cards, stored-price home page
- **Decision** (owner reviewed PR #27, 2026-11-13). Built by the improvement loop in bold mode, Focus UI (IMP-8 to IMP-16; details in `ralph/tasks/IMPROVE-2026-11-08.json`):
  - **One app frame:** a sticky top bar on laptops, a fixed bottom tab bar on phones (Watchlists, Portfolio, Options, Reports, More). Calendar, Macro, Quote lookup and the account sit under **More**, on laptops too (owner OK'd the email and Log out living there). The current section is marked on the menu item (`<li aria-current>`), because tests pin the links to plain `<a href="/x">Label</a>`. Colour names in `style.css` are kept and only their values changed, so the report styles keep working.
  - **Wide tables become labelled cards on phones** (`table.responsive` + `data-label`); laptops keep the tables.
  - **Option chain on phones:** compact columns by default (Strike, Bid, Ask, Mid, IV, Delta, actions) with a "Show all columns" switch, CSS only; **Theta, Volume and Open interest stay hidden by default** (owner 2026-11-13). The strike column is pinned.
  - **Home page uses stored prices only** (no fetching), with fetch time and a stale tag; outside market hours the cards show "stale".
  - **Portfolio summary** (total open P&L, open positions, capital at risk) is calculated in Python with known-answer tests (`app/services/portfolio_summary.py`). Its edge cases (owner 2026-11-13): a position with **no current price** is left out of the P&L and named; a **stale but present price** (source failed, last stored price used) stays in the P&L and is named; a **covered call with no recorded stock price at sale** is left out of capital at risk and named; an **expired position waiting for its outcome** is left out of every total and named. A linked spread or poor man's covered call counts as one position.
  - **Quote lookup → Add to watchlist** goes through `POST /lookup/add`, a pass-through to the existing add-a-stock handler, because a plain form can't take its address from a dropdown without JavaScript.
  - New setting `options.max_quick_tickers` in `config/settings.yaml` (shortcut chips on the Option chains page, default 20, 0 hides them). Kept in config because CLAUDE.md puts limits in `config/`, not in code. No new dependencies.
  - SPEC F7.10 added for the phone-friendly layout (owner OK'd).
- **Why:** The pages were usable on a laptop, but on a phone the menu wrapped, tables scrolled sideways and the portfolio's options table overlapped. A consumer-app layout (inspiration: retail brokerage apps) makes the daily check quicker on any screen.
- **Alternatives considered:** a CSS framework (new dependency, would fight the existing styles); hiding columns on phones without a switch (loses information an options seller needs); JavaScript-driven menus and tables (the app must work without JavaScript, DECISIONS 2026-11-01).
- **Revisit if:** the report rework needs a different frame, the owner wants Theta in the default phone chain, or a very old phone browser shows tables scrolling sideways (the card layout relies on CSS `:has()`).

## 2026-11-13 — Security review before going online: what gets fixed when
- **Decision** (owner approved Claude's suggestion after the review, 2026-11-13). Nothing was open while the app runs only on the owner's computer. The git history has no secrets.
  - **Fix now (loop, F7-S16 to S21, SPEC F7.10):**
    - **H1:** refuse to start when `config/hosting.yaml` lists a public name while login is off. A public name is one with a dot, except 127.0.0.1, so a single-word computer name or "testserver" doesn't count.
    - **H2:** at most 2 password checks at once (`max_parallel_password_checks`). Each uses about 32 MB, and the free server has 1 GB.
    - **M1:** the account command signs out that account's devices after setting a new password.
    - **L2:** downloads refuse a redirect to a non-HTTPS address.
    - **L3:** no automatic API description pages.
    - **L1:** soupsieve upgraded past CVE-2026-126190.
  - **Fixed in the same session, by hand (M3):** `.claude/settings.json` no longer allows `cat` in general, which could have printed `.env` past the "never read .env" rule; `.env` reads are now also denied as `Bash(cat .env*)` and `Read(./.env.*)`. Done by hand, not by a loop, so a loop never edits its own permissions.
  - **At deploy (F7-S15):**
    - **M2:** daily disk snapshot on Google Cloud.
    - **L5:** HTTPS-only (HSTS) switched on in Cloudflare.
    - **H2's second half:** Cloudflare's free rate-limiting rule on `/login` and `/signup`.
  - **Later, before inviting beta users:**
    - **L4:** sign-up and Google messages reveal whether an email is invited or has an account. The lock is per email only (the Cloudflare rule covers most of this).
    - **L6:** pin the container base image and GitHub Actions by exact fingerprint, and switch on Dependabot.
    - **L7:** count a non-owner's report by tickers rather than runs.
  - **Accepted as a known limit (L8):** news text could try to steer the AI's summaries. Every claim must cite a stored source, and the AI never sees personal data.
- **Why:** H1 and H2 would be serious online (anyone treated as the owner; a cheap way to crash the free server). The rest are small and cheap to fix now.
- **Alternatives considered:**
  - For H2, a lower scrypt cost. This would make stolen password hashes easier to crack; queuing costs only a short wait.
  - For H1, forcing login on automatically. A refusal to start makes the mistake visible instead of hiding it.
- **Revisit if:** the app moves to a bigger server (raise the parallel checks), or more people get accounts (L4, L7).

## 2026-11-12 — F7: beta users, invite-only accounts, Google sign-in, hosting on Google Cloud + Cloudflare
- **Decision** (owner's answers to the F7 questions, 2026-11-12):
  - **Users:** the app is built for the owner plus up to about 100 invited beta testers, each with their own data. **Online it stays owner-only for now**, because Yahoo Finance's data (through `yfinance`) is for personal use only, and showing it to others very likely breaks its terms (the same rule that ruled out X scraping). Before anyone is invited, a data source that allows it must be found (SPEC Open questions).
  - **Login only online:** a `login_required` setting, off on the owner's computer, on when hosted (F7.4).
  - **Accounts are invite-only:** emails on an invite list in `config/accounts.yaml` (shipped empty) can sign up with email + password (at least 12 characters) or "Sign in with Google". No open sign-up. The owner's account is the existing default user (it owns all current data); a command on the server sets its real email and password (F7.5, F7.6).
  - **Not now:** passkeys and "forgot password" emails (they would need an email-sending service and a web address for it). A forgotten password is reset by the owner with the same command.
  - **Wrong passwords:** at most 10 for one email in 15 minutes, then 15 minutes' wait (owner: "max 10 tries"; the 15 minutes are Claude's proposal, in config). The lock is per email, so someone could lock the owner's password login on purpose; Google sign-in keeps working during a lock (F7.7).
  - **Reports:** users other than the owner can "Generate now" at most 2 reports a US Eastern day, so the $10/month Gemini credit can't be used up by others (F7.8). The morning report runs only for the owner; how to offer it to others is decided later (F7.9).
  - **Session length:** 30 days (config `session_days`), the planner's proposal, used by F7-S2.
  - **Sign in with Google** is built by hand with `httpx` (already used), following Google's OpenID Connect steps: a one-time `state` and `nonce`, the code exchanged directly with Google over HTTPS, then the ID token's issuer, audience, expiry, nonce and `email_verified` checked. Because the token comes straight from Google's token endpoint over HTTPS, its signature check can be skipped (the OpenID Connect spec allows this), so **no new library** is needed. It lives behind an adapter in `app/providers/`, like every outside service. Keys `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` go in `.env`.
  - **Hosting:** Google Cloud's always-free small server (e2-micro in a US region: 1 GB memory, 30 GB disk, free with no end date; outbound traffic above 1 GB a month costs pennies). SQLite stays: the disk is kept, so no move to Postgres. Cloudflare in front if needed: the web address (sold at cost), HTTPS, a tunnel so the server isn't open to the internet, and optionally Cloudflare Access (Google login before the app is even reached). The app is packaged as a standard container so it can move to another host in about an hour. **Deploying is done in an interactive session, step by step with the owner's OK; loops never deploy.**
- **Why:** Free to start, nothing tied to one company (a plain server plus one database file), and the owner already has a Google Cloud account for Gemini. Invite-only and the report limit keep cost and risk small for a beta.
- **Alternatives considered:**
  - Firebase: built for JavaScript apps with its own database (Firestore). It would mean the paid pay-as-you-go plan and rewriting all data storage.
  - Cloudflare Workers to run the app: limited Python support, no long background report runs or schedule. Kept only as the front door.
  - AWS: new accounts get about 6 months of credits, then the free plan ends.
  - Open sign-up: strangers could use up the AI credit.
  - A login library: the hand-built version needs nothing new and is small enough to test fully with fakes.
- **Revisit if:** a market data source for other users is chosen (then invite people); the free server is too small for the reports (about 1 GB memory, so reports run one at a time); the beta grows past about 100 users; email (password reset) becomes wanted.
- **Supersedes in part:** 2026-11-01 "Local-first, single user": local use stays login-free, but the app now also runs online with accounts.

## 2026-11-10 — Reddit out of scope; F4.12 means near-identical headlines
- **Decision** (owner's answers after the switch to Ralph loops): Reddit moves from "parked" to SPEC "Out of scope", so planning never turns it into stories (its 0.3 sentiment weight stays in the settings). SPEC F4.12 now says what the code does: stories are merged when their headlines are nearly identical. Both make F4 complete (`merged`). Loops may also run `uv run alembic upgrade head` without asking (`.claude/settings.json`).
- **Why:** Reddit still offers no personal-use access (DECISIONS 2026-11-05); near-identical-headline merging is good enough for the owner.
- **Alternatives considered:** Matching the same event told in different words (would need the AI or fuzzy matching; more cost and complexity).
- **Revisit if:** Reddit offers personal-use access again, or duplicate stories become annoying in reports.

## 2026-11-10 — Build with Ralph loops
- **Decision:** Features are split into small stories in `ralph/tasks/` and built by automated loops (one story per round, fresh context each round), each feature on its own branch, merged only through a reviewed pull request.
- **Why:** Lets work run unattended and in parallel while keeping every change small, tested and reviewable.
- **Alternatives considered:** Fully interactive sessions (slower, needs constant attention).
- **Revisit if:** Loops regularly produce work that fails review — fix the spec and tests first.

## 2026-11-10 — How the existing project was moved onto the Ralph workflow
- **Decision:** No application code, tests or dependencies changed. The project as it runs today (Phases 1–9, 755 tests passing) is **F0, the walking skeleton**, status `merged`. SPEC features keep their IDs (F1–F7); the old phase numbers stay in `PLAN.md` for history. Criteria are ticked only where they work in the code and have tests. Work zones follow the real folders: `watchlists` (F1), `options` (F2 + F3, which share the formulas), `reports` (F4), `calendar` (F5), `macro` (F6), `accounts` (F7); the database, `app/main.py`, `app/config.py`, the Yahoo/FRED adapters, `base.html`/`style.css`, `tests/conftest.py`, `config/settings.yaml` and `config/sources.yaml` are shared files. The old rule "ask before deploying" and the template's "never deploy" are combined: loop rounds never deploy; interactive sessions deploy only with the owner's explicit OK. `docs/PROMPTS.md` now follows the template's version; the project's kickoff, bug, handoff, architecture check-in and security review prompts have close equivalents there, and the two without one ("start a work session" and "second opinion") are kept as prompts 14 and 15 (the older wording of the other five was deleted at the owner's request).
- **Why:** The template's planner must not rebuild what already exists, and loops need real commands and zones that match the code.
- **Alternatives considered:** Numbering features by the old phases (would break every F-number already used in tests and docs).
- **Revisit if:** Zones turn out too coarse (e.g. two report stories regularly collide) — split `reports` into sources/AI and market facts.

## 2026-11-08 — Phase 9: macro dashboard indicators, sources and how they're kept
- **Decision** (owner's answers to the proposed list: "1 yes, 2 ICI's weekly, 3 keep, 4 yes"):
  - **Indicators:** yield curves 10y–2y and 10y–3m, CPI / core CPI / core PCE inflation (change from a year earlier, worked out from FRED's price indexes), money market fund assets, equity risk premium, S&P 500 forward P/E, VIX, University of Michigan consumer sentiment; extras Fed funds rate, 10-year expected inflation, unemployment rate, high-yield bond spread. All in `config/macro.yaml`, grouped in sections; any FRED series id + label adds a card (F6.3). Not included: retail trading volume (no free source), fund flows, AAII survey (paid membership), CNN Fear & Greed (no official access; scraping is against our rules).
  - **Money market funds from ICI's weekly report** (owner's choice over FRED's quarterly figure). ICI's page carries only the latest week, in a sentence that has kept the same wording for years ("Total money market fund assets increased by $X billion to $Y trillion for the week ended Wednesday, …"), so the app reads that sentence and saves each week's figure; the chart grows from the first visit. Not chosen: ICI's history spreadsheet (its address changes with each upload, and reading an Excel file would need a new library). **Also added** (not in the owner's list, easy to delete): FRED's quarterly money market fund total (`MMMFFAQ027S`), so there's a long history from day one.
  - **Equity risk premium** = 100 ÷ SPY's trailing P/E (Yahoo, the whole index's last-12-months P/E) minus FRED's 10-year Treasury yield (`DGS10`) on or up to a week before that day. Yahoo has no P/E history, so one reading is saved per trading day (a weekend visit counts as Friday's) and the chart fills in.
  - **S&P 500 forward P/E (estimate)** = total market value ÷ total expected earnings of the **50 largest US companies** (owner: keep the estimate). Companies come from Yahoo's 11 sector lists (the same lists Phase 7 uses); companies based outside the US are left out (e.g. TSMC's US shares), share classes count once (GOOG/GOOGL), companies without a forward P/E (expected losses) are left out of the sum. To avoid reading every sector's whole list, companies are read from whichever sector's next company could still be the biggest, stopping when none could make the top 50: about 65 Yahoo requests a day. Saved once per trading day.
  - **"Change since last month"** = the latest figure minus the figure on or before the same day a month earlier, with that date shown ("change since Oct 5"). Percentages change in points, dollar amounts in %. For a quarterly series the comparison is the previous quarter, shown with its date.
  - **Stored** in two new tables (`macro_observations`, `macro_fetches`), public facts with no `user_id`. FRED series are replaced in full on each refresh (FRED revises old values); 6 years kept (5 for charts plus 1 for yearly changes). Inflation and the premium are worked out when the page is shown, so settings edits apply straight away.
  - **Refreshed in a background thread** every 12 hours (15 minutes after a failure), started by opening the page, because the forward P/E needs ~65 Yahoo requests. The page never waits: it shows what's saved and reloads itself every 20 seconds while a refresh runs. Each card shows "stale" with the reason, or "missing", independently (F6.2).
  - **Charts with Chart.js** (planned in the 2026-11-01 stack decision), version 4.5.1, stored in `app/web/static/` like htmx (no build step, no outside site at run time). One page-wide switch between 1 year and 5 years; daily series over 400 points are thinned to one point per week.
- **Why:** free sources only, every number calculated in code with known-answer tests, and a page that works with any one source down.
- **Alternatives considered:** FRED's quarterly money fund figure only (owner preferred ICI weekly); Damodaran's monthly implied equity risk premium (a different, forward-looking method; SPEC defines the simpler one); a hand-typed list of the 50 largest companies (goes out of date; the owner prefers automatic lists); reading all ~220 sector companies a day (slow); refreshing while the page waits (the first visit would take a minute).
- **Revisit if:** ICI rewords its weekly sentence (the card says the page has changed), Yahoo's sector lists stop being sorted by size or include many foreign companies, Yahoo stops giving SPY's P/E, or the owner wants fund flows after all.

## 2026-11-05 — Phase 8: X through its official pay-per-use API, $5/month; Reddit parked
- **Findings (checked 2026-11-05, from news sites and developer blogs; X's own docs site couldn't be reached from the build machine):** X dropped its monthly API plans in Feb 2026 for **pay-per-use**: you buy credit in advance; about **$0.005 per post** read and **$0.010 per account record**; the same post or account isn't charged twice in one UTC day; a spending limit can be set in X's developer console. **Reddit** has required its approval for every API app since late 2025 (personal projects included, rarely approved; one source says new requests stopped on Oct 31, 2026), and its no-sign-up RSS feeds are reported to end on **Nov 13, 2026**.
- **Decision (owner: "$5 per month", "trusted accounts first, then ticker", "park Reddit", "empty list for now"):**
  - **X via the official API** (recent search, app Bearer Token in `.env`), no third-party services: the cheap ones get their data by scraping X, which breaks X's terms (see 2026-11-01). Settings in a new file, `config/x.yaml`: **off by default**, trusted accounts (empty to start), `$TICKER` search on.
  - **Order:** during collection each ticker gets one search for posts from the trusted accounts that mention it (its cashtag, the ticker, or the company's name). Only after every ticker of the run is collected does the `$TICKER` search run (a new "late source" step), with what the trusted accounts left, tickers searched longest ago first so a small budget rotates through the watchlist.
  - **Hard budgets:** **$5 a month and $0.22 a day** (about $5 over a month's weekdays). Before each request the app sets aside the **most it could cost** (10 posts, plus up to one account record per trusted account in the search) and doesn't send it if that could break either budget; afterwards the set-aside becomes what X actually sent. Requests X refuses cost nothing; an unreadable answer stays counted at its maximum. The total is kept in the database (`x_spending`, UTC days, calendar months), so it holds across restarts. It counts on the high side (no same-day discount), so the real bill is usually lower. X's console spending limit is the second safety net.
  - **No post read twice:** each search asks only for posts after the previous search's end (`x_checks`).
  - **Cost choices:** trusted-account searches ask X for the authors' handles (the report cites "@handle"); the `$TICKER` search doesn't (it would triple the cost), so those posts are cited as "X ($AAPL search)" and the link shows the author. Search posts are English, no reposts or replies, X's "most relevant" first; posts naming more than 5 cashtags are dropped as spam. Tickers without a simple cashtag (e.g. BRK-B) aren't searched.
  - **In the report:** X posts are rated like other items (weight 0.5, as in the spec), labelled "X", and the AI is told they're opinions, unconfirmed unless reported elsewhere. The Sources section ends with X's spending today and this month. **The "weights used" line now lists only the source types switched on**, so a source turned off leaves no trace.
  - **Reddit parked:** not built. Its weight (0.3) stays in the settings for later.
- **Why:** the only option within X's terms and the budget; the budget is enforced before any money is spent, and tested.
- **What $5 buys:** about 1,000 posts a month. Trusted accounts usually post a few times a week about a given stock, so most of the daily budget goes to the `$TICKER` search: roughly 4 tickers searched per day, so a 25-stock watchlist is searched about once a week each.
- **Alternatives considered:** third-party X APIs ($0.15–$0.50 per 1,000 posts, but they scrape); asking for authors on every search (3× the cost); one search per trusted account (many more requests, same cost); per-run instead of daily budgets (repeated "Generate now" clicks could use up the month in a day); applying for Reddit API access (probably closed or refused); Reddit's RSS feeds (ending Nov 13, 2026).
- **Revisit if:** X's prices change (update them in `config/x.yaml`), the owner wants a bigger budget, or Reddit offers personal access again.

## 2026-11-04 — Phase 7: valuation benchmarks, institutional holders, funds followed, unusual options
- **Decision** (owner's answers: automatic peers, no lists to type; AMZN against Technology; add "funds I follow" and Damodaran now; 60 days / 6 dates for options; keep the page minimal, metrics will be pruned later):
  - **Valuation is compared with four things, each in code:** the stock's own past fiscal years (Yahoo's annual statements, usually 4 years, with the price at each year's end: P/E = price ÷ diluted EPS, P/S = price × shares ÷ revenue, EV/EBITDA = (market cap + debt − cash) ÷ EBITDA); its **industry's** 10 largest companies (Yahoo's industry page); its **sector's** 25 largest (Yahoo's sector page); **Damodaran's** US industry average (NYU Stern, updated each January). Groups use the **median**; losses (negative multiples) are left out as "not meaningful", the standard practice. A past year whose share count differs from today's by more than 1.8× is skipped (an unadjusted stock split would make it wrong).
  - **Overrides in `config/benchmarks.yaml`**, per ticker: sector, industry, own peer list, Damodaran industry. AMZN → Technology (Yahoo files it under Consumer Cyclical; its industry peers stay "Internet Retail"). The same file maps Yahoo's ~145 industries to Damodaran's ~95 (Claude's mapping, editable).
  - **Damodaran's pages are read as HTML tables** (no new library for his Excel files): the reader rebuilds merged cells and finds columns by their header text, so small layout changes don't break it. Downloaded at most once a day.
  - **Top institutional holders from Yahoo** (top 10, % held, change since the previous 13F). **Funds you follow from the SEC directly** (`config/institutional.yaml`, starter list of 10 well-known funds): each fund's latest two 13F reports, read from EDGAR with the same contact and pace as other SEC downloads; each report downloaded once. 13F reports name stocks by issuer name and CUSIP, not ticker, so a stock is matched by name (Yahoo's and the SEC's, with 13F abbreviations like "PETE" spelled out; all share classes together); a CUSIP can be set per ticker when a name isn't recognized.
  - **Unusual options:** volume > 2× open interest and ≥ 500 contracts, expirations in the next 60 days, at most 6 dates per stock (each date is one Yahoo request). Thresholds in `config/institutional.yaml`.
  - **Fetched for the whole run together**, a few at a time (`market_data.max_parallel_fetches`); groups and peer figures are reused for 24 hours, so tickers in the same industry cost nothing extra. Stored inside each report's JSON: **no database change**. Each failing part is a "missing" note; nothing of it goes to the AI.
  - **New settings go in new files** (`config/benchmarks.yaml`, `config/institutional.yaml`), not in `settings.yaml` / `sources.yaml`: the owner edits those on their computer, and `git pull` refuses to update a file with local edits.
  - **Compact page:** one valuation table (now / own history / peers / sector / industry average, each with ±%), one-line summaries, and the details (peer list, yearly figures, top holders, contracts) folded behind "more" toggles.
- **Why:** meets F4.7/F4.8 with free sources, no hand-made lists, and every number calculated in code and tested with known answers.
- **Alternatives considered:** the SEC's bulk 13F data sets (complete, but very large quarterly downloads and fiddly ticker matching); free tiers of 13F services (API Ninjas, the13f: limited, may change terms; same SEC data underneath); Damodaran's Excel files (needs a new library); per-ticker hand-made peer lists (owner didn't want to maintain them); comparing with the sector's average instead of median (skewed by outliers).
- **Revisit if:** Yahoo's sector/industry pages or Damodaran's page layout change (shows as "missing"), a followed fund's holding of a stock is missed (add its CUSIP), or reports get too slow.

## 2026-11-04 — Every report covers the last 7 days (replaces "since the last report with AI")
- **Finding** (owner's first report with YouTube channels): a channel's 5-day-old Amazon video wasn't in the AMZN report. The report covered only the 11 hours since the previous AMZN report, and the channel had been added after that one, so its older videos were outside every period.
- **Decision** (owner: "find the simplest solution for now, we will formally fix that later"): **every report covers the last 7 days** (`reports.max_days`), however recent the previous report. This replaces the period rule from the 2026-11-04 "Gemini needs prepaid credit" entry (start where the last report with AI ended, reaching back to unprocessed news), which is no longer needed: a full week always includes anything left over.
- **Why it doesn't waste AI credit:** stored items aren't downloaded again, items already summarized aren't sent to the AI again, and videos are never watched twice (all as before). The only repeated AI work is the report's written statements ("Major developments", "Rumors"), which are rebuilt from the stored one-line summaries of the week: one small text request per ticker.
- **Side effect:** a report made a day after another shows mostly the same stories again. Fine for weekly use; if the morning schedule is switched on, each day's report repeats most of the week. A "what's new since my last report" view or an "update this report" button are the later, proper fixes (owner's suggestions).
- **Alternatives considered:** collecting 7 days back but showing only items found since the last report (keeps reports short, but the "date range covered" (F4.14) would no longer describe the report); an "update" button on past reports (more work; later).

## 2026-11-04 — Phase 6: market events calendar
- **Decision** (owner's answers to the Phase 6 plan):
  - **"My tickers"** = every stock in my watchlists **and** every stock I hold in the virtual portfolio (including the stocks behind option positions and watchlist contracts). SPEC F5.1 and F5.4 reworded. "My tickers only" therefore hides the economic events.
  - **Impact levels:** high = Fed rate decisions, CPI, jobs report, and my stocks' earnings; medium = Fed minutes, PCE, GDP. The home page's "next 3 high-impact events" therefore includes my earnings (owner: OK that earnings season fills it). All in `config/settings.yaml` → `calendar:`.
  - **FRED key:** needed for CPI/jobs/PCE/GDP dates; until it's in `.env` those show as missing (README "Setting up the calendar").
  - **Fed meetings:** from `config/fomc_dates.yaml`, 2026 only (Oct 28 and Dec 9 with their minutes, from the Fed's published schedule); **2027 left empty** for now. The calendar warns once no future meeting is listed.
- **How (small choices):**
  - **Earnings from Yahoo's earnings calendar** (the JSON behind Yahoo's calendar page, through yfinance), because it gives the time and "before the open" / "after the close" and takes all my tickers in one request. yfinance's public function can't filter by ticker, so we call its internal request function with our own ticker filter (pinned by `uv.lock`; a yfinance update could break it, which shows as "earnings dates missing" and falls back to the date saved with watchlist prices). Not chosen: yfinance's `get_earnings_dates` (reads Yahoo's web page, i.e. page scraping, which our rules avoid) and Yahoo's company calendar alone (date only, already used for the watchlist's "next earnings").
  - **Release dates from FRED's release calendar** (`fred/release/dates` with future dates included); release ids 10 (CPI), 50 (Employment Situation), 54 (Personal Income and Outlays, which carries PCE), 53 (GDP) are in the settings, with the usual 8:30 AM ET release time. The FRED key is sent in the address (FRED's rule), so addresses are never logged and error messages never include them.
  - **Stored and refreshed once a day** (two new tables: `calendar_events`, `calendar_fetches`), retried 15 minutes after a failure; old dates are kept and flagged when a source is down. Fed meetings aren't stored. The page and home page never break on a source problem.
  - **List view** = today plus the next 13 days, past events dropped (an event with a known time drops off at that time; a date-only event at the end of its day). **Week view** = Monday to Sunday, every day listed, past days greyed, previous/next links; filters are kept when moving.
  - No new libraries.
- **Alternatives considered:** a paid calendar API (cost); Yahoo's economic-events calendar (unofficial, no impact levels, changes often); scraping the Fed's web page for meeting dates (fragile; a yearly edit is simpler).
- **Revisit if:** yfinance changes its calendar code (earnings show as missing), FRED changes release ids, or the owner wants other events (e.g. ex-dividend dates, options expiration Fridays, holidays).

## 2026-11-04 — Phase 5: blogs, YouTube videos watched once (10 per run), schedule off by default
- **Decision** (owner's answers to the Phase 5 plan):
  - **YouTube:** the AI watches each new video **once, ever**. Its answer (3–5 points, each with its time in the video) is kept with the stored item and reused when the same video turns up for another ticker or in a later run. At most **10 new videos per run** (`ai.max_videos_per_run`), newest first across the whole run; the rest wait for the next run (the next report's period reaches back to them). The channel list starts empty; the owner fills it in later.
  - **Automatic weekday reports are built but off** (`schedule.enabled: false`). The owner will press "Generate now" about once a week to start. With the schedule on, every watchlist is reported one after another at 7:30am ET on weekdays; a run missed by more than 30 minutes (laptop asleep, app closed) is skipped. Market holidays aren't skipped (owner: not relevant while it's off). SPEC F4.9 was reworded to match.
  - **Blogs:** one source per blog feed (name + RSS/Atom address) in `config/sources.yaml`, list empty for now.
- **How (small choices, recorded so they aren't undone by accident):**
  - A post or video counts for a ticker when its title or description mentions the ticker in capitals (with or without `$`; tickers of 1–2 letters only with `$`, since "ON", "IT", "AI" are everyday words), the SEC company name (also without a trailing "COM": "AMAZON COM" → "Amazon"), or a name from `also_known_as` (starter entries: Google for GOOG/GOOGL, since the SEC name is "Alphabet").
  - Videos are watched **from their YouTube link** (no transcript scraping, as decided 2026-11-01), at low video resolution and only the first `ai.max_video_minutes` (60) to keep the cost down; if Gemini refuses that cut-off, the whole video is tried once. 3 videos are watched at the same time to keep a 25-ticker run short. A video Gemini can't watch (private, live, too long) is marked and rated from its title and description; it isn't retried. Running out of credit or a bad key leaves the videos for the next run.
  - The watched video's points become the item's text, and the normal summarize step then gives the one-line summary and bullish/neutral/bearish **for the ticker** (so one viewing serves several tickers). No ticker or watchlist is sent with the video itself.
  - A run now collects for every ticker first, then watches videos, then finishes each report (so the per-run limit is shared fairly and the watching can happen in parallel). No database change: the answer lives in the item's existing `data` column.
  - Each blog or channel feed is downloaded once per run (reused for 10 minutes, failures too), so a site that's down costs one timeout, not 25.
  - New library: **APScheduler 3.11** (chosen in the tech-stack decision; version 4 is still a pre-release).
- **Cost estimate:** at low resolution a video costs roughly 100 AI "tokens" per second, so a 20-minute video is about 120,000 tokens: about 4–6 cents on a Flash model ($0.30–$0.50 per million tokens), three times that for an hour. 10 twenty-minute videos a week is about $1.50–$3 a month; 10 a day would be about $8–15, more than the $10 credit.
- **Alternatives considered:** watching each video per ticker (simpler, but pays for the same video several times); transcript libraries (unofficial, often blocked); summarizing from the description only (cheap, but misses what's said); a timer thread without a library (would need its own handling of sleep/wake and missed runs).
- **Revisit if:** video costs climb (lower `max_videos_per_run` or `max_video_minutes`), matching lets through too many off-topic videos (add names to `also_known_as`, or require the ticker), or the owner turns the schedule on and wants holidays skipped.

## 2026-11-04 — Gemini needs prepaid credit; a failed AI run doesn't use up the report period
- **Finding:** the owner's first report with a Gemini key failed with error 402 ("Your prepayment credits are depleted"). Gemini's "Prepay" billing stops every key at a $0 prepaid balance, and, per a Google community moderator, the subscription's monthly Developer Program credit only applies once the AI Studio prepaid wallet has a positive balance. This corrects the 2026-11-01 AI decision's assumption that the $10 monthly credit alone pays for the API.
- **Decision:**
  - The owner **added prepaid credit** in AI Studio (minimum $5, non-refundable, expires after 12 months). README "Setting up reports" now includes this step. The $10 budget alert stays.
  - The 402 error is shown in plain words ("prepaid credit is used up: add credit in Google AI Studio") instead of "error (402)".
  - **A report's period now starts where the last report *with AI summaries* ended** (owner's choice; still at most 7 days back). Before, the failed 12:05 AMZN report counted as a finished report, so the next one covered only 7 minutes and the earlier news never got AI summaries. Reports store an `ai_complete` flag; older ones are judged by their "missing" notes ("AI summaries:" / "AI report writing:"). A news or SEC source being down doesn't extend the period (owner's choice; it would otherwise push every report back 7 days while a source is down). A run where nothing needed the AI counts as complete.
  - Tiny amounts that round to zero are shown as 0.00 without a minus sign or red colour (the owner saw "-0.00" on a portfolio row).
  - **Correction (same day):** judging past reports wasn't enough. A quiet report after the failed one (nothing new, so nothing to ask the AI) counted as a report "with AI" and became the new starting point, so the failed run's news never came back. The period now **also reaches back to the oldest stored news item from the last 7 days that the AI never processed** (items the AI looked at and judged off-topic count as processed). This is the rule that matters; the report flag stays for the case where item summaries worked but writing the report's statements failed.
- **Alternatives considered:** the Gemini free tier (no cost, but daily limits that change and inputs may be used by Google; kept as a fallback); covering again after any missing source (owner chose AI only); keeping the period as it was.
- **Revisit if:** Google changes how the subscription credit applies to the API, or reports should also catch up after a source outage.

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

## 2026-11-01 — X/Twitter: no scraping; pluggable, capped, decided later (settled 2026-11-05, see Phase 8 entry)
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
- **Update 2026-11-04:** the API also needs a positive prepaid balance in AI Studio (Billing → Buy credits); the monthly credit alone doesn't unlock it. See the 2026-11-04 entry "Gemini needs prepaid credit".

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
