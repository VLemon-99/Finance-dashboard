# Architecture

> The bird's-eye view. Keep this short and current — it's what a fresh agent or reviewer reads to understand the whole system.

## Overview
[2–3 sentences: how the system works at a high level.]

## Components
| Component | What it does | Where it lives |
|---|---|---|
| [e.g. Web UI] | [Shows the dashboard, handles uploads] | `src/ui/` |
| [e.g. API] | [Validates and stores data] | `src/api/` |
| [e.g. Database] | [Stores users and records] | `data/app.db` |

## How data flows
1. [e.g. User uploads a file in the UI]
2. [UI sends it to the API]
3. [API validates it and saves it to the database]

## External services
- [e.g. Stripe for payments — key stored in `.env` as `STRIPE_KEY`]

## Folder map
```
src/        application code
tests/      automated tests
docs/       spec, architecture, decisions
```
