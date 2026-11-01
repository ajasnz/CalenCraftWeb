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

    def get_totp(self, user_id):
        return self._fetchone(self._named("user_totp_get"), (user_id,))

    def enable_totp(self, user_id, secret, last_step, recovery_json):
        self._execute(self._named("user_totp_enable"), (secret, last_step, recovery_json, user_id))

    def disable_totp(self, user_id):
        self._execute(self._named("user_totp_disable"), (user_id,))

    def update_totp_state(self, user_id, last_step, recovery_json):
        self._execute(self._named("user_totp_update"), (last_step, recovery_json, user_id))

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

    def record_feed_hit(self, calendar_id, when_iso):
        self._execute(self._named("calendar_record_feed_hit"), (when_iso, calendar_id))

    def delete_calendar(self, calendar_id):
        """Delete a calendar and everything that belongs to it, all-or-nothing.

        Foreign keys are enforced, so children must go first (same fix as the hotfix on main).
        Sources left linked to no calendar at all are removed too.
        """
        try:
            source_ids = [r["source_id"] for r in self._fetchall(
                "SELECT source_id FROM calendar_sources WHERE calendar_id = ?", (calendar_id,))]
            self._execute(self._named("feed_tokens_delete_for_calendar"), (calendar_id,), commit=False)
            for table in ("rules", "filters", "views", "calendar_sources"):   # fixed names, never user input
                self._execute("DELETE FROM %s WHERE calendar_id = ?" % table, (calendar_id,), commit=False)
            for sid in source_ids:
                self._execute(
                    "DELETE FROM sources WHERE id = ? AND NOT EXISTS "
                    "(SELECT 1 FROM calendar_sources WHERE source_id = ?)", (sid, sid), commit=False)
            self._execute(self._named("calendar_delete"), (calendar_id,), commit=False)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ------------------------------------------------------------------
    # Audit log
    # ------------------------------------------------------------------

    def add_audit(self, user_id, username, action, target, detail, ip, keep):
        import datetime
        self._execute(self._named("audit_insert"), (
            datetime.datetime.utcnow().isoformat(timespec="seconds"),
            user_id, username, action, target, detail, ip), commit=False)
        self._execute(self._named("audit_prune"), (keep,))     # cheap: PK range delete

    def list_audit(self, user_id=None, q="", limit=51, offset=0):
        sql, params = "SELECT * FROM audit_log WHERE 1=1", []
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        if q:
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            sql += " AND (action LIKE ? ESCAPE '\\' OR target LIKE ? ESCAPE '\\' OR detail LIKE ? ESCAPE '\\' OR username LIKE ? ESCAPE '\\')"
            params += [like] * 4
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        return self._fetchall(sql, params)

    # ------------------------------------------------------------------
    # Feed access tokens (only a SHA-256 of each token is ever stored)
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_token(token):
        import hashlib
        return hashlib.sha256((token or "").encode()).hexdigest()

    def create_feed_token(self, calendar_id, view_slug, label):
        """Create a token and return it in plaintext. This is the only time it is available."""
        import secrets
        import datetime
        token = "cct_" + secrets.token_urlsafe(32)
        self._execute(self._named("feed_token_insert"), (
            calendar_id, view_slug or None, (label or "").strip()[:80], self._hash_token(token),
            token[:8], datetime.datetime.utcnow().isoformat(timespec="seconds"),
        ))
        return token

    def list_feed_tokens(self, calendar_id):
        return self._fetchall(self._named("feed_tokens_for_calendar"), (calendar_id,))

    def revoke_feed_token(self, calendar_id, token_id):
        self._execute(self._named("feed_token_revoke"), (token_id, calendar_id))

    def set_feed_token_required(self, calendar_id, required):
        self._execute(self._named("calendar_set_feed_token_required"), (1 if required else 0, calendar_id))

    def verify_feed_token(self, calendar_id, view_slug, token):
        """True if `token` is valid for this calendar and view. Records the use."""
        import datetime
        if not token or len(token) > 200:
            return False
        row = self._fetchone(self._named("feed_token_lookup"),
                             (calendar_id, self._hash_token(token), view_slug))
        if not row:
            return False
        self._execute(self._named("feed_token_touch"),
                      (datetime.datetime.utcnow().isoformat(timespec="seconds"), row["id"]))
        return True

    def duplicate_calendar(self, calendar_id, new_slug, new_title):
        """Copy a calendar with its settings, sources, views, filters and rules.

        Sources are copied (not shared) so edits to the copy never affect the original.
        Returns the new calendar id.
        """
        def copy_row(table, row, skip, overrides):
            cols = [c for c in row if c not in skip and c not in overrides]
            names = cols + list(overrides)
            vals = [row[c] for c in cols] + list(overrides.values())
            sql = "INSERT INTO {} ({}) VALUES ({})".format(
                table, ", ".join(names), ", ".join("?" * len(names)))
            return self._execute(sql, vals, commit=False)

        try:
            src = self._fetchone("SELECT * FROM calendars WHERE id = ?", (calendar_id,))
            # Per-calendar usage stats (feed_hits, last_feed_at) must not carry over to the copy.
            new_id = copy_row("calendars", src, {"id", "feed_hits", "last_feed_at", "feed_token_required"},
                              {"slug": new_slug, "title": new_title})

            source_map = {}
            for s in self._fetchall(
                    "SELECT s.* FROM sources s JOIN calendar_sources cs ON cs.source_id = s.id "
                    "WHERE cs.calendar_id = ?", (calendar_id,)):
                source_map[s["id"]] = copy_row(
                    "sources", s,
                    {"id", "last_fetch_at", "last_fetch_ok", "last_fetch_error"}, {})
                self._execute("INSERT INTO calendar_sources (calendar_id, source_id) VALUES (?, ?)",
                              (new_id, source_map[s["id"]]), commit=False)

            view_map = {}
            for v in self._fetchall("SELECT * FROM views WHERE calendar_id = ?", (calendar_id,)):
                view_map[v["id"]] = copy_row("views", v, {"id"}, {"calendar_id": new_id})

            for table in ("rules", "filters"):
                for r in self._fetchall(
                        "SELECT * FROM {} WHERE calendar_id = ?".format(table), (calendar_id,)):
                    copy_row(table, r, {"id"}, {
                        "calendar_id": new_id,
                        "view_id": view_map.get(r["view_id"]),
                        "source_id": source_map.get(r["source_id"]),
                    })
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return new_id

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
            if not view:
                return None
            for key in ("share_ics", "share_viewer", "viewer_password",
                        "viewer_title", "viewer_description", "viewer_color",
                        "viewer_free_busy_only", "viewer_links",
                        "viewer_show_time", "viewer_show_description", "viewer_show_location",
                        "viewer_hide_private"):
                if view.get(key) is not None:
                    cfg[key] = view[key]
        return cfg

    def update_calendar_sharing(self, calendar_id, share_ics, share_viewer, viewer_password,
                                viewer_title, viewer_description, viewer_color,
                                viewer_free_busy_only, viewer_links,
                                viewer_show_time, viewer_show_description, viewer_show_location,
                                viewer_hide_private=0):
        self._execute(self._named("calendar_update_sharing"), (
            share_ics, share_viewer, viewer_password,
            viewer_title, viewer_description, viewer_color,
            viewer_free_busy_only, viewer_links,
            viewer_show_time, viewer_show_description, viewer_show_location,
            viewer_hide_private,
            calendar_id,
        ))

    def update_source_fetch_status(self, source_id, fetch_at, ok, error=None):
        self._execute(self._named("source_update_fetch_status"), (fetch_at, ok, error, source_id))

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
