migrations = {
    0: {
        "id": 0,
        "min_app_version": 0,
        "max_app_version": 1000000,
        "name": "Initial schema",
        "description": "Create the initial database schema for rules and filters.",
        "sql": """
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT UNIQUE NOT NULL,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE,
                password TEXT,
                display_name TEXT,
                state TEXT DEFAULT 'active',
                is_admin INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS calendars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                slug TEXT NOT NULL,
                title TEXT,
                state TEXT DEFAULT 'active',
                UNIQUE(user_id, slug)
            );
            CREATE TABLE IF NOT EXISTS views (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                calendar_id INTEGER NOT NULL REFERENCES calendars(id),
                slug TEXT NOT NULL,
                title TEXT,
                state TEXT DEFAULT 'active',
                UNIQUE(calendar_id, slug)
            );
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                url TEXT NOT NULL,
                name TEXT,
                enabled INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS calendar_sources (
                calendar_id INTEGER NOT NULL REFERENCES calendars(id),
                source_id INTEGER NOT NULL REFERENCES sources(id),
                PRIMARY KEY(calendar_id, source_id)
            );
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                calendar_id INTEGER NOT NULL REFERENCES calendars(id),
                view_id INTEGER,
                source_id INTEGER,
                priority INTEGER DEFAULT 100,
                state TEXT DEFAULT 'active',
                filter_property TEXT,
                filter_type TEXT,
                filter_pattern TEXT,
                filter_action TEXT DEFAULT 'include',
                include_if_fails INTEGER DEFAULT 0,
                target_property TEXT,
                action TEXT,
                target_value TEXT
            );
            CREATE TABLE IF NOT EXISTS filters (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                calendar_id INTEGER NOT NULL REFERENCES calendars(id),
                view_id INTEGER,
                source_id INTEGER,
                priority INTEGER DEFAULT 100,
                state TEXT DEFAULT 'active',
                property TEXT,
                type TEXT,
                pattern TEXT,
                action TEXT DEFAULT 'include',
                include_if_fails INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_sources_user ON sources(user_id);
            CREATE INDEX IF NOT EXISTS idx_calendar_sources_calendar ON calendar_sources(calendar_id);
            CREATE INDEX IF NOT EXISTS idx_rules_calendar_priority ON rules(calendar_id, view_id, source_id, priority);
            CREATE INDEX IF NOT EXISTS idx_filters_calendar_priority ON filters(calendar_id, view_id, source_id, priority);
        """,
    },
    1: {
        "id": 1,
        "min_app_version": 0,
        "max_app_version": 1000000,
        "name": "Sharing and public viewer settings",
        "description": "Add sharing configuration columns to calendars and views.",
        "sql": """
            ALTER TABLE calendars ADD COLUMN share_ics INTEGER NOT NULL DEFAULT 1;
            ALTER TABLE calendars ADD COLUMN share_viewer INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE calendars ADD COLUMN viewer_password TEXT;
            ALTER TABLE calendars ADD COLUMN viewer_title TEXT;
            ALTER TABLE calendars ADD COLUMN viewer_description TEXT;
            ALTER TABLE calendars ADD COLUMN viewer_color TEXT DEFAULT '#4361ee';
            ALTER TABLE calendars ADD COLUMN viewer_free_busy_only INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE calendars ADD COLUMN viewer_links TEXT;
            ALTER TABLE calendars ADD COLUMN viewer_show_time INTEGER NOT NULL DEFAULT 1;
            ALTER TABLE calendars ADD COLUMN viewer_show_description INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE calendars ADD COLUMN viewer_show_location INTEGER NOT NULL DEFAULT 0;

            ALTER TABLE views ADD COLUMN share_ics INTEGER;
            ALTER TABLE views ADD COLUMN share_viewer INTEGER;
            ALTER TABLE views ADD COLUMN viewer_password TEXT;
            ALTER TABLE views ADD COLUMN viewer_title TEXT;
            ALTER TABLE views ADD COLUMN viewer_description TEXT;
            ALTER TABLE views ADD COLUMN viewer_color TEXT;
            ALTER TABLE views ADD COLUMN viewer_free_busy_only INTEGER;
            ALTER TABLE views ADD COLUMN viewer_links TEXT;
            ALTER TABLE views ADD COLUMN viewer_show_time INTEGER;
            ALTER TABLE views ADD COLUMN viewer_show_description INTEGER;
            ALTER TABLE views ADD COLUMN viewer_show_location INTEGER;
        """,
    },
    2: {
        "id": 2,
        "min_app_version": 0,
        "max_app_version": 1000000,
        "name": "Calendar expansion window and default view control",
        "description": "Per-calendar look-ahead/look-behind days and ability to disable the default view.",
        "sql": """
            ALTER TABLE calendars ADD COLUMN expansion_days_ahead INTEGER NOT NULL DEFAULT 180;
            ALTER TABLE calendars ADD COLUMN expansion_days_behind INTEGER NOT NULL DEFAULT 0;
            ALTER TABLE calendars ADD COLUMN default_view_enabled INTEGER NOT NULL DEFAULT 1;
        """,
    },
}
