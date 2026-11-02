import pytest
import requests
from werkzeug.security import generate_password_hash
from app.classes.calendar import CalendarBuilder, summarize_fetch_error


def _http_error(code):
    r = requests.Response()
    r.status_code = code
    return requests.exceptions.HTTPError(response=r)


@pytest.mark.parametrize("exc,needle", [
    (requests.exceptions.Timeout(), "Timed out"),
    (requests.exceptions.SSLError(), "SSL"),
    (requests.exceptions.ConnectionError(), "connect"),
    (_http_error(404), "HTTP 404 (not found"),
    (_http_error(403), "access denied"),
    (_http_error(500), "HTTP 500."),
    (ValueError("Content line could not be parsed"), "valid iCalendar"),
    (RuntimeError("boom"), "RuntimeError: boom"),
])
def test_summarize_fetch_error(exc, needle):
    assert needle in summarize_fetch_error(exc)


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("APP_VERSION", "1000")
    monkeypatch.setenv("SECRET_KEY", "x")
    from app.server import create_app
    a = create_app()
    uid = a.db.create_user("u", "u", None, generate_password_hash("pw12345678"), "U", is_admin=1)
    cid = a.db.create_calendar(uid, "work", "Work")
    sid = a.db.create_source(uid, "https://example.com/a.ics", "Example")
    a.db.link_source(cid, sid)
    return a


def build(app):
    b = CalendarBuilder()
    b.db = app.db
    import datetime
    now = datetime.datetime.now()
    b.build(user_slug="u", calendar_slug="work", view_slug="default",
            expansion_start=now, expansion_end=now + datetime.timedelta(days=5))


def test_http_404_is_recorded_as_a_specific_error(app, monkeypatch):
    class R:
        status_code = 404
        text = "<html>nope</html>"
        def raise_for_status(self):
            raise _http_error(404)
    monkeypatch.setattr("app.classes.calendar.requests.get", lambda *a, **k: R())
    build(app)
    s = app.db._fetchone("SELECT last_fetch_ok, last_fetch_error, last_fetch_at FROM sources")
    assert s["last_fetch_ok"] == 0 and "HTTP 404" in s["last_fetch_error"] and s["last_fetch_at"]


def test_success_clears_previous_error(app, monkeypatch):
    app.db.update_source_fetch_status(1, "2026-01-01T00:00:00", 0, "old")
    class R:
        text = "BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n"
        def raise_for_status(self): pass
    monkeypatch.setattr("app.classes.calendar.requests.get", lambda *a, **k: R())
    build(app)
    s = app.db._fetchone("SELECT last_fetch_ok, last_fetch_error FROM sources")
    assert s["last_fetch_ok"] == 1 and s["last_fetch_error"] is None


def test_ui_shows_status_message_age_and_dashboard_detail(app):
    app.db.update_source_fetch_status(1, "2026-01-01T00:00:00", 0, "The server returned HTTP 404 (not found, check the URL).")
    c = app.test_client()
    c.post("/login", data={"username": "u", "password": "pw12345678"})
    detail = c.get("/calendars/work").get_data(as_text=True)
    assert "✕ Failed" in detail and "HTTP 404" in detail and "1 source failing" in detail and "ago" in detail
    dash = c.get("/").get_data(as_text=True)
    assert "Example: The server returned HTTP 404" in dash        # tooltip names the source and reason
