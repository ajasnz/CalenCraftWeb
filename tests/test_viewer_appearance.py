import json
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

from app.functions import viewer_appearance as va

PW = "correct-horse-battery"


class TestValidation:
    def test_defaults_reproduce_the_previous_viewer(self):
        o = va.view_options({})                               # a config from before the migration has none of the keys
        assert o == {"views": ["month", "week", "day", "list"], "defaultView": "auto", "weekStart": 1, "time12": False,
                     "hideWeekends": False, "hourStart": 0, "hourEnd": 24, "theme": "auto"}

    def test_valid_values_are_cleaned(self):
        v, errors = va.clean_values({
            "viewer_views": ["list", "week"], "viewer_default_view": "list", "viewer_week_start": "0",
            "viewer_time_format": "12h", "viewer_hide_weekends": "on", "viewer_hour_start": "8", "viewer_hour_end": "18",
            "viewer_theme": "dark", "viewer_footer": "  Questions? hello@example.com  "})
        assert errors == []
        assert v == {"viewer_views": "week,list", "viewer_default_view": "list", "viewer_week_start": 0,
                     "viewer_time_format": "12h", "viewer_hide_weekends": 1, "viewer_hour_start": 8, "viewer_hour_end": 18,
                     "viewer_theme": "dark", "viewer_footer": "Questions? hello@example.com"}

    @pytest.mark.parametrize("raw,needle", [
        ({"viewer_views": []}, "at least one view"),
        ({"viewer_views": ["month"], "viewer_default_view": "list"}, "one of the views you offer"),
        ({"viewer_default_view": "agenda"}, "Unknown default view"),
        ({"viewer_week_start": "3"}, "Sunday or Monday"),
        ({"viewer_time_format": "military"}, "12-hour or 24-hour"),
        ({"viewer_hour_start": "18", "viewer_hour_end": "8"}, "Visible hours"),
        ({"viewer_hour_start": "-1", "viewer_hour_end": "24"}, "Visible hours"),
        ({"viewer_hour_start": "abc"}, None),                 # junk falls back to the default, no crash
        ({"viewer_theme": "neon"}, "Theme must be"),
        ({"viewer_footer": "x" * 301}, "at most 300"),
    ])
    def test_rejects_bad_input(self, raw, needle):
        values, errors = va.clean_values(raw)
        if needle:
            assert any(needle in e for e in errors), errors
        else:
            assert errors == [] and values["viewer_hour_start"] == 0

    def test_only_supplied_fields_are_returned_so_old_files_leave_defaults_alone(self):
        assert va.clean_values({}) == ({}, [])
        assert va.clean_values({"viewer_theme": "light"})[0] == {"viewer_theme": "light"}

    def test_readable_text_on_any_accent(self):
        assert va.ink_for("#00a9ff") == "#0c2340"             # light blue: white text was hard to read
        assert va.ink_for("#ffff00") == "#0c2340"
        assert va.ink_for("#0c2340") == "#ffffff" and va.ink_for("#b5304f") == "#ffffff"
        assert va.ink_for("nonsense") == "#ffffff"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("APP_VERSION", "1000")
    monkeypatch.setenv("SECRET_KEY", "x")
    from app.server import create_app
    a = create_app()
    uid = a.db.create_user("alice", "alice", None, generate_password_hash(PW), "Alice", is_admin=1)
    a.db.create_user("bob", "bob", None, generate_password_hash(PW), "Bob")
    cid = a.db.create_calendar(uid, "work", "Work")
    a.db._execute("UPDATE calendars SET share_viewer = 1 WHERE id = ?", (cid,))
    return a


def login(app, user="alice"):
    c = app.test_client()
    c.post("/login", data={"username": user, "password": PW})
    return c


FORM = {"default_view": "week", "week_start": "0", "time_format": "12h", "hour_start": "8", "hour_end": "18",
        "theme": "dark", "footer": "Visit https://example.com <script>alert(1)</script>", "hide_weekends": "1"}


def save(client, views=("week", "list"), **over):
    data = {**FORM, **over}
    data["views"] = list(views)
    return client.post("/calendars/work/appearance", data=data)


class TestRoute:
    def test_save_persists_and_is_audited(self, app):
        r = save(login(app))
        assert r.status_code == 302 and r.headers["Location"].endswith("tab=sharing#appearance")
        cal = app.db.get_calendar_by_slug("alice", "work")
        assert (cal["viewer_views"], cal["viewer_default_view"], cal["viewer_week_start"], cal["viewer_time_format"]) == ("week,list", "week", 0, "12h")
        assert (cal["viewer_hour_start"], cal["viewer_hour_end"], cal["viewer_theme"], cal["viewer_hide_weekends"]) == (8, 18, "dark", 1)
        assert "calendar.appearance_changed" in [r["action"] for r in app.db._fetchall("SELECT action FROM audit_log")]

    def test_invalid_input_changes_nothing(self, app):
        before = dict(app.db.get_calendar_by_slug("alice", "work"))
        save(login(app), views=(), theme="neon")
        assert dict(app.db.get_calendar_by_slug("alice", "work")) == before

    def test_requires_login_and_ownership(self, app):
        assert save(app.test_client()).status_code == 302
        assert save(login(app, "bob")).status_code == 404
        assert app.db.get_calendar_by_slug("alice", "work")["viewer_theme"] == "auto"

    def test_settings_card_renders_current_values(self, app):
        c = login(app)
        save(c)
        html = c.get("/calendars/work?tab=sharing").get_data(as_text=True)
        assert 'id="appearance"' in html and '<option value="dark" selected>' in html and 'value="8"' in html


class TestViewerReflectsSettings:
    def test_default_page_is_unchanged(self, app):
        html = app.test_client().get("/view/alice/work/default").get_data(as_text=True)
        assert '<html lang="en">' in html                      # no forced theme attribute by default
        assert '"views": ["month", "week", "day", "list"]' in html and '"theme": "auto"' in html
        assert " hidden>" not in html.split("view-toggle")[1].split("</div>")[0]       # all four view buttons visible

    def test_every_setting_reaches_the_page_and_embed(self, app):
        save(login(app))
        for path in ("/view/alice/work/default", "/embed/alice/work/default"):
            html = app.test_client().get(path).get_data(as_text=True)
            assert 'data-theme="dark"' in html.split("<body")[0]
            assert '"time12": true' in html and '"weekStart": 0' in html and '"hideWeekends": true' in html
            assert '"hourStart": 8' in html and '"hourEnd": 18' in html
            assert ':root[data-theme="dark"]' in html                                     # forced-dark rules are emitted

    def test_offered_views_and_default(self, app):
        save(login(app))
        html = app.test_client().get("/view/alice/work/default").get_data(as_text=True)
        toggle = html.split('role="group" aria-label="Calendar view"')[1].split("</div>")[0]
        assert 'data-view="month" aria-pressed="false" hidden' in toggle and 'data-view="day" aria-pressed="false" hidden' in toggle
        assert 'data-view="week" aria-pressed="false">' in toggle and 'data-view="list" aria-pressed="false">' in toggle
        assert '"defaultView": "week"' in html

    def test_single_view_hides_the_whole_toggle(self, app):
        save(login(app), views=("list",), default_view="auto")
        html = app.test_client().get("/view/alice/work/default").get_data(as_text=True)
        assert 'class="view-toggle" role="group" aria-label="Calendar view" hidden>' in html

    def test_footer_is_escaped_and_links_are_safe(self, app):
        save(login(app))
        html = app.test_client().get("/view/alice/work/default").get_data(as_text=True)
        note = html.split('class="pub-note">')[1].split("</p>")[0]
        assert "&lt;script&gt;" in note and "<script>alert" not in note
        assert 'href="https://example.com"' in note and 'rel="noopener noreferrer"' in note and 'target="_blank"' in note

    def test_active_button_text_is_readable_on_a_light_accent(self, app):
        app.db._execute("UPDATE calendars SET viewer_color = '#00a9ff' WHERE id = 1")
        html = app.test_client().get("/view/alice/work/default").get_data(as_text=True)
        assert "color: #0c2340; }" in html.split(".view-btn.active")[1][:120]

    def test_css_selectors_are_not_html_escaped(self, app):
        html = app.test_client().get("/view/alice/work/default").get_data(as_text=True)
        assert "&#34;" not in html.split("</style>")[0]       # autoescaping once turned [data-theme="dark"] into [data-theme=&#34;dark&#34;]
        assert ':root[data-theme="dark"] #cal-loading' in html

    def test_forced_light_stops_the_os_dark_rules(self, app):
        save(login(app), theme="light")
        html = app.test_client().get("/view/alice/work/default").get_data(as_text=True)
        assert 'data-theme="light"' in html.split("<body")[0] and ':root:not([data-theme="light"])' in html


class TestBackupsAndCopies:
    def test_export_import_round_trip_carries_appearance(self, app):
        c = login(app)
        save(c)
        exported = c.get("/calendars/work/export.json").get_json()
        assert exported["calendar"]["viewer_theme"] == "dark" and exported["calendar"]["viewer_hour_end"] == 18
        r = c.post("/calendars/import/confirm", data={"payload": json.dumps(exported)})
        assert r.status_code == 302
        copy = [x for x in app.db.get_calendars(1) if x["slug"] != "work"][0]
        for k in va.COLUMNS:
            assert copy[k] == app.db.get_calendar_by_slug("alice", "work")[k], k

    def test_old_exports_without_appearance_import_with_defaults(self, app):
        c = login(app)
        payload = {"calencraft_export": 1, "calendar": {"title": "Old"}, "sources": [], "views": [], "filters": [], "rules": []}
        c.post("/calendars/import/confirm", data={"payload": json.dumps(payload)})
        cal = app.db.get_calendar_by_slug("alice", "old")
        assert (cal["viewer_theme"], cal["viewer_views"], cal["viewer_hour_end"], cal["viewer_week_start"]) == ("auto", "month,week,day,list", 24, 1)

    def test_a_bad_appearance_in_a_backup_is_rejected(self, app):
        c = login(app)
        payload = {"calencraft_export": 1, "calendar": {"title": "Bad", "viewer_theme": "neon", "viewer_hour_start": 20, "viewer_hour_end": 5},
                   "sources": [], "views": [], "filters": [], "rules": []}
        r = c.post("/calendars/import/confirm", data={"payload": json.dumps(payload)})
        assert r.status_code == 400 and app.db.get_calendar_by_slug("alice", "bad") is None

    def test_duplicate_copies_appearance(self, app):
        c = login(app)
        save(c)
        c.post("/calendars/work/duplicate")
        copy = app.db.get_calendar_by_slug("alice", "work-copy")
        assert copy["viewer_theme"] == "dark" and copy["viewer_views"] == "week,list"


def test_migration_10_upgrades_a_v9_database_and_changes_nothing_visible(tmp_path, monkeypatch):
    import app.functions.db.db_migrations as m
    from app.classes.db import CalendarDB
    path = str(tmp_path / "old.db")
    monkeypatch.setenv("DB_PATH", path)
    monkeypatch.setenv("APP_VERSION", "0")
    conn = sqlite3.connect(path)
    for v in range(0, 10):
        conn.executescript(m.migrations[v]["sql"])
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('db_version', 9)")
    conn.execute("INSERT INTO users (slug, username, password, display_name) VALUES ('a','a','x','A')")
    conn.execute("INSERT INTO calendars (user_id, slug, title, share_viewer) VALUES (1, 'keep', 'Keep', 1)")
    conn.commit()
    conn.close()
    d = CalendarDB()
    d.create_and_initialize()
    cal = d.get_calendar_by_slug("a", "keep")
    assert cal["title"] == "Keep" and cal["share_viewer"] == 1
    assert va.view_options(cal) == va.view_options({})         # an existing calendar looks exactly as it did before
    assert cal["viewer_footer"] is None
