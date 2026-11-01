import json
import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("APP_VERSION", "1000")
    monkeypatch.setenv("SECRET_KEY", "x")
    from app.server import create_app
    app = create_app()
    db = app.db
    uid = db.create_user("u", "u", None, generate_password_hash("pw12345678"), "U", is_admin=1)
    cid = db.create_calendar(uid, "work", "Work")
    sid = db.create_source(uid, "https://example.com/a.ics", "Example")
    db.link_source(cid, sid)
    vid = db.create_view(cid, "team", "Team")
    db.create_rule(cid, vid, sid, 10, "*", "contains", "", "include", 0, "SUMMARY", "append", " [x]")
    db.create_filter(cid, None, None, 5, "SUMMARY", "contains", "ok", "include", 0)
    db._execute("UPDATE calendars SET viewer_password = ? WHERE id = ?", ("SECRETHASH", cid))
    c = app.test_client()
    c.post("/login", data={"username": "u", "password": "pw12345678"})
    return c


def test_export_contents_are_portable_and_secret_free(setup):
    r = setup.get("/calendars/work/export.json")
    assert r.status_code == 200
    assert "attachment" in r.headers["Content-Disposition"]
    assert "SECRETHASH" not in r.get_data(as_text=True)
    d = json.loads(r.data)
    assert d["calendar"]["slug"] == "work" and "viewer_password" not in d["calendar"]
    assert d["sources"] == [{"name": "Example", "url": "https://example.com/a.ics", "enabled": True}]
    assert [v["slug"] for v in d["views"]] == ["team"]
    assert d["rules"][0]["view"] == "team" and d["rules"][0]["source"] == "Example"
    assert d["filters"][0]["view"] is None and d["filters"][0]["pattern"] == "ok"
    assert "id" not in d["rules"][0] and "calendar_id" not in d["rules"][0]


def test_export_requires_login_and_ownership(setup):
    assert setup.get("/calendars/nope/export.json").status_code == 404
    setup.get("/logout")
    assert setup.get("/calendars/work/export.json").status_code == 302
