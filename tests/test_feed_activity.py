import os
import pytest
from werkzeug.security import generate_password_hash
from app.classes.db import CalendarDB


@pytest.fixture
def db(tmp_path):
    os.environ["DB_PATH"] = str(tmp_path / "t.db")
    os.environ["APP_VERSION"] = "0"
    d = CalendarDB()
    d.create_and_initialize()
    return d


def test_record_feed_hit_increments_and_stamps(db):
    uid = db.create_user("a", "a", None, generate_password_hash("pw"), "A")
    cid = db.create_calendar(uid, "c", "C")
    cal = db.get_calendar_by_slug("a", "c")
    assert cal["feed_hits"] == 0 and cal["last_feed_at"] is None

    db.record_feed_hit(cid, "2026-01-02T03:04:05")
    db.record_feed_hit(cid, "2026-01-02T03:05:00")
    cal = db.get_calendar_by_slug("a", "c")
    assert cal["feed_hits"] == 2
    assert cal["last_feed_at"] == "2026-01-02T03:05:00"


def test_migration_4_is_applied_to_existing_db(tmp_path):
    """An existing DB at version 3 must upgrade in place without data loss."""
    import sqlite3
    import app.functions.db.db_migrations as m
    path = str(tmp_path / "old.db")
    os.environ["DB_PATH"] = path
    os.environ["APP_VERSION"] = "0"
    conn = sqlite3.connect(path)
    for v in range(0, 4):
        conn.executescript(m.migrations[v]["sql"])
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('db_version', 3)")
    conn.execute("INSERT INTO users (slug, username, password, display_name) VALUES ('a','a','x','A')")
    conn.execute("INSERT INTO calendars (user_id, slug, title) VALUES (1, 'keep', 'Keep me')")
    conn.commit()
    conn.close()

    d = CalendarDB()
    d.create_and_initialize()
    cal = d.get_calendar_by_slug("a", "keep")
    assert cal["title"] == "Keep me"
    assert cal["feed_hits"] == 0 and cal["last_feed_at"] is None
