import json
import datetime
from flask import Blueprint, render_template, request, session, abort, current_app, jsonify
from werkzeug.security import check_password_hash
from app.classes.calendar import CalendarBuilder

public_bp = Blueprint("public", __name__)


def _strip_tz(dt):
    if isinstance(dt, datetime.date) and not isinstance(dt, datetime.datetime):
        dt = datetime.datetime(dt.year, dt.month, dt.day)
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def _fetch_events_range(app, user_slug, calendar_slug, view_slug, start, end, cfg=None):
    """Fetch and parse events between start and end datetimes."""
    # Normalise to naive UTC so all comparisons are timezone-consistent
    start = _strip_tz(start)
    end   = _strip_tz(end)
    builder = CalendarBuilder()
    builder.db = app.db
    # Apply per-calendar look-behind/ahead to the expansion window
    days_behind = int((cfg or {}).get("expansion_days_behind", 0))
    days_ahead  = int((cfg or {}).get("expansion_days_ahead", 180))
    now = datetime.datetime.utcnow()
    exp_start = min(start, now - datetime.timedelta(days=days_behind))
    exp_end   = max(end,   now + datetime.timedelta(days=days_ahead))
    try:
        ical_bytes = builder.build(
            user_slug=user_slug,
            calendar_slug=calendar_slug,
            view_slug=view_slug,
            expansion_start=exp_start,
            expansion_end=exp_end,
        )
    except Exception:
        return []

    from icalendar import Calendar
    cal = Calendar.from_ical(ical_bytes)
    events = []
    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        dtstart = component.get("DTSTART")
        if not dtstart:
            continue
        raw = dtstart.dt
        # Detect all-day events (DATE type, not DATETIME)
        is_allday = isinstance(raw, datetime.date) and not isinstance(raw, datetime.datetime)
        dt = _strip_tz(raw)
        if dt < start or dt >= end:
            continue
        dtend_raw = component.get("DTEND")
        end_dt = _strip_tz(dtend_raw.dt) if dtend_raw else dt + datetime.timedelta(hours=1)
        cls = str(component.get("CLASS", "PUBLIC")).upper()
        is_private = cls in ("PRIVATE", "CONFIDENTIAL")
        events.append({
            "uid": str(component.get("UID", "")),
            "date": dt.date(),
            "start": dt,
            "end": end_dt,
            "is_allday": is_allday,
            "is_private": is_private,
            "summary": str(component.get("SUMMARY", "")),
            "description": str(component.get("DESCRIPTION", "")),
            "location": str(component.get("LOCATION", "")),
        })
    events.sort(key=lambda e: e["start"])
    return events


def _redact_events(events, cfg):
    """Apply free-busy and private-event redaction in place."""
    free_busy = cfg.get("viewer_free_busy_only")
    hide_private = cfg.get("viewer_hide_private")
    for e in events:
        if free_busy:
            e["summary"] = "Busy"
            e["description"] = ""
            e["location"] = ""
        elif hide_private and e.get("is_private"):
            e["summary"] = "Private"
            e["description"] = ""
            e["location"] = ""
    return events


def _session_key(user_slug, calendar_slug, view_slug):
    return f"pub_auth_{user_slug}_{calendar_slug}_{view_slug}"


def _check_sharing(user_slug, calendar_slug, view_slug):
    """Return (cfg, error_response_or_None). Aborts/returns 404 if not shared."""
    db = current_app.db
    cfg = db.get_sharing_config(user_slug, calendar_slug, view_slug)
    if not cfg or not cfg.get("share_viewer"):
        abort(404)
    if view_slug == "default" and not cfg.get("default_view_enabled", 1):
        abort(404)
    return cfg


def _is_authorized(cfg, user_slug, calendar_slug, view_slug):
    if not cfg.get("viewer_password"):
        return True
    return bool(session.get(_session_key(user_slug, calendar_slug, view_slug)))


@public_bp.route("/view/<user_slug>/<calendar_slug>/<view_slug>", methods=["GET", "POST"])
def viewer(user_slug, calendar_slug, view_slug):
    cfg = _check_sharing(user_slug, calendar_slug, view_slug)

    skey = _session_key(user_slug, calendar_slug, view_slug)
    if cfg.get("viewer_password"):
        if not session.get(skey):
            if request.method == "POST" and "pub_password" in request.form:
                if check_password_hash(cfg["viewer_password"], request.form["pub_password"]):
                    session[skey] = True
                else:
                    return render_template("public_login.html", cfg=cfg, error=True)
            else:
                return render_template("public_login.html", cfg=cfg, error=False)

    links = []
    if cfg.get("viewer_links"):
        try:
            links = json.loads(cfg["viewer_links"])
        except Exception:
            pass

    today = datetime.date.today()
    return render_template(
        "public_viewer.html",
        cfg=cfg, user_slug=user_slug,
        calendar_slug=calendar_slug, view_slug=view_slug,
        today=today, links=links,
    )


@public_bp.route("/view/<user_slug>/<calendar_slug>/<view_slug>/events.json")
def viewer_events(user_slug, calendar_slug, view_slug):
    cfg = _check_sharing(user_slug, calendar_slug, view_slug)
    if not _is_authorized(cfg, user_slug, calendar_slug, view_slug):
        abort(401)

    start_str = request.args.get("start")
    end_str   = request.args.get("end")
    try:
        start_dt = datetime.datetime.fromisoformat(start_str) if start_str else \
            datetime.datetime.combine(datetime.date.today() - datetime.timedelta(days=7), datetime.time.min)
    except ValueError:
        start_dt = datetime.datetime.combine(datetime.date.today() - datetime.timedelta(days=7), datetime.time.min)
    try:
        end_dt = datetime.datetime.fromisoformat(end_str) if end_str else \
            start_dt + datetime.timedelta(days=42)
    except ValueError:
        end_dt = start_dt + datetime.timedelta(days=42)

    app = current_app._get_current_object()
    events = _fetch_events_range(app, user_slug, calendar_slug, view_slug, start_dt, end_dt, cfg)
    _redact_events(events, cfg)

    show_time = bool(cfg.get("viewer_show_time"))
    show_desc = bool(cfg.get("viewer_show_description"))
    show_loc  = bool(cfg.get("viewer_show_location"))

    payload = []
    for i, e in enumerate(events):
        payload.append({
            "id": e["uid"] or f"evt-{i}",
            "calendarId": "cal1",
            "title": e["summary"] or "Busy",
            "body": e["description"] if show_desc else "",
            "location": e["location"] if show_loc else "",
            "start": e["start"].isoformat(),
            "end": e["end"].isoformat(),
            "isAllday": e["is_allday"],
            "category": "allday" if e["is_allday"] else "time",
            "isVisible": True,
            "raw": {"showTime": show_time and not e["is_allday"]},
        })
    return jsonify(payload)
