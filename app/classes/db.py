import sqlite3, os, logging
import app.functions.db.db_migrations as db_migrations
import app.functions.db.db_queries as db_queries

log = logging.getLogger(__name__)

_ALLOWED_ORDER_BY = {
    "priority",
    "source_id, priority",
    "source_id",
}

def _safe_order_by(value):
    if value not in _ALLOWED_ORDER_BY:
        raise ValueError(f"Disallowed ORDER BY value: {value!r}")
    return value


class CalendarDB:
    def __init__(self):
        db_path = os.getenv("DB_PATH", "/data/calencraft.db")
        self.db_path = db_path
        self.conn = None

    def _create_cursor(self):
        if not self.conn:
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
        return self.conn.cursor()

    def _apply_schema_migrations(self):
        cursor = self._create_cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
        )
        self.conn.commit()

        current_app_version = int(os.getenv("APP_VERSION", "0"))

        version_query = "SELECT value FROM settings WHERE key = 'db_version'"
        row = cursor.execute(version_query).fetchone()
        db_version = int(row[0]) if row else -1

        for version in sorted(db_migrations.migrations.keys(), key=lambda k: int(k)):
            migration = db_migrations.migrations[version]
            min_app = int(migration.get("min_app_version", -1))
            max_app = int(migration.get("max_app_version", 1000000))

            if min_app <= current_app_version <= max_app:
                int_version = int(version)
                if int_version > db_version:
                    log.info("Applying DB migration %s: %s", version, migration.get('name'))
                    cursor.executescript(migration.get("sql", ""))
                    cursor.execute(
                        "INSERT OR REPLACE INTO settings (key, value) VALUES ('db_version', ?)",
                        (int_version,),
                    )
                    self.conn.commit()
            else:
                log.debug("Skipping DB migration %s due to app version constraints", version)

    def create_and_initialize(self):
        import logging
        log = logging.getLogger(__name__)
        if not os.path.exists(self.db_path):
            log.info("Creating new database at %s", self.db_path)
        else:
            log.info("Database already exists at %s", self.db_path)

        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # WAL mode: better concurrent read performance, safer for multi-worker deployments
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._apply_schema_migrations()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetchall(self, sql, params=()):
        cursor = self._create_cursor()
        cursor.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def _fetchone(self, sql, params=()):
        cursor = self._create_cursor()
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return dict(row) if row else None

    def _execute(self, sql, params=(), commit=True):
        cursor = self._create_cursor()
        cursor.execute(sql, params)
        if commit:
            self.conn.commit()
        return cursor.lastrowid

    def _named(self, name):
        sql = db_queries.queries.get(name)
        if not sql:
            raise ValueError(f"Query not found: {name}")
        return sql

    # ------------------------------------------------------------------
    # CalendarBuilder fetch_* interface
    # ------------------------------------------------------------------

    def fetch_source_urls(self, user, calendar, view=None, includes=None):
        sql = """
            SELECT s.id, s.url
            FROM sources s
            JOIN calendar_sources cs ON cs.source_id = s.id
            JOIN calendars c ON cs.calendar_id = c.id
            JOIN users u ON c.user_id = u.id
            WHERE u.slug = ? AND c.slug = ? AND s.enabled = 1
            ORDER BY s.id
        """
        rows = self._fetchall(sql, (user, calendar))
        return [(r["id"], r["url"]) for r in rows]

    def fetch_rules_for_source(self, user, calendar, order_by="source_id, priority"):
        sql = f"""
            SELECT r.* FROM rules r
            JOIN calendars c ON r.calendar_id = c.id
            JOIN users u ON c.user_id = u.id
            WHERE u.slug = ? AND c.slug = ?
              AND r.source_id IS NOT NULL AND r.state = 'active'
            ORDER BY r.{_safe_order_by(order_by)}
        """
        return self._fetchall(sql, (user, calendar))

    def fetch_rules_for_calendar(self, user, calendar, order_by="priority"):
        sql = f"""
            SELECT r.* FROM rules r
            JOIN calendars c ON r.calendar_id = c.id
            JOIN users u ON c.user_id = u.id
            WHERE u.slug = ? AND c.slug = ?
              AND r.source_id IS NULL AND r.view_id IS NULL AND r.state = 'active'
            ORDER BY r.{_safe_order_by(order_by)}
        """
        return self._fetchall(sql, (user, calendar))

    def fetch_rules_for_view(self, user, calendar, view, order_by="priority"):
        sql = f"""
            SELECT r.* FROM rules r
            JOIN calendars c ON r.calendar_id = c.id
            JOIN users u ON c.user_id = u.id
            JOIN views v ON v.calendar_id = c.id AND v.slug = ?
            WHERE u.slug = ? AND c.slug = ?
              AND r.view_id = v.id AND r.state = 'active'
            ORDER BY r.{_safe_order_by(order_by)}
        """
        return self._fetchall(sql, (view, user, calendar))

    def fetch_filters_for_source(self, user, calendar, order_by="source_id, priority"):
        sql = f"""
            SELECT f.* FROM filters f
            JOIN calendars c ON f.calendar_id = c.id
            JOIN users u ON c.user_id = u.id
            WHERE u.slug = ? AND c.slug = ?
              AND f.source_id IS NOT NULL AND f.state = 'active'
            ORDER BY f.{_safe_order_by(order_by)}
        """
        return self._fetchall(sql, (user, calendar))

    def fetch_filters_for_calendar(self, user, calendar, order_by="priority"):
        sql = f"""
            SELECT f.* FROM filters f
            JOIN calendars c ON f.calendar_id = c.id
            JOIN users u ON c.user_id = u.id
            WHERE u.slug = ? AND c.slug = ?
              AND f.source_id IS NULL AND f.view_id IS NULL AND f.state = 'active'
            ORDER BY f.{_safe_order_by(order_by)}
        """
        return self._fetchall(sql, (user, calendar))

    def fetch_filters_for_view(self, user, calendar, view, order_by="priority"):
        sql = f"""
            SELECT f.* FROM filters f
            JOIN calendars c ON f.calendar_id = c.id
            JOIN users u ON c.user_id = u.id
            JOIN views v ON v.calendar_id = c.id AND v.slug = ?
            WHERE u.slug = ? AND c.slug = ?
              AND f.view_id = v.id AND f.state = 'active'
            ORDER BY f.{_safe_order_by(order_by)}
        """
        return self._fetchall(sql, (view, user, calendar))

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def get_users(self):
        return self._fetchall(self._named("users_list"))

    def get_user_by_id(self, user_id):
        return self._fetchone(self._named("user_by_id"), (user_id,))

    def get_user_by_username(self, username):
        return self._fetchone(self._named("user_by_username"), (username,))

    def get_user_by_slug(self, slug):
        return self._fetchone(self._named("user_by_slug"), (slug,))

    def create_user(self, slug, username, email, password_hash, display_name, is_admin=0):
        if not self.get_users():
            is_admin = 1
        return self._execute(
            self._named("user_insert"),
            (slug, username, email, password_hash, display_name, is_admin),
        )

    def delete_user(self, user_id):
        self._execute(self._named("user_delete"), (user_id,))

    def update_user_profile(self, user_id, display_name, email):
        self._execute(self._named("user_update_profile"), (display_name, email or None, user_id))

    def update_user_password(self, user_id, password_hash):
        self._execute(self._named("user_update_password"), (password_hash, user_id))

    # ------------------------------------------------------------------
    # Calendar management
    # ------------------------------------------------------------------

    def get_calendars(self, user_id):
        return self._fetchall(self._named("calendars_for_user"), (user_id,))

    def get_calendar_by_slug(self, user_slug, calendar_slug):
        return self._fetchone(self._named("calendar_by_slug"), (user_slug, calendar_slug))

    def create_calendar(self, user_id, slug, title):
        return self._execute(self._named("calendar_insert"), (user_id, slug, title))

    def delete_calendar(self, calendar_id):
        self._execute(self._named("calendar_delete"), (calendar_id,))

    # ------------------------------------------------------------------
    # View management
    # ------------------------------------------------------------------

    def get_views(self, calendar_id):
        return self._fetchall(self._named("views_for_calendar"), (calendar_id,))

    def get_view_by_slug(self, user_slug, calendar_slug, view_slug):
        return self._fetchone(self._named("view_by_slug"), (user_slug, calendar_slug, view_slug))

    def create_view(self, calendar_id, slug, title):
        return self._execute(self._named("view_insert"), (calendar_id, slug, title))

    def delete_view(self, view_id):
        self._execute(self._named("view_delete"), (view_id,))

    def get_sharing_config(self, user_slug, calendar_slug, view_slug="default"):
        """Return effective sharing config, merging view overrides over calendar defaults."""
        cal = self.get_calendar_by_slug(user_slug, calendar_slug)
        if not cal:
            return None
        cfg = dict(cal)
        if view_slug and view_slug != "default":
            view = self.get_view_by_slug(user_slug, calendar_slug, view_slug)
            if view:
                for key in ("share_ics", "share_viewer", "viewer_password",
                            "viewer_title", "viewer_description", "viewer_color",
                            "viewer_free_busy_only", "viewer_links",
                            "viewer_show_time", "viewer_show_description", "viewer_show_location"):
                    if view.get(key) is not None:
                        cfg[key] = view[key]
        return cfg

    def update_calendar_sharing(self, calendar_id, share_ics, share_viewer, viewer_password,
                                viewer_title, viewer_description, viewer_color,
                                viewer_free_busy_only, viewer_links,
                                viewer_show_time, viewer_show_description, viewer_show_location):
        self._execute(self._named("calendar_update_sharing"), (
            share_ics, share_viewer, viewer_password,
            viewer_title, viewer_description, viewer_color,
            viewer_free_busy_only, viewer_links,
            viewer_show_time, viewer_show_description, viewer_show_location,
            calendar_id,
        ))

    def update_calendar_settings(self, calendar_id, expansion_days_ahead,
                                 expansion_days_behind, default_view_enabled):
        self._execute(self._named("calendar_update_settings"), (
            expansion_days_ahead, expansion_days_behind, default_view_enabled, calendar_id,
        ))

    # ------------------------------------------------------------------
    # Source management
    # ------------------------------------------------------------------

    def get_sources(self, calendar_id):
        return self._fetchall(self._named("sources_for_calendar"), (calendar_id,))

    def create_source(self, user_id, url, name):
        return self._execute(self._named("source_insert"), (user_id, url, name))

    def link_source(self, calendar_id, source_id):
        self._execute(self._named("calendar_source_link"), (calendar_id, source_id))

    def unlink_source(self, calendar_id, source_id):
        self._execute(self._named("calendar_source_unlink"), (calendar_id, source_id))

    def get_source_by_id(self, source_id):
        return self._fetchone(self._named("source_by_id"), (source_id,))

    def update_source(self, source_id, name, url):
        self._execute(self._named("source_update"), (name, url, source_id))

    def toggle_source_enabled(self, source_id):
        self._execute(self._named("source_toggle_enabled"), (source_id,))

    # ------------------------------------------------------------------
    # Rule management
    # ------------------------------------------------------------------

    def get_rules(self, user_slug, calendar_slug):
        return self._fetchall(self._named("rules_for_calendar"), (user_slug, calendar_slug))

    def create_rule(self, calendar_id, view_id, source_id, priority,
                    filter_property, filter_type, filter_pattern, filter_action,
                    include_if_fails, target_property, action, target_value):
        return self._execute(
            self._named("rule_insert"),
            (calendar_id, view_id, source_id, priority,
             filter_property, filter_type, filter_pattern, filter_action,
             include_if_fails, target_property, action, target_value),
        )

    def delete_rule(self, rule_id):
        self._execute(self._named("rule_delete"), (rule_id,))

    # ------------------------------------------------------------------
    # Filter management
    # ------------------------------------------------------------------

    def get_filters(self, user_slug, calendar_slug):
        return self._fetchall(self._named("filters_for_calendar"), (user_slug, calendar_slug))

    def create_filter(self, calendar_id, view_id, source_id, priority,
                      property_, type_, pattern, action, include_if_fails):
        return self._execute(
            self._named("filter_insert"),
            (calendar_id, view_id, source_id, priority,
             property_, type_, pattern, action, include_if_fails),
        )

    def delete_filter(self, filter_id):
        self._execute(self._named("filter_delete"), (filter_id,))
