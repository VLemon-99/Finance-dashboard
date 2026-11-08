# Finance Dashboard

A personal research dashboard for stocks and options. It gathers news, SEC filings, blogs,
YouTube, X and Reddit for the tickers you follow and digests them into a short daily report.
It also tracks stocks, option contracts (greeks, IV, days to expiration) and a virtual
portfolio, including the annualized return on short options, and shows a calendar of
market-moving events. It is not linked to any brokerage and never places trades.

**Status:** Phases 1–9 are built (see `PLAN.md`). Phase 3 works: watchlists with live stock data, plus research reports from news and SEC filings, summarized by Gemini ("Generate now" and a history page under **Reports**). Phase 4: option chains with greeks under **Options** (or the "Options" link on any watchlist row), option contracts saved in watchlists, and a virtual **Portfolio**: press Bought or Sold on any stock or contract to record a trade, then see P&L, capital at risk and annualized returns on short options, credit spreads, poor man's covered calls and expiry outcomes. A single-ticker quote lookup is at `/lookup`. Later phases added the **Calendar**, valuation and fund holdings in reports, optional X posts, and the **Macro** page. See `PLAN.md` for the current phase.

## Documents
| File | Purpose |
|---|---|
| [`docs/SPEC.md`](docs/SPEC.md) | What the app must do, with testable acceptance criteria and an out-of-scope list |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How the pieces fit together |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Why things are the way they are (tech stack, data sources, AI) |
| [`PLAN.md`](PLAN.md) | Phases, progress, next steps, handoff notes |
| [`CLAUDE.md`](CLAUDE.md) | Rules, stack and commands Claude Code reads every session |
| [`docs/PROMPTS.md`](docs/PROMPTS.md) | Copy-paste prompts for work sessions, bugs, reviews and handoffs |

## Running it on your computer

**One-time setup**

1. Install `uv`, the tool that installs Python and everything else for this project.
   - Mac or Linux: open Terminal and run:
     ```bash
     curl -LsSf https://astral.sh/uv/install.sh | sh
     ```
   - Windows: open PowerShell and run:
     ```bash
     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
     ```
   Then close and reopen the terminal window.
2. Download the project (if you haven't already) and go into its folder:
   ```bash
   git clone https://github.com/VLemon-99/Finance-dashboard.git
   ```
   ```bash
   cd Finance-dashboard
   ```
3. Install everything (this also downloads Python 3.12 if needed):
   ```bash
   uv sync
   ```
4. Create your settings file from the example. Reports need two values in it: see "Setting up reports" below. Limits such as the number of watchlists are in `config/settings.yaml`, and report sources in `config/sources.yaml`; you can edit both directly:
   ```bash
   cp .env.example .env
   ```
   On Windows use `copy .env.example .env` instead.
5. Create the database (also run this again after pulling new changes):
   ```bash
   uv run alembic upgrade head
   ```

**Every time**

```bash
uv run uvicorn app.main:app --reload
```
Then open http://localhost:8000 in your browser. Press `Ctrl+C` in the terminal to stop the app.

**Setting up reports** (one time)

Reports need two values in your `.env` file (open it in any text editor, e.g. TextEdit or Notepad). Without them the app still makes reports, but without SEC filings or AI summaries, and the report says what's missing.

1. **SEC contact.** The SEC asks everyone who downloads filings to say who they are. It's sent only to the SEC. Replace the example line with your name and email:
   `SEC_USER_AGENT=Jane Doe jane@yourmail.com`
2. **Gemini key.**
   1. Go to https://aistudio.google.com and sign in with the Google account that has your Gemini subscription.
   2. Click **Get API key** (left side), then **Create API key**.
   3. When asked for a project, choose the Google Cloud project that gets your $10 monthly credit. Not sure which one? In https://console.cloud.google.com open **Billing → Credits** to see where the credit sits, and pick a project linked to that billing account.
   4. Copy the key and put it in `.env`: `GEMINI_API_KEY=` followed by the key (no spaces or quotes).
   5. **Add prepaid credit:** in AI Studio open **Billing → Buy credits** and add at least the minimum ($5). The Gemini API stops at a $0 prepaid balance, and the subscription's monthly credit doesn't count until there's some prepaid balance. Without it, reports say "the AI service's prepaid credit is used up". Prepaid credit can't be refunded and expires after 12 months.
3. **Budget alert** (so you're warned before spending more than the credit): in https://console.cloud.google.com open **Billing → Budgets & alerts → Create budget**. Name it "Gemini", set the amount to $10, keep the 50% / 90% / 100% email alerts, and click **Finish**.
4. Restart the app. Google sometimes renames these menus; if a step doesn't match what you see, tell Claude what's on your screen.

Never paste the key into chat, code or GitHub. It belongs only in `.env`, which is never uploaded.

**Adding YouTube channels and blogs**

Reports use only the channels and blogs you list in `config/sources.yaml` (open it in any text editor). Both lists start empty.

1. **A YouTube channel** needs its channel id, which starts with `UC`. On the channel's YouTube page, click **…more** under the channel name, scroll down, click **Share channel**, then **Copy channel ID**. In `config/sources.yaml`, under `youtube:`, replace `channels: []` with (keep the spaces at the start of each line):
   ```yaml
     channels:
       - name: The channel's name
         channel_id: UCxxxxxxxxxxxxxxxxxxxxxx
   ```
   Add more channels as more `- name:` / `channel_id:` pairs, lined up the same way.
2. **A blog** needs the address of its feed: often the blog's address followed by `/feed` (e.g. `https://blog.example.com/feed/`). Under `blogs:`, replace `feeds: []` the same way, with `- name:` and `feed:` lines.
3. Restart the app. If the file has a typo, the app won't start and the terminal names the line; fix it and start again.

A video or post is used when its title or description mentions the ticker (e.g. `AMZN` or `$AMZN`) or the company's name. If a company goes by another name (Alphabet → Google), add it under `also_known_as`. The AI watches each new video once (never twice, even for another ticker), at most 10 per run; the rest wait for the next run. Change the limit in `config/settings.yaml` → `ai: max_videos_per_run`. Each 20-minute video costs roughly 4–6 cents of AI credit.

**Valuation comparisons and funds you follow** (optional)

Each report's "Valuation snapshot" compares the stock with its own past years, its industry's largest companies, its sector's largest companies and Professor Damodaran's industry average (free, no key). Nothing needs setting up. To change a comparison for one stock (another sector, your own peer list), open `config/benchmarks.yaml`: AMZN is already set to Technology, and the file shows an example.

The "Institutional & insider activity" part lists which of the funds you follow hold the stock and whether they bought or sold last quarter (from their 13F reports at the SEC; needs the SEC contact above). The list is in `config/institutional.yaml` and starts with 10 well-known investors (Berkshire Hathaway, Pershing Square and others); add or remove funds there. The same file has the thresholds for unusual options activity. Restart the app after editing either file.

**Setting up the calendar** (one time, about 2 minutes)

The Calendar page (and the "Coming up" box on the home page) shows your stocks' earnings dates, Fed meetings, and the dates of CPI, the jobs report, PCE and GDP. Those four come from FRED, the St. Louis Fed's free data service, which needs a free key. Without it they show as missing; everything else works.

1. Go to https://fredaccount.stlouisfed.org and create a free account (or sign in).
2. Open **My Account → API Keys** and click **Request API Key**. For the description, something like "Personal research dashboard, release dates" is fine. Agree to the terms.
3. Copy the key (32 letters and digits) and put it in `.env`: `FRED_API_KEY=` followed by the key (no spaces or quotes).
4. Restart the app and open **Calendar**.

Fed meeting dates are in `config/fomc_dates.yaml` (2026 only for now). When the Fed publishes next year's dates (usually in the summer, at https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm), add them the same way as the ones already there; the calendar reminds you once no future meeting is listed. Release times and the high/medium impact levels are in `config/settings.yaml` under `calendar:`.

**The macro dashboard** (nothing to set up if the calendar's FRED key is in place)

The **Macro** page shows broad market indicators as cards: the latest figure, the change since about a month earlier, a chart (switch between 1 year and 5 years at the top), the source and the date of the latest figure. Most come from FRED, using the same free `FRED_API_KEY` as the calendar; without it those cards say so and the others still work. The money market fund weekly total comes from ICI's website, and the two valuation cards (equity risk premium, S&P 500 forward P/E) from Yahoo Finance.

- The first visit fetches everything in the background (about a minute); the page reloads by itself. After that, figures are checked again every 12 hours while you use the page.
- Three cards build their charts from one saved reading per trading day (per week for ICI), starting from your first visit, so their charts start as a single dot and fill in over time.
- To add an indicator, find it on https://fred.stlouisfed.org, copy the code at the end of its page address (e.g. `UNRATE`), and add a card to `config/macro.yaml` (the top of the file shows how). Delete a card's lines to remove it. Restart the app after editing.

**Setting up X (Twitter)** (optional, about 10 minutes; costs money)

Reports can include posts on X: first new posts from accounts you trust that mention the stock, then (with the budget left that day) a search of all of X for "$TICKER". X charges for every post it sends, from credit you buy in advance (no subscription). The app keeps to the budgets in `config/x.yaml`: $5 a month and $0.22 a day to start. It is off until you do the steps below. X's site changes often, so the button names may differ slightly.

1. Go to https://developer.x.com and sign in with your X account. Sign up for developer access ("pay-per-use"); for the use description, something like "Personal research dashboard that reads public posts about stocks I follow; no posting, no resale" is fine.
2. In the developer console, create an app (any name, e.g. "Finance dashboard"). Under **Keys and tokens**, generate the **Bearer Token** and copy it right away (it is shown once).
3. Put it in `.env`: `X_BEARER_TOKEN=` followed by the token (no spaces or quotes).
4. In the console's billing page, buy a small amount of credit (e.g. $5) and **set a monthly spending limit of $5**. That's a second safety net besides the app's own budget.
5. Open `config/x.yaml`, change `enabled: false` to `enabled: true`, and add the accounts you trust under `accounts:` (their handles, the name after the "@"; the file shows an example).
6. Restart the app and generate a report. X posts show with an "X" label under Sources, and the end of the Sources section shows what X has cost so far today and this month.

When a budget is used up, the report says so under "Missing from this report" and carries on without X. To switch X off again, set `enabled: false` and restart. Reddit isn't included: Reddit closed its free access for personal projects (see `docs/DECISIONS.md`).

**Automatic morning reports** (off for now)

To have every watchlist reported on weekdays at 7:30am Eastern while the app is running, open `config/settings.yaml`, find `schedule:`, change `enabled: false` to `enabled: true`, and restart the app. The Reports page then shows when the next run is. If your computer is asleep or the app is closed at 7:30, that day's run happens only if the app is back by 8:00.

**Updating to a new version**

Merging changes on GitHub doesn't change the folder on your computer. After each merge, stop the app (`Ctrl+C`), then run these in the project folder:
```bash
git checkout main
```
```bash
git pull
```
```bash
uv sync
```
```bash
uv run alembic upgrade head
```
Then start the app again (the first step stopped it, so until you run this, the browser shows "localhost refused to connect"):
```bash
uv run uvicorn app.main:app --reload
```
Wait for the line `Application startup complete.`, then open http://localhost:8000. Leave this terminal window open while you use the app.

`uv sync` installs any new libraries and `alembic upgrade head` applies any database changes (Phase 9 adds two small tables for the macro figures); both do nothing if nothing changed. If `git pull` shows an error or mentions conflicts, stop and ask before going further. If the app stops with an error instead of `Application startup complete.`, copy the last few lines of the terminal and send them to Claude.

**Checks (optional)**
```bash
uv run pytest
```
```bash
uv run ruff check .
```
