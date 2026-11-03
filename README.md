# Finance Dashboard

A personal research dashboard for stocks and options. It gathers news, SEC filings, blogs,
YouTube, X and Reddit for the tickers you follow and digests them into a short daily report.
It also tracks stocks, option contracts (greeks, IV, days to expiration) and a virtual
portfolio, including the annualized return on short options, and shows a calendar of
market-moving events. It is not linked to any brokerage and never places trades.

**Status:** Phase 3 works: watchlists with live stock data, plus research reports from news and SEC filings, summarized by Gemini ("Generate now" and a history page under **Reports**). Phase 4: option chains with greeks under **Options** (or the "Options" link on any watchlist row), option contracts saved in watchlists, and a virtual **Portfolio**: press Bought or Sold on any stock or contract to record a trade, then see P&L, capital at risk and annualized returns on short options, credit spreads and expiry outcomes. A single-ticker quote lookup is at `/lookup`. See `PLAN.md` for the current phase.

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
3. **Budget alert** (so you're warned before spending more than the credit): in https://console.cloud.google.com open **Billing → Budgets & alerts → Create budget**. Name it "Gemini", set the amount to $10, keep the 50% / 90% / 100% email alerts, and click **Finish**.
4. Restart the app. Google sometimes renames these menus; if a step doesn't match what you see, tell Claude what's on your screen.

Never paste the key into chat, code or GitHub. It belongs only in `.env`, which is never uploaded.

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

`uv sync` installs any new libraries and `alembic upgrade head` applies any database changes; both do nothing if nothing changed. If `git pull` shows an error or mentions conflicts, stop and ask before going further. If the app stops with an error instead of `Application startup complete.`, copy the last few lines of the terminal and send them to Claude.

**Checks (optional)**
```bash
uv run pytest
```
```bash
uv run ruff check .
```
