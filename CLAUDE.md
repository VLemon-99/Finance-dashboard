# Project: Finance Dashboard

A personal research dashboard for one investor (for now): it aggregates and AI-summarizes news, SEC filings, blogs, YouTube, X and Reddit for followed tickers into a daily report, and tracks stocks, option contracts (greeks, IV, DTE) and a virtual portfolio with annualized returns on short options, plus a market-events calendar. Not linked to any brokerage; never places trades.

## About the owner
I'm tech-savvy but not a programmer. Explain choices in plain language, avoid jargon, and tell me exactly what to run or click when I need to do something myself.

## Source of truth — read these before starting any task
- `docs/SPEC.md` — what the product must do (features with testable acceptance criteria)
- `docs/ARCHITECTURE.md` — the big picture: components, data, how they connect, and the work zones that decide which features can be built in parallel
- `docs/DECISIONS.md` — past decisions and why. Don't reverse one without asking me.
- `PLAN.md` — roadmap: which features exist, their status and branch; plus what's next, known issues and the handoff note
- `ralph/tasks/*.json` — the small stories for each feature, with status (`todo` / `done` / `blocked`)
- `ralph/progress/*.md` — append-only log of what each loop round did and learned

If my request conflicts with these documents, point it out and ask before proceeding.

## Tech stack
Status: Phases 1–3 (walking skeleton, watchlists, report v1), Phase 4 (option chains, virtual portfolio), Phase 5 (blogs, YouTube, optional schedule), Phase 6 (calendar), Phase 7 (valuation benchmarks, holders, funds followed, unusual options), Phase 8 (X posts, off by default; Reddit parked) and Phase 9 (macro dashboard) built; the commands below work. Reasons for each choice are in `docs/DECISIONS.md`.
- Language/framework: Python 3.12 + FastAPI, server-rendered pages with Jinja2 templates + HTMX, Chart.js for charts
- Database/storage: SQLite (`data/app.db`, git-ignored) via SQLModel/SQLAlchemy, Alembic migrations; Postgres-compatible for later
- Background jobs: APScheduler (weekday 7:30am US Eastern report; off by default, `schedule.enabled` in `config/settings.yaml`)
- Data sources: Yahoo Finance via `yfinance` (market data, earnings calendar), SEC EDGAR, news/blog/YouTube RSS via `feedparser`, FRED API (release dates, macro series), Fed meeting dates in `config/fomc_dates.yaml`, Damodaran's industry averages (NYU Stern pages), SEC 13F reports for funds followed, ICI's weekly money market fund page; X API pay-per-use (`config/x.yaml`, off by default); Reddit parked (see DECISIONS)
- AI: Gemini API via `google-genai`, behind `app/ai/llm.py` interface
- Tooling: `uv` (dependencies + running), `ruff` (lint + format), `pytest`, GitHub Actions CI
- Hosting/deployment: none yet. Runs locally at http://localhost:8000; cloud + login planned for the last phase

## Commands
- Install: `uv sync`
- Set up the database: `uv run alembic upgrade head`
- Run locally: `uv run uvicorn app.main:app --reload` (then open http://localhost:8000)
- Run all tests: `uv run pytest` (about 2 minutes, no network)
- Lint / format check (what CI runs): `uv run ruff check .` and `uv run ruff format --check .`
- Fix formatting: `uv run ruff format .`

## Two ways of working
- **Interactive session (with me):** planning, reviews, bugs, decisions. Plan before coding: for anything bigger than a small fix, propose a short plan and wait for my OK.
- **Loop round (Ralph):** follow `ralph/PROMPT_build.md` exactly. One story per round, then stop. Never ask me questions in a loop round — if a human decision is needed, mark the story `blocked` and explain why in its notes.

## How to work (both modes)
1. **Small steps.** One story, feature or fix at a time. Keep changes focused; don't refactor unrelated code.
2. **Ask, don't guess.** Don't invent features or requirements. Ambiguity → ask (interactive) or block (loop).
3. **Tests first.** Write or extend tests for the acceptance criteria in `docs/SPEC.md` (refer to their IDs, e.g. F3.6), then make them pass. Run the full test suite before calling anything done, and report results honestly — including failures.
4. **Green commits only.** Commit after each working step with a clear message. Never commit failing code. Never commit to `main` — always work on a branch.
5. **Keep docs current.** Loop rounds update only their own task file and progress log. Interactive sessions update `PLAN.md`, add an entry to `docs/DECISIONS.md` for any meaningful choice, and update `docs/ARCHITECTURE.md` if the structure changed.
6. **Prefer simple.** Choose the simplest approach that meets the spec. Avoid adding dependencies unless clearly needed — and tell me when you add one.

## Test integrity (non-negotiable)
- Never modify, skip, weaken or delete an existing test to make it pass. If a test seems wrong, stop: ask me (interactive) or block the story (loop).
- Never mark a story `done` without running its verify command and the full test suite.

## Security rules (non-negotiable)
- Never put secrets (API keys, passwords, tokens) in code, commits, or logs. Use `.env` (which is git-ignored) and never read it. Keep `.env.example` updated with placeholder values only.
- Never use real personal or customer data in tests or examples; use fake data.
- Validate all user input. Never build database queries or shell commands by gluing strings together with user input.
- Never push to `main`, never force-push. Never deploy (loop rounds never; interactive sessions only with my explicit OK).
- Ask before running anything that deletes data, deploys, or changes settings outside this repo.

## Project-specific rules and lessons learned
<!-- Promoted from the "Lessons" sections of ralph/progress/*.md after a loop's work is merged. Loop rounds don't edit this file. -->
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
