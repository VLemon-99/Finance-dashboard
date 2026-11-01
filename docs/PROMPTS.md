# Reusable prompts

Copy-paste these into Claude Code. They are written to work with the files in this repo, so the agent gets the context it needs from the docs instead of from chat history.

---

## 1. Kickoff — turn a rough idea into the project docs
Use once, at the very start, in plan mode.

> Here's my rough idea: [paste your few lines].
>
> Before writing anything, interview me: ask the questions you need to remove ambiguity (one batch, numbered, plain language). Then fill in `docs/SPEC.md` (with testable acceptance criteria and an "out of scope" list), `docs/ARCHITECTURE.md`, and `PLAN.md` (phases starting with a walking skeleton). Recommend a simple tech stack, explain why in plain language, log it in `docs/DECISIONS.md`, and fill in the stack and commands in `CLAUDE.md`. Don't write any application code yet.

---

## 2. Start a work session

> Read `PLAN.md` and pick up the next task. Tell me in two or three sentences what you're going to do, then propose a short plan and wait for my OK.

---

## 3. Report a bug

> Bug: [what I did] → expected [X] → got [Y].
> Error message (exact): [paste it]
>
> Find the root cause before changing anything, explain it in plain language, then fix it, add a test that would have caught it, and run the tests.

---

## 4. Handoff — end a session before it goes stale
Use when a task is done, or when the agent starts repeating mistakes or forgetting decisions. Then start a fresh session (`/clear`).

> We're wrapping up this session. Commit any working changes. Update `PLAN.md` (done, next up, known issues) and write a handoff note there: where we stopped, anything half-finished, and what the next session should check first. Add any decisions we made to `docs/DECISIONS.md`, and any lessons learned to the bottom of `CLAUDE.md`.

---

## 5. Second opinion — independent review
Use in a **fresh** session (or ask for a subagent) so the reviewer didn't write the code.

> You are reviewing, not building. Read `CLAUDE.md`, `docs/SPEC.md`, `docs/ARCHITECTURE.md` and `docs/DECISIONS.md`, then review the changes on this branch compared with `main`. Report, in plain language and ranked by severity: bugs, anything that contradicts the spec or a logged decision, missing tests, and unnecessary complexity. Don't change any files.

---

## 6. Architecture check-in — the bird's-eye view
Use every few phases, or when things feel tangled.

> Step back and look at the whole project. Compare the actual code with `docs/ARCHITECTURE.md` and `docs/SPEC.md`. Where have they drifted apart? What parts are getting complicated or fragile? What would you simplify before we add more features? Update `ARCHITECTURE.md` to match reality, and list your recommendations — don't refactor anything yet.

---

## 7. Security review — before anything goes live
Use in a fresh session.

> Do a security review of this repo. Check: secrets in code or git history, input validation, authentication and permissions, how data is stored, risky or outdated dependencies, and anything exposed to the internet. Rank findings by severity, explain each in plain language with how to fix it. Don't change anything until I approve.
