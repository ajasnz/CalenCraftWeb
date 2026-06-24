from flask import Blueprint, current_app, Response, request, abort
from app.classes.calendar import CalendarBuilder
from app.functions import cache as feed_cache
import datetime, hashlib

feed_bp = Blueprint("feed", __name__)


@feed_bp.route("/feed/<user_slug>/<calendar_slug>/<view_slug>")
def serve_feed(user_slug, calendar_slug, view_slug):
    start_str = request.args.get("start")
    end_str   = request.args.get("end")

    expansion_start = None
    expansion_end   = None
    if start_str:
        try:
            expansion_start = datetime.datetime.fromisoformat(start_str)
        except ValueError:
            pass
    if end_str:
        try:
            expansion_end = datetime.datetime.fromisoformat(end_str)
        except ValueError:
            pass

    db  = current_app.db
    cfg = db.get_sharing_config(user_slug, calendar_slug, view_slug)
    if not cfg or not cfg.get("share_ics", 1):
        abort(404)

    # Honour default_view_enabled
    if view_slug == "default" and not cfg.get("default_view_enabled", 1):
        abort(404)

    # Apply per-calendar expansion window when caller doesn't specify dates
    if expansion_start is None or expansion_end is None:
        days_behind = int(cfg.get("expansion_days_behind", 0))
        days_ahead  = int(cfg.get("expansion_days_ahead",  180))
        now = datetime.datetime.now()
        if expansion_start is None:
            expansion_start = now - datetime.timedelta(days=days_behind)
        if expansion_end is None:
            expansion_end = now + datetime.timedelta(days=days_ahead)

    # Cache key includes date range so ranged requests don't collide
    cache_key = f"feed:{user_slug}:{calendar_slug}:{view_slug}:{start_str}:{end_str}"
    cached = feed_cache.get(cache_key)
    if cached is not None:
        etag = hashlib.md5(cached).hexdigest()
        if request.headers.get("If-None-Match") == etag:
            return Response(status=304)
        return Response(
            cached,
            mimetype="text/calendar",
            headers={
                "Content-Disposition": f'attachment; filename="{calendar_slug}.ics"',
                "ETag": etag,
                "Cache-Control": "private, max-age=300",
                "X-Cache": "HIT",
            },
        )

    builder = CalendarBuilder()
    builder.db = db

    ical_bytes = builder.build(
        user_slug=user_slug,
        calendar_slug=calendar_slug,
        view_slug=view_slug,
        expansion_start=expansion_start,
        expansion_end=expansion_end,
    )

    feed_cache.set(cache_key, ical_bytes)
    etag = hashlib.md5(ical_bytes).hexdigest()

    return Response(
        ical_bytes,
        mimetype="text/calendar",
        headers={
            "Content-Disposition": f'attachment; filename="{calendar_slug}.ics"',
            "ETag": etag,
            "Cache-Control": "private, max-age=300",
            "X-Cache": "MISS",
        },
    )
