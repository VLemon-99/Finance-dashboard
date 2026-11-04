# Project: Finance Dashboard

A personal research dashboard for one investor (for now): it aggregates and AI-summarizes news, SEC filings, blogs, YouTube, X and Reddit for followed tickers into a daily report, and tracks stocks, option contracts (greeks, IV, DTE) and a virtual portfolio with annualized returns on short options, plus a market-events calendar. Not linked to any brokerage; never places trades.

## About the owner
I'm tech-savvy but not a programmer. Explain choices in plain language, avoid jargon, and tell me exactly what to run or click when I need to do something myself.

## Source of truth — read these before starting any task
- `docs/SPEC.md` — what the product must do (with acceptance criteria)
- `docs/ARCHITECTURE.md` — the big picture: components, data, how they connect
- `docs/DECISIONS.md` — past decisions and why. Don't reverse one without asking me.
- `PLAN.md` — current phase, what's done, what's next, known issues

If my request conflicts with these documents, point it out and ask before proceeding.

## Tech stack
Status: Phases 1–3 (walking skeleton, watchlists, report v1), Phase 4 (option chains, virtual portfolio), Phase 5 (blogs, YouTube, optional schedule), Phase 6 (calendar) and Phase 7 (valuation benchmarks, holders, funds followed, unusual options) built; the commands below work. Reasons for each choice are in `docs/DECISIONS.md`.
- Language/framework: Python 3.12 + FastAPI, server-rendered pages with Jinja2 templates + HTMX, Chart.js for charts
- Database/storage: SQLite (`data/app.db`, git-ignored) via SQLModel/SQLAlchemy, Alembic migrations; Postgres-compatible for later
- Background jobs: APScheduler (weekday 7:30am US Eastern report; off by default, `schedule.enabled` in `config/settings.yaml`)
- Data sources: Yahoo Finance via `yfinance` (market data, earnings calendar), SEC EDGAR, news/blog/YouTube RSS via `feedparser`, FRED API (release dates), Fed meeting dates in `config/fomc_dates.yaml`, Damodaran's industry averages (NYU Stern pages), SEC 13F reports for funds followed; X and Reddit later
- AI: Gemini API via `google-genai`, behind `app/ai/llm.py` interface
- Tooling: `uv` (dependencies + running), `ruff` (lint + format), `pytest`, GitHub Actions CI
- Hosting/deployment: none yet. Runs locally at http://localhost:8000; cloud + login planned for the last phase

## Commands
- Install: `uv sync`
- Set up the database: `uv run alembic upgrade head`
- Run locally: `uv run uvicorn app.main:app --reload` (then open http://localhost:8000)
- Run tests: `uv run pytest`
- Check code style: `uv run ruff check .` and `uv run ruff format .`

## How to work
1. **Plan before coding.** For anything bigger than a small fix, propose a short plan and wait for my OK.
2. **Small steps.** One feature or fix at a time. Keep changes focused; don't refactor unrelated code.
3. **Ask, don't guess.** If a requirement is ambiguous, ask me. Don't invent features or requirements.
4. **Test.** Write or update automated tests for new behavior, tied to the acceptance criteria in SPEC.md. Run them before saying a task is done, and tell me the result honestly — including failures.
5. **Commit.** After each working step, commit with a clear message. Work on a branch for anything non-trivial.
6. **Keep docs current.** At the end of each task, update `PLAN.md`; add an entry to `docs/DECISIONS.md` for any meaningful choice; update `docs/ARCHITECTURE.md` if the structure changed.
7. **Prefer simple.** Choose the simplest approach that meets the spec. Avoid adding dependencies unless clearly needed — and tell me when you add one.

## Security rules (non-negotiable)
- Never put secrets (API keys, passwords, tokens) in code, commits, or logs. Use `.env` (which is git-ignored) and keep `.env.example` updated with placeholder values only.
- Never use real personal or customer data in tests or examples; use fake data.
- Validate all user input. Never build database queries or shell commands by gluing strings together with user input.
- Ask before running anything that deletes data, deploys, or changes settings outside this repo.

## Project-specific rules and lessons learned
<!-- Add rules here as you discover them, e.g. "Dates are always stored in UTC." -->
- Every outside service (market data, content sources, AI) goes through its adapter in `app/providers/`, `app/sources/` or `app/ai/`. Pages and services never call `yfinance`, HTTP feeds or Gemini directly.
- One failing source must never break a page or a report. Show "stale"/"missing" instead (SPEC F1.6, F4.11).
- Never send portfolio, watchlist or other personal data to the AI service. Only public content (SPEC F4.13).
- Every report claim must cite a stored source item. No uncited AI text in reports.
- Finance numbers (greeks, DTE, P&L, capital at risk, annualized return, sentiment score) are calculated in Python code, not by the AI, and each formula has tests with known answers (SPEC F2.3, F3.6).
- Tests never hit the network: use recorded sample responses and mock the AI.
- Dates and times are stored in UTC and shown in US Eastern time.
- Every user-owned table has a `user_id` column (accounts come later).
- Limits, weights, thresholds and trusted sources live in `config/`, not in code.
- No scraping that breaks a site's terms of service (see the X/Twitter decision).
