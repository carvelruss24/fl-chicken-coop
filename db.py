"""SQLite persistence for the admin dashboard.

One small module holds the whole data layer so the marketing site stays a plain
Flask app with no ORM dependency. Three tables:

  users  — dashboard logins (password hashes only, never plaintext)
  leads  — every contact/quote submission, so nothing lives only in an inbox
  posts  — blog posts, draft or published

The database file lives in Flask's instance folder (untracked by git), and
init_db() is idempotent: it creates missing tables and seeds the development
login on every boot, so a fresh clone can log in immediately.
"""

import os
import re
import secrets
import sqlite3
from datetime import datetime

from flask import g
from werkzeug.security import generate_password_hash

# --- Seeded development login -----------------------------------------------
# Handed to the Smash Interactive team for dashboard access. Override either
# value in .env (ADMIN_USERNAME / ADMIN_PASSWORD) before going live, or change
# the password from Account → Change password once you're signed in.
#
# Read lazily (not at import time) because app.py imports this module before
# load_dotenv() runs; the `or DEFAULT` fallback also covers a key that is
# present in .env but left blank.
DEFAULT_SEED_USERNAME = "smashteam"
DEFAULT_SEED_PASSWORD = "SmashInteractiveAgency!2026"


def seed_credentials():
    """(username, password) for the login seeded on first boot."""
    return (
        os.getenv("ADMIN_USERNAME", "").strip() or DEFAULT_SEED_USERNAME,
        os.getenv("ADMIN_PASSWORD", "").strip() or DEFAULT_SEED_PASSWORD,
    )

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    display_name  TEXT    NOT NULL DEFAULT '',
    email         TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS leads (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    email      TEXT NOT NULL DEFAULT '',
    phone      TEXT NOT NULL DEFAULT '',
    zip_code   TEXT NOT NULL DEFAULT '',
    size       TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'contact',
    status     TEXT NOT NULL DEFAULT 'new',
    emailed    INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    slug         TEXT NOT NULL UNIQUE,
    excerpt      TEXT NOT NULL DEFAULT '',
    body         TEXT NOT NULL DEFAULT '',
    cover_image  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'draft',
    author       TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    published_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_leads_created ON leads (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_updated ON posts (updated_at DESC);
"""

# --- Additive migrations ----------------------------------------------------
# SCHEMA above is what a brand-new database gets. Anything added to `posts`
# after the first release goes here instead, because CREATE TABLE IF NOT EXISTS
# silently does nothing on an existing database. Each entry is applied only if
# the column is missing, so this is safe to run on every boot.
#
# Fields the editor reuses rather than duplicating:
#   body         -> Content          cover_image  -> Featured Image
#   excerpt      -> Meta Description published_at -> Publish date
#   slug         -> URL Slug
MIGRATIONS = [
    ("posts", "overview",      "ALTER TABLE posts ADD COLUMN overview TEXT NOT NULL DEFAULT ''"),
    ("posts", "caption",       "ALTER TABLE posts ADD COLUMN caption TEXT NOT NULL DEFAULT ''"),
    ("posts", "author_name",   "ALTER TABLE posts ADD COLUMN author_name TEXT NOT NULL DEFAULT ''"),
    ("posts", "author_avatar", "ALTER TABLE posts ADD COLUMN author_avatar TEXT NOT NULL DEFAULT ''"),
    ("posts", "category",      "ALTER TABLE posts ADD COLUMN category TEXT NOT NULL DEFAULT ''"),
    ("posts", "tags",          "ALTER TABLE posts ADD COLUMN tags TEXT NOT NULL DEFAULT ''"),
    ("posts", "seo_title",     "ALTER TABLE posts ADD COLUMN seo_title TEXT NOT NULL DEFAULT ''"),
    ("posts", "views",         "ALTER TABLE posts ADD COLUMN views INTEGER NOT NULL DEFAULT 0"),
    ("posts", "read_time",     "ALTER TABLE posts ADD COLUMN read_time INTEGER NOT NULL DEFAULT 0"),
    ("users", "role",          "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'client'"),
]

LEAD_STATUSES = ["new", "contacted", "quoted", "won", "lost"]

# --- Account roles ----------------------------------------------------------
# "developer" is the agency login seeded on first boot: it can also provision
# and manage the client's login. "client" is the business's own login — same
# access to leads and posts, but it only ever sees its own profile.
ROLE_DEVELOPER = "developer"
ROLE_CLIENT = "client"
ROLES = [ROLE_DEVELOPER, ROLE_CLIENT]
POST_STATUSES = ["draft", "published"]


def now_iso() -> str:
    """Timestamp string used for every stored date (sortable as plain text)."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def db_path(app) -> str:
    """Absolute path to the SQLite file, honouring a DATABASE_PATH override."""
    override = os.getenv("DATABASE_PATH", "").strip()
    if override:
        return override
    os.makedirs(app.instance_path, exist_ok=True)
    return os.path.join(app.instance_path, "fcc.db")


def get_db():
    """Per-request connection, opened lazily and closed by close_db()."""
    if "db" not in g:
        from flask import current_app

        conn = sqlite3.connect(current_app.config["DATABASE_PATH"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        g.db = conn
    return g.db


def close_db(exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(app):
    """Create the schema and seed the development login. Safe to call anytime."""
    app.config["DATABASE_PATH"] = db_path(app)
    app.teardown_appcontext(close_db)

    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        apply_migrations(conn, app)
        username, password = seed_credentials()
        seeded = conn.execute(
            "SELECT id FROM users WHERE username = ?", (username,)
        ).fetchone()
        if seeded is None:
            conn.execute(
                """INSERT INTO users
                       (username, password_hash, display_name, email, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    username,
                    generate_password_hash(password),
                    "Smash Interactive",
                    # Left blank on purpose — set it from Account in the
                    # dashboard so no address is hard-coded in the repo.
                    "",
                    now_iso(),
                ),
            )
            app.logger.info("Seeded dashboard login '%s'.", username)

        # The seeded account is always the developer one, including on a
        # database that predates the role column (where it defaulted to
        # 'client'). Runs every boot so the roles can't drift.
        conn.execute("UPDATE users SET role = ? WHERE username = ? AND role != ?",
                     (ROLE_DEVELOPER, username, ROLE_DEVELOPER))
        conn.commit()
    finally:
        conn.close()


def apply_migrations(conn, app=None):
    """Add any columns from MIGRATIONS that this database doesn't have yet."""
    for table, column, ddl in MIGRATIONS:
        existing = {row["name"] for row in
                    conn.execute("PRAGMA table_info(%s)" % table).fetchall()}
        if column not in existing:
            conn.execute(ddl)
            if app is not None:
                app.logger.info("Migrated: added %s.%s", table, column)


# --- Content helpers --------------------------------------------------------

def strip_tags(html: str) -> str:
    """Crude tag stripper — good enough for word counts and previews."""
    return re.sub(r"<[^>]+>", " ", html or "")


def read_time_minutes(html: str) -> int:
    """Estimated reading time in whole minutes at 200 wpm (never below 1)."""
    words = len(strip_tags(html).split())
    if not words:
        return 0
    return max(1, round(words / 200))


def normalise_stamp(value: str):
    """Accept the browser's datetime-local format and store our own.

    <input type="datetime-local"> submits "2026-07-04T09:30", which none of the
    display filters parse. Everything in the database is
    "YYYY-MM-DD HH:MM:SS", so convert on the way in and return None for blanks.
    """
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return None


def parse_tags(raw) -> list:
    """Split a comma-separated tag string into a de-duplicated, ordered list."""
    if not raw:
        return []
    seen, out = set(), []
    for tag in str(raw).split(","):
        tag = tag.strip()
        key = tag.lower()
        if tag and key not in seen:
            seen.add(key)
            out.append(tag)
    return out


# --- Accounts ---------------------------------------------------------------

def find_user_by_role(conn, role):
    """The (single) account for a role, or None. Oldest wins if several exist."""
    return conn.execute(
        "SELECT * FROM users WHERE role = ? ORDER BY id LIMIT 1", (role,)
    ).fetchone()


def count_users_by_role(conn, role) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM users WHERE role = ?", (role,)).fetchone()[0]


def suggest_password(length: int = 16) -> str:
    """A strong password offered as the default when provisioning the client.

    Uses `secrets`, and guarantees at least one character from each class so it
    always satisfies the form's own strength rules.
    """
    lower = "abcdefghijkmnopqrstuvwxyz"      # no l
    upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"       # no I, O
    digits = "23456789"                      # no 0, 1
    symbols = "!@#$%^&*?-_"
    pools = [lower, upper, digits, symbols]

    chars = [secrets.choice(pool) for pool in pools]
    everything = "".join(pools)
    chars += [secrets.choice(everything) for _ in range(max(0, length - len(pools)))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


# --- Leads ------------------------------------------------------------------

def create_lead(name, email, phone, zip_code, size, notes,
                source="contact", emailed=False) -> int:
    """Store a form submission and return its new id."""
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO leads
               (name, email, phone, zip_code, size, notes, source, status, emailed, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)""",
        (name, email, phone, zip_code, size, notes, source,
         1 if emailed else 0, now_iso()),
    )
    conn.commit()
    return cur.lastrowid


# --- Posts ------------------------------------------------------------------

def slugify(value: str) -> str:
    """URL-safe slug: lowercase, alphanumerics and single hyphens only."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "post"


def unique_slug(base: str, post_id=None) -> str:
    """slugify() plus a numeric suffix if another post already owns the slug."""
    conn = get_db()
    slug = slugify(base)
    candidate, n = slug, 2
    while True:
        row = conn.execute(
            "SELECT id FROM posts WHERE slug = ? AND (? IS NULL OR id != ?)",
            (candidate, post_id, post_id),
        ).fetchone()
        if row is None:
            return candidate
        candidate = f"{slug}-{n}"
        n += 1
