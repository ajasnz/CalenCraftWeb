import time
import threading
import logging
from flask import Blueprint, render_template, request, redirect, url_for, session, current_app, flash
from werkzeug.security import check_password_hash
from app.functions import totp

auth_bp = Blueprint("auth", __name__)
log = logging.getLogger(__name__)

# ── Simple in-memory login rate limiter ─────────────────────────────────────
# Tracks failed attempts per IP: {ip: [timestamp, ...]}
_lock = threading.Lock()
_failed: dict[str, list[float]] = {}
_WINDOW = 300   # 5-minute rolling window
_MAX_ATTEMPTS = 10  # block after 10 failures in window


def _client_ip():
    # Respect X-Forwarded-For when behind a reverse proxy
    xff = request.headers.get("X-Forwarded-For", "")
    return xff.split(",")[0].strip() if xff else request.remote_addr


def _is_rate_limited(ip: str) -> bool:
    now = time.monotonic()
    with _lock:
        attempts = [t for t in _failed.get(ip, []) if now - t < _WINDOW]
        _failed[ip] = attempts
        return len(attempts) >= _MAX_ATTEMPTS


def _record_failure(ip: str):
    now = time.monotonic()
    with _lock:
        attempts = [t for t in _failed.get(ip, []) if now - t < _WINDOW]
        attempts.append(now)
        _failed[ip] = attempts


def _clear_failures(ip: str):
    with _lock:
        _failed.pop(ip, None)


_PENDING_TTL = 300  # seconds allowed between password and code


def _finish_login(user, ip):
    # Regenerate session to prevent session fixation
    session.clear()
    session["user_id"]   = user["id"]
    session["user_slug"] = user["slug"]
    session["is_admin"]  = bool(user["is_admin"])
    _clear_failures(ip)
    log.info("Login: %s from %s", user["username"], ip)


@auth_bp.route("/login/2fa", methods=["GET", "POST"])
def login_2fa():
    pending = session.get("pending_2fa")
    if not pending or time.time() - pending.get("ts", 0) > _PENDING_TTL:
        session.pop("pending_2fa", None)
        flash("Your sign-in expired. Please log in again.", "error")
        return redirect(url_for("auth.login"))

    if request.method == "POST":
        ip = _client_ip()
        if _is_rate_limited(ip):
            flash("Too many failed attempts. Please wait a few minutes and try again.", "error")
            return render_template("login_2fa.html")

        db = current_app.db
        user = db.get_user_by_id(pending["uid"])
        state = db.get_totp(pending["uid"]) if user else None
        code = request.form.get("code", "")
        ok = False
        if user and user["state"] == "active" and state and state["totp_enabled"]:
            step = totp.verify(state["totp_secret"], code, state["totp_last_step"])
            if step is not None:
                db.update_totp_state(user["id"], step, state["totp_recovery"])
                ok = True
            else:
                remaining = totp.consume_recovery_code(state["totp_recovery"], code)
                if remaining is not None:
                    db.update_totp_state(user["id"], state["totp_last_step"], remaining)
                    log.warning("Recovery code used for %s", user["username"])
                    ok = True
        if ok:
            _finish_login(user, ip)
            return redirect(url_for("ui.dashboard"))
        _record_failure(ip)
        log.warning("Failed 2FA attempt for uid %s from %s", pending["uid"], ip)
        flash("Invalid code.", "error")

    return render_template("login_2fa.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("ui.dashboard"))

    if request.method == "POST":
        ip = _client_ip()

        if _is_rate_limited(ip):
            flash("Too many failed attempts. Please wait a few minutes and try again.", "error")
            return render_template("login.html")

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = current_app.db
        user = db.get_user_by_username(username)

        if user and check_password_hash(user["password"], password):
            if user["state"] != "active":
                flash("Account is disabled.", "error")
                return render_template("login.html")
            if user.get("totp_enabled"):
                # Password OK but not logged in yet: park the user id until the code is verified
                session.clear()
                session["pending_2fa"] = {"uid": user["id"], "ts": time.time()}
                return redirect(url_for("auth.login_2fa"))
            _finish_login(user, ip)
            return redirect(url_for("ui.dashboard"))

        _record_failure(ip)
        log.warning("Failed login attempt for %r from %s", username, ip)
        flash("Invalid username or password.", "error")

    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
