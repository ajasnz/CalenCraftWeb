import json
import datetime
import calendar as cal_module
from flask import Blueprint, render_template, request, session, abort, current_app
from werkzeug.security import check_password_hash
from app.classes.calendar import CalendarBuilder

public_bp = Blueprint("public", __name__)

HOUR_PX = 64  # pixel height per hour in week/day grid


def _strip_tz(dt):
    if isinstance(dt, datetime.date) and not isinstance(dt, datetime.datetime):
        dt = datetime.datetime(dt.year, dt.month, dt.day)
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def _fetch_events_range(app, user_slug, calendar_slug, view_slug, start, end, cfg=None):
    """Fetch and parse events between start and end datetimes."""
    builder = CalendarBuilder()
    builder.db = app.db
    # Apply per-calendar look-behind/ahead to the expansion window
    days_behind = int((cfg or {}).get("expansion_days_behind", 0))
    days_ahead  = int((cfg or {}).get("expansion_days_ahead", 180))
    exp_start = min(start, datetime.datetime.now() - datetime.timedelta(days=days_behind))
    exp_end   = max(end,   datetime.datetime.now() + datetime.timedelta(days=days_ahead))
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
        events.append({
            "date": dt.date(),
            "day": dt.day,
            "start": dt,
            "end": end_dt,
            "is_allday": is_allday,
            "summary": str(component.get("SUMMARY", "")),
            "description": str(component.get("DESCRIPTION", "")),
            "location": str(component.get("LOCATION", "")),
        })
    events.sort(key=lambda e: e["start"])
    return events


def _month_grid(year, month, events):
    cal = cal_module.monthcalendar(year, month)
    events_by_day = {}
    for e in events:
        events_by_day.setdefault(e["day"], []).append(e)
    weeks = []
    for week in cal:
        days = []
        for day in week:
            if day == 0:
                days.append(None)
            else:
                days.append({"day": day, "events": events_by_day.get(day, [])})
        weeks.append(days)
    return weeks


def _event_layout(events, start_hour=0):
    """Add top_px, height_px, left_pct, width_pct to each event for time grid display."""
    # Compute position
    result = []
    for e in events:
        s = e["start"]
        en = e["end"] or (s + datetime.timedelta(hours=1))
        top = (s.hour - start_hour + s.minute / 60) * HOUR_PX
        height = max(((en - s).total_seconds() / 3600) * HOUR_PX, 20)
        result.append({**e, "top_px": round(top), "height_px": round(height)})

    # Simple overlap detection: assign columns
    # Only lay out timed events; all-day events skip positioning
    timed = [e for e in result if not e.get("is_allday")]
    timed.sort(key=lambda e: e["start"])
    columns = []
    for e in timed:
        placed = False
        for i, col_end in enumerate(columns):
            if e["start"] >= col_end:
                columns[i] = e["end"] or e["start"] + datetime.timedelta(hours=1)
                e["_col"] = i
                placed = True
                break
        if not placed:
            e["_col"] = len(columns)
            columns.append(e["end"] or e["start"] + datetime.timedelta(hours=1))

    n_cols = max((e["_col"] for e in timed), default=0) + 1 if timed else 1
    for e in timed:
        e["left_pct"] = e["_col"] / n_cols * 100
        e["width_pct"] = 100 / n_cols
        del e["_col"]

    return result


def _session_key(user_slug, calendar_slug, view_slug):
    return f"pub_auth_{user_slug}_{calendar_slug}_{view_slug}"


@public_bp.route("/view/<user_slug>/<calendar_slug>/<view_slug>", methods=["GET", "POST"])
def viewer(user_slug, calendar_slug, view_slug):
    db = current_app.db
    cfg = db.get_sharing_config(user_slug, calendar_slug, view_slug)

    if not cfg or not cfg.get("share_viewer"):
        abort(404)

    if view_slug == "default" and not cfg.get("default_view_enabled", 1):
        abort(404)

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

    today = datetime.date.today()
    view_mode = request.args.get("view", "month")  # month | week | day

    # --- parse anchor date ---
    date_str = request.args.get("date")
    if date_str:
        try:
            anchor = datetime.date.fromisoformat(date_str)
        except ValueError:
            anchor = today
    else:
        anchor = today

    app = current_app._get_current_object()
    links = []
    if cfg.get("viewer_links"):
        try:
            links = json.loads(cfg["viewer_links"])
        except Exception:
            pass

    # ----------------------------------------------------------------
    # Month view
    # ----------------------------------------------------------------
    if view_mode == "month":
        try:
            year = int(request.args.get("year", anchor.year))
            month = int(request.args.get("month", anchor.month))
        except ValueError:
            year, month = anchor.year, anchor.month
        if month < 1: month, year = 12, year - 1
        if month > 12: month, year = 1, year + 1

        start = datetime.datetime(year, month, 1)
        end = datetime.datetime(year + (month == 12), (month % 12) + 1, 1)
        events = _fetch_events_range(app, user_slug, calendar_slug, view_slug, start, end, cfg)

        if cfg.get("viewer_free_busy_only"):
            for e in events:
                e["summary"] = "Busy"
                e["description"] = ""
                e["location"] = ""

        weeks = _month_grid(year, month, events)
        if month == 1:
            prev_y, prev_m = year - 1, 12
        else:
            prev_y, prev_m = year, month - 1
        if month == 12:
            next_y, next_m = year + 1, 1
        else:
            next_y, next_m = year, month + 1

        return render_template(
            "public_viewer.html",
            cfg=cfg, user_slug=user_slug,
            calendar_slug=calendar_slug, view_slug=view_slug,
            view_mode="month",
            weeks=weeks, events=events,
            year=year, month=month,
            month_name=datetime.date(year, month, 1).strftime("%B %Y"),
            prev_url=f"?view=month&year={prev_y}&month={prev_m}",
            next_url=f"?view=month&year={next_y}&month={next_m}",
            period_label=datetime.date(year, month, 1).strftime("%B %Y"),
            today=today, links=links,
            hour_px=HOUR_PX,
        )

    # ----------------------------------------------------------------
    # Week view
    # ----------------------------------------------------------------
    if view_mode == "week":
        # Anchor to Monday of the week
        week_start = anchor - datetime.timedelta(days=anchor.weekday())
        week_end = week_start + datetime.timedelta(days=7)

        start_dt = datetime.datetime.combine(week_start, datetime.time.min)
        end_dt = datetime.datetime.combine(week_end, datetime.time.min)
        events = _fetch_events_range(app, user_slug, calendar_slug, view_slug, start_dt, end_dt)

        if cfg.get("viewer_free_busy_only"):
            for e in events:
                e["summary"] = "Busy"
                e["description"] = ""
                e["location"] = ""

        # Group events by weekday (0=Mon)
        days = []
        for i in range(7):
            d = week_start + datetime.timedelta(days=i)
            day_events = [e for e in events if e["date"] == d]
            days.append({
                "date": d,
                "label_short": d.strftime("%a"),
                "label_num": d.day,
                "is_today": d == today,
                "events": _event_layout(day_events),
            })

        prev_anchor = (week_start - datetime.timedelta(days=1)).isoformat()
        next_anchor = week_end.isoformat()
        week_label = f"{week_start.strftime('%d %b')} – {(week_end - datetime.timedelta(days=1)).strftime('%d %b %Y')}"

        return render_template(
            "public_viewer.html",
            cfg=cfg, user_slug=user_slug,
            calendar_slug=calendar_slug, view_slug=view_slug,
            view_mode="week",
            days=days,
            prev_url=f"?view=week&date={prev_anchor}",
            next_url=f"?view=week&date={next_anchor}",
            period_label=week_label,
            today=today, links=links,
            hour_px=HOUR_PX,
            hours=list(range(24)),
        )

    # ----------------------------------------------------------------
    # Day view
    # ----------------------------------------------------------------
    start_dt = datetime.datetime.combine(anchor, datetime.time.min)
    end_dt = start_dt + datetime.timedelta(days=1)
    events = _fetch_events_range(app, user_slug, calendar_slug, view_slug, start_dt, end_dt)

    if cfg.get("viewer_free_busy_only"):
        for e in events:
            e["summary"] = "Busy"
            e["description"] = ""
            e["location"] = ""

    prev_anchor = (anchor - datetime.timedelta(days=1)).isoformat()
    next_anchor = (anchor + datetime.timedelta(days=1)).isoformat()

    return render_template(
        "public_viewer.html",
        cfg=cfg, user_slug=user_slug,
        calendar_slug=calendar_slug, view_slug=view_slug,
        view_mode="day",
        day_events=_event_layout(events),
        anchor=anchor,
        prev_url=f"?view=day&date={prev_anchor}",
        next_url=f"?view=day&date={next_anchor}",
        period_label=anchor.strftime("%A, %d %B %Y"),
        today=today, links=links,
        hour_px=HOUR_PX,
        hours=list(range(24)),
    )
