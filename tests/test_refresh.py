import pytest
from werkzeug.security import generate_password_hash
from app.functions import cache as feed_cache


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("APP_VERSION", "1000")
    monkeypatch.setenv("SECRET_KEY", "x")
    from app.server import create_app
    app = create_app()
    uid = app.db.create_user("u", "u", None, generate_password_hash("pw12345678"), "U", is_admin=1)
    app.db.create_calendar(uid, "work", "Work")
    app.db.create_calendar(uid, "home", "Home")
    c = app.test_client()
    c.post("/login", data={"username": "u", "password": "pw12345678"})
    return c


def test_invalidate_prefix_only_removes_matching_keys():
    feed_cache.clear()
    feed_cache.set("feed:u:work:default:None:None", b"a")
    feed_cache.set("feed:u:work:team:None:None", b"b")
    feed_cache.set("feed:u:home:default:None:None", b"c")
    assert feed_cache.invalidate_prefix("feed:u:work:") == 2
    assert feed_cache.get("feed:u:home:default:None:None") == b"c"
    assert feed_cache.get("feed:u:work:default:None:None") is None


def test_refresh_clears_only_this_calendars_cache(client):
    feed_cache.clear()
    feed_cache.set("feed:u:work:default:None:None", b"stale")
    feed_cache.set("feed:u:home:default:None:None", b"keep")
    r = client.post("/calendars/work/refresh")
    assert r.status_code == 302 and "tab=sources" in r.headers["Location"]
    assert feed_cache.get("feed:u:work:default:None:None") is None
    assert feed_cache.get("feed:u:home:default:None:None") == b"keep"


def test_refresh_requires_post_login_and_ownership(client):
    assert client.get("/calendars/work/refresh").status_code == 405
    assert client.post("/calendars/nope/refresh").status_code == 404
    client.get("/logout")
    assert client.post("/calendars/work/refresh").status_code == 302
