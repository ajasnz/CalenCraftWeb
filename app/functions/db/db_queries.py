queries = {
    # --- Users ---
    "users_list": """
        SELECT id, slug, username, email, display_name, state, is_admin
        FROM users ORDER BY username
    """,
    "user_by_id": """
        SELECT id, slug, username, email, display_name, state, is_admin
        FROM users WHERE id = ?
    """,
    "user_by_username": """
        SELECT id, slug, username, email, display_name, password, state, is_admin
        FROM users WHERE username = ?
    """,
    "user_by_slug": """
        SELECT id, slug, username, email, display_name, state, is_admin
        FROM users WHERE slug = ?
    """,
    "user_insert": """
        INSERT INTO users (slug, username, email, password, display_name, is_admin)
        VALUES (?, ?, ?, ?, ?, ?)
    """,
    "user_delete": "DELETE FROM users WHERE id = ?",
    "user_update_profile": """
        UPDATE users SET display_name=?, email=? WHERE id=?
    """,
    "user_update_password": """
        UPDATE users SET password=? WHERE id=?
    """,

    # --- Calendars ---
    "calendars_for_user": """
        SELECT id, slug, title, state FROM calendars
        WHERE user_id = ? AND state = 'active' ORDER BY title
    """,
    "calendar_by_slug": """
        SELECT c.* FROM calendars c JOIN users u ON c.user_id = u.id
        WHERE u.slug = ? AND c.slug = ?
    """,
    "calendar_insert": """
        INSERT INTO calendars (user_id, slug, title) VALUES (?, ?, ?)
    """,
    "calendar_delete": "DELETE FROM calendars WHERE id = ?",
    "calendar_update_sharing": """
        UPDATE calendars SET
            share_ics=?, share_viewer=?, viewer_password=?,
            viewer_title=?, viewer_description=?, viewer_color=?,
            viewer_free_busy_only=?, viewer_links=?,
            viewer_show_time=?, viewer_show_description=?, viewer_show_location=?
        WHERE id=?
    """,
    "calendar_update_settings": """
        UPDATE calendars SET
            expansion_days_ahead=?, expansion_days_behind=?, default_view_enabled=?
        WHERE id=?
    """,

    # --- Views ---
    "views_for_calendar": """
        SELECT id, slug, title, state FROM views
        WHERE calendar_id = ? AND state = 'active' ORDER BY title
    """,
    "view_insert": "INSERT INTO views (calendar_id, slug, title) VALUES (?, ?, ?)",
    "view_delete": "DELETE FROM views WHERE id = ?",
    "view_by_id": "SELECT * FROM views WHERE id = ?",
    "view_by_slug": """
        SELECT v.* FROM views v
        JOIN calendars c ON v.calendar_id = c.id
        JOIN users u ON c.user_id = u.id
        WHERE u.slug = ? AND c.slug = ? AND v.slug = ?
    """,

    # --- Sources ---
    "sources_for_calendar": """
        SELECT s.id, s.url, s.name, s.enabled
        FROM sources s
        JOIN calendar_sources cs ON cs.source_id = s.id
        WHERE cs.calendar_id = ?
        ORDER BY s.name
    """,
    "source_insert": """
        INSERT INTO sources (user_id, url, name) VALUES (?, ?, ?)
    """,
    "calendar_source_link": """
        INSERT OR IGNORE INTO calendar_sources (calendar_id, source_id) VALUES (?, ?)
    """,
    "calendar_source_unlink": """
        DELETE FROM calendar_sources WHERE calendar_id = ? AND source_id = ?
    """,
    "source_by_id": "SELECT * FROM sources WHERE id = ?",
    "source_update": "UPDATE sources SET name=?, url=? WHERE id=?",
    "source_toggle_enabled": "UPDATE sources SET enabled = 1 - enabled WHERE id=?",

    # --- Rules ---
    "rules_for_calendar": """
        SELECT r.* FROM rules r
        JOIN calendars c ON r.calendar_id = c.id
        JOIN users u ON c.user_id = u.id
        WHERE u.slug = ? AND c.slug = ? AND r.state = 'active'
        ORDER BY r.priority
    """,
    "rule_insert": """
        INSERT INTO rules (calendar_id, view_id, source_id, priority,
            filter_property, filter_type, filter_pattern, filter_action, include_if_fails,
            target_property, action, target_value)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
    "rule_delete": "DELETE FROM rules WHERE id = ?",

    # --- Filters ---
    "filters_for_calendar": """
        SELECT f.* FROM filters f
        JOIN calendars c ON f.calendar_id = c.id
        JOIN users u ON c.user_id = u.id
        WHERE u.slug = ? AND c.slug = ? AND f.state = 'active'
        ORDER BY f.priority
    """,
    "filter_insert": """
        INSERT INTO filters (calendar_id, view_id, source_id, priority,
            property, type, pattern, action, include_if_fails)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
    "filter_delete": "DELETE FROM filters WHERE id = ?",
}
