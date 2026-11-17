import sqlite3
import pytest
from werkzeug.security import generate_password_hash


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


def src(app):
    return app.db._fetchone("SELECT last_fetch_ok, last_success_at, failing_since, last_fetch_at FROM sources")


class TestTransitions:
    def test_first_failure_starts_the_clock_and_repeats_keep_it(self, app):
        app.db.update_source_fetch_status(1, "2026-01-01T10:00:00", 0, "boom")
        app.db.update_source_fetch_status(1, "2026-01-01T11:00:00", 0, "boom again")
        app.db.update_source_fetch_status(1, "2026-01-01T12:00:00", 0, "still")
        s = src(app)
        assert s["failing_since"] == "2026-01-01T10:00:00"            # first failure of the run, not the latest
        assert s["last_fetch_at"] == "2026-01-01T12:00:00" and s["last_success_at"] is None

    def test_success_clears_the_run_and_records_when_it_worked(self, app):
        app.db.update_source_fetch_status(1, "2026-01-01T10:00:00", 0, "boom")
        app.db.update_source_fetch_status(1, "2026-01-01T13:00:00", 1, None)
        s = src(app)
        assert s["failing_since"] is None and s["last_success_at"] == "2026-01-01T13:00:00" and s["last_fetch_ok"] == 1

    def test_a_new_failure_after_recovery_starts_a_new_run_and_keeps_last_success(self, app):
        app.db.update_source_fetch_status(1, "2026-01-01T10:00:00", 0, "x")
        app.db.update_source_fetch_status(1, "2026-01-01T11:00:00", 1, None)
        app.db.update_source_fetch_status(1, "2026-01-02T09:00:00", 0, "y")
        s = src(app)
        assert s["failing_since"] == "2026-01-02T09:00:00" and s["last_success_at"] == "2026-01-01T11:00:00"


class TestUI:
    def login(self, app):
        c = app.test_client()
        c.post("/login", data={"username": "u", "password": "pw12345678"})
        return c

    def test_failing_source_shows_since_and_last_worked(self, app):
        app.db.update_source_fetch_status(1, "2026-01-01T10:00:00", 1, None)
        app.db.update_source_fetch_status(1, "2026-01-02T10:00:00", 0, "The server returned HTTP 404.")
        html = self.login(app).get("/calendars/work?tab=sources").get_data(as_text=True)
        assert "Failing since" in html and "last worked" in html and "HTTP 404" in html

    def test_never_worked_wording(self, app):
        app.db.update_source_fetch_status(1, "2026-01-02T10:00:00", 0, "nope")
        html = self.login(app).get("/calendars/work?tab=sources").get_data(as_text=True)
        assert "never worked" in html

    def test_dashboard_tooltip_includes_failing_since(self, app):
        app.db.update_source_fetch_status(1, "2026-01-02T10:00:00", 0, "nope")
        html = self.login(app).get("/").get_data(as_text=True)
        assert "failing since 2026-01-02 10:00 UTC" in html


def test_migration_9_upgrades_a_v8_database_and_backfills(tmp_path, monkeypatch):
    import app.functions.db.db_migrations as m
    from app.classes.db import CalendarDB
    path = str(tmp_path / "old.db")
    monkeypatch.setenv("DB_PATH", path)
    monkeypatch.setenv("APP_VERSION", "0")
    conn = sqlite3.connect(path)
    for v in range(0, 9):
        conn.executescript(m.migrations[v]["sql"])
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('db_version', 8)")
    conn.execute("INSERT INTO users (slug, username, password, display_name) VALUES ('a','a','x','A')")
    for name, ok, at in (("good", 1, "2026-01-01T00:00:00"), ("bad", 0, "2026-01-02T00:00:00"), ("never", None, None)):
        conn.execute("INSERT INTO sources (user_id, url, name, last_fetch_ok, last_fetch_at) VALUES (1, ?, ?, ?, ?)",
                     ("https://example.com/%s.ics" % name, name, ok, at))
    conn.commit()
    conn.close()
    d = CalendarDB()
    d.create_and_initialize()
    rows = {r["name"]: r for r in d._fetchall("SELECT name, last_success_at, failing_since FROM sources")}
    assert rows["good"]["last_success_at"] == "2026-01-01T00:00:00" and rows["good"]["failing_since"] is None
    assert rows["bad"]["failing_since"] == "2026-01-02T00:00:00" and rows["bad"]["last_success_at"] is None
    assert rows["never"]["last_success_at"] is None and rows["never"]["failing_since"] is None
