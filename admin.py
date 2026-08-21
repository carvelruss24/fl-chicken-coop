"""Admin dashboard blueprint — leads inbox, blog editor, account settings.

Everything lives under /admin and is gated by @login_required, which checks a
signed session cookie. Sign-in credentials come from the users table (see
db.py, which seeds the development login on first boot).

Kept deliberately dependency-free: no Flask-Login, no ORM, no build step. The
templates in templates/admin/ extend templates/admin/base.html.
"""

import functools
import math
import os
import re
import uuid
from datetime import datetime

from flask import (
    Blueprint, current_app, flash, g, jsonify, redirect, render_template,
    request, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from db import (
    LEAD_STATUSES, POST_STATUSES, ROLE_CLIENT, ROLE_DEVELOPER,
    find_user_by_role, get_db, normalise_stamp, now_iso, parse_tags,
    read_time_minutes, slugify, suggest_password, unique_slug,
)

bp = Blueprint("admin", __name__, url_prefix="/admin")

PER_PAGE = 20

# Labels shown in the UI for the stored source keys.
SOURCE_LABELS = {
    "contact": "Contact",
    "landing": "Landing",
    "phone": "Phone",
    "referral": "Referral",
    "manual": "Manual",
}


# --- Auth -------------------------------------------------------------------

@bp.before_app_request
def load_current_user():
    """Attach the signed-in user (or None) to g for every request."""
    user_id = session.get("user_id")
    g.user = None
    if user_id is not None:
        g.user = get_db().execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()


def login_required(view):
    """Redirect anonymous visitors to the sign-in page, remembering where they
    were headed so they land there after authenticating."""

    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.get("user") is None:
            return redirect(url_for("admin.login", next=request.full_path))
        return view(**kwargs)

    return wrapped


def _safe_next(target: str) -> str:
    """Only allow same-site relative redirects (blocks open-redirect abuse)."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("admin.dashboard")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user") is not None:
        return redirect(url_for("admin.dashboard"))

    error = None
    username = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        row = get_db().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()

        # One generic message for both branches so the form can't be used to
        # enumerate valid usernames.
        if row is None or not check_password_hash(row["password_hash"], password):
            error = "Incorrect username or password."
            current_app.logger.warning("Failed dashboard sign-in for %r", username)
        else:
            session.clear()
            session["user_id"] = row["id"]
            session.permanent = True
            conn = get_db()
            conn.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?",
                (now_iso(), row["id"]),
            )
            conn.commit()
            return redirect(_safe_next(request.args.get("next", "")))

    return render_template("admin/login.html", error=error, username=username)


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    flash("You have been signed out.", "success")
    return redirect(url_for("admin.login"))


# --- Dashboard --------------------------------------------------------------

@bp.route("/")
@login_required
def dashboard():
    conn = get_db()
    stats = {
        "leads_total": conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0],
        "leads_new": conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status = 'new'"
        ).fetchone()[0],
        "posts_published": conn.execute(
            "SELECT COUNT(*) FROM posts WHERE status = 'published'"
        ).fetchone()[0],
        "posts_drafts": conn.execute(
            "SELECT COUNT(*) FROM posts WHERE status = 'draft'"
        ).fetchone()[0],
    }
    recent_leads = conn.execute(
        "SELECT * FROM leads ORDER BY created_at DESC, id DESC LIMIT 5"
    ).fetchall()
    recent_posts = conn.execute(
        "SELECT * FROM posts ORDER BY updated_at DESC, id DESC LIMIT 5"
    ).fetchall()
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        recent_leads=recent_leads,
        recent_posts=recent_posts,
    )


# --- Leads ------------------------------------------------------------------

@bp.route("/leads")
@login_required
def leads():
    """Paginated leads table with a status filter and a name/email/phone search."""
    conn = get_db()
    status = request.args.get("status", "").strip().lower()
    query = request.args.get("q", "").strip()
    page = max(1, request.args.get("page", 1, type=int) or 1)

    where, params = [], []
    if status in LEAD_STATUSES:
        where.append("status = ?")
        params.append(status)
    if query:
        where.append("(name LIKE ? OR email LIKE ? OR phone LIKE ? OR zip_code LIKE ?)")
        params += ["%" + query + "%"] * 4
    clause = "WHERE " + " AND ".join(where) if where else ""

    total = conn.execute(
        "SELECT COUNT(*) FROM leads " + clause, params
    ).fetchone()[0]
    pages = max(1, math.ceil(total / PER_PAGE))
    page = min(page, pages)
    rows = conn.execute(
        "SELECT * FROM leads " + clause
        + " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        params + [PER_PAGE, (page - 1) * PER_PAGE],
    ).fetchall()

    counts = {
        row["status"]: row["n"]
        for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM leads GROUP BY status"
        ).fetchall()
    }
    return render_template(
        "admin/leads.html",
        leads=rows, total=total, page=page, pages=pages,
        status=status, q=query, counts=counts, statuses=LEAD_STATUSES,
    )


@bp.route("/leads/<int:lead_id>")
@login_required
def lead_detail(lead_id):
    row = get_db().execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if row is None:
        flash("That lead no longer exists.", "error")
        return redirect(url_for("admin.leads"))
    return render_template("admin/lead_detail.html", lead=row, statuses=LEAD_STATUSES)


@bp.route("/leads/<int:lead_id>/status", methods=["POST"])
@login_required
def lead_status(lead_id):
    new_status = request.form.get("status", "").strip().lower()
    if new_status not in LEAD_STATUSES:
        flash("Unknown status.", "error")
    else:
        conn = get_db()
        conn.execute("UPDATE leads SET status = ? WHERE id = ?", (new_status, lead_id))
        conn.commit()
        flash("Lead marked " + new_status + ".", "success")
    return redirect(request.form.get("back") or url_for("admin.leads"))


@bp.route("/leads/<int:lead_id>/delete", methods=["POST"])
@login_required
def lead_delete(lead_id):
    conn = get_db()
    conn.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    conn.commit()
    flash("Lead deleted.", "success")
    return redirect(url_for("admin.leads"))


# --- Blog posts -------------------------------------------------------------

@bp.route("/posts")
@login_required
def posts():
    conn = get_db()
    status = request.args.get("status", "").strip().lower()
    query = request.args.get("q", "").strip()

    where, params = [], []
    if status in POST_STATUSES:
        where.append("status = ?")
        params.append(status)
    if query:
        where.append("(title LIKE ? OR excerpt LIKE ?)")
        params += ["%" + query + "%"] * 2
    clause = "WHERE " + " AND ".join(where) if where else ""

    rows = conn.execute(
        "SELECT * FROM posts " + clause + " ORDER BY updated_at DESC, id DESC",
        params,
    ).fetchall()
    counts = {
        row["status"]: row["n"]
        for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM posts GROUP BY status"
        ).fetchall()
    }
    return render_template(
        "admin/posts.html", posts=rows, status=status, q=query,
        counts=counts, statuses=POST_STATUSES, total=len(rows),
    )


def _post_from_form():
    """Pull and normalise every editor field shared by create and update.

    The two primary buttons are authoritative over the Status select: "Save
    Draft" always stores a draft and "Create Post"/"Publish" always publishes,
    so a mis-synced select can never publish something by accident.
    """
    action = request.form.get("action", "").strip().lower()
    status = request.form.get("status", "draft").strip().lower()
    if action == "draft":
        status = "draft"
    elif action == "publish":
        status = "published"
    if status not in POST_STATUSES:
        status = "draft"

    body = request.form.get("body", "").strip()
    return {
        "title": request.form.get("title", "").strip(),
        "slug": request.form.get("slug", "").strip(),
        "overview": request.form.get("overview", "").strip(),
        "body": body,
        # Meta Description in the SEO tab is the same field the blog index uses
        # as the card excerpt — one value, two labels, never out of sync.
        "excerpt": request.form.get("excerpt", "").strip(),
        "seo_title": request.form.get("seo_title", "").strip(),
        "cover_image": request.form.get("cover_image", "").strip(),
        "caption": request.form.get("caption", "").strip(),
        "author_name": request.form.get("author_name", "").strip(),
        "author_avatar": request.form.get("author_avatar", "").strip(),
        "category": request.form.get("category", "").strip(),
        "tags": ", ".join(parse_tags(request.form.get("tags", ""))),
        # datetime-local -> our stored format; None when left blank.
        "published_at": normalise_stamp(request.form.get("published_at", "")),
        "status": status,
        "read_time": read_time_minutes(body),
    }


def _known_taxonomy():
    """Existing categories and tags, for the combobox and tag suggestions."""
    conn = get_db()
    categories = sorted({
        row["category"] for row in
        conn.execute("SELECT DISTINCT category FROM posts WHERE category != ''")
    })
    tags = set()
    for row in conn.execute("SELECT tags FROM posts WHERE tags != ''"):
        tags.update(parse_tags(row["tags"]))
    return categories, sorted(tags, key=str.lower)


def _editor_context(post, is_new, post_id=None):
    """Everything the editor template needs beyond the post itself."""
    categories, tags = _known_taxonomy()
    return {
        "post": post,
        "is_new": is_new,
        "post_id": post_id,
        "statuses": POST_STATUSES,
        "all_categories": categories,
        "all_tags": tags,
        "default_author": (g.user["display_name"] or g.user["username"]),
    }


@bp.route("/posts/new", methods=["GET", "POST"])
@login_required
def post_new():
    if request.method == "POST":
        data = _post_from_form()
        if not data["title"]:
            flash("A title is required.", "error")
            return render_template("admin/post_form.html",
                                   **_editor_context(data, True))

        conn = get_db()
        stamp = now_iso()
        published_at = data["published_at"] or (
            stamp if data["status"] == "published" else None)
        cur = conn.execute(
            """INSERT INTO posts
                   (title, slug, excerpt, body, cover_image, status, author,
                    created_at, updated_at, published_at, overview, caption,
                    author_name, author_avatar, category, tags, seo_title,
                    views, read_time)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)""",
            (
                data["title"],
                unique_slug(data["slug"] or data["title"]),
                data["excerpt"], data["body"], data["cover_image"],
                data["status"],
                g.user["display_name"] or g.user["username"],
                stamp, stamp, published_at,
                data["overview"], data["caption"],
                data["author_name"] or (g.user["display_name"] or g.user["username"]),
                data["author_avatar"], data["category"], data["tags"],
                data["seo_title"], data["read_time"],
            ),
        )
        conn.commit()
        flash("Post published." if data["status"] == "published"
              else "Draft saved.", "success")
        return redirect(url_for("admin.post_edit", post_id=cur.lastrowid))

    return render_template("admin/post_form.html", **_editor_context(None, True))


@bp.route("/posts/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
def post_edit(post_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if row is None:
        flash("That post no longer exists.", "error")
        return redirect(url_for("admin.posts"))

    if request.method == "POST":
        data = _post_from_form()
        if not data["title"]:
            flash("A title is required.", "error")
            return render_template("admin/post_form.html",
                                   **_editor_context(data, False, post_id))

        # An explicit publish date wins; otherwise stamp the first time a post
        # goes live and keep whatever was already there after that.
        published_at = data["published_at"] or row["published_at"]
        if data["status"] == "published" and not published_at:
            published_at = now_iso()

        conn.execute(
            """UPDATE posts SET title = ?, slug = ?, excerpt = ?, body = ?,
                   cover_image = ?, status = ?, updated_at = ?, published_at = ?,
                   overview = ?, caption = ?, author_name = ?, author_avatar = ?,
                   category = ?, tags = ?, seo_title = ?, read_time = ?
               WHERE id = ?""",
            (
                data["title"],
                unique_slug(data["slug"] or data["title"], post_id=post_id),
                data["excerpt"], data["body"], data["cover_image"],
                data["status"], now_iso(), published_at,
                data["overview"], data["caption"],
                data["author_name"], data["author_avatar"],
                data["category"], data["tags"], data["seo_title"],
                data["read_time"], post_id,
            ),
        )
        conn.commit()
        flash("Post published." if data["status"] == "published"
              else "Changes saved.", "success")
        return redirect(url_for("admin.post_edit", post_id=post_id))

    return render_template("admin/post_form.html",
                           **_editor_context(row, False, post_id))


@bp.route("/posts/preview", methods=["POST"])
@login_required
def post_preview():
    """Render the public post template from unsaved editor input.

    The Preview button posts the live form here with target="_blank", so what
    you see is exactly the draft in front of you — nothing is written to the
    database and nothing has to be saved first.
    """
    data = _post_from_form()
    preview = dict(data)
    preview["author"] = data["author_name"] or (
        g.user["display_name"] or g.user["username"])
    preview["slug"] = data["slug"] or slugify(data["title"] or "preview")
    preview["updated_at"] = now_iso()
    preview["published_at"] = data["published_at"] or now_iso()
    preview["views"] = 0
    return render_template("blog_post.html", post=preview, is_preview=True)


@bp.route("/posts/<int:post_id>/delete", methods=["POST"])
@login_required
def post_delete(post_id):
    conn = get_db()
    conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    conn.commit()
    flash("Post deleted.", "success")
    return redirect(url_for("admin.posts"))


# --- Image uploads ----------------------------------------------------------
# Used by three things: the Featured Image dropzone, the author avatar picker,
# and TinyMCE's own image button. All three POST here and get back JSON with a
# `location` key, which is the shape TinyMCE's images_upload_handler expects.

UPLOAD_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
UPLOAD_DIRNAME = "uploads"


def _safe_upload_name(filename: str) -> str:
    """A collision-proof, path-traversal-proof name that keeps the extension."""
    stem, ext = os.path.splitext(secure_filename(filename or "image"))
    ext = ext.lower()
    if ext not in UPLOAD_EXTENSIONS:
        raise ValueError("unsupported file type")
    stem = (stem or "image")[:60]
    return "%s-%s%s" % (stem, uuid.uuid4().hex[:10], ext)


@bp.route("/uploads", methods=["POST"])
@login_required
def upload():
    """Store one uploaded image under static/uploads/YYYY/MM/ and return its URL."""
    file = request.files.get("file")
    if file is None or not file.filename:
        return jsonify({"error": "No file was received."}), 400

    try:
        name = _safe_upload_name(file.filename)
    except ValueError:
        return jsonify({
            "error": "Unsupported file type. Use PNG, JPG, WEBP or GIF."
        }), 400

    stamp = datetime.now()
    rel_dir = "%s/%s/%s" % (UPLOAD_DIRNAME, stamp.strftime("%Y"),
                            stamp.strftime("%m"))
    abs_dir = os.path.join(current_app.static_folder, *rel_dir.split("/"))
    os.makedirs(abs_dir, exist_ok=True)
    file.save(os.path.join(abs_dir, name))

    url = url_for("static", filename="%s/%s" % (rel_dir, name))
    current_app.logger.info("Uploaded %s", url)
    return jsonify({"location": url, "url": url})

# --- Account ----------------------------------------------------------------
# Two kinds of login share this page:
#
#   developer  the agency account seeded on first boot. Sees its own profile
#              AND a panel to provision / manage the client's login.
#   client     the business's own account. Same access to leads and posts, but
#              it only ever sees its own profile — never the developer's.
#
# There is at most ONE client account, which is what makes the "no client
# account exists yet" state meaningful.

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
MIN_PASSWORD = 10


def developer_required(view):
    """Gate a view to the developer account.

    Anything that provisions or edits another login lives behind this. A client
    hitting one of these is bounced with a message rather than a bare 403, since
    the only way to get here is a hand-typed URL.
    """

    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.get("user") is None:
            return redirect(url_for("admin.login", next=request.full_path))
        if g.user["role"] != ROLE_DEVELOPER:
            flash("Only the developer account can manage other logins.", "error")
            return redirect(url_for("admin.account"))
        return view(**kwargs)

    return wrapped


def _validate_new_login(conn, username, password, confirm, exclude_id=None):
    """Shared checks for creating a login or resetting its password.

    Returns an error string, or None when everything is acceptable.
    """
    if username is not None:
        if not USERNAME_RE.match(username or ""):
            return ("Choose a username of 3–32 characters: lowercase letters, "
                    "numbers, dots, dashes or underscores.")
        clash = conn.execute(
            "SELECT id FROM users WHERE username = ? AND (? IS NULL OR id != ?)",
            (username, exclude_id, exclude_id),
        ).fetchone()
        if clash is not None:
            return "That username is already taken."

    if len(password or "") < MIN_PASSWORD:
        return "Choose a password of at least %d characters." % MIN_PASSWORD
    if password != confirm:
        return "The passwords don't match."
    return None


@bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    conn = get_db()

    if request.method == "POST":
        action = request.form.get("action", "profile")

        if action == "profile":
            display_name = request.form.get("display_name", "").strip()
            email = request.form.get("email", "").strip()
            conn.execute(
                "UPDATE users SET display_name = ?, email = ? WHERE id = ?",
                (display_name, email, g.user["id"]),
            )
            conn.commit()
            flash("Profile saved.", "success")

        elif action == "password":
            current = request.form.get("current_password", "")
            new = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")

            if not check_password_hash(g.user["password_hash"], current):
                flash("Your current password is incorrect.", "error")
            elif len(new) < MIN_PASSWORD:
                flash("Choose a new password of at least %d characters." % MIN_PASSWORD,
                      "error")
            elif new != confirm:
                flash("The new passwords do not match.", "error")
            else:
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(new), g.user["id"]),
                )
                conn.commit()
                flash("Password changed.", "success")

        return redirect(url_for("admin.account"))

    return render_template("admin/account.html", **_account_context())


def _account_context():
    """Everything the Account page needs, shaped by who is signed in."""
    conn = get_db()
    is_developer = g.user["role"] == ROLE_DEVELOPER
    client = find_user_by_role(conn, ROLE_CLIENT) if is_developer else None
    return {
        "is_developer": is_developer,
        "client": client,
        # Sensible defaults for the create-client form, so provisioning is a
        # matter of reviewing rather than inventing.
        "suggested": {
            "username": os.getenv("CLIENT_USERNAME", "flchickencoops").strip()
            or "flchickencoops",
            "display_name": "Florida Chicken Coops",
            "email": current_app.config.get("CONTACT_RECEIVER", ""),
            "password": suggest_password(),
        },
        # Shown once, immediately after creating or resetting, so the developer
        # can hand it over. Never stored anywhere — only the hash is.
        "new_credentials": session.pop("new_credentials", None),
    }


@bp.route("/account/client", methods=["POST"])
@developer_required
def client_create():
    """Provision the single client login."""
    conn = get_db()
    if find_user_by_role(conn, ROLE_CLIENT) is not None:
        flash("A client account already exists.", "error")
        return redirect(url_for("admin.account"))

    username = request.form.get("username", "").strip().lower()
    display_name = request.form.get("display_name", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm_password", password)

    error = _validate_new_login(conn, username, password, confirm)
    if error:
        flash(error, "error")
        return redirect(url_for("admin.account"))

    conn.execute(
        """INSERT INTO users
               (username, password_hash, display_name, email, created_at, role)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (username, generate_password_hash(password),
         display_name or "Client", email, now_iso(), ROLE_CLIENT),
    )
    conn.commit()
    current_app.logger.info("Created client account '%s'.", username)

    # Surfaced once on the next render so it can be copied and handed over.
    # No flash here: the handover card that renders next is itself the
    # confirmation, and two identical messages stacked read as a glitch.
    session["new_credentials"] = {"username": username, "password": password,
                                 "kind": "created"}
    return redirect(url_for("admin.account"))


@bp.route("/account/client/profile", methods=["POST"])
@developer_required
def client_profile():
    """Rename the client account (username, display name, email)."""
    conn = get_db()
    client = find_user_by_role(conn, ROLE_CLIENT)
    if client is None:
        flash("There is no client account yet.", "error")
        return redirect(url_for("admin.account"))

    username = request.form.get("username", "").strip().lower()
    display_name = request.form.get("display_name", "").strip()
    email = request.form.get("email", "").strip()

    if not USERNAME_RE.match(username):
        flash("Choose a username of 3–32 characters: lowercase letters, numbers, "
              "dots, dashes or underscores.", "error")
        return redirect(url_for("admin.account"))
    clash = conn.execute("SELECT id FROM users WHERE username = ? AND id != ?",
                         (username, client["id"])).fetchone()
    if clash is not None:
        flash("That username is already taken.", "error")
        return redirect(url_for("admin.account"))

    conn.execute(
        "UPDATE users SET username = ?, display_name = ?, email = ? WHERE id = ?",
        (username, display_name or "Client", email, client["id"]),
    )
    conn.commit()
    flash("Client account updated.", "success")
    return redirect(url_for("admin.account"))


@bp.route("/account/client/password", methods=["POST"])
@developer_required
def client_password():
    """Set a new password for the client without knowing the old one."""
    conn = get_db()
    client = find_user_by_role(conn, ROLE_CLIENT)
    if client is None:
        flash("There is no client account yet.", "error")
        return redirect(url_for("admin.account"))

    password = request.form.get("password", "")
    confirm = request.form.get("confirm_password", password)
    error = _validate_new_login(conn, None, password, confirm)
    if error:
        flash(error, "error")
        return redirect(url_for("admin.account"))

    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                 (generate_password_hash(password), client["id"]))
    conn.commit()
    current_app.logger.info("Reset password for client '%s'.", client["username"])
    session["new_credentials"] = {"username": client["username"],
                                 "password": password, "kind": "reset"}
    return redirect(url_for("admin.account"))


@bp.route("/account/client/delete", methods=["POST"])
@developer_required
def client_delete():
    """Remove the client login. Their posts and leads are untouched."""
    conn = get_db()
    client = find_user_by_role(conn, ROLE_CLIENT)
    if client is None:
        flash("There is no client account yet.", "error")
        return redirect(url_for("admin.account"))

    # Belt and braces: the decorator already blocks non-developers, and a
    # developer can never be the client row, but deleting yourself would lock
    # the dashboard out entirely.
    if client["id"] == g.user["id"]:
        flash("You can't delete the account you're signed in with.", "error")
        return redirect(url_for("admin.account"))

    conn.execute("DELETE FROM users WHERE id = ?", (client["id"],))
    conn.commit()
    current_app.logger.info("Deleted client account '%s'.", client["username"])
    flash("Client account deleted. Blog posts and leads were not affected.",
          "success")
    return redirect(url_for("admin.account"))

# --- Agency brand mark ------------------------------------------------------
# The Smash Interactive wordmark stands in for the initials badge in the topbar
# once the asset exists. Any of these filenames works, so saving the logo as
# svg, png, or webp all just work with no code change. Blueprint-scoped, so the
# public marketing pages never pay for the lookup.
SMASH_LOGO_FILES = (
    "img/smash-logo.svg",
    "img/smash-logo.png",
    "img/smash-logo.webp",
)


@bp.context_processor
def inject_smash_logo():
    """Static URL of the agency wordmark, or None if it hasn't been added yet.

    Resolved per request (a few cheap stat calls on an admin-only page) so
    dropping the file into static/img/ takes effect without a restart. When it
    is absent the topbar falls back to the initials badge.
    """
    for rel in SMASH_LOGO_FILES:
        if os.path.exists(os.path.join(current_app.static_folder, rel)):
            return {"smash_logo": url_for("static", filename=rel)}
    return {"smash_logo": None}


# --- Template helpers -------------------------------------------------------

@bp.app_template_filter("pretty_date")
def pretty_date(value, with_time=False):
    """'2026-08-21 14:03:11' -> 'Aug 21, 2026' (or '... at 2:03 PM')."""
    if not value:
        return "—"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(str(value), fmt)
        except ValueError:
            continue
        if with_time:
            return dt.strftime("%b %d, %Y at %I:%M %p").replace(" 0", " ")
        return dt.strftime("%b %d, %Y")
    return value


@bp.app_template_filter("tag_list")
def tag_list(value):
    """Comma-separated tag string -> list, for rendering chips."""
    return parse_tags(value)


@bp.app_template_filter("dt_local")
def dt_local(value):
    """Stored timestamp -> the value an <input type="datetime-local"> expects."""
    if not value:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value), fmt).strftime("%Y-%m-%dT%H:%M")
        except ValueError:
            continue
    return ""


@bp.context_processor
def inject_seo_domain():
    """Domain shown in the editor's search-result preview."""
    return {"seo_domain": os.getenv("SITE_DOMAIN", "www.flchickencoops.com").strip()}


@bp.app_template_filter("source_label")
def source_label(value):
    return SOURCE_LABELS.get(value, (value or "—").title())
