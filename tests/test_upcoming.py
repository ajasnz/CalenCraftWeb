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
    app.db.create_calendar(uid, "work", "Work")
    c = app.test_client()
    c.post("/login", data={"username": "u", "password": "pw12345678"})
    return c


def test_upcoming_requires_login(client):
    client.get("/logout")
    r = client.get("/calendars/work/upcoming.json")
    assert r.status_code == 302


def test_upcoming_empty_calendar_returns_empty_list(client):
    r = client.get("/calendars/work/upcoming.json")
    assert r.status_code == 200
    assert r.get_json()["events"] == []


def test_upcoming_unknown_view_and_calendar_404(client):
    assert client.get("/calendars/work/upcoming.json?view=nope").status_code == 404
    assert client.get("/calendars/nope/upcoming.json").status_code == 404


def test_upcoming_days_is_clamped_and_tolerant(client):
    assert client.get("/calendars/work/upcoming.json?days=9999").get_json()["days"] == 90
    assert client.get("/calendars/work/upcoming.json?days=abc").get_json()["days"] == 30


def test_detail_page_has_preview_tab(client):
    html = client.get("/calendars/work").get_data(as_text=True)
    assert 'id="tab-preview"' in html and "upcoming.json" in html
