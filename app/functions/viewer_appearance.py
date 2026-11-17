"""Owner-controlled appearance and behaviour of the public viewer / embed.

Every default reproduces the viewer exactly as it was before these settings existed.
"""
ALL_VIEWS = ("month", "week", "day", "list")
DEFAULTS = {
    "viewer_default_view": "auto",          # auto = day on phones, month elsewhere
    "viewer_week_start": 1,                 # 1 = Monday, 0 = Sunday
    "viewer_time_format": "24h",
    "viewer_hide_weekends": 0,
    "viewer_hour_start": 0,
    "viewer_hour_end": 24,
    "viewer_theme": "auto",                 # auto = follow the visitor's OS
    "viewer_views": ",".join(ALL_VIEWS),
    "viewer_footer": None,
}
COLUMNS = tuple(DEFAULTS)
FOOTER_MAX = 300


def _int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clean_values(raw):
    """Validate a mapping of column name -> raw value. Returns (values, errors).

    `viewer_views` may be a list or a comma-separated string. Only columns present in `raw`
    are returned, so importing an old export leaves the defaults alone.
    """
    values, errors = {}, []

    if "viewer_views" in raw:
        given = raw["viewer_views"]
        given = given.split(",") if isinstance(given, str) else list(given or [])
        views = [v for v in ALL_VIEWS if v in given]
        if not views:
            errors.append("Offer at least one view.")
            views = list(ALL_VIEWS)
        values["viewer_views"] = ",".join(views)
    offered = (values.get("viewer_views") or DEFAULTS["viewer_views"]).split(",")

    if "viewer_default_view" in raw:
        v = raw["viewer_default_view"] or "auto"
        if v != "auto" and v not in ALL_VIEWS:
            errors.append("Unknown default view.")
            v = "auto"
        elif v != "auto" and v not in offered:
            errors.append("The default view must be one of the views you offer.")
            v = "auto"
        values["viewer_default_view"] = v

    if "viewer_week_start" in raw:
        ws = _int(raw["viewer_week_start"], 1)
        if ws not in (0, 1):
            errors.append("Week start must be Sunday or Monday.")
            ws = 1
        values["viewer_week_start"] = ws

    if "viewer_time_format" in raw:
        tf = raw["viewer_time_format"] or "24h"
        if tf not in ("24h", "12h"):
            errors.append("Time format must be 12-hour or 24-hour.")
            tf = "24h"
        values["viewer_time_format"] = tf

    if "viewer_hide_weekends" in raw:
        values["viewer_hide_weekends"] = 1 if raw["viewer_hide_weekends"] in (True, 1, "1", "on", "true") else 0

    if "viewer_hour_start" in raw or "viewer_hour_end" in raw:
        hs = _int(raw.get("viewer_hour_start", DEFAULTS["viewer_hour_start"]), 0)
        he = _int(raw.get("viewer_hour_end", DEFAULTS["viewer_hour_end"]), 24)
        if not (0 <= hs <= 23 and 1 <= he <= 24 and he > hs):
            errors.append("Visible hours must run from 0–23 to 1–24, with the end after the start.")
            hs, he = 0, 24
        values["viewer_hour_start"], values["viewer_hour_end"] = hs, he

    if "viewer_theme" in raw:
        th = raw["viewer_theme"] or "auto"
        if th not in ("auto", "light", "dark"):
            errors.append("Theme must be automatic, light or dark.")
            th = "auto"
        values["viewer_theme"] = th

    if "viewer_footer" in raw:
        ft = (raw["viewer_footer"] or "").strip()
        if len(ft) > FOOTER_MAX:
            errors.append(f"The footer note can be at most {FOOTER_MAX} characters.")
            ft = ft[:FOOTER_MAX]
        values["viewer_footer"] = ft or None

    return values, errors


def view_options(cfg):
    """What the page's JavaScript needs, resolved from a (possibly pre-migration) config."""
    def get(key):
        v = cfg.get(key)
        return DEFAULTS[key] if v is None else v

    views = [v for v in ALL_VIEWS if v in str(get("viewer_views")).split(",")] or list(ALL_VIEWS)
    default = get("viewer_default_view")
    return {
        "views": views,
        "defaultView": default if default == "auto" or default in views else "auto",
        "weekStart": 0 if get("viewer_week_start") == 0 else 1,
        "time12": get("viewer_time_format") == "12h",
        "hideWeekends": bool(get("viewer_hide_weekends")),
        "hourStart": int(get("viewer_hour_start")),
        "hourEnd": int(get("viewer_hour_end")),
        "theme": get("viewer_theme") if get("viewer_theme") in ("auto", "light", "dark") else "auto",
    }


def ink_for(hex_color):
    """Readable text colour (white or navy) for a #rrggbb background, by WCAG contrast."""
    try:
        r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    except (TypeError, ValueError, IndexError):
        return "#ffffff"
    lin = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    # contrast with white = 1.05/(lum+.05); with navy (#0c2340, lum ~0.016) = (lum+.05)/0.066
    return "#ffffff" if 1.05 / (lum + 0.05) >= (lum + 0.05) / 0.066 else "#0c2340"
