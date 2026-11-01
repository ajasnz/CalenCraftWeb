import json
import os
from flask import Blueprint, Response, current_app, render_template, url_for

pwa_bp = Blueprint("pwa", __name__)

THEME_COLOR = "#0891b2"


def _static_version():
    """Cache-busting token: newest mtime among the assets the service worker precaches."""
    newest = 0
    for name in ("style.css",):
        try:
            newest = max(newest, int(os.path.getmtime(os.path.join(current_app.static_folder, name))))
        except OSError:
            pass
    return str(newest)


@pwa_bp.route("/manifest.webmanifest")
def manifest():
    data = {
        "name": "CalenCraft",
        "short_name": "CalenCraft",
        "description": "Merge, filter and transform calendar feeds into one clean URL.",
        "id": "/",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#eef3f6",
        "theme_color": THEME_COLOR,
        "icons": [
            {"src": url_for("static", filename="icons/icon-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": url_for("static", filename="icons/icon-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": url_for("static", filename="icons/icon-maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        "shortcuts": [
            {"name": "New calendar", "url": "/calendars/new"},
        ],
    }
    return Response(json.dumps(data), mimetype="application/manifest+json",
                    headers={"Cache-Control": "no-cache"})


@pwa_bp.route("/sw.js")
def service_worker():
    # Served from the root so its scope covers the whole app. Never cached by the browser
    # so a new deploy's worker is picked up on the next navigation.
    body = render_template(
        "sw.js",
        version=_static_version(),
        precache=[url_for("static", filename="style.css"),
                  url_for("static", filename="icons/icon-192.png"),
                  "/offline"],
    )
    return Response(body, mimetype="text/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@pwa_bp.route("/offline")
def offline():
    return render_template("offline.html")
