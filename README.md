# Finance Dashboard

A personal research dashboard for stocks and options. It gathers news, SEC filings, blogs,
YouTube, X and Reddit for the tickers you follow and digests them into a short daily report.
It also tracks stocks, option contracts (greeks, IV, days to expiration) and a virtual
portfolio, including the annualized return on short options, and shows a calendar of
market-moving events. It is not linked to any brokerage and never places trades.

**Status:** Phase 1 (walking skeleton) works: type a ticker, see its price. See `PLAN.md` for the current phase.

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
4. Create your settings file from the example (nothing in it is needed yet for Phase 1):
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

**Checks (optional)**
```bash
uv run pytest
```
```bash
uv run ruff check .
```
