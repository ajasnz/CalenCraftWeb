import json
import os
import shutil
import subprocess

import pytest
from werkzeug.security import generate_password_hash

ROOT = os.path.join(os.path.dirname(__file__), "..")
STATIC = os.path.join(ROOT, "app", "static")
NODE = shutil.which("node")


def run_node(snippet):
    code = (f"const H = require({json.dumps(os.path.abspath(os.path.join(STATIC, 'viewer-helpers.js')))});"
            f"globalThis.CCViewer = H;"
            f"const P = require({json.dumps(os.path.abspath(os.path.join(STATIC, 'viewer-popup.js')))});"
            f"const out = ({snippet}); console.log(JSON.stringify(out));")
    r = subprocess.run([NODE, "-e", code], capture_output=True, text=True, encoding="utf-8", timeout=30,
                       env={**os.environ, "TZ": "Pacific/Auckland"})
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


FMT = "(d) => String(d.getHours()).padStart(2,'0') + ':' + String(d.getMinutes()).padStart(2,'0')"


@pytest.mark.skipif(not NODE, reason="node is not installed")
class TestRender:
    def test_shows_what_visitors_need_and_nothing_editable(self):
        html = run_node("P.render({id:'e1', title:'Maker night', start:'2026-11-18T06:00:00Z', end:'2026-11-18T08:00:00Z', "
                        "location:'TAP lab, Auckland', body:'Bring a project'}, " + FMT + ")")
        assert "Maker night" in html and "Wednesday, 18 November 2026" in html and "19:00–21:00" in html     # shown in the visitor's zone
        assert "TAP lab, Auckland" in html and "Bring a project" in html
        assert 'data-act="ics"' in html and "calendar.google.com" in html and 'aria-label="Close"' in html
        assert 'role' not in html                                   # the role/aria-modal live on the wrapper the DOM code adds
        lowered = html.lower()
        assert "edit" not in lowered and "delete" not in lowered and "remove" not in lowered

    def test_everything_is_escaped_and_only_http_becomes_a_link(self):
        html = run_node("P.render({id:'x', title:'<img src=x onerror=alert(1)>', start:'2026-11-18T06:00:00Z', end:'2026-11-18T07:00:00Z', "
                        "location:'javascript:alert(1)', body:'see https://example.com/a?b=1&c=2 <script>x</script>'}, " + FMT + ")")
        assert "<img" not in html and "&lt;img" in html and "<script>" not in html and "&lt;script&gt;" in html
        assert '<a href="javascript' not in html
        assert '<a href="https://example.com/a?b=1&amp;c=2"' in html

    def test_all_day_and_multi_day_ranges(self):
        one = run_node("P.whenText({start:'2026-11-05T00:00:00', end:'2026-11-06T00:00:00', isAllday:true}, " + FMT + ")")
        assert one == "Thursday, 5 November 2026"                    # the exclusive end date must not leak into the text
        span = run_node("P.whenText({start:'2026-11-05T00:00:00', end:'2026-11-08T00:00:00', isAllday:true}, " + FMT + ")")
        assert span == "Thursday, 5 November 2026 – Saturday, 7 November 2026"
        timed = run_node("P.whenText({start:'2026-11-05T20:00:00', end:'2026-11-06T02:00:00'}, " + FMT + ")")
        assert timed == "Thursday, 5 November 2026 20:00 – Friday, 6 November 2026 02:00"

    def test_untitled_and_untimed_events_still_render(self):
        html = run_node("P.render({id:'u', start:'2026-11-18T06:00:00Z'}, " + FMT + ")")
        assert ">Busy<" in html and "19:00" in html

    def test_finds_the_original_payload_for_a_library_event(self):
        got = run_node("P.fromTui({id:'a', title:'T', start:{toDate:()=>new Date('2026-11-18T06:00:00Z')}}, "
                       "[{id:'a', start:'2026-11-17T06:00:00Z', title:'other day'}, {id:'a', start:'2026-11-18T06:00:00Z', title:'right one', isAllday:false}])")
        assert got["title"] == "right one"                          # same UID, different occurrence: match by time too
        fallback = run_node("P.fromTui({id:'z', title:'Lib', start:{toDate:()=>new Date('2026-11-18T06:00:00Z')}, end:{toDate:()=>new Date('2026-11-18T07:00:00Z')}}, [])")
        assert fallback["title"] == "Lib" and fallback["start"] == "2026-11-18T06:00:00.000Z"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("APP_VERSION", "1000")
    monkeypatch.setenv("SECRET_KEY", "x")
    from app.server import create_app
    a = create_app()
    uid = a.db.create_user("alice", "alice", None, generate_password_hash("pw12345678"), "Alice", is_admin=1)
    cid = a.db.create_calendar(uid, "work", "Work")
    a.db._execute("UPDATE calendars SET share_viewer = 1 WHERE id = ?", (cid,))
    return a


@pytest.mark.parametrize("path", ["/view/alice/work/default", "/embed/alice/work/default"])
class TestPages:
    def test_use_the_read_only_dialog_instead_of_the_library_popup(self, app, path):
        html = app.test_client().get(path).get_data(as_text=True)
        assert "useDetailPopup: false" in html and "useDetailPopup: true" not in html
        assert "popupEdit" not in html and "popupDelete" not in html           # the Edit/Delete workaround is gone, not just hidden
        assert "viewer-popup.js" in html and "viewer-popup.css" in html and "viewer-helpers.js" in html
        assert "CCPopup.open(" in html and "clickEvent" in html

    def test_script_order_helpers_before_popup_before_page_code(self, app, path):
        html = app.test_client().get(path).get_data(as_text=True)
        assert html.index("viewer-helpers.js") < html.index("viewer-popup.js") < html.index("new tui.Calendar")


def test_static_assets_are_served(app):
    c = app.test_client()
    js, css = c.get("/static/viewer-popup.js"), c.get("/static/viewer-popup.css")
    assert js.status_code == 200 and b"CCPopup" in js.data
    assert css.status_code == 200 and b".cc-pop-overlay" in css.data


def test_dialog_css_cannot_leave_the_viewport():
    css = open(os.path.join(STATIC, "viewer-popup.css"), encoding="utf-8").read()
    assert "position: fixed; inset: 0" in css                     # anchored to the screen, not to the clicked event
    assert "max-height: min(85vh, 100%)" in css and "overflow-y: auto" in css
    assert "@media (max-width: 600px)" in css and "align-items: flex-end" in css      # bottom sheet on phones
