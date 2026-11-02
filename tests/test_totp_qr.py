import re
import pytest
from werkzeug.security import generate_password_hash
from app.functions import totp


def test_qr_svg_is_inline_and_deterministic():
    uri = totp.provisioning_uri("JBSWY3DPEHPK3PXP", "alice")
    svg = totp.provisioning_qr_svg(uri)
    assert svg.startswith("<svg") and "</svg>" in svg and "<script" not in svg.lower()
    assert svg == totp.provisioning_qr_svg(uri)                     # same input, same code
    assert svg != totp.provisioning_qr_svg(totp.provisioning_uri("ABCDEFGHIJKLMNOP", "alice"))


def test_missing_segno_degrades_to_no_qr(monkeypatch):
    import builtins
    real = builtins.__import__
    monkeypatch.setattr(builtins, "__import__",
                        lambda name, *a, **k: (_ for _ in ()).throw(ImportError()) if name == "segno" else real(name, *a, **k))
    assert totp.provisioning_qr_svg("otpauth://totp/x?secret=ABC") is None


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("APP_VERSION", "1000")
    monkeypatch.setenv("SECRET_KEY", "x")
    from app.server import create_app
    a = create_app()
    a.db.create_user("alice", "alice", None, generate_password_hash("correct-horse"), "Alice", is_admin=1)
    c = a.test_client()
    c.post("/login", data={"username": "alice", "password": "correct-horse"})
    return c


def test_setup_page_shows_qr_and_still_shows_manual_key(client):
    assert "totp-qr" not in client.get("/profile").get_data(as_text=True)
    client.post("/profile", data={"_action": "totp_start"})
    html = client.get("/profile").get_data(as_text=True)
    assert 'class="totp-qr"' in html and "<svg" in html
    assert "totp-secret" in html and "otpauth://" in html
    # the QR is only offered during setup, never once 2FA is on
    with client.session_transaction() as s:
        secret = s["totp_setup_secret"]
    client.post("/profile", data={"_action": "totp_confirm", "code": totp.code_at(secret, totp.current_step())})
    assert "totp-qr" not in client.get("/profile").get_data(as_text=True)
