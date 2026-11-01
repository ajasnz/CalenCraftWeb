"""Strict validation of an exported calendar configuration (see ui.build_calendar_export).

The file is untrusted input: nothing from it reaches the database until it has been rebuilt
here from whitelisted, type-checked, length-capped values. Pure functions, no I/O.
"""
import re

MAX_BYTES = 400_000          # stays under Werkzeug's default 500 kB form-field cap (preview round-trip)
LIMITS = {"sources": 50, "views": 50, "filters": 500, "rules": 500}
MAX_ERRORS = 20

_PROP_RE = re.compile(r"^[A-Za-z0-9*_-]{1,100}$")

# column -> kind. Calendars carry all of these; views carry the nullable sharing overrides.
BOOL, TEXT, COLOR, DAYS = "bool", "text", "color", "days"
CAL_FIELDS = {
    "share_ics": BOOL, "share_viewer": BOOL, "viewer_title": TEXT, "viewer_description": TEXT,
    "viewer_color": COLOR, "viewer_free_busy_only": BOOL, "viewer_links": TEXT,
    "viewer_show_time": BOOL, "viewer_show_description": BOOL, "viewer_show_location": BOOL,
    "viewer_hide_private": BOOL, "default_view_enabled": BOOL,
    "expansion_days_ahead": DAYS, "expansion_days_behind": DAYS,
}
VIEW_FIELDS = {k: v for k, v in CAL_FIELDS.items()
               if k not in ("default_view_enabled", "expansion_days_ahead", "expansion_days_behind")}
_TEXT_MAX = {"viewer_title": 200, "viewer_description": 2000, "viewer_links": 4000}


class _Errors(list):
    def add(self, msg):
        if len(self) < MAX_ERRORS:
            self.append(msg)


def _str(value, maxlen, errors, where, required=False):
    if value is None:
        if required:
            errors.add(f"{where}: missing")
        return ""
    if not isinstance(value, str):
        errors.add(f"{where}: must be text")
        return ""
    value = value.strip() if required else value
    if required and not value:
        errors.add(f"{where}: must not be empty")
    if len(value) > maxlen:
        errors.add(f"{where}: longer than {maxlen} characters")
        return value[:maxlen]
    return value


def _int(value, lo, hi, default, errors, where):
    if value is None or value == "":
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        errors.add(f"{where}: must be a number")
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        errors.add(f"{where}: must be a number")
        return default
    return max(lo, min(hi, n))


def _field(kind, name, value, errors, where, sanitize_color, nullable):
    """Return the cleaned value, or None to mean 'not set' (only if nullable)."""
    if value is None:
        return None if nullable else (0 if kind == BOOL else "")
    if kind == BOOL:
        return 1 if value in (True, 1, "1", "true", "True") else 0
    if kind == COLOR:
        return sanitize_color(value if isinstance(value, str) else "")
    if kind == DAYS:
        return _int(value, 0, 3650, 0, errors, f"{where}.{name}")
    return _str(value, _TEXT_MAX.get(name, 2000), errors, f"{where}.{name}")


def _list(data, key, errors):
    items = data.get(key, [])
    if items is None:
        return []
    if not isinstance(items, list):
        errors.add(f"{key}: must be a list")
        return []
    if len(items) > LIMITS[key]:
        errors.add(f"{key}: at most {LIMITS[key]} allowed (found {len(items)})")
        return []
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            errors.add(f"{key}[{i}]: must be an object")
            return []
    return items


def _check_regex(ftype, pattern, errors, where):
    if "regex" in ftype and pattern:
        try:
            re.compile(pattern)
        except re.error as e:
            errors.add(f"{where}: invalid regular expression ({e})")


def validate_config(data, *, url_validator, slugify, sanitize_color,
                    filter_types, filter_actions, rule_actions):
    """Return (clean, warnings, errors). `clean` is None when there are errors."""
    errors, warnings = _Errors(), []
    if not isinstance(data, dict) or data.get("calencraft_export") != 1:
        return None, [], ["This doesn't look like a CalenCraft export (missing or unsupported format version)."]

    cal = data.get("calendar")
    if not isinstance(cal, dict):
        return None, [], ["calendar: missing"]
    title = _str(cal.get("title"), 200, errors, "calendar.title", required=True)
    clean_cal = {name: _field(kind, name, cal.get(name), errors, "calendar", sanitize_color, nullable=False)
                 for name, kind in CAL_FIELDS.items() if name in cal}

    # sources ────────────────────────────────────────────────────────────
    sources, source_names = [], set()
    for i, s in enumerate(_list(data, "sources", errors)):
        where = f"sources[{i}]"
        url = _str(s.get("url"), 2000, errors, f"{where}.url", required=True)
        name = _str(s.get("name"), 200, errors, f"{where}.name") or url[:60]
        if url:
            ok, why = url_validator(url)
            if not ok:
                errors.add(f"{where}.url: {why}")
        if name in source_names:
            warnings.append(f"Duplicate source name “{name}”: rules will attach to the first one.")
        source_names.add(name)
        sources.append({"name": name, "url": url, "enabled": 0 if s.get("enabled") in (False, 0, "0") else 1})

    # views ──────────────────────────────────────────────────────────────
    views, view_slugs = [], set()
    for i, v in enumerate(_list(data, "views", errors)):
        where = f"views[{i}]"
        vtitle = _str(v.get("title"), 200, errors, f"{where}.title")
        slug = slugify(_str(v.get("slug"), 100, errors, f"{where}.slug") or vtitle)
        if not slug:
            errors.add(f"{where}: needs a slug or title")
            continue
        if slug == "default":
            warnings.append("Skipped a view named “default”: every calendar already has one.")
            continue
        if slug in view_slugs:
            warnings.append(f"Skipped duplicate view “{slug}”.")
            continue
        view_slugs.add(slug)
        overrides = {n: _field(k, n, v.get(n), errors, where, sanitize_color, nullable=True)
                     for n, k in VIEW_FIELDS.items() if n in v}
        views.append({"slug": slug, "title": vtitle or slug,
                      "overrides": {k: x for k, x in overrides.items() if x is not None}})

    def scope(item, where):
        view, source = item.get("view"), item.get("source")
        if view is not None and slugify(str(view)) not in view_slugs:
            errors.add(f"{where}: refers to unknown view “{view}”")
        if source is not None and source not in source_names:
            errors.add(f"{where}: refers to unknown source “{source}”")
        return (slugify(str(view)) if view is not None else None), source

    def common(item, where, kind_key, pat_key, prop_key, act_key, actions, types):
        ftype = item.get(kind_key) or "contains"
        if ftype not in types:
            errors.add(f"{where}.{kind_key}: unknown match type “{ftype}”")
        prop = item.get(prop_key)
        if not isinstance(prop, str) or not _PROP_RE.match(prop):
            errors.add(f"{where}.{prop_key}: invalid property name")
        pattern = _str(item.get(pat_key), 2000, errors, f"{where}.{pat_key}")
        _check_regex(str(ftype), pattern, errors, f"{where}.{pat_key}")
        action = item.get(act_key)
        if action not in actions:
            errors.add(f"{where}.{act_key}: must be one of {', '.join(sorted(actions))}")
        return ftype, prop, pattern, action

    # filters ────────────────────────────────────────────────────────────
    filters = []
    for i, f in enumerate(_list(data, "filters", errors)):
        where = f"filters[{i}]"
        view, source = scope(f, where)
        ftype, prop, pattern, action = common(f, where, "type", "pattern", "property", "action",
                                              filter_actions, filter_types)
        filters.append({"view": view, "source": source,
                        "priority": _int(f.get("priority"), 0, 100000, 100, errors, f"{where}.priority"),
                        "property": prop, "type": ftype, "pattern": pattern, "action": action,
                        "include_if_fails": 1 if f.get("include_if_fails") in (True, 1, "1") else 0})

    # rules ──────────────────────────────────────────────────────────────
    rules = []
    for i, r in enumerate(_list(data, "rules", errors)):
        where = f"rules[{i}]"
        view, source = scope(r, where)
        ftype, prop, pattern, faction = common(r, where, "filter_type", "filter_pattern", "filter_property",
                                               "filter_action", filter_actions, filter_types)
        raction = r.get("action")
        if raction not in rule_actions:
            errors.add(f"{where}.action: must be one of {', '.join(sorted(rule_actions))}")
        tprop = r.get("target_property")
        if not isinstance(tprop, str) or not _PROP_RE.match(tprop):
            errors.add(f"{where}.target_property: invalid property name")
        rules.append({"view": view, "source": source,
                      "priority": _int(r.get("priority"), 0, 100000, 100, errors, f"{where}.priority"),
                      "filter_property": prop, "filter_type": ftype, "filter_pattern": pattern,
                      "filter_action": faction,
                      "include_if_fails": 1 if r.get("include_if_fails") in (True, 1, "1") else 0,
                      "target_property": tprop, "action": raction,
                      "target_value": _str(r.get("target_value"), 2000, errors, f"{where}.target_value")})

    if clean_cal.get("share_viewer"):
        warnings.append("This calendar's public web viewer will be ON after import.")

    if errors:
        return None, warnings, list(errors)
    return {"title": title, "calendar": clean_cal, "sources": sources, "views": views,
            "filters": filters, "rules": rules}, warnings, []
