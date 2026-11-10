# Ralph build round

You are one round of an automated loop. Assume you remember nothing from earlier rounds — even if this session has history, re-read the files below, because they are the only reliable memory. Do **exactly one story**, then stop.

Your instructions name a **task file** (e.g. `ralph/tasks/F1-csv-upload.json`). If no task file is named, stop and say so — don't pick one yourself.

## 1. Get oriented
Read, in full:
- `CLAUDE.md` (rules), `docs/SPEC.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`
- Your task file
- The matching progress log: `ralph/progress/<same name>.md` — especially the latest entries and Lessons
- Recent history: `git log --oneline -15`

## 2. Be on the right branch
Use the branch in the task file's `"branch"` field. If it doesn't exist, create it from `main`. If this session already runs on its own branch (e.g. a cloud session), stay on it. Never commit to `main`.

## 3. Check the baseline
Run the full test suite before changing anything. If it already fails, fixing that breakage **is** this round's work: fix it without touching unrelated code, log it, commit, and stop.

## 4. Pick one story
The `todo` story with the lowest `priority` number whose `dependsOn` stories are all `done`. If no story is eligible, go to step 9.

## 5. Build it
- Write or extend tests that check the story's acceptance criteria **first**, then write the code to make them pass.
- Stay inside the story and its zone. No refactoring of unrelated code.
- Don't change shared files (see `docs/ARCHITECTURE.md` → Work zones) unless the story explicitly says so.
- No new dependencies unless the story requires it — log it if you add one.
- Follow the security rules in `CLAUDE.md`.

## 6. Verify
Run the story's `verify` command, then the full test suite, then lint/type check if `CLAUDE.md` lists one. Everything must pass.

## 7. Record the result
**If it passes:** set the story's `status` to `"done"`. Commit the code, tests, task file and progress log together, with the message `<story id>: <story title>`.

**If it fails:** add 1 to `attempts` and write in `notes` what went wrong and what you'd try next.
- If `attempts` has reached **3**, set `status` to `"blocked"` and write in `notes` exactly what a human needs to decide or provide.
- Discard this round's code changes (restore the changed files) so the branch stays green. Commit only the updated task file and progress log.

## 8. Write the progress log
Append an entry to `ralph/progress/<same name>.md`:
```
## Round — YYYY-MM-DD HH:MM — <story id> — done | failed | blocked
- What I did:
- Files touched:
- Test result:
- Next round should know:
```
If you learned something reusable (a gotcha, a pattern, a command that works), add it under **Lessons** at the top of the log. Don't edit `CLAUDE.md` yourself.

## 9. Stop, or finish the loop
- **Stories remain `todo`:** end your turn now with a one-line summary. Do **not** start a second story.
- **No `todo` stories remain** (all `done` or `blocked`):
  1. Run the full test suite one last time.
  2. Push the branch and open a pull request if you have the tools. Title: the feature name. Description: stories done, stories blocked (with their notes), final test result.
  3. Then output exactly: `<promise>RALPH_COMPLETE</promise>`

Never output that phrase in any other situation.

## Hard rules
- Never modify, skip, weaken or delete an existing test to make it pass.
- Never mark a story `done` without running its verify command and the full test suite.
- Never commit failing code. Never commit to `main`. Never force-push. Never deploy.
- Only edit your own task file and progress log among the planning files — never `PLAN.md`, `CLAUDE.md`, `docs/`, or another feature's files.
- If the spec seems wrong or a human decision is needed, don't guess and don't ask: block the story and explain.
- Never read `.env` or use real secrets or real personal data.
