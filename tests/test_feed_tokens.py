import os
import re
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

from app.functions import cache as feed_cache


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("APP_VERSION", "1000")
    monkeypatch.setenv("SECRET_KEY", "x")
    from app.server import create_app
    feed_cache.clear()
    a = create_app()
    uid = a.db.create_user("alice", "alice", None, generate_password_hash("pw12345678"), "Alice", is_admin=1)
    a.db.create_user("bob", "bob", None, generate_password_hash("pw12345678"), "Bob")
    cid = a.db.create_calendar(uid, "work", "Work")
    a.db.create_view(cid, "team", "Team")
    return a


def login(app, user="alice"):
    c = app.test_client()
    c.post("/login", data={"username": user, "password": "pw12345678"})
    return c


def feed(client, view="default", token=None):
    url = f"/feed/alice/work/{view}"
    return client.get(url + (f"?token={token}" if token else ""))


def make_token(app, view=None, label="t"):
    return app.db.create_feed_token(1, view, label)


class TestDefaultsUnchanged:
    def test_existing_feeds_stay_open_until_tokens_are_required(self, app):
        assert app.db.get_calendar_by_slug("alice", "work")["feed_token_required"] == 0
        assert feed(app.test_client()).status_code == 200


class TestEnforcement:
    def test_required_means_no_token_wrong_token_and_cache_cannot_bypass(self, app):
        tok = make_token(app)
        app.db.set_feed_token_required(1, True)
        anon = app.test_client()
        assert feed(anon).status_code == 404
        assert feed(anon, token="cct_nope").status_code == 404
        assert feed(anon, token=tok).status_code == 200            # warms the cache
        assert feed(anon).status_code == 404                        # cached copy is NOT served without a token
        assert feed(anon, token=tok).status_code == 200

    def test_view_scope(self, app):
        default_only = make_token(app, "default")
        team_only = make_token(app, "team")
        any_view = make_token(app, None)
        app.db.set_feed_token_required(1, True)
        c = app.test_client()
        assert feed(c, "default", default_only).status_code == 200
        assert feed(c, "team", default_only).status_code == 404
        assert feed(c, "team", team_only).status_code == 200
        assert feed(c, "default", team_only).status_code == 404
        assert feed(c, "default", any_view).status_code == 200 and feed(c, "team", any_view).status_code == 200

    def test_token_for_another_calendar_does_not_work(self, app):
        other = app.db.create_calendar(1, "home", "Home")
        stray = app.db.create_feed_token(other, None, "stray")
        make_token(app)
        app.db.set_feed_token_required(1, True)
        assert feed(app.test_client(), token=stray).status_code == 404

    def test_usage_is_recorded(self, app):
        tok = make_token(app)
        app.db.set_feed_token_required(1, True)
        c = app.test_client()
        feed(c, token=tok)
        feed(c, token=tok)
        row = app.db.list_feed_tokens(1)[0]
        assert row["use_count"] == 2 and row["last_used_at"]

    def test_only_a_hash_is_stored(self, app):
        tok = make_token(app)
        rows = app.db._fetchall("SELECT * FROM feed_tokens")
        assert tok not in str(rows) and rows[0]["token_hash"] != tok and len(rows[0]["token_hash"]) == 64
        assert tok.startswith(rows[0]["token_prefix"])


class TestManagement:
    def test_create_shows_url_once_then_revoke_kills_it(self, app):
        c = login(app)
        r = c.post("/calendars/work/tokens/create", data={"label": "Alice's phone", "view": ""})
        assert r.status_code == 302 and r.headers["Location"].endswith("tab=sharing#feed-access")
        page = c.get("/calendars/work?tab=sharing").get_data(as_text=True)
        m = re.search(r"feed/alice/work/default\?token=(cct_[A-Za-z0-9_-]+)", page)
        assert m, "full URL shown once after creation"
        token = m.group(1)
        again = c.get("/calendars/work?tab=sharing").get_data(as_text=True)
        assert token not in again and "Alice&#39;s phone" in again          # label listed, secret gone

        c.post("/calendars/work/tokens/require", data={"required": "1"})
        assert feed(app.test_client(), token=token).status_code == 200
        tid = app.db.list_feed_tokens(1)[0]["id"]
        c.post(f"/calendars/work/tokens/{tid}/revoke")
        assert feed(app.test_client(), token=token).status_code == 404

    def test_cannot_require_without_a_token(self, app):
        c = login(app)
        c.post("/calendars/work/tokens/require", data={"required": "1"})
        assert app.db.get_calendar_by_slug("alice", "work")["feed_token_required"] == 0

    def test_turning_it_on_purges_cached_open_feed(self, app):
        make_token(app)
        c = login(app)
        assert feed(app.test_client()).status_code == 200                   # cached while open
        c.post("/calendars/work/tokens/require", data={"required": "1"})
        assert feed(app.test_client()).status_code == 404

    def test_other_users_cannot_touch_my_tokens(self, app):
        tok = make_token(app)
        tid = app.db.list_feed_tokens(1)[0]["id"]
        bob = login(app, "bob")
        assert bob.post("/calendars/work/tokens/create", data={"label": "x"}).status_code == 404
        assert bob.post(f"/calendars/work/tokens/{tid}/revoke").status_code == 404
        # even via his own calendar, revoking uses calendar scoping so my token id is untouched
        app.db.create_calendar(2, "mine", "Mine")
        bob.post(f"/calendars/mine/tokens/{tid}/revoke")
        assert len(app.db.list_feed_tokens(1)) == 1
        assert tok

    def test_invalid_scope_rejected_and_login_required(self, app):
        c = login(app)
        c.post("/calendars/work/tokens/create", data={"label": "x", "view": "ghost"})
        assert app.db.list_feed_tokens(1) == []
        assert app.test_client().post("/calendars/work/tokens/create", data={"label": "x"}).status_code == 302

    def test_duplicate_does_not_inherit_requirement_or_tokens(self, app):
        make_token(app)
        app.db.set_feed_token_required(1, True)
        login(app).post("/calendars/work/duplicate")
        copy = app.db.get_calendar_by_slug("alice", "work-copy")
        assert copy["feed_token_required"] == 0 and app.db.list_feed_tokens(copy["id"]) == []

    def test_deleting_a_calendar_removes_its_tokens(self, app):
        make_token(app)
        login(app).post("/calendars/work/delete")
        assert app.db._fetchall("SELECT id FROM feed_tokens") == []

    def test_ui_marks_required_state_on_dashboard_and_header(self, app):
        make_token(app)
        app.db.set_feed_token_required(1, True)
        c = login(app)
        assert "Token required to subscribe" in c.get("/").get_data(as_text=True)
        assert "Token required" in c.get("/calendars/work").get_data(as_text=True)


def test_migration_6_upgrades_a_v5_database_and_keeps_feeds_open(tmp_path, monkeypatch):
    import app.functions.db.db_migrations as m
    from app.classes.db import CalendarDB
    path = str(tmp_path / "old.db")
    monkeypatch.setenv("DB_PATH", path)
    monkeypatch.setenv("APP_VERSION", "0")
    conn = sqlite3.connect(path)
    for v in range(0, 6):
        conn.executescript(m.migrations[v]["sql"])
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('db_version', 5)")
    conn.execute("INSERT INTO users (slug, username, password, display_name) VALUES ('a','a','x','A')")
    conn.execute("INSERT INTO calendars (user_id, slug, title) VALUES (1, 'keep', 'Keep')")
    conn.commit()
    conn.close()

    d = CalendarDB()
    d.create_and_initialize()
    cal = d.get_calendar_by_slug("a", "keep")
    assert cal["title"] == "Keep" and cal["feed_token_required"] == 0     # existing feeds unchanged
    assert d.list_feed_tokens(cal["id"]) == []
