import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("APP_VERSION", "1000")
    monkeypatch.setenv("SECRET_KEY", "x")
    from app.server import create_app
    a = create_app()
    a.db.create_user("u", "u", None, generate_password_hash("old-password-1"), "U", is_admin=1)
    return a


def login(app, pw="old-password-1"):
    c = app.test_client()
    c.post("/login", data={"username": "u", "password": pw})
    return c


def full_calendar(db, slug="work"):
    cid = db.create_calendar(1, slug, slug.title())
    sid = db.create_source(1, "https://example.com/%s.ics" % slug, "Src")
    db.link_source(cid, sid)
    vid = db.create_view(cid, "team", "Team")
    db.create_rule(cid, vid, sid, 10, "*", "contains", "", "include", 0, "SUMMARY", "append", "x")
    db.create_filter(cid, vid, sid, 10, "SUMMARY", "contains", "a", "include", 0)
    return cid, sid


class TestDeleteCalendar:
    def test_deleting_a_calendar_with_sources_views_rules_and_filters_works(self, app):
        db = app.db
        cid, sid = full_calendar(db)
        r = login(app).post("/calendars/work/delete")
        assert r.status_code == 302
        assert db.get_calendar_by_slug("u", "work") is None
        for table in ("rules", "filters", "views", "calendar_sources"):
            assert db._fetchall("SELECT 1 FROM %s WHERE calendar_id = ?" % table, (cid,)) == []
        assert db._fetchall("SELECT 1 FROM sources WHERE id = ?", (sid,)) == []     # orphaned source cleaned up

    def test_other_calendars_and_shared_sources_are_untouched(self, app):
        db = app.db
        cid, sid = full_calendar(db, "work")
        other = db.create_calendar(1, "home", "Home")
        db.link_source(other, sid)                                  # same source also feeds another calendar
        keep_cid, keep_sid = full_calendar(db, "keep")
        login(app).post("/calendars/work/delete")
        assert db._fetchall("SELECT 1 FROM sources WHERE id = ?", (sid,)) != []     # still used by "home"
        assert db.get_calendar_by_slug("u", "home") and db.get_calendar_by_slug("u", "keep")
        assert len(db.get_rules("u", "keep")) == 1 and len(db.get_sources(keep_cid)) == 1

    def test_failure_rolls_back_completely(self, app, monkeypatch):
        db = app.db
        cid, _ = full_calendar(db)
        orig = db.__class__._execute

        def flaky(self, sql, params=(), commit=True):
            if sql.startswith("DELETE FROM calendar_sources"):
                raise RuntimeError("boom")
            return orig(self, sql, params, commit)
        monkeypatch.setattr(db.__class__, "_execute", flaky)
        with pytest.raises(RuntimeError):
            db.delete_calendar(cid)
        monkeypatch.setattr(db.__class__, "_execute", orig)
        assert db.get_calendar_by_slug("u", "work") is not None       # nothing half-deleted
        assert len(db.get_rules("u", "work")) == 1


class TestPasswordChange:
    def post(self, c, cur, new, confirm=None):
        return c.post("/profile", data={"_action": "password", "current_password": cur,
                                        "new_password": new, "confirm_password": confirm or new})

    def test_change_password_works_and_new_password_logs_in(self, app):
        c = login(app)
        r = self.post(c, "old-password-1", "new-password-22")
        assert r.status_code == 302                                   # was a 500 (KeyError: 'password')
        assert login(app, "new-password-22").get("/").status_code == 200
        assert b"Log in" in login(app, "old-password-1").get("/").data  # old one no longer works

    def test_wrong_current_password_is_rejected_without_changing_it(self, app):
        c = login(app)
        assert self.post(c, "not-it", "new-password-22").status_code == 302
        assert b"Log in" not in login(app, "old-password-1").get("/").data   # unchanged, still works

    def test_validation_still_applies(self, app):
        c = login(app)
        self.post(c, "old-password-1", "short")
        self.post(c, "old-password-1", "new-password-22", "different-22222")
        assert b"Log in" not in login(app, "old-password-1").get("/").data
