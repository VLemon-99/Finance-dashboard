# Ralph planning round

Turn the spec into task files that build loops can work through. Don't write any application code.

## Read first
`CLAUDE.md`, `docs/SPEC.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `PLAN.md`, and the format example `ralph/tasks/EXAMPLE.json`.

## Create, for each feature (or only the features I name)
- `ralph/tasks/<ID>-<short-name>.json` — same format as `EXAMPLE.json`. Branch: `ralph/<ID>-<short-name>`.
- `ralph/progress/<ID>-<short-name>.md` — copy the header from `ralph/progress/EXAMPLE.md`.

## Story rules
1. **One session per story.** Each story is one change you can describe in one sentence ("add a filter dropdown to the reports page"). Anything bigger ("build the dashboard", "add authentication") gets split.
2. **Testable.** Each story lists acceptance criteria derived from the spec, referencing the criterion IDs (e.g. "F1.2"), and a `verify` command that proves it.
3. **Ordered.** Foundations first. Use `priority` (1 = first) and `dependsOn` for stories that need others done before them.
4. **Zoned.** Each story names its zone from `docs/ARCHITECTURE.md` → Work zones. Stories that change shared files say so in their description.
5. **No invention.** Only what the spec asks for. Nothing from "Out of scope". Anything unclear becomes a question for me — never a story built on a guess.
6. **F0 first.** If F0 (walking skeleton) isn't merged yet, plan it first: it sets up the project, the test runner and all shared files, so later features can run in parallel safely.

## Then
- Update the Roadmap table in `PLAN.md` (status: planned) and the "Can run in parallel" list.
- If you find gaps in the spec or architecture (e.g. missing work zones), list them — don't fix those documents without my OK.

## Report back, in plain language
- Each feature: number of stories, zones touched, depends on
- Which features can run in parallel, and why
- Stories you consider risky or hard to test
- Open questions for me
