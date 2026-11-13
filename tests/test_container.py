"""F7.3 (story F7-S14): the app is packaged as a container any host can run.

Docker isn't available in the test run, so these tests read the Dockerfile and
.dockerignore and check the rules that matter (no secrets in the image, not run as root,
data kept outside the image, HTTPS seen through the proxy)."""

import re
import shlex

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.config import PROJECT_ROOT, AccountsConfig, ScryptCost
from app.db.models import User
from app.main import create_app
from app.services.users import hash_password

DOCKERFILE = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
DOCKERIGNORE = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8")
FAST = ScryptCost(n=2**4, r=1, p=1)
PASSWORD = "correct horse battery"  # noqa: S105 (fake test password)


def instructions():
    """(INSTRUCTION, arguments) pairs, with comments dropped and continued lines joined."""
    text = re.sub(r"\\\n\s*", " ", DOCKERFILE)
    pairs = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        word, _, rest = line.partition(" ")
        pairs.append((word.upper(), rest.strip()))
    return pairs


def args_of(name):
    return [rest for word, rest in instructions() if word == name]


def start_command():
    (cmd,) = args_of("CMD")
    return " ".join(__import__("json").loads(cmd))


def test_f7_3_base_image_is_a_pinned_python_312_slim():
    first_from = args_of("FROM")[-1]  # the image the app runs in
    assert re.fullmatch(
        r"python:3\.12\.\d+-slim(-\w+)?(@sha256:[0-9a-f]{64})?( AS \w+)?", first_from
    )


def test_f7_3_uv_is_pinned_and_installs_from_the_lock_file_without_dev_tools():
    copies = " ".join(args_of("COPY"))
    assert re.search(r"ghcr\.io/astral-sh/uv:\d+\.\d+\.\d+", copies)
    runs = " ".join(args_of("RUN"))
    assert "uv sync" in runs
    assert "--locked" in runs
    assert "--no-dev" in runs


def test_f7_3_runs_as_a_non_root_user():
    users = args_of("USER")
    assert users, "no USER instruction"
    assert users[-1] not in ("root", "0")
    # The last USER comes after every RUN, so nothing else runs as root later.
    words = [word for word, _ in instructions()]
    last_user = max(i for i, w in enumerate(words) if w == "USER")
    assert all(w != "RUN" for w in words[last_user:])


def test_f7_3_start_runs_migrations_then_uvicorn_behind_the_proxy():
    command = start_command()
    assert command.index("alembic upgrade head") < command.index("uvicorn app.main:app")
    parts = shlex.split(command.split("uvicorn", 1)[1])
    for flag, value in (("--host", "0.0.0.0"), ("--port", "8000")):  # noqa: S104
        assert parts[parts.index(flag) + 1] == value
    assert "--proxy-headers" in parts
    assert parts[parts.index("--forwarded-allow-ips") + 1] == "$FORWARDED_ALLOW_IPS"
    envs = " ".join(args_of("ENV"))
    assert "FORWARDED_ALLOW_IPS=127.0.0.1" in envs


def test_f7_3_database_lives_in_a_volume():
    assert "/app/data" in " ".join(args_of("VOLUME"))
    assert "DATABASE_URL=sqlite:////app/data/app.db" in " ".join(args_of("ENV"))


@pytest.mark.parametrize("path", [".env", "data", ".git", "tests", "ralph/logs"])
def test_f7_3_dockerignore_keeps_secrets_and_data_out(path):
    entries = {
        line.strip().rstrip("/")
        for line in DOCKERIGNORE.splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert path in entries


def test_f7_3_dockerfile_never_copies_env_or_sets_secrets():
    for source in args_of("COPY") + args_of("ADD"):
        assert ".env" not in source
        assert source.split()[0] not in (".", "./")  # no "copy everything"
    envs = " ".join(args_of("ENV") + args_of("ARG")).upper()
    for secret in ("KEY", "SECRET", "TOKEN", "PASSWORD"):
        assert secret not in envs


# --- HTTPS seen through the proxy ---------------------------------------------------


@pytest.fixture
def behind_proxy(
    engine, fake_market_data, app_config, report_services, fake_fred, fomc, macro_setup
):
    def make(trusted):
        app = create_app(
            engine=engine,
            market_data=fake_market_data,
            app_config=app_config,
            report_services=report_services,
            fred=fake_fred,
            fomc=fomc,
            macro=macro_setup,
            accounts=AccountsConfig(scrypt=FAST),
        )
        # What uvicorn --proxy-headers --forwarded-allow-ips does. Test requests come from
        # the address "testclient".
        return app, ProxyHeadersMiddleware(app, trusted_hosts=trusted)

    return make


def _login(client):
    return client.post(
        "/login",
        data={"email": "alice@example.com", "password": PASSWORD},
        headers={"x-forwarded-proto": "https", "x-forwarded-for": "203.0.113.5"},
        follow_redirects=False,
    )


def _add_alice(engine):
    with Session(engine) as s:
        s.add(User(email="alice@example.com", password_hash=hash_password(PASSWORD, FAST)))
        s.commit()


def test_f7_3_https_from_the_trusted_proxy_makes_the_cookie_secure(behind_proxy, engine):
    app, wrapped = behind_proxy("testclient")
    with TestClient(app):  # startup
        _add_alice(engine)
        response = _login(TestClient(wrapped))
    assert response.status_code == 303
    assert "Secure" in response.headers["set-cookie"]


def test_f7_3_https_header_from_anyone_else_is_ignored(behind_proxy, engine):
    app, wrapped = behind_proxy("127.0.0.1")
    with TestClient(app):
        _add_alice(engine)
        response = _login(TestClient(wrapped))
    assert response.status_code == 303
    assert "Secure" not in response.headers["set-cookie"]
