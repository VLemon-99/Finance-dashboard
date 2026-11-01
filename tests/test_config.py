"""config/settings.yaml: limits come from the file and can be raised without code changes (F1.3)."""

import pytest

from app.config import PROJECT_ROOT, ConfigFileError, load_app_config


def test_shipped_settings_file_has_the_spec_defaults():
    cfg = load_app_config(PROJECT_ROOT / "config" / "settings.yaml")
    assert cfg.watchlists.max_watchlists == 30
    assert cfg.watchlists.max_items_per_list == 25
    assert cfg.market_data.refresh_minutes == 15


def test_limits_can_be_raised_in_the_file(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text("watchlists:\n  max_watchlists: 50\n  max_items_per_list: 40\n")
    cfg = load_app_config(path)
    assert cfg.watchlists.max_watchlists == 50
    assert cfg.watchlists.max_items_per_list == 40
    assert cfg.market_data.refresh_minutes == 15  # left out -> default


def test_misspelled_setting_is_reported(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text("watchlists:\n  max_watchlist: 50\n")
    with pytest.raises(ConfigFileError, match="max_watchlist"):
        load_app_config(path)


@pytest.mark.parametrize("text", ["watchlists:\n  max_watchlists: 0\n", "watchlists: [1, 2\n"])
def test_bad_values_are_reported(tmp_path, text):
    path = tmp_path / "settings.yaml"
    path.write_text(text)
    with pytest.raises(ConfigFileError, match="settings.yaml"):
        load_app_config(path)


def test_missing_file_is_reported(tmp_path):
    with pytest.raises(ConfigFileError, match="not found"):
        load_app_config(tmp_path / "nope.yaml")
