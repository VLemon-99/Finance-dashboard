# Starter template — how to use

1. Create a new **private** GitHub repo and copy all of these files into it (including the hidden `.gitignore` and `.env.example`).
   - Tip: put this set in its own repo once and mark it as a **template repository** in GitHub's settings. Every new project is then one click: "Use this template".
2. Open the repo in Claude Code, turn on plan mode, and paste prompt **#1 (Kickoff)** from `docs/PROMPTS.md` with your rough idea.
3. Answer its questions, review the docs it fills in, then start building with prompt **#2**.
4. End each task with prompt **#4 (Handoff)**, then start a fresh session.

| File | Purpose |
|---|---|
| `CLAUDE.md` | Rules Claude Code reads automatically every session |
| `docs/SPEC.md` | What to build, with testable acceptance criteria |
| `docs/ARCHITECTURE.md` | The bird's-eye view of the system |
| `docs/DECISIONS.md` | Why things are the way they are |
| `PLAN.md` | Progress, next steps, handoff notes |
| `docs/PROMPTS.md` | Copy-paste prompts for kickoff, bugs, reviews, handoffs |
| `.gitignore` / `.env.example` | Keep secrets out of git |

Replace this README with a real one once the project takes shape.
