import re
import json
from functools import wraps
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, current_app, flash, abort, jsonify,
)
from werkzeug.security import generate_password_hash, check_password_hash

_HEX_COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')

def _sanitize_color(value, default="#4361ee"):
    """Accept only valid 6-digit hex colours; return default otherwise."""
    v = (value or "").strip()
    return v if _HEX_COLOR_RE.match(v) else default


import ipaddress
from urllib.parse import urlparse

_PRIVATE_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / AWS metadata
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

def _validate_source_url(url):
    """
    Return (ok, error_message).
    Rejects non-http(s) schemes and URLs that resolve to private/loopback addresses.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Invalid URL."
    if parsed.scheme not in ("http", "https"):
        return False, "URL must use http or https."
    host = parsed.hostname or ""
    if not host:
        return False, "URL must include a hostname."
    # Block obvious private hostnames
    if host in ("localhost", "0.0.0.0"):
        return False, "Private or loopback URLs are not allowed."
    try:
        addr = ipaddress.ip_address(host)
        for net in _PRIVATE_NETS:
            if addr in net:
                return False, "Private or loopback IP addresses are not allowed."
    except ValueError:
        pass  # hostname, not an IP — DNS resolution happens at fetch time
    return True, None

ui_bp = Blueprint("ui", __name__)

# ------------------------------------------------------------------
# Human-readable label mappings
# ------------------------------------------------------------------

PROPERTY_LABELS = {
    "*":                          "All events",
    "SUMMARY":                    "Event Title",
    "DESCRIPTION":                "Description",
    "LOCATION":                   "Location",
    "CATEGORIES":                 "Categories",
    "STATUS":                     "Event Status",
    "TRANSP":                     "Free/Busy",
    "X-MICROSOFT-CDO-BUSYSTATUS": "Busy Status (Outlook)",
    "X-SOURCE-ID":                "Source ID",
}

# Properties whose values are a fixed set — shown as a dropdown in forms
PROPERTY_ENUM_VALUES = {
    "STATUS": [
        ("CONFIRMED",  "Confirmed"),
        ("TENTATIVE",  "Tentative"),
        ("CANCELLED",  "Cancelled"),
    ],
    "TRANSP": [
        ("OPAQUE",       "Busy (Opaque)"),
        ("TRANSPARENT",  "Free (Transparent)"),
    ],
    "X-MICROSOFT-CDO-BUSYSTATUS": [
        ("FREE",             "Free"),
        ("BUSY",             "Busy"),
        ("TENTATIVE",        "Tentative"),
        ("OOF",              "Out of Office"),
        ("WORKINGELSEWHERE", "Working Elsewhere"),
    ],
}
FILTER_TYPE_LABELS = {
    "contains":       "Contains",
    "equals":         "Equals",
    "starts_with":    "Starts with",
    "ends_with":      "Ends with",
    "regex":          "Matches regex",
    "not_contains":   "Does not contain",
    "not_equals":     "Does not equal",
    "not_starts_with":"Does not start with",
    "not_ends_with":  "Does not end with",
    "not_regex":      "Does not match regex",
}
RULE_ACTION_LABELS = {
    "append":  "Append",
    "prepend": "Prepend",
    "replace": "Replace",
    "delete":  "Delete",
}
APPLY_WHEN_LABELS = {
    "include": "When event matches",
    "exclude": "When event does NOT match",
}
FILTER_ACTION_LABELS = {
    "include": "Include matching events",
    "exclude": "Exclude matching events",
}

# Flat lookup: {property: {raw_value: human_label}} — used in table display
ENUM_VALUE_LABELS = {
    prop: {val: label for val, label in opts}
    for prop, opts in PROPERTY_ENUM_VALUES.items()
}

COMMON_PROPERTIES = list(PROPERTY_LABELS.keys())
FILTER_TYPES      = list(FILTER_TYPE_LABELS.keys())
RULE_ACTIONS      = list(RULE_ACTION_LABELS.keys())
FILTER_ACTIONS    = list(FILTER_ACTION_LABELS.keys())
APPLY_WHEN        = list(APPLY_WHEN_LABELS.keys())


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("is_admin"):
            abort(403)
        return f(*args, **kwargs)
    return login_required(decorated)


def slugify(text):
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text


def _label_maps():
    return dict(
        property_labels=PROPERTY_LABELS,
        filter_type_labels=FILTER_TYPE_LABELS,
        rule_action_labels=RULE_ACTION_LABELS,
        apply_when_labels=APPLY_WHEN_LABELS,
        filter_action_labels=FILTER_ACTION_LABELS,
        property_enum_values=PROPERTY_ENUM_VALUES,
        enum_value_labels=ENUM_VALUE_LABELS,
    )


def _nav_calendars():
    return current_app.db.get_calendars(session["user_id"])


# ------------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------------

@ui_bp.route("/")
@login_required
def dashboard():
    calendars = current_app.db.get_calendars(session["user_id"])
    return render_template("dashboard.html", calendars=calendars, nav_calendars=calendars)


# ------------------------------------------------------------------
# Calendars
# ------------------------------------------------------------------

@ui_bp.route("/calendars/new", methods=["GET", "POST"])
@login_required
def calendar_new():
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        slug = slugify(title)
        if not slug:
            flash("Title is required.", "error")
            return render_template("calendar_new.html", nav_calendars=_nav_calendars())
        db = current_app.db
        try:
            db.create_calendar(session["user_id"], slug, title)
        except Exception:
            flash("A calendar with that name already exists.", "error")
            return render_template("calendar_new.html", nav_calendars=_nav_calendars())
        return redirect(url_for("ui.calendar_detail", calendar_slug=slug))
    return render_template("calendar_new.html", nav_calendars=_nav_calendars())


@ui_bp.route("/calendars/<calendar_slug>", methods=["GET", "POST"])
@login_required
def calendar_detail(calendar_slug):
    db = current_app.db
    cal = db.get_calendar_by_slug(session["user_slug"], calendar_slug)
    if not cal:
        abort(404)

    sources = db.get_sources(cal["id"])
    views   = db.get_views(cal["id"])
    rules   = db.get_rules(session["user_slug"], calendar_slug)
    filters = db.get_filters(session["user_slug"], calendar_slug)

    # Build lookup dicts so templates can resolve IDs → names
    views_by_id   = {v["id"]: v["title"] for v in views}
    sources_by_id = {s["id"]: s["name"]  for s in sources}

    # Load sharing config
    sharing_links = []
    if cal.get("viewer_links"):
        try:
            sharing_links = json.loads(cal["viewer_links"])
        except Exception:
            pass

    # Handle settings form POST inline
    active_tab = request.args.get("tab", "sources")
    if request.method == "POST" and request.form.get("_form") == "settings":
        active_tab = "advanced"
        f = request.form
        try:
            ahead  = max(0, min(3650, int(f.get("expansion_days_ahead",  180))))
            behind = max(0, min(3650, int(f.get("expansion_days_behind", 0))))
        except (ValueError, TypeError):
            ahead, behind = 180, 0
        db.update_calendar_settings(
            calendar_id=cal["id"],
            expansion_days_ahead=ahead,
            expansion_days_behind=behind,
            default_view_enabled=1 if f.get("default_view_enabled") else 0,
        )
        flash("Settings saved.", "success")
        return redirect(url_for("ui.calendar_detail", calendar_slug=calendar_slug, tab="advanced"))

    # Handle sharing form POST inline
    if request.method == "POST" and request.form.get("_form") == "sharing":
        active_tab = "sharing"
        f = request.form
        new_pw    = f.get("viewer_password_new", "").strip()
        remove_pw = f.get("viewer_password_remove")
        if remove_pw:
            viewer_password = None
        elif new_pw:
            viewer_password = generate_password_hash(new_pw)
        else:
            viewer_password = cal.get("viewer_password")

        link_labels = f.getlist("link_label")
        link_urls   = f.getlist("link_url")
        links = [{"label": l, "url": u} for l, u in zip(link_labels, link_urls) if l.strip() and u.strip()]

        db.update_calendar_sharing(
            calendar_id=cal["id"],
            share_ics=1 if f.get("share_ics") else 0,
            share_viewer=1 if f.get("share_viewer") else 0,
            viewer_password=viewer_password,
            viewer_title=f.get("viewer_title", "").strip() or None,
            viewer_description=f.get("viewer_description", "").strip() or None,
            viewer_color=_sanitize_color(f.get("viewer_color")),
            viewer_free_busy_only=1 if f.get("viewer_free_busy_only") else 0,
            viewer_links=json.dumps(links) if links else None,
            viewer_show_time=1 if f.get("viewer_show_time") else 0,
            viewer_show_description=1 if f.get("viewer_show_description") else 0,
            viewer_show_location=1 if f.get("viewer_show_location") else 0,
        )
        flash("Sharing settings saved.", "success")
        return redirect(url_for("ui.calendar_detail", calendar_slug=calendar_slug, tab="sharing"))

        # Reload sharing after save
        cal = db.get_calendar_by_slug(session["user_slug"], calendar_slug)
        sharing_links = json.loads(cal["viewer_links"]) if cal.get("viewer_links") else []

    return render_template(
        "calendar_detail.html",
        cal=cal, sources=sources, views=views, rules=rules, filters=filters,
        views_by_id=views_by_id, sources_by_id=sources_by_id,
        sharing_links=sharing_links,
        active_tab=active_tab,
        nav_calendars=_nav_calendars(),
        user_slug=session["user_slug"],
        **_label_maps(),
    )


@ui_bp.route("/calendars/<calendar_slug>/delete", methods=["POST"])
@login_required
def calendar_delete(calendar_slug):
    db = current_app.db
    cal = db.get_calendar_by_slug(session["user_slug"], calendar_slug)
    if not cal:
        abort(404)
    db.delete_calendar(cal["id"])
    flash("Calendar deleted.", "success")
    return redirect(url_for("ui.dashboard"))


# ------------------------------------------------------------------
# Sources
# ------------------------------------------------------------------

@ui_bp.route("/calendars/<calendar_slug>/sources/add", methods=["GET", "POST"])
@login_required
def source_add(calendar_slug):
    db = current_app.db
    cal = db.get_calendar_by_slug(session["user_slug"], calendar_slug)
    if not cal:
        abort(404)
    if request.method == "POST":
        url  = request.form.get("url", "").strip()
        name = request.form.get("name", "").strip() or url
        if not url:
            flash("URL is required.", "error")
            return render_template("source_add.html", cal=cal, nav_calendars=_nav_calendars())
        ok, err = _validate_source_url(url)
        if not ok:
            flash(f"Invalid URL: {err}", "error")
            return render_template("source_add.html", cal=cal, nav_calendars=_nav_calendars())
        source_id = db.create_source(session["user_id"], url, name)
        db.link_source(cal["id"], source_id)
        flash("Source added.", "success")
        return redirect(url_for("ui.calendar_detail", calendar_slug=calendar_slug, tab="sources"))
    return render_template("source_add.html", cal=cal, nav_calendars=_nav_calendars())


@ui_bp.route("/calendars/<calendar_slug>/sources/<int:source_id>/delete", methods=["POST"])
@login_required
def source_delete(calendar_slug, source_id):
    db = current_app.db
    cal = db.get_calendar_by_slug(session["user_slug"], calendar_slug)
    if not cal:
        abort(404)
    db.unlink_source(cal["id"], source_id)
    flash("Source removed.", "success")
    return redirect(url_for("ui.calendar_detail", calendar_slug=calendar_slug, tab="sources"))


def _get_source_for_calendar(db, user_slug, calendar_slug, source_id):
    """Return (cal, source) if source_id belongs to this user's calendar, else abort 404."""
    cal = db.get_calendar_by_slug(user_slug, calendar_slug)
    if not cal:
        abort(404)
    # Verify the source is actually linked to this calendar (prevents IDOR)
    sources = db.get_sources(cal["id"])
    source = next((s for s in sources if s["id"] == source_id), None)
    if not source:
        abort(404)
    return cal, source


@ui_bp.route("/calendars/<calendar_slug>/sources/<int:source_id>/edit", methods=["GET", "POST"])
@login_required
def source_edit(calendar_slug, source_id):
    db = current_app.db
    cal, source = _get_source_for_calendar(db, session["user_slug"], calendar_slug, source_id)
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        url  = request.form.get("url", "").strip()
        if not url:
            flash("URL is required.", "error")
        else:
            ok, err = _validate_source_url(url)
            if not ok:
                flash(f"Invalid URL: {err}", "error")
            else:
                db.update_source(source_id, name or url, url)
                flash("Source updated.", "success")
                return redirect(url_for("ui.calendar_detail", calendar_slug=calendar_slug, tab="sources"))
    return render_template("source_edit.html", cal=cal, source=source, nav_calendars=_nav_calendars())


@ui_bp.route("/calendars/<calendar_slug>/sources/<int:source_id>/toggle", methods=["POST"])
@login_required
def source_toggle(calendar_slug, source_id):
    db = current_app.db
    _get_source_for_calendar(db, session["user_slug"], calendar_slug, source_id)
    db.toggle_source_enabled(source_id)
    return redirect(url_for("ui.calendar_detail", calendar_slug=calendar_slug, tab="sources"))


@ui_bp.route("/calendars/<calendar_slug>/sources/<int:source_id>/test", methods=["POST"])
@login_required
def source_test(calendar_slug, source_id):
    db = current_app.db
    _cal, source = _get_source_for_calendar(db, session["user_slug"], calendar_slug, source_id)
    if not source:
        return jsonify({"ok": False, "error": "Source not found"}), 404
    try:
        import requests as req
        from icalendar import Calendar as iCal
        r = req.get(source["url"], timeout=10, headers={"User-Agent": "CalenCraft/1.0"})
        r.raise_for_status()
        cal_data = iCal.from_ical(r.content)
        event_count = sum(1 for c in cal_data.walk() if c.name == "VEVENT")
        return jsonify({"ok": True, "event_count": event_count})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ------------------------------------------------------------------
# Views
# ------------------------------------------------------------------

@ui_bp.route("/calendars/<calendar_slug>/views/new", methods=["GET", "POST"])
@login_required
def view_new(calendar_slug):
    db = current_app.db
    cal = db.get_calendar_by_slug(session["user_slug"], calendar_slug)
    if not cal:
        abort(404)
    if request.method == "POST":
        title = request.form.get("title", "").strip()
        slug  = slugify(title)
        if not slug:
            flash("Title is required.", "error")
            return render_template("view_new.html", cal=cal, nav_calendars=_nav_calendars())
        db.create_view(cal["id"], slug, title)
        flash("View created.", "success")
        return redirect(url_for("ui.calendar_detail", calendar_slug=calendar_slug, tab="views"))
    return render_template("view_new.html", cal=cal, nav_calendars=_nav_calendars())


@ui_bp.route("/calendars/<calendar_slug>/views/<int:view_id>/delete", methods=["POST"])
@login_required
def view_delete(calendar_slug, view_id):
    db = current_app.db
    cal = db.get_calendar_by_slug(session["user_slug"], calendar_slug)
    if not cal:
        abort(404)
    db.delete_view(view_id)
    flash("View deleted.", "success")
    return redirect(url_for("ui.calendar_detail", calendar_slug=calendar_slug, tab="views"))


# ------------------------------------------------------------------
# Rules
# ------------------------------------------------------------------

def _rule_form_data(f):
    filter_property = f.get("filter_property", "")
    if filter_property == "X-SOURCE-ID":
        filter_pattern = f.get("filter_pattern_source") or f.get("filter_pattern", "")
        filter_type = "equals"
    elif filter_property == "*":
        filter_pattern = ""
        filter_type = "equals"
    elif filter_property in PROPERTY_ENUM_VALUES:
        filter_pattern = f.get("filter_pattern_enum") or f.get("filter_pattern", "")
        filter_type = "equals"
    else:
        filter_pattern = f.get("filter_pattern", "")
        filter_type = f.get("filter_type", "")

    target_property = f.get("target_property", "")
    action = f.get("action", "")
    if target_property in PROPERTY_ENUM_VALUES and action == "replace":
        target_value = f.get("target_value_enum") or f.get("target_value", "")
    else:
        target_value = f.get("target_value", "")

    return dict(
        view_id=f.get("view_id") or None,
        source_id=f.get("source_id") or None,
        priority=int(f.get("priority", 100)),
        filter_property=filter_property,
        filter_type=filter_type,
        filter_pattern=filter_pattern,
        filter_action=f.get("filter_action", "include"),
        include_if_fails=1 if f.get("include_if_fails") else 0,
        target_property=target_property,
        action=action,
        target_value=target_value,
    )


@ui_bp.route("/calendars/<calendar_slug>/rules/new", methods=["GET", "POST"])
@login_required
def rule_new(calendar_slug):
    db = current_app.db
    cal = db.get_calendar_by_slug(session["user_slug"], calendar_slug)
    if not cal:
        abort(404)
    views   = db.get_views(cal["id"])
    sources = db.get_sources(cal["id"])
    if request.method == "POST":
        db.create_rule(calendar_id=cal["id"], **_rule_form_data(request.form))
        flash("Rule created.", "success")
        return redirect(url_for("ui.calendar_detail", calendar_slug=calendar_slug, tab="rules"))
    return render_template(
        "rule_form.html", cal=cal, views=views, sources=sources,
        rule=None, form_title="New Rule",
        properties=COMMON_PROPERTIES, filter_types=FILTER_TYPES,
        rule_actions=RULE_ACTIONS, apply_when=APPLY_WHEN,
        nav_calendars=_nav_calendars(), **_label_maps(),
    )


@ui_bp.route("/calendars/<calendar_slug>/rules/<int:rule_id>/edit", methods=["GET", "POST"])
@login_required
def rule_edit(calendar_slug, rule_id):
    db = current_app.db
    cal = db.get_calendar_by_slug(session["user_slug"], calendar_slug)
    if not cal:
        abort(404)
    rule    = db._fetchone("SELECT * FROM rules WHERE id=?", (rule_id,))
    views   = db.get_views(cal["id"])
    sources = db.get_sources(cal["id"])
    if not rule or rule["calendar_id"] != cal["id"]:
        abort(404)
    if request.method == "POST":
        fd = _rule_form_data(request.form)
        db._execute(
            "UPDATE rules SET view_id=?,source_id=?,priority=?,filter_property=?,filter_type=?,"
            "filter_pattern=?,filter_action=?,include_if_fails=?,target_property=?,action=?,target_value=? "
            "WHERE id=?",
            (fd["view_id"], fd["source_id"], fd["priority"], fd["filter_property"], fd["filter_type"],
             fd["filter_pattern"], fd["filter_action"], fd["include_if_fails"], fd["target_property"],
             fd["action"], fd["target_value"], rule_id),
        )
        flash("Rule updated.", "success")
        return redirect(url_for("ui.calendar_detail", calendar_slug=calendar_slug, tab="rules"))
    return render_template(
        "rule_form.html", cal=cal, views=views, sources=sources,
        rule=rule, form_title="Edit Rule",
        properties=COMMON_PROPERTIES, filter_types=FILTER_TYPES,
        rule_actions=RULE_ACTIONS, apply_when=APPLY_WHEN,
        nav_calendars=_nav_calendars(), **_label_maps(),
    )


@ui_bp.route("/calendars/<calendar_slug>/rules/<int:rule_id>/delete", methods=["POST"])
@login_required
def rule_delete(calendar_slug, rule_id):
    db = current_app.db
    cal = db.get_calendar_by_slug(session["user_slug"], calendar_slug)
    if not cal:
        abort(404)
    db.delete_rule(rule_id)
    flash("Rule deleted.", "success")
    return redirect(url_for("ui.calendar_detail", calendar_slug=calendar_slug, tab="rules"))


# ------------------------------------------------------------------
# Filters
# ------------------------------------------------------------------

def _filter_form_data(f):
    property_ = f.get("property", "")
    if property_ == "X-SOURCE-ID":
        pattern = f.get("pattern_source") or f.get("pattern", "")
        type_ = "equals"
    elif property_ in PROPERTY_ENUM_VALUES:
        pattern = f.get("pattern_enum") or f.get("pattern", "")
        type_ = "equals"
    else:
        pattern = f.get("pattern", "")
        type_ = f.get("type", "")
    return dict(
        view_id=f.get("view_id") or None,
        source_id=f.get("source_id") or None,
        priority=int(f.get("priority", 100)),
        property_=property_,
        type_=type_,
        pattern=pattern,
        action=f.get("action", "include"),
        include_if_fails=1 if f.get("include_if_fails") else 0,
    )


@ui_bp.route("/calendars/<calendar_slug>/filters/new", methods=["GET", "POST"])
@login_required
def filter_new(calendar_slug):
    db = current_app.db
    cal = db.get_calendar_by_slug(session["user_slug"], calendar_slug)
    if not cal:
        abort(404)
    views   = db.get_views(cal["id"])
    sources = db.get_sources(cal["id"])
    if request.method == "POST":
        db.create_filter(calendar_id=cal["id"], **_filter_form_data(request.form))
        flash("Filter created.", "success")
        return redirect(url_for("ui.calendar_detail", calendar_slug=calendar_slug, tab="filters"))
    return render_template(
        "filter_form.html", cal=cal, views=views, sources=sources,
        filter_=None, form_title="New Filter",
        properties=COMMON_PROPERTIES, filter_types=FILTER_TYPES, filter_actions=FILTER_ACTIONS,
        nav_calendars=_nav_calendars(), **_label_maps(),
    )


@ui_bp.route("/calendars/<calendar_slug>/filters/<int:filter_id>/edit", methods=["GET", "POST"])
@login_required
def filter_edit(calendar_slug, filter_id):
    db = current_app.db
    cal = db.get_calendar_by_slug(session["user_slug"], calendar_slug)
    if not cal:
        abort(404)
    filter_ = db._fetchone("SELECT * FROM filters WHERE id=?", (filter_id,))
    views   = db.get_views(cal["id"])
    sources = db.get_sources(cal["id"])
    if not filter_ or filter_["calendar_id"] != cal["id"]:
        abort(404)
    if request.method == "POST":
        fd = _filter_form_data(request.form)
        db._execute(
            "UPDATE filters SET view_id=?,source_id=?,priority=?,property=?,type=?,"
            "pattern=?,action=?,include_if_fails=? WHERE id=?",
            (fd["view_id"], fd["source_id"], fd["priority"], fd["property_"], fd["type_"],
             fd["pattern"], fd["action"], fd["include_if_fails"], filter_id),
        )
        flash("Filter updated.", "success")
        return redirect(url_for("ui.calendar_detail", calendar_slug=calendar_slug, tab="filters"))
    return render_template(
        "filter_form.html", cal=cal, views=views, sources=sources,
        filter_=filter_, form_title="Edit Filter",
        properties=COMMON_PROPERTIES, filter_types=FILTER_TYPES, filter_actions=FILTER_ACTIONS,
        nav_calendars=_nav_calendars(), **_label_maps(),
    )


@ui_bp.route("/calendars/<calendar_slug>/filters/<int:filter_id>/delete", methods=["POST"])
@login_required
def filter_delete(calendar_slug, filter_id):
    db = current_app.db
    cal = db.get_calendar_by_slug(session["user_slug"], calendar_slug)
    if not cal:
        abort(404)
    db.delete_filter(filter_id)
    flash("Filter deleted.", "success")
    return redirect(url_for("ui.calendar_detail", calendar_slug=calendar_slug, tab="filters"))


# ------------------------------------------------------------------
# Profile
# ------------------------------------------------------------------

@ui_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    db = current_app.db
    user = db.get_user_by_id(session["user_id"])
    if request.method == "POST":
        action = request.form.get("_action")

        if action == "profile":
            display_name = request.form.get("display_name", "").strip() or user["username"]
            email = request.form.get("email", "").strip()
            db.update_user_profile(session["user_id"], display_name, email)
            flash("Profile updated.", "success")

        elif action == "password":
            current_pw  = request.form.get("current_password", "")
            new_pw      = request.form.get("new_password", "")
            confirm_pw  = request.form.get("confirm_password", "")
            if not check_password_hash(user["password"], current_pw):
                flash("Current password is incorrect.", "error")
            elif len(new_pw) < 8:
                flash("New password must be at least 8 characters.", "error")
            elif new_pw != confirm_pw:
                flash("Passwords do not match.", "error")
            else:
                db.update_user_password(session["user_id"], generate_password_hash(new_pw))
                flash("Password changed.", "success")

        return redirect(url_for("ui.profile"))

    return render_template("profile.html", user=user, nav_calendars=_nav_calendars())


# ------------------------------------------------------------------
# Admin
# ------------------------------------------------------------------

@ui_bp.route("/admin/users")
@admin_required
def admin_users():
    users = current_app.db.get_users()
    return render_template("admin_users.html", users=users, nav_calendars=_nav_calendars())


@ui_bp.route("/admin/users/new", methods=["GET", "POST"])
@admin_required
def admin_user_new():
    if request.method == "POST":
        f            = request.form
        username     = f.get("username", "").strip()
        email        = f.get("email", "").strip() or None
        display_name = f.get("display_name", "").strip() or username
        password     = f.get("password", "")
        is_admin     = 1 if f.get("is_admin") else 0
        slug         = slugify(username)
        if not username or not password:
            flash("Username and password are required.", "error")
            return render_template("admin_user_new.html", nav_calendars=_nav_calendars())
        db = current_app.db
        try:
            db.create_user(slug, username, email, generate_password_hash(password), display_name, is_admin)
        except Exception:
            flash("Username or email already exists.", "error")
            return render_template("admin_user_new.html", nav_calendars=_nav_calendars())
        flash(f"User '{username}' created.", "success")
        return redirect(url_for("ui.admin_users"))
    return render_template("admin_user_new.html", nav_calendars=_nav_calendars())


@ui_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_user_delete(user_id):
    if user_id == session["user_id"]:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("ui.admin_users"))
    current_app.db.delete_user(user_id)
    flash("User deleted.", "success")
    return redirect(url_for("ui.admin_users"))


@ui_bp.route("/admin/users/<int:user_id>/reset-password", methods=["GET", "POST"])
@admin_required
def admin_user_reset_password(user_id):
    db = current_app.db
    user = db.get_user_by_id(user_id)
    if not user:
        abort(404)
    if request.method == "POST":
        new_pw  = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if len(new_pw) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif new_pw != confirm:
            flash("Passwords do not match.", "error")
        else:
            db.update_user_password(user_id, generate_password_hash(new_pw))
            flash(f"Password reset for {user['username']}.", "success")
            return redirect(url_for("ui.admin_users"))
    return render_template("admin_user_reset_password.html", user=user, nav_calendars=_nav_calendars())
