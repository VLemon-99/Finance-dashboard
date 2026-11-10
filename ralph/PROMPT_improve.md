# Ralph improvement round

You are one round of an automated improvement loop: **suggest → independent review → test → implement**. Assume you remember nothing from earlier rounds — re-read the files below every round. Do **one step's worth of work per round** (one survey + review, or one improvement), then stop.

Your instructions give you these settings (defaults in brackets):
- **Task file** — e.g. `ralph/tasks/IMPROVE-2026-11-08.json`. Create it if it doesn't exist (same format as `ralph/tasks/EXAMPLE.json`, with feature `"IMPROVE"` and branch `ralph/improve-<date>`), plus a matching progress log in `ralph/progress/` copied from `EXAMPLE.md`.
- **Mode** — [`safe`] or `bold`.
  - `safe`: implement only improvements users wouldn't notice as a change in what the product does — bugs, robustness, security, tests, code structure, performance, accessibility, small UI polish. New features and UI redesigns are written up as **proposals for the owner** (status `blocked`, notes start with "PROPOSAL:") and not built.
  - `bold`: may also implement new features and UI changes, as long as they serve the goal in `docs/SPEC.md` and nothing in "Out of scope".
- **Max** — [5] improvements to implement in this run.
- **Focus** — [all] or a category (`bugs`, `security`, `tests`, `architecture`, `performance`, `ui`, `features`) or a work zone from `docs/ARCHITECTURE.md`.

## 1. Get oriented
Read in full: `CLAUDE.md`, `docs/SPEC.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `PLAN.md`, the task file and its progress log, and `git log --oneline -15`. Be on the task file's branch (create it from `main` if needed; if this session already runs on its own branch, stay on it). Never commit to `main`.

## 2. Check the baseline
Run the full test suite. If it already fails, fixing that is this round's work: fix it, log it, commit, stop.

## 3. Decide what this round does
- If **Max** improvements are already `done` → go to step 8.
- If there is a `todo` item → go to step 5 (test).
- Otherwise → step 4 (suggest + review). If the previous survey round found nothing worth doing, go to step 8 instead.

## 4. Suggest, then get an independent review (one round)
**Suggest.** Explore the codebase (within the Focus) and add up to 5 improvement items to the task file with status `"proposed"`. For each, fill in:
- `title`, `description` — what and why, in plain language
- `category`, `value` (high/medium/low), `risk` (high/medium/low)
- `acceptance` — checkable criteria; `verify` — the command that proves them
- `zone`, `priority` (best value-for-risk first)

Don't propose anything already `done`, `rejected` or `blocked` in this file, or that contradicts `docs/DECISIONS.md`.

**Independent review.** Launch a subagent with a fresh context. Give it only: the paths to `CLAUDE.md`, `docs/SPEC.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, the task file, and the current Mode — not your reasoning. Ask it to judge each `proposed` item, skeptically: is it worth doing, is the risk honest, does it conflict with the spec, decisions or out-of-scope list, are the acceptance criteria testable, does it fit the Mode? It returns per item: **approve**, **reject** or **needs owner**, with a one-line reason.

Record each verdict in the item's `notes` (prefix "REVIEW:"). Approved → `todo`. Rejected → `rejected`. Needs owner, or a feature/redesign in `safe` mode → `blocked` (notes start with "PROPOSAL:" and say what the owner must decide).

If you can't launch a subagent: leave the items `proposed`, log that, and stop — the next round acts as the reviewer and does only the review.

Log the round, commit the task file and progress log, and stop.

## 5. Test first
Take the `todo` item with the lowest `priority`.
- Write or extend tests that check its acceptance criteria. Run them — new behavior should fail before you implement it.
- For refactors or performance work, first add tests that **lock in the current behavior** and confirm they pass before you change any code.

## 6. Implement
- Make the change. Stay within the item and its zone; don't change shared files unless the item says so.
- Run the item's `verify` command, the full test suite, and lint/type check if `CLAUDE.md` lists one. Everything must pass.
- **Second independent check:** launch a fresh subagent to review your diff against the item's acceptance criteria. It must confirm: no existing test was weakened or deleted, nothing outside the item changed, no security problems. If it finds a serious issue, fix it once and re-run everything.

## 7. Record the result
- **Passed and confirmed:** status `"done"`. Commit code, tests, task file and progress log together: `<item id>: <title>`.
- **Failed:** add 1 to `attempts`, explain in `notes`. At 3 attempts → `blocked` with what the owner needs to decide. Discard the code changes so the branch stays green; commit only the task file and progress log.

Append the round to the progress log (same format as `ralph/progress/EXAMPLE.md`), adding reusable learnings under Lessons. Then stop — one improvement per round.

## 8. Finish the run
1. Run the full test suite one last time.
2. Push the branch and open a pull request if you have the tools. Description: improvements done (with why), items rejected (with the reviewer's reason), **proposals for the owner**, final test result.
3. Output exactly: `<promise>RALPH_COMPLETE</promise>`

Never output that phrase in any other situation.

## Hard rules
- All rules in `CLAUDE.md` apply, including test integrity and security.
- Never build anything listed in "Out of scope", and never reverse a logged decision — propose it instead.
- Never modify, skip, weaken or delete an existing test to make it pass.
- Never commit failing code, commit to `main`, force-push or deploy.
- Only edit your own task file and progress log among the planning files — never `PLAN.md`, `CLAUDE.md`, `docs/`, or other features' files.
- Don't run this loop in parallel with other loops unless its Focus is a single work zone no other loop is using.
