"""F7.3 (story F7-S13, same fix as IMPROVE IMP-7): the app only answers requests addressed
to its own host names, so a "DNS rebinding" website can't read it through its own name."""

import pytest
from fastapi.testclient import TestClient

from app.config import PROJECT_ROOT, ConfigFileError, HostingConfig, load_hosting_config
from app.main import create_app


@pytest.fixture
def make_web(engine, fake_market_data, app_config, report_services, fake_fred, fomc, macro_setup):
    clients = []

    def make(allowed_hosts=None):
        hosting = (
            HostingConfig() if allowed_hosts is None else HostingConfig(allowed_hosts=allowed_hosts)
        )
        app = create_app(
            engine=engine,
            market_data=fake_market_data,
            app_config=app_config,
            report_services=report_services,
            fred=fake_fred,
            fomc=fomc,
            macro=macro_setup,
            hosting=hosting,
        )
        client = TestClient(app, base_url="http://localhost:8000")
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def get(web, host, path="/"):
    return web.get(path, headers={"host": host}, follow_redirects=False)


def test_f7_3_other_host_names_are_refused(make_web):
    web = make_web()
    for host in ("evil.example", "evil.example:8000", "localhost.evil.example", "testserver"):
        response = get(web, host)
        assert response.status_code == 400, host
        assert "config/hosting.yaml" in response.text
        assert "Watchlists" not in response.text


def test_f7_3_local_names_work(make_web):
    web = make_web()
    for host in ("localhost:8000", "127.0.0.1:8000", "[::1]:8000", "localhost", "LOCALHOST:8000"):
        assert get(web, host).status_code == 200, host


def test_f7_3_static_files_follow_the_same_rule(make_web):
    web = make_web()
    assert get(web, "localhost:8000", "/static/style.css").status_code == 200
    assert get(web, "evil.example", "/static/style.css").status_code == 400


def test_f7_3_changes_are_refused_too(make_web):
    web = make_web()
    response = web.post(
        "/watchlists",
        data={"name": "X"},
        headers={"host": "evil.example"},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_f7_3_missing_or_strange_host_is_refused(make_web):
    web = make_web()
    for host in ("", "[::1", "a" * 300):
        assert get(web, host).status_code == 400, repr(host)


def test_f7_3_hosted_name_can_be_added_in_config(make_web):
    web = make_web(["localhost", "Dashboard.Example.com"])
    assert get(web, "dashboard.example.com").status_code == 200
    assert get(web, "DASHBOARD.example.com:443").status_code == 200
    assert get(web, "127.0.0.1:8000").status_code == 400  # only what's listed


def test_f7_3_security_headers_still_sent_on_refusal(make_web):
    web = make_web()
    response = get(web, "evil.example")
    assert response.headers["X-Frame-Options"] == "DENY"


# --- config/hosting.yaml ------------------------------------------------------------


def test_f7_3_defaults_are_the_local_names():
    assert HostingConfig().allowed_hosts == ["localhost", "127.0.0.1", "[::1]"]


def test_f7_3_real_hosting_config_has_the_local_names():
    loaded = load_hosting_config(PROJECT_ROOT / "config" / "hosting.yaml")
    assert loaded.allowed_hosts == ["localhost", "127.0.0.1", "[::1]"]


def test_f7_3_allowed_hosts_are_cleaned_and_checked(tmp_path):
    path = tmp_path / "hosting.yaml"
    path.write_text("allowed_hosts:\n  - ' Dash.Example.com '\n  - localhost\n", encoding="utf-8")
    assert load_hosting_config(path).allowed_hosts == ["dash.example.com", "localhost"]
    for bad in (
        "allowed_hosts: []\n",
        "allowed_hosts:\n  - 'has space.com'\n",
        "allowed_hosts:\n  - 'http://dash.example.com'\n",
        "allowed_hosts:\n  - 'dash.example.com:8000'\n",
        "typo: 1\n",
    ):
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(ConfigFileError, match="hosting.yaml"):
            load_hosting_config(path)
