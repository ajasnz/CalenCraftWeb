import time
import threading
import logging
from flask import Blueprint, render_template, request, redirect, url_for, session, current_app, flash
from werkzeug.security import check_password_hash

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


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
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
            # Regenerate session to prevent session fixation
            session.clear()
            session["user_id"]   = user["id"]
            session["user_slug"] = user["slug"]
            session["is_admin"]  = bool(user["is_admin"])
            _clear_failures(ip)
            log.info("Login: %s from %s", username, ip)
            return redirect(url_for("ui.dashboard"))

        _record_failure(ip)
        log.warning("Failed login attempt for %r from %s", username, ip)
        flash("Invalid username or password.", "error")

    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
