"""F7.10 (story F7-S16, security review H1): the app refuses to start when it's set up for a
public address with login switched off, so visitors can never be treated as the owner."""

import pytest
from fastapi.testclient import TestClient

from app.config import AccountsConfig, HostingConfig
from app.main import create_app

LOCAL = ["localhost", "127.0.0.1", "[::1]"]


@pytest.fixture
def start(
    engine,
    fake_market_data,
    app_config,
    report_services,
    fake_fred,
    fomc,
    macro_setup,
    monkeypatch,
):
    import app.main

    def run(allowed_hosts, login_required):
        # As if config/hosting.yaml listed these names (the real app reads that file).
        monkeypatch.setattr(
            app.main,
            "load_hosting_config",
            lambda path: HostingConfig(allowed_hosts=allowed_hosts),
        )
        application = create_app(
            engine=engine,
            market_data=fake_market_data,
            app_config=app_config,
            report_services=report_services,
            fred=fake_fred,
            fomc=fomc,
            macro=macro_setup,
            accounts=AccountsConfig(login_required=login_required),
        )
        with TestClient(application, base_url="http://localhost:8000") as client:
            return client.get("/login").status_code

    return run


def test_f7_10_local_names_with_login_off_start_as_before(start):
    assert start(LOCAL, login_required=False) == 200


def test_f7_10_public_name_with_login_off_refuses_to_start(start):
    with pytest.raises(RuntimeError, match=r"login_required.*config/accounts\.yaml"):
        start([*LOCAL, "dashboard.example.com"], login_required=False)


def test_f7_10_public_name_with_login_on_starts(start):
    assert start([*LOCAL, "dashboard.example.com"], login_required=True) == 200


def test_f7_10_the_message_names_the_public_address(start):
    with pytest.raises(RuntimeError, match="dashboard.example.com"):
        start(["localhost", "dashboard.example.com"], login_required=False)


@pytest.mark.parametrize(
    "names, public",
    [
        (["localhost", "127.0.0.1", "[::1]"], []),
        (["testserver", "my-laptop"], []),
        (["dashboard.example.com"], ["dashboard.example.com"]),
        (["192.168.1.20"], ["192.168.1.20"]),
        (["localhost", "a.b", "127.0.0.1"], ["a.b"]),
    ],
)
def test_f7_10_which_names_count_as_public(names, public):
    assert HostingConfig(allowed_hosts=names).public_names() == public
