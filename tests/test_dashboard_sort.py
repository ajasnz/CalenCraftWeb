import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("APP_VERSION", "1000")
    monkeypatch.setenv("SECRET_KEY", "x")
    from app.server import create_app
    app = create_app()
    uid = app.db.create_user("u", "u", None, generate_password_hash("pw12345678"), "U", is_admin=1)
    a = app.db.create_calendar(uid, "alpha", "Alpha")
    app.db.create_calendar(uid, "beta", "Beta")
    app.db.record_feed_hit(a, "2026-01-01T00:00:00")
    c = app.test_client()
    c.post("/login", data={"username": "u", "password": "pw12345678"})
    return c


def test_dashboard_has_sort_control_and_sortable_data(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="cal-sort"' in html
    assert 'data-hits="1"' in html and 'data-hits="0"' in html
    assert 'data-last="2026-01-01T00:00:00"' in html
