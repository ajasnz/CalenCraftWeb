import json
import sqlite3

import pytest
from werkzeug.security import generate_password_hash

from app.functions.link_redaction import (ID_PLACEHOLDER, PLACEHOLDER, SECRET_PLACEHOLDER, redact_event,
                                          redact_links, redact_secrets, redact_text)

PW = "correct-horse-battery"


class TestRedactLinks:
    @pytest.mark.parametrize("text,expected", [
        ("Join: https://us06web.zoom.us/j/86395996406?pwd=abc123&x=1", f"Join: {PLACEHOLDER}"),
        ("https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc/0?context=%7b%7d.", f"{PLACEHOLDER}."),
        ("Meet: meet.google.com/abc-defg-hij (dial in later)", f"Meet: {PLACEHOLDER} (dial in later)"),
        ("see www.example.com/page, then ask", f"see {PLACEHOLDER}, then ask"),
        ("zoommtg://zoom.us/join?confno=123", PLACEHOLDER),
        ("msteams://l/meetup-join/xyz", PLACEHOLDER),
        ("call tel:+6491234567 or mailto:bob@example.com", f"call {PLACEHOLDER} or {PLACEHOLDER}"),
        ("Details (https://a.b/c) thanks", f"Details ({PLACEHOLDER}) thanks"),
        ("ZOOM.US/J/123?PWD=ABC", PLACEHOLDER),
        ("HTTPS://EXAMPLE.COM/Path", PLACEHOLDER),
        ("Join at zoom.us/j/123456789 password 1234", f"Join at {PLACEHOLDER} password 1234"),
        ("a https://one.example/x and https://two.example/y!", f"a {PLACEHOLDER} and {PLACEHOLDER}!"),
        ("https://example.com:8443/a/b?c=d#frag", PLACEHOLDER),
        ("line one\nhttps://x.y/z\nline three", f"line one\n{PLACEHOLDER}\nline three"),
        ("<https://example.com/x>", f"<{PLACEHOLDER}>"[0:1] + PLACEHOLDER + ">"),
    ])
    def test_links_are_replaced_and_punctuation_survives(self, text, expected):
        assert redact_links(text) == expected

    @pytest.mark.parametrize("text", [
        "", "plain text", "alice@example.com", "e.g. bring a laptop", "Room 2.5, Level 3", "notes.txt and v2.0.1",
        "Level 3, 12 High St.", "3.14 pi", "Dr. Smith's office",
        "10.30am to 11.45am", "report.pdf", "U.S.A. rules",
    ])
    def test_ordinary_text_is_left_alone(self, text):
        assert redact_links(text) == text

    def test_non_strings_pass_through(self):
        assert redact_links(None) is None and redact_links(5) == 5

    def test_no_link_fragment_survives_even_in_long_urls(self):
        out = redact_links("x https://us06web.zoom.us/j/86395996406?pwd=SECRETPWD&tk=GTMKqHev6Hjp4Q64 y")
        assert out == f"x {PLACEHOLDER} y" and "SECRETPWD" not in out and "zoom" not in out


class TestSecrets:
    @pytest.mark.parametrize("text,expected", [
        ("Meeting ID: 863 9599 6406", f"Meeting ID: {ID_PLACEHOLDER}"),                 # 11 digits: nothing left showing
        ("Zoom ID 8639599640", f"Zoom ID {ID_PLACEHOLDER}"),
        ("meeting id:86395996406, thanks", f"meeting id:{ID_PLACEHOLDER}, thanks"),
        ("ID# 123-456-789", f"ID# {ID_PLACEHOLDER}"),
        ("Webinar ID 123 456 789 0000", f"Webinar ID {ID_PLACEHOLDER}"),
        ("Password: Hunter2", f"Password: {SECRET_PLACEHOLDER}"),
        ("password abc", f"password {SECRET_PLACEHOLDER}"),                              # no colon needed
        ("Password is abc123.", f"Password is {SECRET_PLACEHOLDER}."),
        ("Passcode: 123456 see you there", f"Passcode: {SECRET_PLACEHOLDER} see you there"),
        ("PASSPHRASE=correct-horse", f"PASSPHRASE={SECRET_PLACEHOLDER}"),
        ("PIN: 9921 (do not share)", f"PIN: {SECRET_PLACEHOLDER} (do not share)"),
        ("pin 4821", f"pin {SECRET_PLACEHOLDER}"),
        ("Pwd: x1y2", f"Pwd: {SECRET_PLACEHOLDER}"),
        ("passcode\n  s3cret", f"passcode\n  {SECRET_PLACEHOLDER}"),                     # value on the next line
    ])
    def test_ids_and_passwords_are_removed_keeping_the_label(self, text, expected):
        assert redact_secrets(text) == expected

    @pytest.mark.parametrize("text", [
        "This calendar is password protected.", "Password is required to join", "Please pin the poster to the wall",
        "Photo ID required", "forgot your password?", "ID 2 only", "Room 12, ID badge", "Update your password policy",
        "I did it", "Spinning wheel", "Bring ID", "the pin is optional",
    ])
    def test_ordinary_sentences_are_left_alone(self, text):
        assert redact_text(text) == text

    def test_no_digit_of_a_long_id_or_character_of_a_password_survives(self):
        out = redact_text("Meeting ID: 863 9599 6406 Passcode: S3cr3tPass Join https://zoom.us/j/86395996406?pwd=zzz")
        assert out == f"Meeting ID: {ID_PLACEHOLDER} Passcode: {SECRET_PLACEHOLDER} Join {PLACEHOLDER}"
        for leaked in ("863", "6406", "S3cr3t", "zzz", "zoom"):
            assert leaked not in out

    def test_a_password_that_is_itself_a_url_is_removed_once(self):
        assert redact_text("password: https://secret.example/x") == f"password: {SECRET_PLACEHOLDER}"

    ZOOM_INVITE = (
        "Sam Lee is inviting you to a scheduled Zoom meeting.\n\nJoin Zoom Meeting\nhttps://us06web.zoom.us/j/81234567890?pwd=AbCdEf123\n\n"
        "Meeting ID: 812 3456 7890\nPasscode: 765432\n\n---\n\nOne tap mobile\n"
        "+16465588656,,81234567890#,,,,*765432# US (New York)\n+13017158592,,81234567890#,,,,*765432# US (Washington DC)\n\n"
        "---\n\nJoin by SIP\n81234567890@zoomcrc.com\n\nJoin instructions\nhttps://us06web.zoom.us/meetings/81234567890/invitations?signature=xyz\n")

    def test_a_real_zoom_invite_leaves_no_trace_of_the_id_or_passcode_anywhere(self):
        out = redact_text(self.ZOOM_INVITE)
        for leaked in ("81234567890", "812 3456", "7890", "765432", "AbCdEf123", "zoom.us", "signature"):
            assert leaked not in out, (leaked, out)
        assert "Meeting ID: " + ID_PLACEHOLDER in out and "Passcode: " + SECRET_PLACEHOLDER in out
        assert f"+16465588656,,{ID_PLACEHOLDER}#,,,,*{SECRET_PLACEHOLDER}#" in out        # the repeats are removed too
        assert f"{ID_PLACEHOLDER}@zoomcrc.com" in out
        assert "One tap mobile" in out and "+16465588656" in out                         # the public dial-in number itself stays

    def test_repeat_scrubbing_does_not_touch_unrelated_numbers(self):
        out = redact_text("Meeting ID: 812 3456 7890. Room 4, level 12, phone 09 123 4567, year 2026, pwd: ab")
        assert "level 12" in out and "09 123 4567" in out and "2026" in out
        assert "81234567890" not in out and "pwd: ab" in out.replace(SECRET_PLACEHOLDER, "pwd: ab")   # 2-char values are not scrubbed elsewhere

    def test_same_id_in_different_groupings(self):
        out = redact_text("Meeting ID 863 9599 6406 or 863-9599-6406 or 86395996406")
        assert out == f"Meeting ID {ID_PLACEHOLDER} or {ID_PLACEHOLDER} or {ID_PLACEHOLDER}"

    def test_links_only_helper_still_leaves_ids_alone(self):
        assert redact_links("Meeting ID: 863 9599 6406") == "Meeting ID: 863 9599 6406"

    def test_non_strings(self):
        assert redact_secrets(None) is None and redact_text("") == ""


ICS = (
    "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//t//EN\r\n"
    "BEGIN:VEVENT\r\nUID:meet@x\r\nSUMMARY:Planning\r\nDTSTART:20261103T080000Z\r\nDTEND:20261103T090000Z\r\n"
    "LOCATION:https://us06web.zoom.us/j/111?pwd=LOCSECRET\r\n"
    "DESCRIPTION:Join https://teams.microsoft.com/l/meetup-join/DESCSECRET or zoom.us/j/222\\nMeeting ID: 863 9599 6406\\nPasscode: PASSSECRET pin 4821\r\n"
    "URL:https://example.com/URLSECRET\r\n"
    "X-GOOGLE-CONFERENCE:https://meet.google.com/CONFSECRET\r\n"
    "X-MICROSOFT-SKYPETEAMSMEETINGURL:https://teams.microsoft.com/TEAMSSECRET\r\n"
    "X-ALT-DESC;FMTTYPE=text/html:<a href=\"https://example.com/ALTSECRET\">join</a>\r\n"
    "ATTACH:https://files.example.com/ATTACHSECRET.pdf\r\n"
    "END:VEVENT\r\nEND:VCALENDAR\r\n"
)
SECRETS = ("LOCSECRET", "DESCSECRET", "URLSECRET", "CONFSECRET", "TEAMSSECRET", "ALTSECRET", "ATTACHSECRET", "PASSSECRET",
           "863 9599", "6406", "4821", "zoom.us", "teams.microsoft.com")
RANGE = "start=2026-11-01T00:00:00Z&end=2026-11-10T00:00:00Z"


@pytest.fixture
def env(tmp_path, monkeypatch):
    class R:
        text = ICS
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
    db._execute("UPDATE calendars SET share_viewer = 1, viewer_show_description = 1, viewer_show_location = 1 WHERE id = ?", (cid,))
    return app


def login(app, user="alice"):
    c = app.test_client()
    c.post("/login", data={"username": user, "password": PW})
    return c


def feed(app):
    # the sample events are from early November, so ask for that window explicitly (the default starts today)
    return app.test_client().get("/feed/alice/work/default?start=2026-11-01&end=2026-11-10").get_data(as_text=True)


def viewer_event(app):
    r = app.test_client().get(f"/view/alice/work/default/events.json?{RANGE}")
    assert r.status_code == 200
    return r.get_json()[0]


class TestFeedsAndViewer:
    def test_off_by_default_nothing_changes(self, env):
        body = feed(env).replace("\r\n ", "")
        assert "LOCSECRET" in body and "ATTACHSECRET" in body and "CONFSECRET" in body
        e = viewer_event(env)
        assert "zoom.us" in e["location"] and "teams.microsoft.com" in e["body"]

    def test_feed_loses_every_link_and_every_join_field(self, env):
        env.db.set_redact_links(1, True)
        body = feed(env).replace("\r\n ", "")                  # unfold long lines before searching
        for secret in SECRETS:
            assert secret not in body, secret
        assert PLACEHOLDER in body and ID_PLACEHOLDER in body and SECRET_PLACEHOLDER in body
        assert "Meeting ID:" in body and "Passcode:" in body    # the labels stay so people know something was removed
        for prop in ("URL:", "X-GOOGLE-CONFERENCE", "X-MICROSOFT-SKYPETEAMSMEETINGURL", "X-ALT-DESC", "ATTACH"):
            assert prop not in body.replace("DTSTART", "").replace("PRODID", "") or prop == "URL:" and "URL:" not in body, prop
        assert "SUMMARY:Planning" in body and "UID:meet@x" in body            # the event itself is intact

    def test_viewer_and_embed_data_lose_the_links(self, env):
        env.db.set_redact_links(1, True)
        e = viewer_event(env)
        assert e["location"] == PLACEHOLDER
        assert "teams.microsoft.com" not in e["body"] and "zoom.us" not in e["body"] and PLACEHOLDER in e["body"]
        assert "863" not in e["body"] and "PASSSECRET" not in e["body"] and "4821" not in e["body"]
        assert ID_PLACEHOLDER in e["body"] and SECRET_PLACEHOLDER in e["body"]

    def test_toggling_takes_effect_immediately_even_with_a_warm_cache(self, env):
        c = login(env)
        assert "LOCSECRET" in feed(env).replace("\r\n ", "")                    # warms the feed cache
        assert "zoom.us" in viewer_event(env)["location"]                       # and the viewer cache
        c.post("/calendars/work/privacy", data={"redact_links": "1"})
        assert "LOCSECRET" not in feed(env).replace("\r\n ", "") and viewer_event(env)["location"] == PLACEHOLDER
        c.post("/calendars/work/privacy", data={})                              # unchecked box sends nothing
        assert "LOCSECRET" in feed(env).replace("\r\n ", "") and "zoom.us" in viewer_event(env)["location"]

    def test_the_owners_preview_shows_what_subscribers_will_see(self, env):
        c = login(env)
        c.post("/calendars/work/privacy", data={"redact_links": "1"})
        events = c.get("/calendars/work/upcoming.json?days=90").get_json()["events"]
        assert all("zoom.us" not in (e.get("location") or "") for e in events)


class TestRedactEvent:
    def test_strips_listed_properties_and_rewrites_text_in_place(self):
        from icalendar import Event
        ev = Event()
        ev.add("summary", "Keep me")
        ev.add("location", "Room 3 or https://zoom.us/j/1")
        ev.add("description", "see www.example.com")
        ev.add("url", "https://example.com/x")
        ev.add("attach", "https://example.com/f.pdf")
        redact_event(ev)
        assert str(ev["SUMMARY"]) == "Keep me" and str(ev["LOCATION"]) == f"Room 3 or {PLACEHOLDER}"
        assert str(ev["DESCRIPTION"]) == f"see {PLACEHOLDER}" and "URL" not in ev and "ATTACH" not in ev

    def test_ids_and_passwords_in_the_location_are_removed_too(self):
        from icalendar import Event
        ev = Event()
        ev.add("location", "Room 3, Meeting ID 123 456 789 pwd hunter2")
        redact_event(ev)
        assert str(ev["LOCATION"]) == f"Room 3, Meeting ID {ID_PLACEHOLDER} pwd {SECRET_PLACEHOLDER}"


class TestSetting:
    def test_saved_audited_and_scoped_to_the_owner(self, env):
        c = login(env)
        r = c.post("/calendars/work/privacy", data={"redact_links": "1"})
        assert r.status_code == 302 and r.headers["Location"].endswith("tab=advanced#privacy")
        assert env.db.get_calendar_by_slug("alice", "work")["redact_links"] == 1
        assert "calendar.privacy_changed" in [x["action"] for x in env.db._fetchall("SELECT action FROM audit_log")]
        assert login(env, "bob").post("/calendars/work/privacy", data={"redact_links": "0"}).status_code == 404
        assert env.test_client().post("/calendars/work/privacy", data={}).status_code == 302          # login required
        assert env.db.get_calendar_by_slug("alice", "work")["redact_links"] == 1

    def test_card_is_on_the_advanced_tab_and_reflects_state(self, env):
        c = login(env)
        html = c.get("/calendars/work?tab=advanced").get_data(as_text=True)
        assert 'id="privacy"' in html and "Redact links, meeting IDs and passwords" in html and "checked" not in html.split('id="privacy"')[1].split("</form>")[0]
        env.db.set_redact_links(1, True)
        assert "checked" in c.get("/calendars/work?tab=advanced").get_data(as_text=True).split('id="privacy"')[1].split("</form>")[0]

    def test_survives_export_import_and_duplicate(self, env):
        c = login(env)
        c.post("/calendars/work/privacy", data={"redact_links": "1"})
        exported = c.get("/calendars/work/export.json").get_json()
        assert exported["calendar"]["redact_links"] == 1
        c.post("/calendars/import/confirm", data={"payload": json.dumps(exported)})
        c.post("/calendars/work/duplicate")
        slugs = [x["slug"] for x in env.db.get_calendars(1) if x["slug"] != "work"]
        assert len(slugs) == 2
        for slug in slugs:
            assert env.db.get_calendar_by_slug("alice", slug)["redact_links"] == 1         # never silently re-exposes links


def test_migration_11_upgrades_a_v10_database_with_the_setting_off(tmp_path, monkeypatch):
    import app.functions.db.db_migrations as m
    from app.classes.db import CalendarDB
    path = str(tmp_path / "old.db")
    monkeypatch.setenv("DB_PATH", path)
    monkeypatch.setenv("APP_VERSION", "0")
    conn = sqlite3.connect(path)
    for v in range(0, 11):
        conn.executescript(m.migrations[v]["sql"])
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('db_version', 10)")
    conn.execute("INSERT INTO users (slug, username, password, display_name) VALUES ('a','a','x','A')")
    conn.execute("INSERT INTO calendars (user_id, slug, title) VALUES (1, 'keep', 'Keep')")
    conn.commit()
    conn.close()
    d = CalendarDB()
    d.create_and_initialize()
    cal = d.get_calendar_by_slug("a", "keep")
    assert cal["title"] == "Keep" and cal["redact_links"] == 0              # existing calendars behave exactly as before
