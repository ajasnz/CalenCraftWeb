import os
import secrets
import logging
from flask import Flask, jsonify
from app.classes.db import CalendarDB


def create_app():
    # ── Logging ─────────────────────────────────────────────────────────
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    log = logging.getLogger(__name__)

    app = Flask(__name__, template_folder="templates", static_folder="static")
    # Reload templates from disk on every request in non-production environments
    if os.getenv("FLASK_ENV") != "production":
        app.config["TEMPLATES_AUTO_RELOAD"] = True

    # ── Secret key ──────────────────────────────────────────────────────
    secret_key = os.getenv("SECRET_KEY", "")
    if not secret_key:
        if os.getenv("FLASK_ENV") == "production":
            raise RuntimeError(
                "SECRET_KEY environment variable must be set in production. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        # Dev only: random key per process (sessions reset on restart — expected)
        secret_key = secrets.token_hex(32)
        log.warning("SECRET_KEY not set — using ephemeral key. Sessions will reset on restart.")
    app.secret_key = secret_key

    # ── Session cookie hardening ─────────────────────────────────────────
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.getenv("FLASK_ENV") == "production"
    app.config["PERMANENT_SESSION_LIFETIME"] = 86400 * 14  # 14 days max

    # ── Database ────────────────────────────────────────────────────────
    db = CalendarDB()
    db.create_and_initialize()
    app.db = db

    # ── Blueprints ──────────────────────────────────────────────────────
    from app.routes.feed import feed_bp
    from app.routes.auth import auth_bp
    from app.routes.ui import ui_bp
    from app.routes.public import public_bp
    from app.routes.setup import setup_bp

    app.register_blueprint(feed_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(ui_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(setup_bp)

    # ── First-run gate: redirect everything to /setup when no users exist ─
    from flask import redirect, url_for, request as flask_request

    _SETUP_EXEMPT = {"/setup", "/health", "/static"}

    @app.before_request
    def first_run_gate():
        path = flask_request.path
        # Allow setup route, health check, and static files through unconditionally
        if path == "/setup" or path.startswith("/static") or path == "/health":
            return
        # Only check if no users exist (cheap query, cached after first user is created)
        if not app.db.get_users():
            return redirect("/setup")

    # ── Health check ────────────────────────────────────────────────────
    @app.route("/health")
    def health():
        try:
            # Verify DB is reachable
            app.db._fetchone("SELECT 1")
            return jsonify({"status": "ok"}), 200
        except Exception as e:
            log.error("Health check failed: %s", e)
            return jsonify({"status": "error", "detail": str(e)}), 503

    # ── Custom error pages ───────────────────────────────────────────────
    from flask import render_template

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403, message="You don't have permission to access this page."), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404, message="Page not found."), 404

    @app.errorhandler(500)
    def server_error(e):
        log.exception("Internal server error")
        return render_template("error.html", code=500, message="Something went wrong on our end."), 500

    # ── Security headers ────────────────────────────────────────────────
    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "script-src 'self' 'unsafe-inline';"
        )
        return response

    return app
