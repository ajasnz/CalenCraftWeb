import os
import pytest
from werkzeug.security import generate_password_hash
from app.classes.db import CalendarDB


@pytest.fixture
def db(tmp_path):
    os.environ["DB_PATH"] = str(tmp_path / "test.db")
    os.environ["APP_VERSION"] = "0"
    d = CalendarDB()
    d.create_and_initialize()
    return d


def seed(db):
    uid = db.create_user("alice", "alice", None, generate_password_hash("pw"), "Alice")
    cid = db.create_calendar(uid, "my-cal", "My Cal")
    sid = db.create_source(uid, "https://example.com/cal.ics", "Example")
    db.link_source(cid, sid)
    vid = db.create_view(cid, "work", "Work")
    return uid, cid, sid, vid


class TestUsers:
    def test_create_and_fetch(self, db):
        db.create_user("bob", "bob", "bob@example.com", generate_password_hash("pw"), "Bob")
        user = db.get_user_by_username("bob")
        assert user["username"] == "bob"
        assert user["slug"] == "bob"

    def test_get_users_list(self, db):
        db.create_user("u1", "u1", None, "h", "U1")
        db.create_user("u2", "u2", None, "h", "U2")
        users = db.get_users()
        assert len(users) == 2


class TestCalendars:
    def test_create_and_fetch(self, db):
        uid, cid, sid, vid = seed(db)
        cals = db.get_calendars(uid)
        assert len(cals) == 1
        assert cals[0]["slug"] == "my-cal"

    def test_get_by_slug(self, db):
        uid, cid, sid, vid = seed(db)
        cal = db.get_calendar_by_slug("alice", "my-cal")
        assert cal is not None
        assert cal["id"] == cid


class TestSources:
    def test_sources_for_calendar(self, db):
        uid, cid, sid, vid = seed(db)
        sources = db.get_sources(cid)
        assert len(sources) == 1
        assert sources[0]["url"] == "https://example.com/cal.ics"

    def test_unlink_source(self, db):
        uid, cid, sid, vid = seed(db)
        db.unlink_source(cid, sid)
        assert db.get_sources(cid) == []


class TestFetchMethods:
    def test_fetch_source_urls(self, db):
        uid, cid, sid, vid = seed(db)
        urls = db.fetch_source_urls("alice", "my-cal")
        assert len(urls) == 1
        assert urls[0][1] == "https://example.com/cal.ics"

    def test_fetch_rules_for_calendar_empty(self, db):
        uid, cid, sid, vid = seed(db)
        rules = db.fetch_rules_for_calendar("alice", "my-cal")
        assert rules == []

    def test_fetch_filters_for_calendar_empty(self, db):
        uid, cid, sid, vid = seed(db)
        filters = db.fetch_filters_for_calendar("alice", "my-cal")
        assert filters == []

    def test_create_and_fetch_rule(self, db):
        uid, cid, sid, vid = seed(db)
        db.create_rule(cid, None, None, 100, "SUMMARY", "contains", "Meeting",
                       "include", 0, "SUMMARY", "append", " [WORK]")
        rules = db.fetch_rules_for_calendar("alice", "my-cal")
        assert len(rules) == 1
        assert rules[0]["filter_pattern"] == "Meeting"

    def test_create_and_fetch_filter(self, db):
        uid, cid, sid, vid = seed(db)
        db.create_filter(cid, None, None, 100, "SUMMARY", "contains", "OOO", "exclude", 0)
        filters = db.fetch_filters_for_calendar("alice", "my-cal")
        assert len(filters) == 1
        assert filters[0]["action"] == "exclude"
