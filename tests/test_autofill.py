import re
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


def _input(html, name):
    m = re.search(r"<input[^>]*name=\"%s\"[^>]*>" % name, html)
    assert m, name
    return m.group(0)


def test_login_fields_are_autofill_friendly(client):
    html = client.get("/login").get_data(as_text=True)
    u, p = _input(html, "username"), _input(html, "password")
    assert 'autocomplete="username"' in u and 'type="text"' in u and 'autocapitalize="none"' in u
    assert 'autocomplete="current-password"' in p and 'type="password"' in p
    # username must come before the password field in the same form
    assert html.index('name="username"') < html.index('name="password"')
