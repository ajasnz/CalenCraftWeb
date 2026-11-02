import os
import sqlite3
import time

import pytest
from werkzeug.security import generate_password_hash

from app.functions import totp

# RFC 6238 appendix B secret ("12345678901234567890"), base32-encoded
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


class TestAlgorithm:
    def test_rfc6238_vectors(self):
        # Last six digits of the RFC's 8-digit SHA-1 vectors
        assert totp.code_at(RFC_SECRET, 59 // 30) == "287082"
        assert totp.code_at(RFC_SECRET, 1111111109 // 30) == "081804"
        assert totp.code_at(RFC_SECRET, 1234567890 // 30) == "005924"

    def test_verify_accepts_drift_but_not_replay(self):
        now = 1_000_000_000
        step = totp.current_step(now)
        good = totp.code_at(RFC_SECRET, step)
        assert totp.verify(RFC_SECRET, good, now=now) == step
        assert totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, step - 1), now=now) == step - 1
        assert totp.verify(RFC_SECRET, totp.code_at(RFC_SECRET, step - 2), now=now) is None
        assert totp.verify(RFC_SECRET, good, last_step=step, now=now) is None  # replay

    def test_verify_rejects_garbage_and_tolerates_spaces(self):
        now = 1_000_000_000
        good = totp.code_at(RFC_SECRET, totp.current_step(now))
        assert totp.verify(RFC_SECRET, "abcdef", now=now) is None
        assert totp.verify(RFC_SECRET, "", now=now) is None
        assert totp.verify(RFC_SECRET, None, now=now) is None
        assert totp.verify(RFC_SECRET, good[:3] + " " + good[3:], now=now) is not None

    def test_recovery_codes_are_single_use_and_stored_hashed(self):
        codes, stored = totp.generate_recovery_codes()
        assert len(codes) == totp.RECOVERY_CODE_COUNT
        assert codes[0].replace("-", "") not in stored            # plaintext never stored
        after = totp.consume_recovery_code(stored, codes[0].upper())  # case/format tolerant
        assert after is not None and totp.recovery_codes_remaining(after) == len(codes) - 1
        assert totp.consume_recovery_code(after, codes[0]) is None  # already used
        assert totp.consume_recovery_code(stored, "nope-nope1") is None

    def test_provisioning_uri(self):
        uri = totp.provisioning_uri("ABC", "alice")
        assert uri.startswith("otpauth://totp/CalenCraft%3Aalice?secret=ABC&issuer=CalenCraft")


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("APP_VERSION", "1000")
    monkeypatch.setenv("SECRET_KEY", "x")
    from app.functions import cache  # noqa: F401
    from app.routes import auth
    auth._failed.clear()
    from app.server import create_app
    a = create_app()
    a.db.create_user("alice", "alice", None, generate_password_hash("correct-horse"), "Alice", is_admin=1)
    a.db.create_user("bob", "bob", None, generate_password_hash("battery-staple"), "Bob")
    return a


def login(client, user="alice", pw="correct-horse"):
    return client.post("/login", data={"username": user, "password": pw})


def enroll(client, app):
    """Enrol alice via the real profile flow; returns (secret, recovery_codes)."""
    login(client)
    client.post("/profile", data={"_action": "totp_start"})
    with client.session_transaction() as s:
        secret = s["totp_setup_secret"]
    code = totp.code_at(secret, totp.current_step())
    client.post("/profile", data={"_action": "totp_confirm", "code": code})
    with client.session_transaction() as s:
        codes = s.pop("totp_new_recovery")
    return secret, codes


class TestFlow:
    def test_no_2fa_login_unchanged(self, app):
        c = app.test_client()
        assert login(c).headers["Location"].endswith("/")
        with c.session_transaction() as s:
            assert s["user_id"]

    def test_enrol_then_login_requires_code(self, app):
        c = app.test_client()
        secret, codes = enroll(c, app)
        assert len(codes) == 8
        c.get("/logout")

        r = login(c)
        assert r.status_code == 302 and "/login/2fa" in r.headers["Location"]
        with c.session_transaction() as s:
            assert "user_id" not in s and "pending_2fa" in s   # not logged in yet
        assert c.get("/").status_code == 200 and b"Log in" in c.get("/").data   # landing, not dashboard

        # wrong code
        assert "Invalid code" in c.post("/login/2fa", data={"code": "000000"}, follow_redirects=True).get_data(as_text=True)
        # right code (a step after the one used at enrolment, as replay is blocked)
        state = app.db.get_totp(1)
        step = state["totp_last_step"] + 1
        code = totp.code_at(secret, step)
        monkey_now = step * totp.PERIOD
        import app.functions.totp as t
        orig = t.current_step
        t.current_step = lambda now=None: step
        try:
            r = c.post("/login/2fa", data={"code": code})
        finally:
            t.current_step = orig
        assert r.status_code == 302 and r.headers["Location"].endswith("/")
        with c.session_transaction() as s:
            assert s["user_id"] == 1 and "pending_2fa" not in s
        assert monkey_now > 0

    def test_replayed_code_is_rejected(self, app):
        c = app.test_client()
        secret, _ = enroll(c, app)       # enrolment consumed the current step
        c.get("/logout")
        login(c)
        code = totp.code_at(secret, totp.current_step())
        r = c.post("/login/2fa", data={"code": code}, follow_redirects=True)
        assert "Invalid code" in r.get_data(as_text=True)

    def test_recovery_code_works_once(self, app):
        c = app.test_client()
        _, codes = enroll(c, app)
        c.get("/logout")
        login(c)
        assert c.post("/login/2fa", data={"code": codes[0]}).headers["Location"].endswith("/")
        c.get("/logout")
        login(c)
        r = c.post("/login/2fa", data={"code": codes[0]}, follow_redirects=True)
        assert "Invalid code" in r.get_data(as_text=True)
        assert totp.recovery_codes_remaining(app.db.get_totp(1)["totp_recovery"]) == 7

    def test_2fa_page_needs_pending_state_and_expires(self, app):
        c = app.test_client()
        assert c.get("/login/2fa").status_code == 302                  # nothing pending
        enroll(c, app)
        c.get("/logout")
        login(c)
        with c.session_transaction() as s:
            s["pending_2fa"] = {"uid": 1, "ts": time.time() - 10_000}
        assert c.get("/login/2fa").status_code == 302                  # expired

    def test_failed_codes_are_rate_limited(self, app):
        c = app.test_client()
        enroll(c, app)
        c.get("/logout")
        login(c)
        for _ in range(10):
            c.post("/login/2fa", data={"code": "000000"})
        r = c.post("/login/2fa", data={"code": "000000"}, follow_redirects=True)
        assert "Too many failed attempts" in r.get_data(as_text=True)

    def test_confirm_with_bad_code_does_not_enable(self, app):
        c = app.test_client()
        login(c)
        c.post("/profile", data={"_action": "totp_start"})
        c.post("/profile", data={"_action": "totp_confirm", "code": "000000"})
        assert not app.db.get_totp(1)["totp_enabled"]

    def test_disable_requires_password_and_code(self, app):
        c = app.test_client()
        secret, codes = enroll(c, app)
        c.post("/profile", data={"_action": "totp_disable", "password": "wrong", "code": codes[0]})
        assert app.db.get_totp(1)["totp_enabled"]
        c.post("/profile", data={"_action": "totp_disable", "password": "correct-horse", "code": "000000"})
        assert app.db.get_totp(1)["totp_enabled"]
        c.post("/profile", data={"_action": "totp_disable", "password": "correct-horse", "code": codes[0]})
        state = app.db.get_totp(1)
        assert not state["totp_enabled"] and state["totp_secret"] is None and state["totp_recovery"] is None

    def test_admin_can_reset_a_users_2fa_but_non_admin_cannot(self, app):
        c = app.test_client()
        enroll(c, app)                      # alice is admin
        # make bob the enrolled one instead: enable directly
        app.db.enable_totp(2, totp.generate_secret(), 0, "[]")
        assert c.post("/admin/users/2/reset-2fa").status_code == 302
        assert not app.db.get_totp(2)["totp_enabled"]
        c.get("/logout")
        login(c, "bob", "battery-staple")
        assert c.post("/admin/users/1/reset-2fa").status_code == 403
        assert app.db.get_totp(1)["totp_enabled"]

    def test_profile_page_renders_in_each_state(self, app):
        c = app.test_client()
        login(c)
        assert "Set up" in c.get("/profile").get_data(as_text=True)
        c.post("/profile", data={"_action": "totp_start"})
        html = c.get("/profile").get_data(as_text=True)
        assert "otpauth://" in html and "Confirm and turn on" in html
        enroll(c, app)
        assert "Turn off" in c.get("/profile").get_data(as_text=True)


def test_migration_5_upgrades_an_existing_v4_database(tmp_path, monkeypatch):
    import app.functions.db.db_migrations as m
    from app.classes.db import CalendarDB
    path = str(tmp_path / "old.db")
    monkeypatch.setenv("DB_PATH", path)
    monkeypatch.setenv("APP_VERSION", "0")
    conn = sqlite3.connect(path)
    for v in range(0, 5):
        conn.executescript(m.migrations[v]["sql"])
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('db_version', 4)")
    conn.execute("INSERT INTO users (slug, username, password, display_name) VALUES ('a','a','x','A')")
    conn.commit()
    conn.close()

    d = CalendarDB()
    d.create_and_initialize()
    state = d.get_totp(1)
    assert state["totp_enabled"] == 0 and state["totp_secret"] is None and state["totp_last_step"] == 0
    assert d.get_user_by_username("a")["totp_enabled"] == 0     # existing users unaffected
