"""Pages link to static files with a version code, so browsers don't keep using an old copy.

The bug this guards against: after updating to Phase 2, the browser kept Phase 1's saved
style.css (same address, no instructions on how long to keep it), so the new pages showed
with the old look.
"""

import re

from app.web.assets import STATIC_DIR, asset_version


def test_version_changes_when_content_changes(tmp_path):
    css = tmp_path / "style.css"
    css.write_text("main { max-width: 640px; }")
    old = asset_version("style.css", static_dir=tmp_path)
    assert asset_version("style.css", static_dir=tmp_path) == old  # same content, same code
    css.write_text("main { max-width: 1180px; }")
    assert asset_version("style.css", static_dir=tmp_path) != old


def test_pages_link_to_the_current_version_of_each_file(client):
    html = client.get("/").text
    for name in ("style.css", "htmx.min.js"):
        match = re.search(rf'/static/{re.escape(name)}\?v=([0-9a-f]{{12}})"', html)
        assert match, f"{name} is linked without a version code"
        assert match.group(1) == asset_version(name, static_dir=STATIC_DIR)


def test_versioned_address_still_serves_the_file(client):
    response = client.get(f"/static/style.css?v={asset_version('style.css')}")
    assert response.status_code == 200
    assert "max-width: 1180px" in response.text
