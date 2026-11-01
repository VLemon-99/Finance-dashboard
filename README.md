# Finance Dashboard

A personal research dashboard for stocks and options. It gathers news, SEC filings, blogs,
YouTube, X and Reddit for the tickers you follow and digests them into a short daily report.
It also tracks stocks, option contracts (greeks, IV, days to expiration) and a virtual
portfolio, including the annualized return on short options, and shows a calendar of
market-moving events. It is not linked to any brokerage and never places trades.

**Status:** planning complete; no application code yet. See `PLAN.md` for the current phase.

## Documents
| File | Purpose |
|---|---|
| [`docs/SPEC.md`](docs/SPEC.md) | What the app must do, with testable acceptance criteria and an out-of-scope list |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How the pieces fit together |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Why things are the way they are (tech stack, data sources, AI) |
| [`PLAN.md`](PLAN.md) | Phases, progress, next steps, handoff notes |
| [`CLAUDE.md`](CLAUDE.md) | Rules, stack and commands Claude Code reads every session |
| [`docs/PROMPTS.md`](docs/PROMPTS.md) | Copy-paste prompts for work sessions, bugs, reviews and handoffs |

## Running it (from Phase 1 onward)
```bash
uv sync
```
```bash
cp .env.example .env
```
```bash
uv run uvicorn app.main:app --reload
```
Then open http://localhost:8000.
