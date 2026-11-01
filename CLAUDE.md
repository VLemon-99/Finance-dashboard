# Project: [NAME]

[One or two sentences: what this project is and who it's for.]

## About the owner
I'm tech-savvy but not a programmer. Explain choices in plain language, avoid jargon, and tell me exactly what to run or click when I need to do something myself.

## Source of truth — read these before starting any task
- `docs/SPEC.md` — what the product must do (with acceptance criteria)
- `docs/ARCHITECTURE.md` — the big picture: components, data, how they connect
- `docs/DECISIONS.md` — past decisions and why. Don't reverse one without asking me.
- `PLAN.md` — current phase, what's done, what's next, known issues

If my request conflicts with these documents, point it out and ask before proceeding.

## Tech stack
- Language/framework: [e.g. Python 3.12 + FastAPI]
- Database/storage: [e.g. SQLite]
- Hosting/deployment: [e.g. none yet / Vercel]

## Commands
- Install: `[command]`
- Run locally: `[command]`
- Run tests: `[command]`

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
- [none yet]
