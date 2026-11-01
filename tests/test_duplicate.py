from tests.test_db import db, seed  # noqa: F401


def test_duplicate_calendar_copies_everything_independently(db):
    uid, cid, sid, vid = seed(db)
    db.create_rule(cid, vid, sid, 10, "*", "contains", "", "include", 0, "SUMMARY", "append", " [x]")
    db.create_filter(cid, vid, sid, 10, "SUMMARY", "contains", "ok", "include", 0)

    new_id = db.duplicate_calendar(cid, "my-cal-copy", "My Cal (copy)")

    assert new_id != cid
    new_sources = db.get_sources(new_id)
    assert len(new_sources) == 1 and new_sources[0]["id"] != sid
    assert new_sources[0]["url"] == "https://example.com/cal.ics"

    new_views = db.get_views(new_id)
    assert [v["slug"] for v in new_views] == ["work"] and new_views[0]["id"] != vid

    rules = db.get_rules("alice", "my-cal-copy")
    filters = db.get_filters("alice", "my-cal-copy")
    assert len(rules) == 1 and rules[0]["view_id"] == new_views[0]["id"]
    assert rules[0]["source_id"] == new_sources[0]["id"]
    assert len(filters) == 1 and filters[0]["source_id"] == new_sources[0]["id"]

    # original untouched
    assert len(db.get_rules("alice", "my-cal")) == 1
    assert db.get_sources(cid)[0]["id"] == sid
