# Progress log — F7 Accounts & hosting

> Append-only. Each loop round adds one entry at the bottom. Never rewrite or delete old entries.

## Lessons
<!-- Reusable learnings for future rounds: gotchas, patterns, commands that work. After the feature is merged, an interactive session promotes the useful ones into CLAUDE.md. -->
- The full test suite (`uv run pytest -q`) prints no summary line here; check the exit code (`echo $?`) instead. It takes about 3-4 minutes, so give it a long timeout.
- SQLite column changes in migrations need `op.batch_alter_table` (see `e1f7a2c3b4d5_user_password_hash.py`). Migration up/down/up test pattern: `tests/test_accounts_passwords.py::test_f7_2_password_migration_up_down_up_keeps_users`.
- Password helpers: `hash_password(password, cost)` / `check_password(stored, password)` in `app/services/users.py`; cost comes from `load_accounts_config(settings.accounts_config_file).scrypt`. Tests use a cheap `ScryptCost(n=2**4, r=1, p=1)` for speed.
- Login sessions: `start_session(session, user_id, days, now=None) -> token`, `user_for_token(session, token, now=None) -> User | None`, `end_session(session, token)` in `app/services/users.py`; length is `AccountsConfig.session_days`. Pass `now=` for a fixed clock in tests.
- Who is signed in: middleware `find_signed_in_user` in `app/main.py` sets `request.state.account` (a `SignedIn(user_id, email)` or None) from the `session` cookie on every non-static request. F7-S4 can read `request.state.account` in `get_user_id`.
- Jinja escapes apostrophes as `&#39;`, so don't assert on message text containing `'` in page tests.
- Ruff's S105/S106 flag fake passwords in tests; mark them `# noqa: S105 (fake test password)` / `# noqa: S106`.

## Rounds
<!--
## Round — YYYY-MM-DD HH:MM — F1-S1 — done | failed | blocked
- What I did:
- Files touched:
- Test result:
- Next round should know:
-->

## Round — 2026-11-12 — F7-S1 — done
- What I did: added a nullable `users.password_hash` column (migration `e1f7a2c3b4d5`, after `92cad3ca91ff`), `hash_password()` and `check_password()` in `app/services/users.py` using Python's built-in `hashlib.scrypt` with a 16-byte random salt and `hmac.compare_digest` (no new dependency). Stored form `scrypt$n$r$p$salt$hash` keeps the cost with each hash, so changing the config never locks out older passwords. Cost settings in new `config/accounts.yaml` (`AccountsConfig`/`ScryptCost` in `app/config.py`, `Settings.accounts_config_file`); defaults n=32768, r=8, p=1 (about 32 MB per check). Empty passwords can't be set; damaged hashes check false instead of crashing.
- Files touched: app/config.py, app/db/models.py, app/db/migrations/versions/e1f7a2c3b4d5_user_password_hash.py, app/services/users.py, config/accounts.yaml, tests/test_accounts_passwords.py (14 tests), this task file and log.
- Test result: verify 14 passed; full suite exit 0; ruff check + format clean.
- Next round should know: the accounts config is not yet loaded in `app/main.py` (nothing uses it at startup yet); F7-S2 adds session length to the same `AccountsConfig` and will likely wire it in. Worked on the cloud session's branch `claude/eloquent-bohr-tmxsww` instead of `ralph/F7-accounts`, as PROMPT_build step 2 allows.

## Round — 2026-11-12 — F7-S2 — done
- What I did: new `login_sessions` table (`LoginSession`: user_id, token_hash, created_at, expires_at; migration `f2a8b3c4d5e6` after `e1f7a2c3b4d5`) and `start_session` / `user_for_token` / `end_session` in `app/services/users.py`. Tokens come from `secrets.token_urlsafe(32)`; only their SHA-256 hash is stored (a plain hash is enough for long random tokens). Expiry is checked against a passed-in clock, all in UTC. Ending one session leaves the user's others working. `session_days` added to `AccountsConfig` and `config/accounts.yaml`.
- Session length: the owner hadn't answered the 30-day question, so it defaults to **30 days** as the story's notes said (a config value, 1-365). The owner can change `session_days` in `config/accounts.yaml`.
- Files touched: app/config.py, app/db/models.py, app/db/migrations/versions/f2a8b3c4d5e6_login_sessions.py, app/services/users.py, config/accounts.yaml, tests/test_accounts_sessions.py (12 tests), this task file and log.
- Test result: verify 12 passed (26 with F7-S1's); full suite exit 0; ruff check + format clean.
- Next round should know: expired session rows are never cleaned up yet (harmless; they just stop working). The accounts config still isn't loaded in `app/main.py`; F7-S3 needs it there for `session_days`.

## Round — 2026-11-12 — F7-S3 — done
- What I did: `/login` (GET form, POST check) and `POST /logout` in new `app/web/account_routes.py` + `login.html`. Email is trimmed, checked loosely (one @, no spaces, at most 320 characters; `owner@localhost` passes) and matched case-insensitively with a parameterized query. Wrong password, unknown email and user without a password all give the same "Email or password is wrong." (401); an unknown email is checked against a dummy hash so it takes as long. Bad input gives a clear message (400). Success: `start_session` with `session_days`, cookie `session` (HttpOnly, SameSite=Lax, Secure over HTTPS, Max-Age = session length), 303 to `/`. Logout ends the session and clears the cookie. The menu (base.html) shows the email + "Log out" button when signed in, else a "Log in" link. `create_app(accounts=...)` loads `config/accounts.yaml`. The existing cross-site check already covers /login and /logout (tested).
- Files touched: app/main.py, app/web/account_routes.py, app/web/templates/login.html, app/web/templates/base.html, app/web/static/style.css, tests/test_web_login.py (26 tests), this task file and log.
- Test result: verify 26 passed; full suite exit 0; ruff check + format clean.
- Next round should know: pages still use the default user whether signed in or not; F7-S4 changes `get_user_id` in `app/web/routes.py` to use `request.state.account`. No one can log in for real yet: no user has a password until F7-S6 (blocked on the owner). Passwords longer than 1024 characters are refused before hashing.
