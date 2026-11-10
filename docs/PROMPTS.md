# Reusable prompts

Copy-paste these into Claude Code. They work with the files in this repo, so the agent gets its context from the docs, not from chat history.

**The cycle:** Kickoff (1) → Plan stories (2) → Check the stories (3) → Run loops (4) → Review the pull request (5) → Unblock (6) → Merge wrap-up (7) → back to 2 for the next features. Once features exist, run the improvement loop (13) between feature cycles.

---

## 1. Kickoff — rough idea → spec
Once per project, in plan mode.

> Here's my rough idea: [paste your few lines].
>
> Before writing anything, interview me: ask the questions you need to remove ambiguity (one numbered batch, plain language). Then fill in `docs/SPEC.md` — features with IDs, acceptance criteria that an automated test can check, an F0 walking skeleton, and an "Out of scope" list. Fill in `docs/ARCHITECTURE.md`, including work zones and shared files. Recommend a simple tech stack, explain why in plain language, and log it in `docs/DECISIONS.md`. Fill in the stack and commands in `CLAUDE.md`, and add the install, test and lint commands to the `allow` list in `.claude/settings.json` so loops can run them. Don't write application code yet.

---

## 2. Plan stories — spec → task files
In a fresh session, after kickoff or whenever new features are added to the spec.

> Follow `ralph/PROMPT_plan.md`. [Optional: Only plan features F1 and F2.]

---

## 3. Check the stories — before any loop runs
In a **fresh** session, so the checker didn't write the stories.

> You are reviewing, not building. Read `docs/SPEC.md`, `docs/ARCHITECTURE.md` and every file in `ralph/tasks/` (except `EXAMPLE.json`). Report: spec criteria no story covers, stories too big for one session, acceptance criteria that can't be tested automatically, wrong ordering or missing dependencies, stories that touch shared files or other zones without saying so, and anything invented that isn't in the spec. Don't change any files — list the fixes and I'll approve them.

---

## 4. Run a loop
One loop per feature, each on its own branch. Only run loops in parallel if their zones don't overlap (see `PLAN.md` → "Can run in parallel").

**Option A — the official plugin (easiest; works in the desktop app and cloud sessions).** Install the `ralph-loop` plugin once (type `/plugin` in Claude Code to browse and install; older listings call it `ralph-wiggum`), then start a session and type:

```
/ralph-loop "Follow ralph/PROMPT_build.md exactly. Task file: ralph/tasks/F1-csv-upload.json" --completion-promise "RALPH_COMPLETE" --max-iterations 15
```
Stop it any time with `/cancel-ralph`. Note: the plugin repeats the prompt inside one session, so context builds up over rounds — keep the round limit modest and run one feature per session.

**Option B — the loop script (true fresh start every round; needs a terminal).**

```
./ralph/ralph.sh ralph/tasks/F1-csv-upload.json 15
```
Run it in a disposable environment, not on a computer with personal files. Logs go to `ralph/logs/`.

**Round limit rule of thumb:** number of stories × 1.5, rounded up. Always set one.

---

## 5. Review a loop's pull request
In a **fresh** session.

> You are reviewing, not building. Read `CLAUDE.md`, `docs/SPEC.md`, `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, the task file `[ralph/tasks/F1-...json]` and its progress log. Then review this branch's changes compared with `main`. Check specifically:
> 1. Every story marked `done` really meets its acceptance criteria — run the tests yourself.
> 2. No existing test was modified, skipped, weakened or deleted (look at every change under the tests folder).
> 3. Nothing outside the feature's zone or in shared files changed without the story saying so.
> 4. Bugs, security problems (secrets, input validation), and nothing built that's out of scope.
> 5. Unnecessary complexity or new dependencies.
>
> Report in plain language, ranked by severity, and end with a clear recommendation: merge, merge after small fixes, or send back. Don't change any files.

---

## 6. Unblock stories
When a loop finishes with blocked stories.

> Read `[ralph/tasks/F1-...json]` and its progress log. For each `blocked` story, explain in plain language what went wrong and what decision you need from me, with your recommended option. After I answer, update the story (description, acceptance criteria or notes), reset `attempts` to 0 and `status` to `todo`, and log my decision in `docs/DECISIONS.md` if it's a meaningful choice. Then I'll restart the loop.

---

## 7. Merge wrap-up
After merging a feature's pull request.

> Feature [F1] is merged. Update `PLAN.md` (status, what can now run in parallel, waiting-on-me list). Read the Lessons in `ralph/progress/[F1-...].md` and propose which ones belong in `CLAUDE.md` — add the ones I approve. Add any decisions the loop proposed to `docs/DECISIONS.md` if I approve them. Update `docs/ARCHITECTURE.md` if the structure changed.

---

## 8. Report a bug
> Bug: [what I did] → expected [X] → got [Y].
> Error message (exact): [paste it]
>
> Find the root cause before changing anything and explain it in plain language. If it's a small fix, fix it on a branch with a test that would have caught it, and run all tests. If it's bigger, add it as a new story (priority 1) to the relevant task file instead, so a loop can handle it.

---

## 9. Interactive work session
For work that needs back-and-forth with you rather than a loop.

> Read `PLAN.md`. I want to work on: [task]. Tell me in two or three sentences what you're going to do, then propose a short plan and wait for my OK.

---

## 10. Handoff — end an interactive session
Then start fresh with `/clear`.

> We're wrapping up. Commit any working changes on a branch. Update `PLAN.md` (including the handoff note: where we stopped, anything half-finished, what to check first). Add decisions we made to `docs/DECISIONS.md` and lessons learned to `CLAUDE.md`.

---

## 11. Architecture check-in
Every few features, or when things feel tangled.

> Step back and look at the whole project. Compare the actual code with `docs/ARCHITECTURE.md` and `docs/SPEC.md`. Where have they drifted apart? Are the work zones still accurate — are parallel loops stepping on each other? What's getting fragile? Update `ARCHITECTURE.md` to match reality and list recommendations — don't refactor anything yet.

---

## 12. Security review
Before anything goes live, in a fresh session.

> Do a security review of this repo. Check: secrets in code or git history, input validation, authentication and permissions, how data is stored, risky or outdated dependencies, anything exposed to the internet, and whether `.claude/settings.json` is still tight enough. Rank findings by severity and explain each in plain language with how to fix it. Don't change anything until I approve.

---

## 13. Improvement loop — suggest → independent review → test → implement
Goes through the existing code on its own: proposes improvements (features, architecture, UI, bugs, security, performance, tests), has a fresh reviewer agent approve or reject each one, writes tests first, implements the approved ones one per round, and ends with a pull request. The full round instructions are in `ralph/PROMPT_improve.md`.

**Settings you choose:**
- **Mode:** `safe` (default) builds only changes users wouldn't notice as new behavior — fixes, security, tests, structure, performance, accessibility, small UI polish — and writes feature or redesign ideas up as proposals for you. `bold` also builds features and UI changes that fit the spec.
- **Max:** how many improvements to implement this run (default 5).
- **Focus:** `all`, a category (`bugs`, `security`, `tests`, `architecture`, `performance`, `ui`, `features`), or one work zone.

**With the plugin:**
```
/ralph-loop "Follow ralph/PROMPT_improve.md exactly. Task file: ralph/tasks/IMPROVE-2026-11-08.json. Mode: safe. Max: 5. Focus: all" --completion-promise "RALPH_COMPLETE" --max-iterations 12
```

**With the script:**
```
./ralph/ralph.sh ralph/tasks/IMPROVE-2026-11-08.json 12 ralph/PROMPT_improve.md "Mode: safe. Max: 5. Focus: all"
```

**Single supervised run (no loop)** — paste into a normal session and stay to watch:
> Follow `ralph/PROMPT_improve.md`, using task file `ralph/tasks/IMPROVE-[today's date].json`, Mode: safe, Max: 1, Focus: all. Keep going round after round until you reach step 8, pausing after each round to give me a two-line summary.

**Round limit rule of thumb:** Max × 2 + 2. Use a new task file name (today's date) for each run.

**Afterwards:** review the pull request with prompt 5, decide on the proposals it lists (approved ones go into `docs/SPEC.md` and get planned with prompt 2), then run prompt 7.

Don't run this in parallel with feature loops unless Focus is a single zone no other loop is touching.

---

## 14. Start a work session — pick up the next task
Kept from this project's earlier prompts. For interactive work when you don't have a specific task in mind.

> Read `PLAN.md` and pick up the next task. Tell me in two or three sentences what you're going to do, then propose a short plan and wait for my OK.

---

## 15. Second opinion — independent review of any branch
Kept from this project's earlier prompts. For changes made in an interactive session (loop pull requests use prompt 5). Use in a **fresh** session (or ask for a subagent) so the reviewer didn't write the code.

> You are reviewing, not building. Read `CLAUDE.md`, `docs/SPEC.md`, `docs/ARCHITECTURE.md` and `docs/DECISIONS.md`, then review the changes on this branch compared with `main`. Report, in plain language and ranked by severity: bugs, anything that contradicts the spec or a logged decision, missing tests, and unnecessary complexity. Don't change any files.

