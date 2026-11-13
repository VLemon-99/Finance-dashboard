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
- Pages with an "e.g. AAPL" placeholder (portfolio, forms) make `"AAPL" not in page` checks useless; check option symbols or a specific line like "My tickers: AAPL." instead. A planted `Report(body={})` can't be opened (the template needs real sections).
- Run the baseline test suite to completion **before** editing: a background run while files change gives false failures (e.g. a new setting in `config/accounts.yaml` read by the old `AccountsConfig`, which forbids unknown keys).
- App with custom settings in tests: see the `make_client` fixture in `tests/test_web_login_required.py` (`create_app(..., accounts=AccountsConfig(...))` with all the fakes).
- The owner is `ensure_owner(session, default_email)` in `app/services/users.py`: the first user by id (created when there are none). `app.state.user_id` is the owner. Email helpers there too: `is_valid_email()`, `find_by_email()` (case-insensitive), `MAX_EMAIL`. Minimum password length: `AccountsConfig.min_password_length` (12).
- Starting a signed-in session after login/sign-up (and later Google): `_signed_in_redirect(request, session, user_id, next_url)` in `app/web/account_routes.py` (cookie + safe redirect). Invite list: `AccountsConfig.invited` (lower-cased, checked) and `AccountsConfig.is_invited(email)`. `EMAIL_PATTERN` / `MAX_EMAIL` now live in `app/config.py` (users.py imports them).
- The login route reads the time through `utc_now` imported into `app/web/account_routes.py`; tests freeze it with `monkeypatch.setattr(app.web.account_routes, "utc_now", clock)` (see `tests/test_web_login_throttle.py`). Migration head is now `a3c9e7f1b2d4` (login_failures).
- When a scripted multi-edit fails on one replacement, none of that file's edits are written: rerun that file only.
- Google adapter: `GoogleSignIn(client_id, client_secret, transport=..., clock=...)` in `app/providers/google_auth.py`; `start(redirect_uri) -> SignInStart(url, state, nonce, code_verifier)`, `finish(code, redirect_uri, nonce, code_verifier) -> email`, `is_set_up`, errors are `GoogleAuthError` (plain, safe to show). Fake Google in tests: `FakeGoogle` + `id_token(**claims)` in `tests/test_google_auth.py`.
- `create_app(google=...)` takes the Google adapter (`app.state.google`); tests use `FakeGoogle` in `tests/test_web_google_login.py` (states are "state-0", "state-1", ...).
- Host names: `tests/conftest.py` has an autouse fixture that makes every test app also answer to "testserver" (it replaces `app.main.load_hosting_config`). To test the host rule, pass `hosting=HostingConfig(...)` to `create_app` and use `base_url="http://localhost:8000"`.
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

## Round — 2026-11-12 — F7-S4 — done
- What I did: `get_user_id` in `app/web/routes.py` now returns the signed-in user's id from `request.state.account` (set by the cookie middleware from F7-S3), falling back to the default user without a valid cookie. Every page and action already goes through `UserDep`, so nothing else changed. New tests sign two users in on two test clients: each sees only their own watchlists, portfolio, report history and calendar "My tickers"; 13 kinds of request on the other user's ids answer 404 (settling their position: 400) and change nothing; a report run started by Alice is stored under Alice, 404 for Bob, and doesn't block Bob's own run; new watchlists belong to whoever is signed in; without a cookie, after logout or with a made-up cookie, pages use the default user.
- Files touched: app/web/routes.py, tests/test_web_login_isolation.py (6 tests), this task file and log. `tests/test_user_isolation.py` unchanged.
- Test result: verify 6 passed; full suite exit 0; ruff check + format clean.
- Next round should know: all ready stories (F7-S1 to F7-S4) are done; F7-S5 to F7-S9 stay blocked on the owner's answers in their notes. The scheduler (app/main.py) still runs for the default user only (F7-S8).

## Round — 2026-11-12 — F7-S5 — done
- What I did: `login_required` (default false) in `AccountsConfig` and `config/accounts.yaml`. New `require_login` middleware in `app/main.py` (runs after `find_signed_in_user`): when on and nobody is signed in, pages and actions get a 303 to `/login?next=<path and query>` and run nothing; HTMX requests get 401 + `HX-Redirect: /login`. Open without login: `/login`, `/signup`, anything under `/login/` (for Google sign-in later) and `/static/`. The login form carries `next` in a hidden field; `safe_next()` in `app/web/account_routes.py` keeps only addresses inside the app (starts with one '/', no backslash, no control characters, at most 2000 characters), anything else goes to '/'.
- Files touched: app/config.py, config/accounts.yaml, app/main.py, app/web/account_routes.py, app/web/templates/login.html, tests/test_web_login_required.py (30 tests), this task file and log.
- Test result: verify 30 passed; full suite exit 0; ruff check + format clean. The background baseline run failed only because it overlapped my edit of `config/accounts.yaml` (see Lessons); the committed starting point had passed the full suite in the F7-S4 round.
- Next round should know: F7-S7 should add `/signup` (already open when login is required). F7-S12's Google routes must live under `/login/` to stay open.

## Round — 2026-11-12 — F7-S6 — done
- What I did: new `app/accounts_cli.py`: `uv run python -m app.accounts_cli owner --email X` renames the owner (all current data stays with it) and sets its password; `... password --email X` sets a new password for an existing account. Passwords are asked twice with getpass, at least `min_password_length` (12, new in `AccountsConfig` / `config/accounts.yaml`), never printed. Refusals (bad email, too short, not matching, unknown email, email taken by another account, database not set up) print a plain message to stderr, exit 1 and change nothing. Startup now uses `ensure_owner()` (first user by id) instead of finding the default user by email, so a renamed owner stays the owner and no second default user appears. The email check moved to `users.py` (`is_valid_email`), and the login form uses it.
- Files touched: app/accounts_cli.py (new), app/services/users.py, app/main.py, app/config.py, config/accounts.yaml, app/web/account_routes.py, tests/test_accounts_cli.py (22 tests), this task file and log.
- Test result: baseline exit 0 (run to completion first); verify 22 passed; full suite exit 0; ruff check + format clean.
- Next round should know: `ensure_default_user()` is kept (several existing tests use it). The README doesn't mention the command yet: the deploy session (F7-S15) should add it. In tests, a user created before the app starts becomes the owner.

## Round — 2026-11-12 — F7-S7 — done
- What I did: `invited` list in `AccountsConfig` / `config/accounts.yaml` (ships empty, with a note not to invite anyone until the market data question is settled), cleaned to lower case, checked as emails (a bad one stops the app with a message naming the file). New `/signup` page (`signup.html`): email, password, password again; invited emails only (403 "not on the invite list"); an email with an account already gets 409 with a "Log in instead" link; too short (< `min_password_length`), too long (> 1024), not matching, empty or malformed email: 400 with a clear message, nothing created. Success creates the user (password hashed), starts a session and goes to the safe `next`. A double submit hitting the unique email is caught. The login page links to sign-up. The login/sign-up redirect code is now one helper, `_signed_in_redirect`.
- Files touched: app/config.py, config/accounts.yaml, app/services/users.py, app/web/account_routes.py, app/web/templates/login.html, app/web/templates/signup.html (new), tests/test_web_signup.py (20 tests), this task file and log.
- Test result: baseline exit 0; verify 20 passed; full suite exit 0; ruff check + format clean.
- Next round should know: telling a stranger "not on the invite list" or "already has an account" reveals whether an email is invited or has an account; accepted for an invite-only beta (clear messages matter more), worth a line in the security review.

## Round — 2026-11-12 — F7-S8 — done
- What I did: new `login_failures` table (`LoginFailure`: email in lower case, failed_at; no user_id because unknown emails are counted too; migration `a3c9e7f1b2d4`). `login_locked_until()`, `record_failed_login()`, `clear_failed_logins()` in `app/services/users.py`. Locked when the latest wrong try made `max_failed_logins` (10) within `failed_login_window_minutes` (15), until `lockout_minutes` (15) after it. Refused attempts aren't recorded, so hammering doesn't extend the lock. `/login` checks the lock before the password: 429 with "Too many wrong passwords for this email. Try again in N minutes, or use Sign in with Google." (same text for unknown emails). A wrong password is recorded (old rows past window + lockout deleted at the same time); a successful login clears the email's count. Numbers in `AccountsConfig` / `config/accounts.yaml`.
- Files touched: app/db/models.py, app/db/migrations/versions/a3c9e7f1b2d4_login_failures.py, app/config.py, config/accounts.yaml, app/services/users.py, app/web/account_routes.py, tests/test_web_login_throttle.py (14 tests), this task file and log.
- Test result: baseline exit 0; verify 14 passed; full suite exit 0; ruff check + format clean.
- Next round should know: the lock is per email only (as the SPEC says): someone can lock a known email's password login for 15 minutes on purpose; Google sign-in (F7-S12) must not check the lock. There's no per-address (IP) limit; worth a line in the security review.

## Round — 2026-11-12 — F7-S9 — done
- What I did: `reports_per_day` (2) in `AccountsConfig` / `config/accounts.yaml`. `reports_left_today()` in `app/web/report_routes.py` counts the user's runs started since midnight US Eastern (None for the owner, `app.state.user_id`). "Generate now" (ticker or watchlist) is refused with 429 and "You have used all 2 reports for today..." once none are left; being sent to an already running report isn't counted. The Reports page shows "N of 2 reports left today." for other users only.
- Files touched: app/config.py, config/accounts.yaml, app/web/report_routes.py, app/web/templates/reports.html, tests/test_web_report_limit.py (11 tests), this task file and log.
- Test result: baseline exit 0; verify 11 passed; full suite exit 0; ruff check + format clean.
- Next round should know: tests freeze both `app.web.report_routes.utc_now` and `app.services.reports.utc_now` (run start times come from `start_run`). The watchlist page's own "Generate" buttons don't show the count (only the Reports page does), but they're limited the same way.

## Round — 2026-11-12 — F7-S10 — done
- What I did: no scheduler code change was needed: `app/main.py` already starts the weekday job with the owner's id (`app.state.user_id`, now from `ensure_owner`). New tests start the app with the schedule on, with three users who all have watchlists, and run the job exactly as the scheduler would (`job.func(*job.args)`): only the owner's watchlist is reported on, stored under the owner. Other users still generate by hand. One small page fix: the Reports page told every user "Automatic reports: on"; users other than the owner now see "Automatic morning reports are only for the owner for now." instead (`reports.html`, keyed on `reports_left`, which is None only for the owner).
- Files touched: app/web/templates/reports.html, tests/test_scheduler_users.py (4 tests), this task file and log. `tests/test_scheduler.py` unchanged.
- Test result: baseline exit 0; verify 4 passed; full suite exit 0; ruff check + format clean.
- Next round should know: next is F7-S11 (Google sign-in adapter), the first story that adds an outside service; keep it in `app/providers/` with `httpx.MockTransport` in tests.

## Round — 2026-11-12 — F7-S11 — done
- What I did: new `app/providers/google_auth.py`, built with httpx only (no new dependency). `start()` builds Google's sign-in address (client id, redirect address, scope "openid email", random state and nonce, PKCE S256 challenge, prompt=select_account). `finish()` POSTs the code, client secret and code verifier to Google's token endpoint over HTTPS, reads the ID token's claims (signature not checked on purpose, DECISIONS 2026-11-12), then checks issuer, audience (string or list), expiry (with the passed clock), nonce (constant-time compare), `email_verified` (true or "true") and a usable email; returns it in lower case. Every failure (timeouts, Google errors, bad JSON or tokens) becomes a `GoogleAuthError` with a plain message that never includes the secret or the code. `is_set_up` is false unless both keys are set and aren't the `.env.example` placeholders. New settings `google_client_id` / `google_client_secret` (empty by default); placeholders added to `.env.example`.
- Files touched: app/providers/google_auth.py (new), app/config.py, .env.example, tests/test_google_auth.py (36 tests), this task file and log.
- Test result: baseline exit 0; verify 36 passed; full suite exit 0; ruff check + format clean.
- Next round should know: the adapter isn't wired into the app yet; F7-S12 passes a `GoogleSignIn` into `create_app` (fake in tests) and adds the routes under `/login/google` (open while login is required).

## Round — 2026-11-12 — F7-S12 — done
- What I did: `GET /login/google` starts sign-in (redirect address built from the request, so it follows HTTPS behind a proxy) and keeps state, nonce, code verifier, the safe `next` and an expiry in a cookie `google_signin` (HttpOnly, SameSite=Lax, path `/login/google`, 10 minutes, Secure over HTTPS), HMAC-signed with a random key made at app start (a restart just means signing in again). `GET /login/google/callback` refuses a missing, changed or expired cookie or a state that doesn't match (400, Google not asked), explains a cancel at Google, shows adapter errors on the login page, then signs in the user with that email (any case), or creates an account without a password for an invited email, or refuses with 403 "not on the invite list". The pending cookie is always cleared. Password lockouts don't apply. The "Sign in with Google" / "Sign up with Google" buttons show only when Google is set up. `create_app(google=...)`; the real one is built from `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.
- Files touched: app/web/account_routes.py, app/main.py, app/web/templates/login.html, app/web/templates/signup.html, app/web/static/style.css, tests/test_web_google_login.py (23 tests), this task file and log.
- Test result: baseline exit 0; verify 23 passed; full suite exit 0; ruff check + format clean.
- Next round should know: when deploying, the Google console needs the redirect address `https://<host>/login/google/callback`, and the app must see HTTPS (F7-S14's proxy headers) or Google will reject the http address. Jinja's `urlencode` leaves '/' as is.

## Round — 2026-11-12 — F7-S13 — done
- What I did: new `config/hosting.yaml` (`HostingConfig.allowed_hosts`, default localhost, 127.0.0.1, [::1]; names checked: no scheme, no port, lower-cased) and `request_host_name()` in `app/config.py`. New `refuse_other_host_names` middleware in `app/main.py`, run before everything except the security headers: a request whose Host header (port ignored, case ignored) isn't on the list gets 400 "Refused: this app doesn't answer to that address... config/hosting.yaml". Covers pages, actions and static files; missing or malformed Host headers are refused too. `create_app(hosting=...)`.
- Shared test plumbing: `tests/conftest.py` got one autouse fixture so test apps also allow "testserver" (the story allowed this); no existing test changed.
- Files touched: app/config.py, config/hosting.yaml (new), app/main.py, tests/conftest.py, tests/test_web_allowed_hosts.py (10 tests), this task file and log.
- Test result: baseline exit 0; verify 10 passed; full suite exit 0; ruff check + format clean.
- Next round should know: this is the same fix as IMPROVE IMP-7; an interactive session should mark IMP-7 as covered by F7-S13 in `ralph/tasks/IMPROVE-2026-11-08.json` (loops don't edit other task files). When hosted, the owner adds the public name to `config/hosting.yaml`.

## Round — 2026-11-13 — F7-S14 — done
- What I did: `Dockerfile`: pinned `python:3.12.11-slim-bookworm` base, pinned uv (`ghcr.io/astral-sh/uv:0.11.32`, same as used here), `uv sync --locked --no-dev`, copies only `pyproject.toml`, `uv.lock`, `.python-version`, `alembic.ini`, `app/`, `config/`; a non-admin user (uid 10001) that can only write `/app/data`; `DATABASE_URL=sqlite:////app/data/app.db` with `/app/data` as a volume; start = `alembic upgrade head` then uvicorn on 0.0.0.0:8000 with `--proxy-headers --forwarded-allow-ips $FORWARDED_ALLOW_IPS` (default 127.0.0.1). `.dockerignore` keeps out `.env*`, `data/`, `.git/`, `tests/`, `ralph/logs/`, `.venv/`, caches. Tests read both files, and check with uvicorn's own ProxyHeadersMiddleware that `X-Forwarded-Proto: https` from the trusted proxy makes the login cookie Secure, while the same header from anyone else is ignored.
- Files touched: Dockerfile (new), .dockerignore (new), tests/test_container.py (13 tests), this task file and log.
- Test result: baseline exit 0; verify 13 passed; full suite exit 0; ruff check + format clean. A real `docker build` could not run here (the Docker program is installed but its background service isn't running), so the image itself has not been built yet.
- Next round should know: the first real build happens in the deploy session (F7-S15): check the base image tag still exists (pick the newest 3.12 patch), that the build works, and that `config/` edits on the server are kept (mount `config/` or rebuild after edits). With Cloudflare's tunnel on the same server, FORWARDED_ALLOW_IPS=127.0.0.1 is right; if the tunnel runs in another container, set it to that container's address.

## Round — 2026-11-13 — F7-S16 — done
- What I did: `HostingConfig.public_names()` in `app/config.py` (names with a dot, except 127.0.0.1; so localhost, [::1], "testserver" and single-word computer names aren't public, but a LAN address like 192.168.1.20 is). `create_app` in `app/main.py` raises a RuntimeError naming the address, `login_required` and `config/accounts.yaml` when the names read from `config/hosting.yaml` include a public one while login is off. Because `app = create_app()` runs when uvicorn loads the app, the app won't start at all.
- Design note: the check runs on the names read from the file (where the real app always gets them), not on a `hosting=` passed in by tests. My first version checked both and broke the existing `test_f7_3_hosted_name_can_be_added_in_config` (it adds a public name with login off to test the host rule). Per the test-integrity rule I left that test unchanged and narrowed the check instead; the new tests go through the file-reading path (monkeypatching `app.main.load_hosting_config`).
- Files touched: app/config.py, app/main.py, tests/test_web_online_failsafe.py (9 tests), this task file and log.
- Test result: baseline exit 0; verify 9 passed; full suite exit 0; ruff check + format clean.
- Next round should know: next is F7-S17 (limit parallel password checks).

## Round — 2026-11-13 — F7-S17 — done
- What I did: every scrypt call in `app/services/users.py` (so `hash_password` and `check_password`, used by login, sign-up, the dummy check and the account command) now takes one of a few "slots" (`threading.BoundedSemaphore`); extra checks wait their turn, none are refused. The slot count is `max_parallel_password_checks` (default 2, 1 to 16) in `AccountsConfig` / `config/accounts.yaml`, applied by `create_app` through `set_max_parallel_password_checks()`. The real scrypt call moved into `_derive()` so tests can swap in a slow counting fake.
- Files touched: app/services/users.py, app/config.py, config/accounts.yaml, app/main.py, tests/test_accounts_password_limit.py (8 tests), this task file and log.
- Test result: baseline exit 0; verify 8 passed; full suite exit 0; ruff check + format clean.
- Next round should know: the limit is per running app (one process), which matches the single-server plan. Waiting requests still each hold a web worker thread; the Cloudflare rate-limit rule at deploy (F7-S15) is the other half of H2.

## Round — 2026-11-13 — F7-S18 — done
- What I did: new `end_all_sessions(session, user_id) -> count` in `app/services/users.py`. Both account commands (`owner`, `password`) call it after saving the new password, and their message ends with "N logged-in devices were signed out." Refused commands change nothing, so nobody is signed out.
- Files touched: app/services/users.py, app/accounts_cli.py, tests/test_accounts_cli_signout.py (7 tests), this task file and log. `tests/test_accounts_cli.py` unchanged and passing.
- Test result: baseline exit 0; verify 7 passed; full suite exit 0; ruff check + format clean.
- Next round should know: next is F7-S19 (refuse redirects to non-HTTPS addresses in `app/sources/http.py`, reports zone).

## Round — 2026-11-13 — F7-S19 — done
- What I did: `HttpFetcher` in `app/sources/http.py` now has an httpx "request" event hook (`_refuse_non_https`), which httpx runs for every request it sends, redirects included: a redirect to a non-HTTPS address raises SourceError("Refused a redirect to a non-HTTPS address.") before it is requested. `max_redirects=5` (`MAX_REDIRECTS`); a longer chain becomes a SourceError through the existing httpx error handling.
- Files touched: app/sources/http.py, tests/test_sources_http_redirects.py (5 tests), this task file and log. Existing source tests unchanged and passing.
- Test result: baseline exit 0; verify 5 passed; full suite exit 0; ruff check + format clean.
- Next round should know: only `HttpFetcher` (news, blogs, YouTube, SEC, Damodaran, ICI) follows redirects; FRED, X and Google use fixed HTTPS addresses with their own clients. Next is F7-S20 (no API description pages).

## Round — 2026-11-13 — F7-S20 — done
- What I did: `FastAPI(..., docs_url=None, redoc_url=None, openapi_url=None)` in `app/main.py`, so /docs, /redoc, /openapi.json (and the docs' OAuth helper page) answer 404.
- Files touched: app/main.py, tests/test_web_no_api_docs.py (5 tests), this task file and log.
- Test result: baseline exit 0; verify 5 passed (4 failed before the change, as expected); full suite exit 0; ruff check + format clean.
- Next round should know: last ready story is F7-S21 (soupsieve upgrade; changes uv.lock, a shared file). If beautifulsoup4 or yfinance cap soupsieve below 4, block it.

## Round — 2026-11-13 — F7-S21 — done
- What I did: `uv lock --upgrade-package soupsieve` moved soupsieve 3.0.4 -> 4.1 (no other package changed; beautifulsoup4 and yfinance allow it). **Dependency upgrade, no new package; `pyproject.toml` unchanged.** New test checks the installed version is at least 4.0.0. `pip-audit` (run with `uvx pip-audit` on `uv export --no-dev`) now finds no known vulnerabilities.
- Files touched: uv.lock, tests/test_dependency_versions.py (1 test), this task file and log.
- Test result: baseline exit 0; verify 1 passed; full suite exit 0 (Yahoo parsing tests unchanged); ruff check + format clean.
- Next round should know: all ready stories are done. Only F7-S15 (deploy, interactive only) is left, blocked by design.
