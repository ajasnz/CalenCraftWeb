import json
import os
import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("APP_VERSION", "1000")
    monkeypatch.setenv("SECRET_KEY", "x")
    from app.server import create_app
    app = create_app()
    app.db.create_user("u", "u", None, "h", "U", is_admin=1)  # past the first-run gate
    return app.test_client()


def test_manifest_is_valid_and_installable(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200 and r.mimetype == "application/manifest+json"
    m = json.loads(r.data)
    assert m["display"] == "standalone" and m["start_url"] == "/"
    sizes = {i["sizes"] for i in m["icons"]}
    assert {"192x192", "512x512"} <= sizes
    for icon in m["icons"]:
        assert client.get(icon["src"]).status_code == 200


def test_service_worker_served_at_root_and_never_caches_feeds(client):
    r = client.get("/sw.js")
    assert r.status_code == 200 and r.mimetype == "text/javascript"
    assert r.headers["Cache-Control"] == "no-cache"
    body = r.get_data(as_text=True)
    assert "/offline" in body and "feed|view|embed" in body


def test_offline_page_and_head_tags(client):
    assert client.get("/offline").status_code == 200
    html = client.get("/login").get_data(as_text=True)
    assert 'rel="manifest"' in html and 'name="theme-color"' in html and "serviceWorker" in html
