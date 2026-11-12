# Progress log — F7 Accounts & hosting

> Append-only. Each loop round adds one entry at the bottom. Never rewrite or delete old entries.

## Lessons
<!-- Reusable learnings for future rounds: gotchas, patterns, commands that work. After the feature is merged, an interactive session promotes the useful ones into CLAUDE.md. -->
- The full test suite (`uv run pytest -q`) prints no summary line here; check the exit code (`echo $?`) instead. It takes about 3-4 minutes, so give it a long timeout.
- SQLite column changes in migrations need `op.batch_alter_table` (see `e1f7a2c3b4d5_user_password_hash.py`). Migration up/down/up test pattern: `tests/test_accounts_passwords.py::test_f7_2_password_migration_up_down_up_keeps_users`.
- Password helpers: `hash_password(password, cost)` / `check_password(stored, password)` in `app/services/users.py`; cost comes from `load_accounts_config(settings.accounts_config_file).scrypt`. Tests use a cheap `ScryptCost(n=2**4, r=1, p=1)` for speed.

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
