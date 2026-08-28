"""JSON-file persistence for the admin dashboard.

The dashboard only ever holds three small collections — dashboard logins, contact
leads, and blog posts — so they live in plain JSON files rather than a database.
One module holds the whole data layer; there is no ORM and no SQL.

    instance/data/users.json     dashboard logins (password HASHES only)
    instance/data/leads.json     every contact/quote submission
    instance/data/posts.json     blog posts, draft or published
    instance/data/security.json  failed sign-in counters (brute-force lockout)
    instance/secret_key          session-signing key, generated once

Everything sits in Flask's instance folder, which is outside `static/` and is
therefore not reachable by any URL, and is gitignored so a deploy never
overwrites live data.

Three properties this module guarantees, because a flat file gets all of them
wrong by default:

* **Atomic writes.** Every save is written to a temporary file in the same
  directory and then `os.replace()`d over the target, which is atomic on both
  POSIX and Windows. A crash mid-write can never leave a truncated users.json
  that locks the dashboard out.
* **Cross-process locking.** Gunicorn runs several workers. Read-modify-write
  cycles hold an OS-level lock on a sidecar `.lock` file for their whole
  duration, so two workers saving a post at the same moment can't lose one of
  them.
* **Least privilege on disk.** The data directory is created 0700 and
  users.json / security.json / secret_key are chmod 0600 on every write, so on
  the Linux VPS only the account running the app can read the password hashes.

Records are plain dicts. Missing keys are filled from the DEFAULTS tables below
on every read, which is what replaces schema migrations: adding a field here is
enough, old files upgrade themselves the next time they're loaded.
"""

import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

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


LEAD_STATUSES = ["new", "contacted", "quoted", "won", "lost"]
POST_STATUSES = ["draft", "published"]

# --- Account roles ----------------------------------------------------------
# "developer" is the agency login seeded on first boot: it can also provision
# and manage the client's login. "client" is the business's own login — same
# access to leads and posts, but it only ever sees its own profile.
ROLE_DEVELOPER = "developer"
ROLE_CLIENT = "client"
ROLES = [ROLE_DEVELOPER, ROLE_CLIENT]

# --- Record shapes ----------------------------------------------------------
# These replace SQL schemas AND migrations. Every record is topped up with any
# missing key on the way out of the store, so adding a field here is the whole
# job: files written by an older build gain it the next time they're read.
USER_DEFAULTS = {
    "id": 0, "username": "", "password_hash": "", "display_name": "",
    "email": "", "role": ROLE_CLIENT, "created_at": "", "last_login_at": None,
}

LEAD_DEFAULTS = {
    "id": 0, "name": "", "email": "", "phone": "", "zip_code": "", "size": "",
    "notes": "", "source": "contact", "status": "new", "emailed": 0,
    "created_at": "",
}

POST_DEFAULTS = {
    "id": 0, "title": "", "slug": "", "excerpt": "", "body": "",
    "cover_image": "", "status": "draft", "author": "", "created_at": "",
    "updated_at": "", "published_at": None, "overview": "", "caption": "",
    "author_name": "", "author_avatar": "", "category": "", "tags": "",
    "seo_title": "", "views": 0, "read_time": 0,
}

DEFAULTS = {"users": USER_DEFAULTS, "leads": LEAD_DEFAULTS, "posts": POST_DEFAULTS}

# Files holding secrets are readable only by the owner. The rest are 0644 so a
# backup script running as another user can still copy them.
PRIVATE_STORES = {"users", "security"}

_DATA_DIR = None                 # resolved by init_db()
_THREAD_LOCK = threading.RLock()  # guards against threads inside one worker


def now_iso() -> str:
    """Timestamp string used for every stored date (sortable as plain text)."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# --- Store plumbing ---------------------------------------------------------

def data_dir(app=None) -> str:
    """Absolute path to the folder holding the JSON files.

    DATABASE_PATH overrides it (a directory, or a legacy *.db path whose parent
    is used) so tests can point at a throwaway location.
    """
    global _DATA_DIR
    override = os.getenv("DATABASE_PATH", "").strip()
    if override:
        # Accept a path to a file (the old SQLite-era value) as well as a folder.
        base = override if not os.path.splitext(override)[1] else override + ".data"
        _DATA_DIR = os.path.abspath(base)
    elif _DATA_DIR is None and app is not None:
        _DATA_DIR = os.path.join(app.instance_path, "data")
    if _DATA_DIR is None:
        raise RuntimeError("db.init_db(app) has not been called yet.")
    _ensure_dir(_DATA_DIR)
    return _DATA_DIR


def _ensure_dir(path):
    os.makedirs(path, exist_ok=True)
    _chmod(path, 0o700)


def _chmod(path, mode):
    """Best-effort permission tightening.

    Meaningful on the Linux VPS. On Windows os.chmod only moves the read-only
    bit, so this is close to a no-op there — which is fine, because the machine
    that matters is the server.
    """
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _path(name) -> str:
    return os.path.join(data_dir(), name + ".json")


@contextmanager
def _file_lock(name):
    """Hold an exclusive OS lock for a whole read-modify-write cycle.

    Gunicorn runs multiple worker processes, so a plain read-then-write races:
    two workers both read 5 posts, both append, and one append is lost. The lock
    lives on a sidecar file rather than the data file itself, so the atomic
    os.replace() below never swaps a locked inode out from under a waiter.
    """
    lock_path = _path(name) + ".lock"
    with _THREAD_LOCK:                       # threads within this worker
        handle = open(lock_path, "a+b")      # processes across workers
        try:
            _lock_file(handle)
            yield
        finally:
            try:
                _unlock_file(handle)
            finally:
                handle.close()


try:                                          # POSIX (the VPS)
    import fcntl

    def _lock_file(handle):
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)

    def _unlock_file(handle):
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

except ImportError:                           # Windows (local development)
    import msvcrt

    def _lock_file(handle):
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock_file(handle):
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


def _load(name) -> dict:
    """Read a store file. A missing or unreadable file reads as empty.

    `seq` is the id counter. It only ever goes up, so a deleted lead's id is
    never handed to a different lead later.
    """
    path = _path(name)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {"seq": 0, "records": []}
    except (json.JSONDecodeError, OSError):
        # Never let a damaged file take the site down. Keep the original next
        # to it so the data can be recovered by hand.
        broken = path + ".corrupt"
        try:
            shutil.copyfile(path, broken)
        except OSError:
            pass
        return {"seq": 0, "records": []}

    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        return {"seq": 0, "records": []}
    data.setdefault("seq", max([r.get("id", 0) for r in data["records"]] or [0]))
    return data


def _save(name, data):
    """Write a store file atomically, then tighten its permissions.

    Written to a temp file in the same directory and moved into place, so a
    crash or a full disk can never leave a half-written users.json behind.
    """
    path = _path(name)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix="." + name,
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())        # survive a power cut, not just a crash
        _chmod(tmp, 0o600 if name in PRIVATE_STORES else 0o644)
        os.replace(tmp, path)            # atomic on POSIX and on Windows
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _hydrate(name, record) -> dict:
    """A stored record topped up with any field it predates."""
    out = dict(DEFAULTS.get(name, {}))
    out.update(record or {})
    return out


def _all(name) -> list:
    """Every record in a store, hydrated. Read-only — mutations won't persist."""
    return [_hydrate(name, r) for r in _load(name)["records"]]


@contextmanager
def _mutate(name):
    """Read-modify-write a store under lock. Yields the raw record list."""
    with _file_lock(name):
        data = _load(name)
        yield data
        _save(name, data)


def _insert(name, record) -> int:
    """Append a record with a fresh id and return that id."""
    with _mutate(name) as data:
        data["seq"] = int(data.get("seq", 0)) + 1
        record["id"] = data["seq"]
        data["records"].append(record)
        return record["id"]


def _update(name, record_id, changes) -> bool:
    """Apply `changes` to one record in place. False if it's gone."""
    with _mutate(name) as data:
        for record in data["records"]:
            if record.get("id") == record_id:
                record.update(changes)
                return True
        return False


def _delete(name, record_id) -> bool:
    with _mutate(name) as data:
        before = len(data["records"])
        data["records"] = [r for r in data["records"] if r.get("id") != record_id]
        return len(data["records"]) < before


def _find(name, **match):
    """First hydrated record matching every keyword, or None."""
    for record in _all(name):
        if all(record.get(k) == v for k, v in match.items()):
            return record
    return None


# --- Session signing key ----------------------------------------------------

def secret_key(app) -> str:
    """A session key that survives restarts and is shared by all workers.

    This is load-bearing for sign-in, not a nicety. Flask signs the session
    cookie with it, so a key that differs between gunicorn workers means the
    cookie set by the worker that handled the login is rejected by the next
    worker — the browser bounces straight back to the sign-in page and the
    credentials look wrong when they aren't.

    FLASK_SECRET_KEY in .env wins. Otherwise one is generated once and kept in
    instance/secret_key (0600), so there is no way to end up without one.
    """
    from_env = os.getenv("FLASK_SECRET_KEY", "").strip()
    if from_env:
        return from_env

    os.makedirs(app.instance_path, exist_ok=True)
    path = os.path.join(app.instance_path, "secret_key")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            existing = fh.read().strip()
        if existing:
            return existing
    except OSError:
        pass

    generated = secrets.token_hex(32)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(generated)
        _chmod(path, 0o600)
        app.logger.info("Generated a persistent session key at %s", path)
    except OSError:
        app.logger.warning(
            "Could not persist a session key; sign-ins will not survive a "
            "restart. Set FLASK_SECRET_KEY in .env."
        )
    return generated


# --- Boot -------------------------------------------------------------------

def init_db(app):
    """Create the store, import any legacy SQLite data, seed the login."""
    app.config["DATA_DIR"] = data_dir(app)

    _migrate_from_sqlite(app)

    username, password = seed_credentials()
    with _file_lock("users"):
        data = _load("users")
        existing = next((r for r in data["records"]
                         if r.get("username") == username), None)
        if existing is None:
            data["seq"] = int(data.get("seq", 0)) + 1
            data["records"].append({
                **USER_DEFAULTS,
                "id": data["seq"],
                "username": username,
                "password_hash": generate_password_hash(password),
                "display_name": "Smash Interactive",
                # Left blank on purpose — set it from Account in the dashboard
                # so no address is hard-coded in the repo.
                "email": "",
                "role": ROLE_DEVELOPER,
                "created_at": now_iso(),
            })
            app.logger.info("Seeded dashboard login '%s'.", username)
        elif existing.get("role") != ROLE_DEVELOPER:
            # The seeded account is always the developer one, including in a
            # store written before roles existed. Runs every boot so the roles
            # can't drift.
            existing["role"] = ROLE_DEVELOPER
        _save("users", data)


def _migrate_from_sqlite(app):
    """One-time import of an existing instance/fcc.db into the JSON store.

    Only runs when there is no users.json yet, so it happens exactly once — on
    the first boot after this change. The .db file is left untouched as a
    backup rather than deleted.
    """
    if os.path.exists(_path("users")):
        return

    legacy = os.getenv("SQLITE_IMPORT_PATH", "").strip() or os.path.join(
        app.instance_path, "fcc.db")
    if not os.path.exists(legacy):
        return

    try:
        conn = sqlite3.connect(legacy)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        app.logger.warning("Could not open %s for import: %s", legacy, exc)
        return

    try:
        for table in ("users", "leads", "posts"):
            try:
                rows = [dict(r) for r in conn.execute("SELECT * FROM " + table)]
            except sqlite3.Error:
                continue
            if not rows:
                continue
            records = [_hydrate(table, r) for r in rows]
            _save(table, {
                "seq": max(r.get("id", 0) for r in records),
                "records": records,
            })
            app.logger.info("Imported %d %s from %s", len(records), table, legacy)
    finally:
        conn.close()


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
    display filters parse. Everything in the store is "YYYY-MM-DD HH:MM:SS",
    so convert on the way in and return None for blanks.
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


def _matches(record, fields, query) -> bool:
    """Case-insensitive substring search across `fields` (SQL LIKE %q%)."""
    needle = query.lower()
    return any(needle in str(record.get(f) or "").lower() for f in fields)


# --- Accounts ---------------------------------------------------------------

def get_user(user_id):
    return _find("users", id=user_id)


def get_user_by_username(username):
    return _find("users", username=username)


def find_user_by_role(role):
    """The (single) account for a role, or None. Oldest wins if several exist."""
    matches = [u for u in _all("users") if u["role"] == role]
    return sorted(matches, key=lambda u: u["id"])[0] if matches else None


def count_users_by_role(role) -> int:
    return sum(1 for u in _all("users") if u["role"] == role)


def username_taken(username, exclude_id=None) -> bool:
    return any(u["username"] == username and u["id"] != exclude_id
               for u in _all("users"))


def create_user(username, password, display_name="", email="",
                role=ROLE_CLIENT) -> int:
    return _insert("users", {
        **USER_DEFAULTS,
        "username": username,
        "password_hash": generate_password_hash(password),
        "display_name": display_name,
        "email": email,
        "role": role,
        "created_at": now_iso(),
    })


def update_user(user_id, **fields) -> bool:
    """Update a user. Pass password="..." to have it hashed on the way in."""
    password = fields.pop("password", None)
    if password is not None:
        fields["password_hash"] = generate_password_hash(password)
    return _update("users", user_id, fields)


def delete_user(user_id) -> bool:
    return _delete("users", user_id)


def touch_last_login(user_id):
    _update("users", user_id, {"last_login_at": now_iso()})


# A hash of a value nobody can supply. Checking an unknown username against it
# makes a failed sign-in take the same time whether or not the account exists,
# so the form can't be timed to enumerate usernames. Built once, on first use,
# so it doesn't add a KDF run to every boot.
_DUMMY_HASH = None


def verify_login(username, password):
    """The matching user for these credentials, or None. Constant-ish time."""
    global _DUMMY_HASH
    user = get_user_by_username(username)
    if user is None:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = generate_password_hash(secrets.token_hex(16))
        check_password_hash(_DUMMY_HASH, password or "")
        return None
    if not check_password_hash(user["password_hash"], password or ""):
        return None
    return user


def check_user_password(user, password) -> bool:
    return check_password_hash(user["password_hash"], password or "")


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


# --- Sign-in throttling -----------------------------------------------------
# Counters live in the store rather than in memory so they are shared by every
# gunicorn worker and survive a restart — an attacker can't reset the count by
# spreading guesses across workers or by waiting for a redeploy.

MAX_ATTEMPTS = 5          # failures before the first lockout
ATTEMPT_WINDOW = 900      # seconds of quiet that clears the counter
LOCKOUT_STEPS = [60, 300, 900, 3600]   # 1 min, 5 min, 15 min, then 1 hour


def _now_epoch() -> float:
    return datetime.now().timestamp()


def lockout_seconds(key) -> int:
    """Seconds left before `key` may try again. 0 when it's free to proceed."""
    entry = next((r for r in _load("security")["records"]
                  if r.get("key") == key), None)
    if not entry:
        return 0
    remaining = float(entry.get("locked_until", 0)) - _now_epoch()
    return int(remaining) + 1 if remaining > 0 else 0


def record_login_failure(key) -> int:
    """Count one failure against `key` and return the lockout it earned."""
    now = _now_epoch()
    with _mutate("security") as data:
        # Drop anything that has been quiet for a full window, so the file
        # can't grow without bound from scattered one-off attempts.
        data["records"] = [
            r for r in data["records"]
            if now - float(r.get("last_at", 0)) < ATTEMPT_WINDOW * 4
            or float(r.get("locked_until", 0)) > now
        ]
        entry = next((r for r in data["records"] if r.get("key") == key), None)
        if entry is None:
            entry = {"key": key, "failures": 0, "last_at": 0, "locked_until": 0}
            data["records"].append(entry)

        if now - float(entry.get("last_at", 0)) > ATTEMPT_WINDOW:
            entry["failures"] = 0

        entry["failures"] = int(entry.get("failures", 0)) + 1
        entry["last_at"] = now

        over = entry["failures"] - MAX_ATTEMPTS
        if over >= 0:
            wait = LOCKOUT_STEPS[min(over, len(LOCKOUT_STEPS) - 1)]
            entry["locked_until"] = now + wait
            return wait
        return 0


def clear_login_failures(key):
    """Wipe the counter after a successful sign-in."""
    with _mutate("security") as data:
        data["records"] = [r for r in data["records"] if r.get("key") != key]


# --- Leads ------------------------------------------------------------------

def create_lead(name, email, phone, zip_code, size, notes,
                source="contact", emailed=False) -> int:
    """Store a form submission and return its new id."""
    return _insert("leads", {
        **LEAD_DEFAULTS,
        "name": name, "email": email, "phone": phone, "zip_code": zip_code,
        "size": size, "notes": notes, "source": source, "status": "new",
        "emailed": 1 if emailed else 0, "created_at": now_iso(),
    })


def _lead_sort_key(lead):
    return (str(lead.get("created_at") or ""), lead.get("id", 0))


def get_lead(lead_id):
    return _find("leads", id=lead_id)


def all_leads() -> list:
    """Every lead, newest first."""
    return sorted(_all("leads"), key=_lead_sort_key, reverse=True)


def search_leads(status="", query="", page=1, per_page=20):
    """(rows, total, pages, page) for the leads table's filter + search + pager."""
    rows = all_leads()
    if status in LEAD_STATUSES:
        rows = [r for r in rows if r["status"] == status]
    if query:
        rows = [r for r in rows
                if _matches(r, ("name", "email", "phone", "zip_code"), query)]

    total = len(rows)
    pages = max(1, -(-total // per_page))       # ceil without importing math
    page = min(max(1, page), pages)
    start = (page - 1) * per_page
    return rows[start:start + per_page], total, pages, page


def lead_status_counts() -> dict:
    counts = {}
    for lead in _all("leads"):
        counts[lead["status"]] = counts.get(lead["status"], 0) + 1
    return counts


def count_leads(status=None) -> int:
    if status is None:
        return len(_load("leads")["records"])
    return sum(1 for lead in _all("leads") if lead["status"] == status)


def recent_leads(limit=5) -> list:
    return all_leads()[:limit]


def set_lead_status(lead_id, status) -> bool:
    return _update("leads", lead_id, {"status": status})


def mark_lead_emailed(lead_id) -> bool:
    return _update("leads", lead_id, {"emailed": 1})


def delete_lead(lead_id) -> bool:
    return _delete("leads", lead_id)


# --- Posts ------------------------------------------------------------------

def slugify(value: str) -> str:
    """URL-safe slug: lowercase, alphanumerics and single hyphens only."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "post"


def unique_slug(base: str, post_id=None) -> str:
    """slugify() plus a numeric suffix if another post already owns the slug."""
    slug = slugify(base)
    taken = {p["slug"] for p in _all("posts") if p["id"] != post_id}
    candidate, n = slug, 2
    while candidate in taken:
        candidate = "%s-%d" % (slug, n)
        n += 1
    return candidate


def _post_sort_key(post):
    return (str(post.get("updated_at") or ""), post.get("id", 0))


def get_post(post_id):
    return _find("posts", id=post_id)


def get_post_by_slug(slug, status=None):
    post = _find("posts", slug=slug)
    if post is None or (status is not None and post["status"] != status):
        return None
    return post


def all_posts() -> list:
    """Every post, most recently edited first."""
    return sorted(_all("posts"), key=_post_sort_key, reverse=True)


def search_posts(status="", query="") -> list:
    rows = all_posts()
    if status in POST_STATUSES:
        rows = [r for r in rows if r["status"] == status]
    if query:
        rows = [r for r in rows if _matches(r, ("title", "excerpt"), query)]
    return rows


def published_posts() -> list:
    """Published posts for the public blog, newest publish date first."""
    rows = [p for p in _all("posts") if p["status"] == "published"]
    return sorted(
        rows,
        key=lambda p: (str(p.get("published_at") or p.get("updated_at") or ""),
                       p.get("id", 0)),
        reverse=True,
    )


def post_status_counts() -> dict:
    counts = {}
    for post in _all("posts"):
        counts[post["status"]] = counts.get(post["status"], 0) + 1
    return counts


def count_posts(status=None) -> int:
    if status is None:
        return len(_load("posts")["records"])
    return sum(1 for post in _all("posts") if post["status"] == status)


def recent_posts(limit=5) -> list:
    return all_posts()[:limit]


def create_post(fields) -> int:
    return _insert("posts", {**POST_DEFAULTS, **fields})


def update_post(post_id, fields) -> bool:
    return _update("posts", post_id, fields)


def delete_post(post_id) -> bool:
    return _delete("posts", post_id)


def increment_views(post_id) -> bool:
    with _mutate("posts") as data:
        for post in data["records"]:
            if post.get("id") == post_id:
                post["views"] = int(post.get("views", 0)) + 1
                return True
        return False


def known_taxonomy():
    """Existing categories and tags, for the combobox and tag suggestions."""
    posts = _all("posts")
    categories = sorted({p["category"] for p in posts if p["category"]})
    tags = set()
    for post in posts:
        tags.update(parse_tags(post["tags"]))
    return categories, sorted(tags, key=str.lower)
