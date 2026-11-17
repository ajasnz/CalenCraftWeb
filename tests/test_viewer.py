import pytest
from werkzeug.security import generate_password_hash

ICS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\n"
    # 08:00 UTC
    "BEGIN:VEVENT\r\nUID:utc@x\r\nSUMMARY:UTC meeting\r\nDTSTART:20261103T080000Z\r\nDTEND:20261103T090000Z\r\nEND:VEVENT\r\n"
    # 09:00 in Auckland (NZDT, UTC+13) = 20:00 UTC the day before
    "BEGIN:VEVENT\r\nUID:akl@x\r\nSUMMARY:Auckland meeting\r\nDTSTART;TZID=Pacific/Auckland:20261103T090000\r\n"
    "DTEND;TZID=Pacific/Auckland:20261103T100000\r\nEND:VEVENT\r\n"
    # floating time (no zone): means 12:00 wherever you are
    "BEGIN:VEVENT\r\nUID:float@x\r\nSUMMARY:Floating\r\nDTSTART:20261103T120000\r\nDTEND:20261103T130000\r\nEND:VEVENT\r\n"
    # all-day
    "BEGIN:VEVENT\r\nUID:allday@x\r\nSUMMARY:Holiday\r\nDTSTART;VALUE=DATE:20261105\r\nDTEND;VALUE=DATE:20261106\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)
RANGE = "start=2026-11-01T00:00:00Z&end=2026-11-10T00:00:00Z"


@pytest.fixture
def env(tmp_path, monkeypatch):
    calls = {"n": 0}

    class R:
        text = ICS
        def raise_for_status(self): pass

    def fake_get(*a, **k):
        calls["n"] += 1
        return R()
    monkeypatch.setattr("app.classes.calendar.requests.get", fake_get)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("APP_VERSION", "1000")
    monkeypatch.setenv("SECRET_KEY", "x")
    from app.functions import cache
    cache.clear()
    from app.server import create_app
    app = create_app()
    db = app.db
    uid = db.create_user("alice", "alice", None, generate_password_hash("pw12345678"), "Alice", is_admin=1)
    cid = db.create_calendar(uid, "work", "Work")
    sid = db.create_source(uid, "https://example.com/a.ics", "Src")
    db.link_source(cid, sid)
    db.create_view(cid, "team", "Team")
    db._execute("UPDATE calendars SET share_viewer = 1 WHERE id = ?", (cid,))
    return app, calls


def events(client, query=RANGE, view="default"):
    r = client.get(f"/view/alice/work/{view}/events.json?{query}")
    assert r.status_code == 200, r.status_code
    return {e["id"]: e for e in r.get_json()}


class TestTimezones:
    def test_utc_event_is_sent_as_utc(self, env):
        app, _ = env
        assert events(app.test_client())["utc@x"]["start"] == "2026-11-03T08:00:00Z"

    def test_zoned_event_is_converted_to_utc_so_the_browser_can_localise_it(self, env):
        app, _ = env
        e = events(app.test_client())["akl@x"]
        assert e["start"] == "2026-11-02T20:00:00Z" and e["end"] == "2026-11-02T21:00:00Z"

    def test_floating_and_all_day_events_are_not_shifted(self, env):
        app, _ = env
        got = events(app.test_client())
        assert got["float@x"]["start"] == "2026-11-03T12:00:00"          # no Z: stays 12:00 for everyone
        assert got["allday@x"]["start"].startswith("2026-11-05T00:00:00") and not got["allday@x"]["start"].endswith("Z")
        assert got["allday@x"]["isAllday"] is True

    def test_range_filter_compares_real_instants(self, env):
        app, _ = env
        c = app.test_client()
        # the Auckland meeting is 2 Nov 20:00Z: outside a window that starts 3 Nov 00:00Z, inside one starting 2 Nov
        assert "akl@x" not in events(c, "start=2026-11-03T00:00:00Z&end=2026-11-10T00:00:00Z")
        assert "akl@x" in events(c, "start=2026-11-02T00:00:00Z&end=2026-11-10T00:00:00Z")


class TestCaching:
    def test_visitors_in_the_same_window_share_one_upstream_fetch(self, env):
        app, calls = env
        events(app.test_client(), "start=2026-11-01T03:15:22.123Z&end=2026-11-10T11:59:59.999Z")
        events(app.test_client(), "start=2026-11-01T17:40:01.456Z&end=2026-11-10T01:02:03.000Z")
        events(app.test_client(), "start=2026-11-01T00:00:00Z&end=2026-11-10T08:30:00Z")
        assert calls["n"] == 1

    def test_views_are_cached_separately(self, env):
        app, calls = env
        events(app.test_client())
        events(app.test_client(), view="team")
        assert calls["n"] == 2

    def test_refresh_now_and_overrides_drop_the_cache(self, env):
        app, calls = env
        events(app.test_client())
        c = app.test_client()
        c.post("/login", data={"username": "alice", "password": "pw12345678"})
        c.post("/calendars/work/refresh")                       # itself rebuilds once for source health
        before = calls["n"]
        events(app.test_client())
        assert calls["n"] == before + 1                           # cache was dropped, so the viewer refetches
        events(app.test_client())
        assert calls["n"] == before + 1                           # ...then caches again

    def test_privacy_settings_apply_immediately_even_with_a_warm_cache(self, env):
        app, _ = env
        assert events(app.test_client())["utc@x"]["title"] == "UTC meeting"
        app.db._execute("UPDATE calendars SET viewer_free_busy_only = 1 WHERE id = 1")
        assert events(app.test_client())["utc@x"]["title"] == "Busy"       # redaction is applied after the cache
        app.db._execute("UPDATE calendars SET viewer_free_busy_only = 0 WHERE id = 1")
        assert events(app.test_client())["utc@x"]["title"] == "UTC meeting"

    def test_password_protection_is_enforced_even_when_the_cache_is_warm(self, env):
        app, _ = env
        app.db._execute("UPDATE calendars SET viewer_password = ? WHERE id = 1", (generate_password_hash("open-sesame"),))
        c = app.test_client()
        c.post("/view/alice/work/default", data={"pub_password": "open-sesame"})
        assert c.get(f"/view/alice/work/default/events.json?{RANGE}").status_code == 200   # warms the cache
        stranger = app.test_client()
        assert stranger.get(f"/view/alice/work/default/events.json?{RANGE}").status_code == 401


class TestNoIndex:
    @pytest.mark.parametrize("path", [
        "/view/alice/work/default", "/embed/alice/work/default", f"/view/alice/work/default/events.json?{RANGE}"])
    def test_public_pages_are_not_search_indexed(self, env, path):
        app, _ = env
        r = app.test_client().get(path)
        assert r.status_code == 200 and "noindex" in r.headers.get("X-Robots-Tag", "")

    def test_html_also_carries_the_meta_tag_and_so_does_the_password_page(self, env):
        app, _ = env
        for path in ("/view/alice/work/default", "/embed/alice/work/default"):
            assert 'name="robots"' in app.test_client().get(path).get_data(as_text=True)
        app.db._execute("UPDATE calendars SET viewer_password = ? WHERE id = 1", (generate_password_hash("pw"),))
        login = app.test_client().get("/view/alice/work/default")
        assert 'name="robots"' in login.get_data(as_text=True) and "noindex" in login.headers["X-Robots-Tag"]


def test_list_view_groups_days_by_the_visitors_local_date():
    """The list view must not bucket by UTC date (that put morning events under the previous day in NZ)."""
    import os
    base = os.path.join(os.path.dirname(__file__), "..", "app")
    page = open(os.path.join(base, "templates", "public_viewer.html"), encoding="utf-8").read()
    helpers = open(os.path.join(base, "static", "viewer-helpers.js"), encoding="utf-8").read()
    assert "toISOString().slice(0, 10)" not in page and "toISOString().slice(0, 10)" not in helpers
    assert "function localDayKey" in helpers and "localDayKey(" in page
