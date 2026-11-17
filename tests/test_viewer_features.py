import json
import os
import shutil
import subprocess

import pytest
from werkzeug.security import generate_password_hash

ROOT = os.path.join(os.path.dirname(__file__), "..")
HELPERS = os.path.join(ROOT, "app", "static", "viewer-helpers.js")
NODE = shutil.which("node")


def run_node(snippet):
    """Execute the real helper file under Node and return its JSON output."""
    code = f"const h = require({json.dumps(os.path.abspath(HELPERS))}); const out = ({snippet}); console.log(JSON.stringify(out));"
    r = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30,
                       env={**os.environ, "TZ": "Pacific/Auckland"})
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.skipif(not NODE, reason="node is not installed")
class TestHelpers:
    def test_linkify_escapes_markup_and_only_links_http(self):
        out = run_node("h.linkify('<script>alert(1)</script> see https://a.b/c?x=1&y=2. javascript:evil ftp://nope')")
        assert "<script>" not in out and "&lt;script&gt;" in out
        assert '<a href="https://a.b/c?x=1&amp;y=2" target="_blank" rel="noopener noreferrer">https://a.b/c?x=1&amp;y=2</a>.' in out
        assert "<a href=\"javascript" not in out and "<a href=\"ftp" not in out

    def test_escape_covers_quotes(self):
        assert run_node("h.escHtml('<a href=\"x\" onclick=\\'y\\'>&')") == "&lt;a href=&quot;x&quot; onclick=&#39;y&#39;&gt;&amp;"

    def test_local_day_key_uses_the_local_date_not_utc(self):
        # 08:00 in Auckland on 3 Nov is 2 Nov in UTC: the old toISOString() bucketing got this wrong
        assert run_node("h.localDayKey(new Date('2026-11-02T19:00:00Z'))") == "2026-11-03"

    def test_ics_for_a_utc_event_floating_event_and_all_day(self):
        utc = run_node("h.eventToIcs({id:'e1', title:'Team, sync; weekly', start:'2026-11-02T19:00:00Z', end:'2026-11-02T20:00:00Z', "
                       "location:'Room 1', body:'Line1\\nLine2'}, new Date('2026-11-01T00:00:00Z'))")
        assert "DTSTART:20261102T190000Z" in utc and "DTEND:20261102T200000Z" in utc
        assert "SUMMARY:Team\\, sync\\; weekly" in utc and "DESCRIPTION:Line1\\nLine2" in utc and "LOCATION:Room 1" in utc
        assert utc.startswith("BEGIN:VCALENDAR\r\n") and utc.endswith("END:VCALENDAR\r\n") and "UID:e1@calencraft" in utc
        floating = run_node("h.eventToIcs({id:'f', title:'F', start:'2026-11-03T12:00:00', end:'2026-11-03T13:00:00'})")
        assert "DTSTART:20261103T120000\r\n" in floating                      # no Z: stays 12:00 for whoever opens it
        allday = run_node("h.eventToIcs({id:'a', title:'Holiday', start:'2026-11-05T00:00:00', end:'2026-11-06T00:00:00', isAllday:true})")
        assert "DTSTART;VALUE=DATE:20261105" in allday and "DTEND;VALUE=DATE:20261106" in allday

    def test_long_lines_are_folded_and_uid_is_sanitised(self):
        ics = run_node("h.eventToIcs({id:'a b\\r\\nINJECT:1', title:'x'.repeat(200), start:'2026-11-03T12:00:00'})")
        assert all(len(line) <= 75 for line in ics.split("\r\n"))
        assert "\r\nINJECT:1" not in ics and "UID:abINJECT1@calencraft" in ics     # can't inject extra properties

    def test_google_calendar_url(self):
        url = run_node("h.googleCalendarUrl({title:'A & B', start:'2026-11-02T19:00:00Z', end:'2026-11-02T20:00:00Z', location:'X'})")
        assert url.startswith("https://calendar.google.com/calendar/render?action=TEMPLATE")
        assert "text=A%20%26%20B" in url and "dates=20261102T190000Z/20261102T200000Z" in url and "location=X" in url

    def test_file_names(self):
        assert run_node("[h.safeFileName('Team Sync! 2026'), h.safeFileName(''), h.safeFileName('///')]") == [
            "team-sync-2026.ics", "event.ics", "event.ics"]

    def test_url_state_round_trip_and_validation(self):
        ok = run_node("(()=>{const s=h.parseHash('#v=week&d=2026-11-03&q=maker'); return {v:s.view, d:h.localDayKey(s.date), q:s.query};})()")
        assert ok == {"v": "week", "d": "2026-11-03", "q": "maker"}
        bad = run_node("h.parseHash('#v=evil&d=2026-02-31&q=' + 'x'.repeat(500))")
        assert "view" not in bad and "date" not in bad and len(bad["query"]) == 80        # junk ignored, query capped
        assert run_node("h.parseHash('')") == {} and run_node("h.parseHash('#d=not-a-date')") == {}
        assert run_node("h.buildHash({view:'list', date:new Date(2026,10,3), query:'a b'})") == "#v=list&d=2026-11-03&q=a+b"
        assert run_node("h.buildHash({})") == ""

    def test_search_matching(self):
        assert run_node("[h.matchesQuery({title:'Maker Night'}, 'maker'), h.matchesQuery({title:'x', location:'The Lab'}, 'lab'), "
                        "h.matchesQuery({title:'x', body:'bring snacks'}, 'SNACKS'), h.matchesQuery({title:'x'}, 'zzz'), h.matchesQuery({title:'x'}, '')]") == [
            True, True, True, False, True]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("APP_VERSION", "1000")
    monkeypatch.setenv("SECRET_KEY", "x")
    from app.server import create_app
    app = create_app()
    uid = app.db.create_user("alice", "alice", None, generate_password_hash("pw12345678"), "Alice", is_admin=1)
    cid = app.db.create_calendar(uid, "work", "Work")
    app.db._execute("UPDATE calendars SET share_viewer = 1, share_ics = 1 WHERE id = ?", (cid,))
    return app


def page(app, path="/view/alice/work/default"):
    r = app.test_client().get(path)
    assert r.status_code == 200
    return r.get_data(as_text=True)


class TestSubscribeLink:
    def test_shown_when_the_feed_is_public_and_unredacted(self, env):
        html = page(env)
        assert 'id="subscribe-link"' in html and "/feed/alice/work/default" in html and 'id="copy-feed"' in html

    @pytest.mark.parametrize("sql", [
        "share_ics = 0",
        "viewer_free_busy_only = 1",                 # the raw feed isn't redacted, so don't point at it
        "viewer_hide_private = 1",
        "feed_token_required = 1",
    ])
    def test_hidden_when_it_would_undermine_a_setting(self, env, sql):
        env.db._execute(f"UPDATE calendars SET {sql} WHERE id = 1")
        assert 'id="subscribe-link"' not in page(env)

    def test_hidden_on_password_protected_viewers(self, env):
        env.db._execute("UPDATE calendars SET viewer_password = ? WHERE id = 1", (generate_password_hash("pw"),))
        c = env.test_client()
        c.post("/view/alice/work/default", data={"pub_password": "pw"})
        assert 'id="subscribe-link"' not in c.get("/view/alice/work/default").get_data(as_text=True)


class TestPage:
    def test_has_error_recovery_search_and_accessibility_hooks(self, env):
        html = page(env)
        assert 'id="cal-error"' in html and 'role="alert"' in html and 'id="cal-error-retry"' in html
        assert 'id="list-search"' in html and 'aria-label="Search events"' in html
        assert 'aria-label="Previous period"' in html and 'aria-live="polite"' in html
        assert html.count('aria-pressed="false"') == 4

    def test_helper_script_is_served_and_referenced(self, env):
        assert "viewer-helpers.js" in page(env)
        r = env.test_client().get("/static/viewer-helpers.js")
        assert r.status_code == 200 and b"CCViewer" in r.data

    def test_no_inline_copies_of_the_shared_helpers_remain(self, env):
        html = page(env)
        assert "function escHtml" not in html and "function localDayKey" not in html and "toISOString().slice(0, 10)" not in html
