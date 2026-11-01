import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("APP_VERSION", "1000")
    monkeypatch.setenv("SECRET_KEY", "x")
    from app.server import create_app
    app = create_app()
    app.db.create_user("u", "u", None, "h", "U", is_admin=1)
    return app.test_client()


def test_pages_have_main_landmark_and_no_skip_link(client):
    html = client.get("/login").get_data(as_text=True)
    assert 'id="main"' in html
    assert "skip-link" not in html
