import io
import json
import pytest
from werkzeug.security import generate_password_hash


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("APP_VERSION", "1000")
    monkeypatch.setenv("SECRET_KEY", "x")
    from app.server import create_app
    a = create_app()
    a.db.create_user("u", "u", None, generate_password_hash("pw12345678"), "U", is_admin=1)
    return a


@pytest.fixture
def client(app):
    c = app.test_client()
    c.post("/login", data={"username": "u", "password": "pw12345678"})
    return c


def sample(**over):
    d = {
        "calencraft_export": 1,
        "calendar": {"title": "Imported", "share_ics": 1, "share_viewer": 0, "viewer_color": "#112233",
                     "expansion_days_ahead": 90, "expansion_days_behind": 7, "default_view_enabled": 1},
        "sources": [{"name": "Ex", "url": "https://example.com/a.ics", "enabled": True}],
        "views": [{"slug": "team", "title": "Team", "viewer_title": "Team view"}],
        "filters": [{"view": None, "source": "Ex", "priority": 5, "property": "SUMMARY", "type": "contains",
                     "pattern": "ok", "action": "include", "include_if_fails": 0}],
        "rules": [{"view": "team", "source": None, "priority": 10, "filter_property": "*", "filter_type": "contains",
                   "filter_pattern": "", "filter_action": "include", "include_if_fails": 0,
                   "target_property": "SUMMARY", "action": "append", "target_value": " [x]"}],
    }
    d.update(over)
    return d


def upload(client, data, path="/calendars/import"):
    body = data if isinstance(data, str) else json.dumps(data)
    return client.post(path, data={"file": (io.BytesIO(body.encode()), "x.json")},
                       content_type="multipart/form-data")


def validate(app, data):
    from app.routes import ui
    with app.test_request_context():
        return ui._validate_uploaded_config(json.dumps(data))


class TestValidation:
    def test_valid_file_is_accepted_and_cleaned(self, app):
        clean, warnings, errors = validate(app, sample())
        assert errors == [] and clean["title"] == "Imported"
        assert clean["calendar"]["viewer_color"] == "#112233"
        assert clean["views"][0]["overrides"] == {"viewer_title": "Team view"}
        assert clean["rules"][0]["view"] == "team" and clean["filters"][0]["source"] == "Ex"

    @pytest.mark.parametrize("mutate,needle", [
        (lambda d: d.update(calencraft_export=2), "CalenCraft export"),
        (lambda d: d["sources"][0].update(url="http://localhost/x.ics"), "Private or loopback"),
        (lambda d: d["sources"][0].update(url="file:///etc/passwd"), "http or https"),
        (lambda d: d["filters"][0].update(type="evil"), "unknown match type"),
        (lambda d: d["filters"][0].update(type="regex", pattern="(unclosed"), "regular expression"),
        (lambda d: d["filters"][0].update(action="nuke"), "must be one of"),
        (lambda d: d["rules"][0].update(action="exec"), "must be one of"),
        (lambda d: d["rules"][0].update(target_property="a b; DROP"), "invalid property"),
        (lambda d: d["rules"][0].update(view="ghost"), "unknown view"),
        (lambda d: d["filters"][0].update(source="ghost"), "unknown source"),
        (lambda d: d["calendar"].update(title=""), "must not be empty"),
        (lambda d: d.update(sources=[{"url": "https://e.com/a.ics"}] * 51), "at most 50"),
    ])
    def test_rejects_bad_input(self, app, mutate, needle):
        d = sample()
        mutate(d)
        clean, _w, errors = validate(app, d)
        assert clean is None and any(needle in e for e in errors), errors

    def test_hostile_values_are_neutralised_not_trusted(self, app):
        d = sample()
        d["calendar"].update(viewer_color="red; background:url(x)", expansion_days_ahead=10**9,
                             **{"id": 99, "user_id": 7, "viewer_password": "pwn"})
        clean, _w, errors = validate(app, d)
        assert errors == []
        assert clean["calendar"]["viewer_color"] == "#4361ee"           # falls back to the safe default
        assert clean["calendar"]["expansion_days_ahead"] == 3650        # clamped
        assert not {"id", "user_id", "viewer_password"} & set(clean["calendar"])

    def test_default_and_duplicate_views_are_skipped_with_warning(self, app):
        d = sample(views=[{"slug": "default", "title": "x"}, {"slug": "a", "title": "A"}, {"slug": "a", "title": "A2"}])
        d["rules"] = []
        clean, warnings, errors = validate(app, d)
        assert errors == [] and [v["slug"] for v in clean["views"]] == ["a"] and len(warnings) == 2

    def test_viewer_on_is_flagged(self, app):
        d = sample()
        d["calendar"]["share_viewer"] = 1
        _c, warnings, _e = validate(app, d)
        assert any("public web viewer" in w for w in warnings)


class TestFlow:
    def test_preview_then_confirm_creates_everything(self, app, client):
        r = upload(client, sample())
        assert r.status_code == 200 and b"Will be created as" in r.data and b"example.com/a.ics" in r.data
        assert app.db.get_calendars(1) == []                      # preview creates nothing

        r = client.post("/calendars/import/confirm", data={"payload": json.dumps(sample())})
        assert r.status_code == 302 and r.headers["Location"].endswith("/calendars/imported")
        cal = app.db.get_calendar_by_slug("u", "imported")
        assert cal["viewer_color"] == "#112233" and cal["expansion_days_ahead"] == 90
        srcs = app.db.get_sources(cal["id"])
        views = app.db.get_views(cal["id"])
        assert [s["name"] for s in srcs] == ["Ex"] and [v["slug"] for v in views] == ["team"]
        assert app.db._fetchone("SELECT viewer_title FROM views WHERE id = ?", (views[0]["id"],))["viewer_title"] == "Team view"
        rules, filters = app.db.get_rules("u", "imported"), app.db.get_filters("u", "imported")
        assert rules[0]["view_id"] == views[0]["id"] and filters[0]["source_id"] == srcs[0]["id"]

    def test_never_overwrites_existing_calendar(self, app, client):
        client.post("/calendars/import/confirm", data={"payload": json.dumps(sample())})
        client.post("/calendars/import/confirm", data={"payload": json.dumps(sample())})
        client.post("/calendars/import/confirm", data={"payload": json.dumps(sample())})
        slugs = sorted(c["slug"] for c in app.db.get_calendars(1))
        assert slugs == ["imported", "imported-imported", "imported-imported-2"]

    def test_failed_import_rolls_back(self, app, client, monkeypatch):
        orig = app.db.__class__._execute

        def boom(self, sql, params=(), commit=True):
            if "INTO rules" in sql:
                raise RuntimeError("disk on fire")
            return orig(self, sql, params, commit)
        monkeypatch.setattr(app.db.__class__, "_execute", boom)
        r = client.post("/calendars/import/confirm", data={"payload": json.dumps(sample())})
        monkeypatch.setattr(app.db.__class__, "_execute", orig)
        assert r.status_code == 302 and "/calendars/new" in r.headers["Location"]
        assert app.db.get_calendars(1) == []
        assert app.db._fetchall("SELECT id FROM sources") == []          # nothing half-created

    def test_bad_json_and_oversize_and_missing_file(self, app, client):
        assert b"valid JSON" in upload(client, "{not json").data
        r = client.post("/calendars/import", data={}, content_type="multipart/form-data")
        assert r.status_code == 302
        from app.functions import config_import as ci
        r = upload(client, "x" * (ci.MAX_BYTES + 10))
        assert r.status_code == 302

    def test_requires_login(self, app):
        c = app.test_client()
        assert upload(c, sample()).status_code == 302
        assert c.post("/calendars/import/confirm", data={"payload": json.dumps(sample())}).status_code == 302
        assert app.db.get_calendars(1) == []

    def test_export_import_round_trip(self, app, client):
        from app.routes.ui import build_calendar_export
        db = app.db
        client.post("/calendars/import/confirm", data={"payload": json.dumps(sample())})
        cal = db.get_calendar_by_slug("u", "imported")
        exported = build_calendar_export(db, "u", cal)
        r = client.post("/calendars/import/confirm", data={"payload": json.dumps(exported)})
        assert r.status_code == 302
        copy = [c for c in db.get_calendars(1) if c["slug"] != "imported"][0]
        again = build_calendar_export(db, "u", db.get_calendar_by_slug("u", copy["slug"]))
        for key in ("sources", "views", "filters", "rules"):
            assert again[key] == exported[key], key
