# CalenCraft

**Programmable iCalendar feed aggregator.**

CalenCraft pulls together multiple `.ics` feeds, applies your filters and transformation rules, and serves a single merged calendar URL that any calendar client can subscribe to. You can create multiple *views* of the same calendar — filtered differently for different audiences — and optionally expose a public read-only web viewer with password protection, custom branding, and free/busy-only mode.

---

## Features

- **Feed aggregation** — combine any number of `.ics` source URLs into one calendar
- **Filters** — include or exclude events by property match (title, location, categories, free/busy status, source, and more)
- **Rules** — transform events: append, prepend, replace, or delete any property
- **Views** — multiple filtered/transformed variants of the same calendar, each with its own feed URL
- **Public web viewer** — shareable month/week/day calendar page; no account required for visitors
- **Free/busy mode** — expose only availability without leaking event details
- **Password protection** — optional per-calendar or per-view viewer password
- **Custom branding** — page title, description, accent colour, and external links on the public viewer
- **Per-calendar expansion window** — configurable look-ahead and look-behind for recurring event expansion
- **Multi-user** — each user manages their own calendars; first user is automatically admin
- **First-run setup** — web-based admin account creation on a fresh install
- **Feed caching** — in-memory TTL cache reduces upstream fetches under load
- **Security-hardened** — security headers, CSRF mitigations, source URL validation, rate-limited login

---

## Quick start (Docker)

```bash
docker run -d \
  --name calencraft \
  -p 8000:8000 \
  -v calencraft-data:/data \
  -e SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))") \
  calencraft:latest
```

Then open **http://localhost:8000** — you'll be guided through creating your first admin account.

### Docker Compose

```yaml
services:
  calencraft:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./data:/data
    environment:
      SECRET_KEY: "change-me"          # required in production
      DB_PATH: /data/calencraft.db
      APP_VERSION: "1"
      FEED_CACHE_TTL: "300"            # seconds; 0 to disable
    restart: unless-stopped
```

---

## Configuration

All configuration is via environment variables.

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | *(required in production)* | Flask session signing key. Generate with `python3 -c "import secrets; print(secrets.token_hex(32))"` |
| `DB_PATH` | `/data/calencraft.db` | Path to the SQLite database file |
| `APP_VERSION` | `0` | Controls which DB migrations run. Set to `1` for a fresh install |
| `FLASK_ENV` | `development` | Set to `production` to enable HTTPS-only cookies and enforce `SECRET_KEY` |
| `FEED_CACHE_TTL` | `300` | Feed cache lifetime in seconds. Set to `0` to disable |

> **Production note:** `SECRET_KEY` must be set. If it is missing and `FLASK_ENV=production`, the server will refuse to start.

---

## Development setup

```bash
git clone <repo>
cd calencraft-v5
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export DB_PATH=data/calencraft.db
export APP_VERSION=1
mkdir -p data

python3 run.py
# Open http://localhost:5001
```

### Running tests

```bash
pytest tests/
```

---

## How it works

### Calendars

A **Calendar** is your top-level container. It has one or more **Sources** (upstream `.ics` URLs), optional **Filters** and **Rules**, and one or more **Views**.

```
Calendar
├── Sources  (upstream .ics feeds)
├── Filters  (include/exclude events)
├── Rules    (transform event properties)
└── Views    (filtered/transformed variants → each gets its own feed URL)
```

### Sources

Add any publicly accessible `.ics` URL as a source. CalenCraft fetches all sources, merges the events, then applies your filters and rules before serving the output feed.

Sources can be individually enabled or disabled without deleting them. Use the **Test** button to verify a URL is reachable and see how many events it returns.

### Filters

Filters run first and determine which events make it into the feed at all.

| Field | Description |
|---|---|
| Event property | Which field to inspect (`Title`, `Description`, `Location`, `Free/Busy`, `Source ID`, …) |
| Match type | `Contains`, `Equals`, `Starts with`, `Regex`, and negated variants |
| Pattern | Value to match against |
| Action | `Include` matching events or `Exclude` matching events |
| Scope | Optionally limit to a specific source and/or view |

When **filter_action** is set to "does NOT match", a **NOT** badge appears in the overview table so the intent is immediately clear.

### Rules

Rules run after filters and transform surviving events.

| Field | Description |
|---|---|
| Condition | Same property/match/pattern as filters — use **All events** to apply unconditionally |
| Apply when | When condition matches, or when it does **NOT** match |
| Transform | `Replace`, `Append`, `Prepend`, or `Delete` a target property |
| Scope | Optionally limit to a specific source and/or view |

Rules are executed in priority order (lower number = runs first). The rules table can be toggled between a flat list and grouped-by-view display.

**Example rules:**

| Condition | Transform |
|---|---|
| All events | Append ` [Work]` to Event Title |
| Title contains `OOO` | Replace Free/Busy with Free (Transparent) |
| Free/Busy = Busy (Opaque) | Replace Busy Status (Outlook) with Busy |
| Title does NOT contain `Zoom` | Delete Location |

### Views

A **View** is a named variant of a calendar. Each view gets its own feed URL:

```
/feed/<user>/<calendar>/<view>
```

The `default` view is always available. Additional views let you expose different filtered slices — for example a `public` view with sensitive events removed, or a `work` view scoped to a specific source.

The default view can be disabled per-calendar in **Advanced → Default View** if you only want named views accessible.

### Event window

By default CalenCraft expands recurring events 180 days into the future from today. You can adjust this per-calendar in **Advanced → Event Window**:

- **Look behind** — include past events (days before today). Default: 0
- **Look ahead** — include future events (days from today). Default: 180

### Feed URL

```
https://your-host/feed/<user_slug>/<calendar_slug>/<view_slug>
```

Subscribe to this URL in any calendar client (Apple Calendar, Google Calendar, Outlook, Thunderbird, etc.). Append `?start=YYYY-MM-DD&end=YYYY-MM-DD` to override the expansion window for a specific request.

---

## Public viewer

Each calendar can optionally expose a web-based viewer accessible without logging in.

Enable it in the **Sharing** tab:

- **ICS subscribe** — allows clients to subscribe to the raw `.ics` feed
- **Public web viewer** — enables the browser-based month/week/day calendar page

### Viewer URL

```
https://your-host/view/<user_slug>/<calendar_slug>/<view_slug>
```

### Viewer options

| Option | Description |
|---|---|
| Page title | Custom heading shown on the viewer page |
| Message | Description shown below the title |
| Accent colour | Hex colour used for event highlights and the header |
| Free/busy only | Show events as "Busy" with no other details |
| Show time / description / location | Control which event fields visitors can see |
| Password | Restrict the viewer to visitors who know the password |
| Links | Add buttons (booking pages, contact forms, etc.) to the header |

### Views (month / week / day)

The viewer supports month grid, week time-grid, and day time-grid. All-day events appear in a separate banner row in week and day views. The current time is indicated by a coloured line.

---

## Admin

The first user created is automatically promoted to admin. Admins can:

- Create new user accounts (`/admin/users`)
- Reset any user's password
- Delete users

Users can edit their own display name, email, and password at `/profile`.

---

## API / Feed endpoint

The feed endpoint is intentionally unauthenticated — the URL itself acts as the access token (keep it private). Sharing is controlled per-calendar:

- `share_ics = 0` → feed endpoint returns 404 even if you know the URL
- `share_viewer = 0` → viewer returns 404

---

## Tech stack

| Layer | Technology |
|---|---|
| Language | Python 3.12+ |
| Web framework | Flask 3 |
| Templates | Jinja2 (server-rendered, no build step) |
| Database | SQLite (WAL mode) with versioned migrations |
| iCal parsing | `icalendar` + `recurring-ical-events` |
| Production server | Gunicorn |
| Auth | Session-based (`werkzeug.security` password hashing) |

---

## Project structure

```
app/
├── classes/
│   ├── calendar.py      # CalendarBuilder — core aggregation logic
│   └── db.py            # CalendarDB — SQLite interface
├── functions/
│   ├── cache.py         # In-memory TTL feed cache
│   └── db/
│       ├── db_migrations.py   # Versioned schema migrations
│       └── db_queries.py      # Named SQL queries
├── routes/
│   ├── auth.py          # Login / logout
│   ├── feed.py          # /feed/<user>/<cal>/<view> endpoint
│   ├── public.py        # /view/<user>/<cal>/<view> public viewer
│   ├── setup.py         # First-run setup
│   └── ui.py            # Dashboard, calendars, sources, rules, filters
├── templates/           # Jinja2 HTML templates
├── static/
│   └── style.css        # Single CSS file — no build tooling required
└── server.py            # Flask app factory
tests/
├── test_calendar_builder.py
└── test_db.py
run.py                   # Development entry point
Dockerfile
requirements.txt
.env.example
```

---

## Security notes

- `SECRET_KEY` must be a securely generated random value in production
- Source URLs are validated to reject private/loopback addresses (SSRF mitigation)
- Login is rate-limited (10 failed attempts per 5-minute window per IP)
- All responses include `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`, and a `Content-Security-Policy` header
- Viewer accent colour is sanitised to `#rrggbb` format before being injected into CSS
- Session cookies are `HttpOnly`, `SameSite=Lax`, and `Secure` in production

---

## AI

This version of the software is largely AI-assisted following guidance, specifications, and documentation from a Human. All code was Human reviewed and tested before being included. Core schema and design are Human led and structured, with AI used to "flesh out" functionans and callsbased on the scaffolding.

---

## Licence

MIT