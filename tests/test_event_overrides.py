import datetime
import json
import sqlite3

import pytest
from icalendar import Calendar
from werkzeug.security import generate_password_hash

from app.classes.calendar import (CalendarBuilder, event_start_key, ical_events_to_dicts, pick_override)

PW = "correct-horse-battery"


def _ics():
    """One single event plus a weekly series (4 occurrences), all in the next few weeks (UTC)."""
    first = (datetime.datetime.utcnow() + datetime.timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
    single = first + datetime.timedelta(days=1, hours=3)
    f = lambda d: d.strftime("%Y%m%dT%H%M%SZ")
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\n"
            f"BEGIN:VEVENT\r\nUID:standup@x\r\nSUMMARY:Standup\r\nDTSTART:{f(first)}\r\nDTEND:{f(first + datetime.timedelta(hours=1))}\r\n"
            "RRULE:FREQ=WEEKLY;COUNT=4\r\nEND:VEVENT\r\n"
            f"BEGIN:VEVENT\r\nUID:lunch@x\r\nSUMMARY:Lunch\r\nDTSTART:{f(single)}\r\nDTEND:{f(single + datetime.timedelta(hours=1))}\r\nEND:VEVENT\r\n"
            "END:VCALENDAR\r\n"), first


@pytest.fixture
def env(tmp_path, monkeypatch):
    ics, first = _ics()

    class R:
        text = ics
        def raise_for_status(self): pass
    monkeypatch.setattr("app.classes.calendar.requests.get", lambda *a, **k: R())
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("APP_VERSION", "1000")
    monkeypatch.setenv("SECRET_KEY", "x")
    from app.functions import cache
    cache.clear()
    from app.server import create_app
    app = create_app()
    db = app.db
    uid = db.create_user("alice", "alice", None, generate_password_hash(PW), "Alice", is_admin=1)
    db.create_user("bob", "bob", None, generate_password_hash(PW), "Bob")
    cid = db.create_calendar(uid, "work", "Work")
    sid = db.create_source(uid, "https://example.com/a.ics", "Src")
    db.link_source(cid, sid)
    db.create_view(cid, "team", "Team")
    return app, cid, first


def occurrences(app, view="default", apply_overrides=True):
    b = CalendarBuilder()
    b.db = app.db
    now = datetime.datetime.utcnow()
    ical = b.build(user_slug="alice", calendar_slug="work", view_slug=view,
                   expansion_start=now, expansion_end=now + datetime.timedelta(days=60),
                   apply_overrides=apply_overrides)
    return [(str(c.get("UID")), str(c.get("SUMMARY")), event_start_key(c))
            for c in Calendar.from_ical(ical).walk("VEVENT")]


def titles(app, **kw):
    return sorted(t for _u, t, _s in occurrences(app, **kw))


class TestBuilder:
    def test_baseline_has_four_standups_and_one_lunch(self, env):
        app, *_ = env
        assert titles(app) == ["Lunch"] + ["Standup"] * 4

    def test_hide_one_occurrence_only(self, env):
        app, cid, first = env
        key = first.isoformat()
        app.db.add_event_override(cid, None, "standup@x", key, "hide", None, "Standup")
        got = occurrences(app)
        assert len([g for g in got if g[0] == "standup@x"]) == 3
        assert key not in [s for u, _t, s in got if u == "standup@x"]
        assert any(t == "Lunch" for _u, t, _s in got)

    def test_hide_whole_series_and_restore(self, env):
        app, cid, _ = env
        app.db.add_event_override(cid, None, "standup@x", None, "hide", None, "Standup")
        assert titles(app) == ["Lunch"]
        app.db.delete_event_override(cid, app.db.list_event_overrides(cid)[0]["id"])
        assert len(titles(app)) == 5

    def test_rename_occurrence_and_series(self, env):
        app, cid, first = env
        app.db.add_event_override(cid, None, "standup@x", first.isoformat(), "rename", "Kickoff", "Standup")
        assert titles(app).count("Kickoff") == 1 and titles(app).count("Standup") == 3
        app.db.add_event_override(cid, None, "lunch@x", None, "rename", "Team lunch", "Lunch")
        assert "Team lunch" in titles(app) and "Lunch" not in titles(app)

    def test_view_scope(self, env):
        app, cid, _ = env
        app.db.add_event_override(cid, "team", "lunch@x", None, "hide", None, "Lunch")
        assert "Lunch" not in titles(app, view="team")
        assert "Lunch" in titles(app, view="default")

    def test_most_specific_rule_wins(self, env):
        app, cid, first = env
        # series hidden everywhere, but this one occurrence renamed and kept
        app.db.add_event_override(cid, None, "standup@x", None, "hide", None, "Standup")
        app.db.add_event_override(cid, None, "standup@x", first.isoformat(), "rename", "Special", "Standup")
        assert titles(app) == ["Lunch", "Special"]

    def test_same_target_replaces_instead_of_stacking(self, env):
        app, cid, _ = env
        app.db.add_event_override(cid, None, "lunch@x", None, "rename", "A", "Lunch")
        app.db.add_event_override(cid, None, "lunch@x", None, "rename", "B", "Lunch")
        rows = app.db.list_event_overrides(cid)
        assert len(rows) == 1 and rows[0]["new_title"] == "B"

    def test_apply_overrides_false_shows_everything(self, env):
        app, cid, _ = env
        app.db.add_event_override(cid, None, "standup@x", None, "hide", None, "Standup")
        assert len(titles(app, apply_overrides=False)) == 5

    def test_start_key_matches_the_preview_dicts(self, env):
        app, *_ = env
        b = CalendarBuilder()
        b.db = app.db
        now = datetime.datetime.utcnow()
        ical = b.build(user_slug="alice", calendar_slug="work", view_slug="default",
                       expansion_start=now, expansion_end=now + datetime.timedelta(days=60))
        keys = sorted(event_start_key(c) for c in Calendar.from_ical(ical).walk("VEVENT"))
        assert keys == sorted(e["start"] for e in ical_events_to_dicts(ical))

    def test_pick_override_ranking(self):
        mk = lambda uid, inst, view, action: dict(event_uid=uid, instance_start=inst, view_slug=view, action=action)
        series_all = mk("u", None, None, "hide")
        series_view = mk("u", None, "v", "rename")
        inst_all = mk("u", "S", None, "rename")
        assert pick_override([series_all, series_view], "u", "S") is series_view
        assert pick_override([series_all, series_view, inst_all], "u", "S") is inst_all
        assert pick_override([inst_all], "u", "OTHER") is None
        assert pick_override([series_all], "someone-else", "S") is None


def login(app, user="alice"):
    c = app.test_client()
    c.post("/login", data={"username": user, "password": PW})
    return c


class TestEndpoints:
    def test_create_via_endpoint_hides_event_in_the_actual_feed(self, env):
        app, cid, first = env
        c = login(app)
        r = c.post("/calendars/work/overrides", data={"uid": "standup@x", "start": first.isoformat(), "action": "hide",
                                                       "scope": "this", "view": "", "label": "Standup"})
        assert r.get_json()["ok"] is True
        body = app.test_client().get("/feed/alice/work/default").get_data(as_text=True)
        assert body.count("UID:standup@x") == 3 and "UID:lunch@x" in body

    def test_preview_shows_hidden_events_annotated_with_an_undo(self, env):
        app, cid, first = env
        c = login(app)
        c.post("/calendars/work/overrides", data={"uid": "lunch@x", "action": "hide", "scope": "all", "label": "Lunch"})
        c.post("/calendars/work/overrides", data={"uid": "standup@x", "action": "rename", "scope": "all",
                                                   "title": "Sync", "label": "Standup"})
        d = c.get("/calendars/work/upcoming.json?days=60").get_json()
        by_uid = {}
        for e in d["events"]:
            by_uid.setdefault(e["uid"], []).append(e)
        assert by_uid["lunch@x"][0]["override"]["action"] == "hide"            # still listed, flagged
        assert all(e["override"]["new_title"] == "Sync" for e in by_uid["standup@x"])
        assert len(d["overrides"]) == 2 and {o["scope"] for o in d["overrides"]} == {"all"}

    def test_delete_restores_the_feed(self, env):
        app, cid, _ = env
        c = login(app)
        anon = app.test_client()
        assert "UID:lunch@x" in anon.get("/feed/alice/work/default").get_data(as_text=True)       # warms the cache
        oid = c.post("/calendars/work/overrides", data={"uid": "lunch@x", "action": "hide", "scope": "all"}).get_json()["id"]
        assert "UID:lunch@x" not in anon.get("/feed/alice/work/default").get_data(as_text=True)   # hidden at once
        assert c.post(f"/calendars/work/overrides/{oid}/delete").get_json()["ok"] is True
        assert "UID:lunch@x" in anon.get("/feed/alice/work/default").get_data(as_text=True)       # and restored at once

    @pytest.mark.parametrize("data,needle", [
        ({"uid": "", "action": "hide", "scope": "all"}, "stable id"),
        ({"uid": "x" * 501, "action": "hide", "scope": "all"}, "stable id"),
        ({"uid": "u", "action": "explode", "scope": "all"}, "Unknown action"),
        ({"uid": "u", "action": "rename", "scope": "all", "title": ""}, "Enter a title"),
        ({"uid": "u", "action": "rename", "scope": "all", "title": "x" * 201}, "Enter a title"),
        ({"uid": "u", "action": "hide", "scope": "forever"}, "Unknown scope"),
        ({"uid": "u", "action": "hide", "scope": "this", "start": "not-a-date"}, "event time"),
        ({"uid": "u", "action": "hide", "scope": "all", "view": "ghost"}, "doesn't exist"),
    ])
    def test_validation(self, env, data, needle):
        app, cid, _ = env
        r = login(app).post("/calendars/work/overrides", data=data)
        assert r.status_code == 400 and needle in r.get_json()["error"]
        assert app.db.list_event_overrides(cid) == []

    def test_limit_per_calendar(self, env):
        app, cid, _ = env
        app.db.MAX_OVERRIDES_PER_CALENDAR = 3
        c = login(app)
        for i in range(3):
            assert c.post("/calendars/work/overrides", data={"uid": f"e{i}", "action": "hide", "scope": "all"}).status_code == 200
        r = c.post("/calendars/work/overrides", data={"uid": "e9", "action": "hide", "scope": "all"})
        assert r.status_code == 400 and "Too many" in r.get_json()["error"]
        assert len(app.db.list_event_overrides(cid)) == 3

    def test_auth_and_isolation(self, env):
        app, cid, _ = env
        assert app.test_client().post("/calendars/work/overrides", data={"uid": "u", "action": "hide", "scope": "all"}).status_code == 302
        bob = login(app, "bob")
        assert bob.post("/calendars/work/overrides", data={"uid": "u", "action": "hide", "scope": "all"}).status_code == 404
        oid = login(app).post("/calendars/work/overrides", data={"uid": "u", "action": "hide", "scope": "all"}).get_json()["id"]
        assert bob.post(f"/calendars/work/overrides/{oid}/delete").status_code == 404
        assert len(app.db.list_event_overrides(cid)) == 1

    def test_changes_are_audited_without_leaking_more_than_the_title(self, env):
        app, cid, _ = env
        c = login(app)
        oid = c.post("/calendars/work/overrides", data={"uid": "u", "action": "hide", "scope": "all", "label": "Dentist"}).get_json()["id"]
        c.post(f"/calendars/work/overrides/{oid}/delete")
        acts = [r["action"] for r in app.db._fetchall("SELECT action FROM audit_log")]
        assert "override.created" in acts and "override.removed" in acts

    def test_preview_tab_renders_controls(self, env):
        app, *_ = env
        html = login(app).get("/calendars/work").get_data(as_text=True)
        assert "pvRow" in html and "overrides" in html


class TestLifecycle:
    def test_duplicate_keeps_overrides_and_delete_removes_them(self, env):
        app, cid, _ = env
        app.db.add_event_override(cid, "team", "lunch@x", None, "hide", None, "Lunch")
        c = login(app)
        c.post("/calendars/work/duplicate")
        copy = app.db.get_calendar_by_slug("alice", "work-copy")
        copied = app.db.list_event_overrides(copy["id"])
        assert len(copied) == 1 and copied[0]["event_uid"] == "lunch@x" and copied[0]["view_slug"] == "team"
        c.post("/calendars/work/delete")
        assert app.db.list_event_overrides(cid) == []
        assert len(app.db.list_event_overrides(copy["id"])) == 1       # the copy is unaffected


def test_migration_8_upgrades_a_v7_database(tmp_path, monkeypatch):
    import app.functions.db.db_migrations as m
    from app.classes.db import CalendarDB
    path = str(tmp_path / "old.db")
    monkeypatch.setenv("DB_PATH", path)
    monkeypatch.setenv("APP_VERSION", "0")
    conn = sqlite3.connect(path)
    for v in range(0, 8):
        conn.executescript(m.migrations[v]["sql"])
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('db_version', 7)")
    conn.execute("INSERT INTO users (slug, username, password, display_name) VALUES ('a','a','x','A')")
    conn.execute("INSERT INTO calendars (user_id, slug, title) VALUES (1, 'keep', 'Keep')")
    conn.commit()
    conn.close()
    d = CalendarDB()
    d.create_and_initialize()
    assert d.list_event_overrides(1) == [] and d.get_calendar_by_slug("a", "keep")["title"] == "Keep"
    d.add_event_override(1, None, "u", None, "hide", None, "x")
    assert len(d.list_event_overrides(1)) == 1
