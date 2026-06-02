# Proposed SQLite schema for CalenCraft

This file describes a theoretical SQLite schema designed to support the
operations used by `app/classes/calendar.py`. It focuses on the tables and
queries that correspond to the `fetch_*` methods the code expects.

## Design goals

- Support multiple users, calendars, calendar views, and source URLs.
- Store rules and filters scoped to source, calendar, or view.
- Keep types simple and SQLite-friendly.

---

## Tables

### `users`
```
CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slug TEXT UNIQUE NOT NULL,
  username TEXT UNIQUE NOT NULL,
  email TEXT UNIQUE,
  password TEXT,
  display_name TEXT,
  state TEXT DEFAULT 'active'
);
```

Stores the `slug` used by the code as `user_slug` as well as
`username`, `email`, `password`, and an account `state` (e.g. `active`, `disabled`).

### `calendars`
```
CREATE TABLE calendars (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  slug TEXT NOT NULL,
  title TEXT,
  state TEXT DEFAULT 'active',
  UNIQUE(user_id, slug)
);
```

### `views`
```
CREATE TABLE views (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  calendar_id INTEGER NOT NULL REFERENCES calendars(id),
  slug TEXT NOT NULL,
  title TEXT,
  state TEXT DEFAULT 'active',
  UNIQUE(calendar_id, slug)
);
```

### `sources`
```
CREATE TABLE sources (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  url TEXT NOT NULL,
  name TEXT,
  enabled INTEGER NOT NULL DEFAULT 1
);
```

Source URLs are owned by a `user` and may be attached to many calendars
via the association table `calendar_sources` (see below). This avoids
duplicating the same source row each time it is used by multiple calendars.

### `calendar_sources` (association table)
```
CREATE TABLE calendar_sources (
  calendar_id INTEGER NOT NULL REFERENCES calendars(id),
  source_id INTEGER NOT NULL REFERENCES sources(id),
  PRIMARY KEY(calendar_id, source_id)
);
```

### `rules`
```
CREATE TABLE rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  calendar_id INTEGER NOT NULL REFERENCES calendars(id),
  view_id INTEGER,          -- nullable: rule can be scoped to a view
  source_id INTEGER,        -- nullable: rule can be scoped to a source
  priority INTEGER DEFAULT 100,
  state TEXT DEFAULT 'active',

  -- filter (when the rule applies)
  filter_property TEXT,
  filter_type TEXT,
  filter_pattern TEXT,
  filter_action TEXT DEFAULT 'include', -- include|exclude
  include_if_fails INTEGER DEFAULT 0,

  -- transformation
  target_property TEXT,
  action TEXT,             -- append|prepend|replace|delete
  target_value TEXT,

  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

Notes:
- Rules are linked to calendars/views/sources; they no longer carry a direct
  `user_id`. The owning user is resolved through the calendar relationship.

### `filters`
```
CREATE TABLE filters (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  calendar_id INTEGER NOT NULL REFERENCES calendars(id),
  view_id INTEGER,
  source_id INTEGER,
  priority INTEGER DEFAULT 100,
  state TEXT DEFAULT 'active',

  property TEXT,
  type TEXT,
  pattern TEXT,
  action TEXT DEFAULT 'include', -- include|exclude
  include_if_fails INTEGER DEFAULT 0,

  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

This table mirrors the filter fields accessed in `calendar.py` (e.g. `filter["property"]`, `filter["type"]`, `filter["pattern"]`).

---

## Indexes (suggested)

```
CREATE INDEX idx_sources_user ON sources(user_id);
CREATE INDEX idx_calendar_sources_calendar ON calendar_sources(calendar_id);
CREATE INDEX idx_rules_calendar_priority ON rules(calendar_id, view_id, source_id, priority);
CREATE INDEX idx_filters_calendar_priority ON filters(calendar_id, view_id, source_id, priority);
```

---

## Example queries (map to `calendar.py` expectations)

-- fetch_source_urls(user=slug, calendar=slug, view=slug, includes=["id","url"])

```
-- Resolve user_id & calendar_id first and fetch sources attached to the calendar
SELECT s.id, s.url
FROM sources s
JOIN calendar_sources cs ON cs.source_id = s.id
JOIN calendars c ON cs.calendar_id = c.id
JOIN users u ON c.user_id = u.id
WHERE u.slug = :user_slug AND c.slug = :calendar_slug AND s.enabled = 1
ORDER BY s.id;
```

- fetch_rules_for_source(user=slug, calendar=slug, order_by="source_id, priority")

```
SELECT r.*
FROM rules r
JOIN calendars c ON r.calendar_id = c.id
JOIN users u ON c.user_id = u.id
WHERE u.slug = :user_slug AND c.slug = :calendar_slug
  AND r.source_id IS NOT NULL
ORDER BY r.source_id, r.priority;
```

- fetch_rules_for_calendar(user=slug, calendar=slug, order_by="priority")

```
SELECT r.*
FROM rules r
JOIN calendars c ON r.calendar_id = c.id
JOIN users u ON c.user_id = u.id
WHERE u.slug = :user_slug AND c.slug = :calendar_slug
ORDER BY r.priority;
```

- fetch_rules_for_view(user=slug, calendar=slug, view=slug, order_by="priority")

```
SELECT r.*
FROM rules r
JOIN calendars c ON r.calendar_id = c.id
JOIN users u ON c.user_id = u.id
JOIN views v ON v.calendar_id = c.id AND v.slug = :view_slug
WHERE u.slug = :user_slug AND c.slug = :calendar_slug
  AND (r.view_id = v.id OR r.source_id IS NOT NULL)
ORDER BY r.priority;
```

-- fetch_filters_for_source / calendar / view follow the same patterns as rules

---

## Storage notes & caveats

- `target_value`, `pattern`, and other free-text fields are TEXT; the app
  may store JSON in `target_value` if complex replacements are needed.
- `include_if_fails` and other booleans are stored as INTEGER 0/1 for SQLite.
- The schema allows nullable `source_id` and `view_id`, but requires
  `calendar_id` so every rule/filter remains owned through a calendar.
- Consider adding an `enabled` flag on `rules`/`filters` for runtime toggling.

---

If you want, I can:
- produce a minimal `app/classes/db.py` implementation that implements the
  `fetch_*` methods against this schema, or
- generate a `schema.sql` file with `CREATE TABLE` statements ready to run.

Which would you like next?