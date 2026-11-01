import sqlite3

import pytest
from werkzeug.security import generate_password_hash

from app.functions import audit as audit_mod
from app.functions import totp

PW = "correct-horse-battery"
SOURCE_URL = "https://example.com/private/SECRETKEY123/cal.ics"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("APP_VERSION", "1000")
    monkeypatch.setenv("SECRET_KEY", "x")
    from app.routes import auth
    auth._failed.clear()
    from app.server import create_app
    a = create_app()
    a.db.create_user("alice", "alice", None, generate_password_hash(PW), "Alice", is_admin=1)
    a.db.create_user("bob", "bob", None, generate_password_hash(PW), "Bob")
    return a


def login(app, user="alice", pw=PW):
    c = app.test_client()
    c.post("/login", data={"username": user, "password": pw}, environ_base={"REMOTE_ADDR": "203.0.113.9"})
    return c


def rows(app, **where):
    sql = "SELECT * FROM audit_log" + (" WHERE " + " AND ".join(f"{k}=?" for k in where) if where else "") + " ORDER BY id"
    return app.db._fetchall(sql, tuple(where.values()))


def actions(app, **where):
    return [r["action"] for r in rows(app, **where)]


class TestAuthEvents:
    def test_login_success_records_user_and_ip(self, app):
        login(app)
        r = rows(app)[0]
        assert r["action"] == "login.success" and r["username"] == "alice" and r["user_id"] == 1
        assert r["ip"] == "203.0.113.9" and r["ts"]

    def test_failed_login_names_existing_account_but_never_typed_text(self, app):
        c = app.test_client()
        c.post("/login", data={"username": "alice", "password": "wrong-guess-1"})
        c.post("/login", data={"username": "my-actual-password-oops", "password": "x"})
        a, b = rows(app)
        assert a["action"] == "login.failed" and a["username"] == "alice" and a["detail"] == "wrong password"
        assert b["action"] == "login.failed" and b["username"] is None and b["detail"] == "unknown username"
        blob = str(rows(app))
        assert "wrong-guess-1" not in blob and "my-actual-password-oops" not in blob

    def test_logout_and_2fa_events(self, app):
        secret = totp.generate_secret()
        app.db.enable_totp(1, secret, 0, totp.generate_recovery_codes()[1])
        c = app.test_client()
        c.post("/login", data={"username": "alice", "password": PW})
        c.post("/login/2fa", data={"code": "000000"})
        codes, stored = totp.generate_recovery_codes()
        app.db.enable_totp(1, secret, 0, stored)
        c.post("/login/2fa", data={"code": codes[0]})
        c.get("/logout")
        assert actions(app) == ["login.2fa_failed", "login.recovery_code_used", "login.success", "logout"]
        assert "7 left" in [r["detail"] for r in rows(app)][1]


class TestChangeEvents:
    def test_calendar_lifecycle_is_recorded_and_secrets_are_not(self, app):
        c = login(app)
        c.post("/calendars/new", data={"title": "Work"})
        c.post("/calendars/work/sources/add", data={"name": "Team", "url": SOURCE_URL})
        c.post("/calendars/work/tokens/create", data={"label": "Alice phone", "view": ""})
        token = app.db._fetchone("SELECT token_prefix FROM feed_tokens")["token_prefix"]
        c.post("/calendars/work/delete")
        got = actions(app, user_id=1)
        for expected in ("calendar.created", "source.added", "token.created", "calendar.deleted"):
            assert expected in got, got
        blob = str(rows(app))
        assert "SECRETKEY123" not in blob and SOURCE_URL not in blob       # source URLs never logged
        assert PW not in blob and token not in blob and "cct_" not in blob  # nor passwords or tokens
        added = [r for r in rows(app) if r["action"] == "source.added"][0]
        assert added["target"] == "calendar:work" and added["detail"] == "Team"

    def test_failed_actions_are_not_recorded_as_success(self, app):
        c = login(app)
        c.post("/calendars/new", data={"title": "Work"})
        before = len(rows(app))
        c.post("/calendars/work/sources/add", data={"name": "x", "url": "http://localhost/x.ics"})  # rejected (SSRF check)
        c.post("/calendars/work/tokens/require", data={"required": "1"})                            # rejected (no token yet)
        assert len(rows(app)) == before

    def test_security_changes_are_recorded(self, app):
        c = login(app)
        c.post("/profile", data={"_action": "password", "current_password": PW,
                                 "new_password": "brand-new-password-1", "confirm_password": "brand-new-password-1"})
        c.post("/admin/users/new", data={"username": "carol", "password": "carol-password-1"})
        c.post("/admin/users/2/reset-2fa")
        got = actions(app, user_id=1)
        assert "password.changed" in got and "admin.user_created" in got and "admin.2fa_reset" in got
        assert "brand-new-password-1" not in str(rows(app)) and "carol-password-1" not in str(rows(app))


class TestResilience:
    def test_audit_failure_never_breaks_the_request(self, app, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("disk full")
        monkeypatch.setattr(app.db.__class__, "add_audit", boom)
        c = app.test_client()
        r = c.post("/login", data={"username": "alice", "password": PW})
        assert r.status_code == 302 and r.headers["Location"].endswith("/")      # still logged in
        assert c.post("/calendars/new", data={"title": "Work"}).status_code == 302

    def test_old_rows_are_pruned(self, app, monkeypatch):
        monkeypatch.setattr(audit_mod, "KEEP_ROWS", 5)
        c = login(app)
        for i in range(12):
            c.post("/calendars/new", data={"title": f"Cal {i}"})
        left = rows(app)
        assert len(left) == 5 and left[-1]["target"] == "calendar:cal-11"


class TestActivityPage:
    def test_requires_login(self, app):
        assert app.test_client().get("/activity").status_code == 302

    def test_users_see_only_their_own_admins_see_all(self, app):
        login(app, "bob").post("/calendars/new", data={"title": "Bobs"})
        alice = login(app)
        alice.post("/calendars/new", data={"title": "Alices"})
        bob = login(app, "bob")
        bob_page = bob.get("/activity").get_data(as_text=True)
        assert "calendar:bobs" in bob_page and "calendar:alices" not in bob_page
        admin_page = alice.get("/activity").get_data(as_text=True)
        assert "calendar:bobs" in admin_page and "calendar:alices" in admin_page and "<th>Who</th>" in admin_page

    def test_filter_and_wildcards_are_literal(self, app):
        c = login(app)
        c.post("/calendars/new", data={"title": "Work"})
        c.post("/calendars/new", data={"title": "Home"})
        page = c.get("/activity?q=calendar:work").get_data(as_text=True)
        assert "calendar:work" in page and "calendar:home" not in page
        assert "No activity" in c.get("/activity?q=%25").get_data(as_text=True)      # '%' is not a wildcard
        assert "No activity" in c.get("/activity?q=_ork").get_data(as_text=True)     # nor '_'

    def test_pagination(self, app):
        c = login(app)
        for i in range(60):
            app.db.add_audit(1, "alice", "test.event", f"n{i:02d}", None, None, 1000)
        p1 = c.get("/activity").get_data(as_text=True)
        assert "Older" in p1 and "Newer" not in p1 and "n59" in p1 and "n09" not in p1
        p2 = c.get("/activity?page=2").get_data(as_text=True)
        assert "Newer" in p2 and "n09" in p2
        assert c.get("/activity?page=abc").status_code == 200


def test_migration_7_upgrades_a_v6_database(tmp_path, monkeypatch):
    import app.functions.db.db_migrations as m
    from app.classes.db import CalendarDB
    path = str(tmp_path / "old.db")
    monkeypatch.setenv("DB_PATH", path)
    monkeypatch.setenv("APP_VERSION", "0")
    conn = sqlite3.connect(path)
    for v in range(0, 7):
        conn.executescript(m.migrations[v]["sql"])
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('db_version', 6)")
    conn.execute("INSERT INTO users (slug, username, password, display_name) VALUES ('a','a','x','A')")
    conn.commit()
    conn.close()
    d = CalendarDB()
    d.create_and_initialize()
    assert d.list_audit() == []
    d.add_audit(1, "a", "x.y", None, None, None, 10)
    assert len(d.list_audit()) == 1 and d.get_user_by_username("a")["slug"] == "a"      # existing data intact
