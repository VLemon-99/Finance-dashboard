"""F7.10 (story F7-S17, security review H2): at most a few password checks run at the same
time, so a flood of logins can't use up the server's memory (each check uses ~32 MB)."""

import hashlib
import threading
import time

import pytest

import app.services.users as users
from app.config import PROJECT_ROOT, AccountsConfig, ScryptCost, load_accounts_config
from app.services.users import (
    check_password,
    hash_password,
    set_max_parallel_password_checks,
)

FAST = ScryptCost(n=2**4, r=1, p=1)


class SlowDerive:
    """Stands in for scrypt: slow on purpose, and counts how many run at once."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = 0
        self.most = 0

    def __call__(self, password, salt, n, r, p):
        with self.lock:
            self.running += 1
            self.most = max(self.most, self.running)
        time.sleep(0.05)
        with self.lock:
            self.running -= 1
        return hashlib.sha256(password.encode() + salt).digest()  # 32 bytes, deterministic


@pytest.fixture
def slow(monkeypatch):
    fake = SlowDerive()
    monkeypatch.setattr(users, "_derive", fake)
    yield fake
    set_max_parallel_password_checks(AccountsConfig().max_parallel_password_checks)


def run_at_once(work, count=10):
    results = [None] * count
    start = threading.Barrier(count)

    def one(i):
        start.wait()
        results[i] = work(i)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results


def test_f7_10_never_more_than_two_checks_at_once(slow):
    set_max_parallel_password_checks(2)
    stored = [hash_password(f"password number {i}", FAST) for i in range(10)]
    slow.most = 0
    results = run_at_once(lambda i: check_password(stored[i], f"password number {i}"))
    assert results == [True] * 10
    assert slow.most == 2


def test_f7_10_hashing_new_passwords_is_limited_too(slow):
    set_max_parallel_password_checks(2)
    results = run_at_once(lambda i: hash_password(f"password number {i}", FAST))
    assert all(r.startswith("scrypt$") for r in results)
    assert slow.most == 2


def test_f7_10_wrong_passwords_still_fail_under_the_limit(slow):
    set_max_parallel_password_checks(2)
    stored = hash_password("the right password", FAST)
    results = run_at_once(lambda i: check_password(stored, f"wrong {i}"))
    assert results == [False] * 10


@pytest.mark.parametrize("limit", [1, 3])
def test_f7_10_changing_the_setting_changes_the_limit(slow, limit):
    set_max_parallel_password_checks(limit)
    run_at_once(lambda i: hash_password(f"password number {i}", FAST))
    assert slow.most == limit


def test_f7_10_default_is_2():
    assert AccountsConfig().max_parallel_password_checks == 2
    real = load_accounts_config(PROJECT_ROOT / "config" / "accounts.yaml")
    assert real.max_parallel_password_checks == 2


def test_f7_10_setting_is_checked(tmp_path):
    from app.config import ConfigFileError

    path = tmp_path / "accounts.yaml"
    for bad in ("max_parallel_password_checks: 0\n", "max_parallel_password_checks: 17\n"):
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(ConfigFileError):
            load_accounts_config(path)


def test_f7_10_the_app_applies_the_setting_at_start(
    engine, fake_market_data, app_config, report_services, fake_fred, fomc, macro_setup, slow
):
    from app.main import create_app

    create_app(
        engine=engine,
        market_data=fake_market_data,
        app_config=app_config,
        report_services=report_services,
        fred=fake_fred,
        fomc=fomc,
        macro=macro_setup,
        accounts=AccountsConfig(scrypt=FAST, max_parallel_password_checks=1),
    )
    run_at_once(lambda i: hash_password(f"password number {i}", FAST), count=4)
    assert slow.most == 1
