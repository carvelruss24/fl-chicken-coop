"""Admin dashboard blueprint — leads inbox, blog editor, account settings.

Everything lives under /admin and is gated by @login_required, which checks a
signed session cookie. Sign-in credentials come from the users store (see
db.py, which seeds the development login on first boot).

Kept deliberately dependency-free: no Flask-Login, no ORM, no database and no
build step — the data layer is a handful of JSON files behind db.py. The
templates in templates/admin/ extend templates/admin/base.html.
"""

import functools
import os
import re
import uuid
from datetime import datetime

from flask import (
    Blueprint, current_app, flash, g, jsonify, redirect, render_template,
    request, session, url_for,
)
from werkzeug.utils import secure_filename

import db
from db import (
    LEAD_STATUSES, POST_STATUSES, ROLE_CLIENT, ROLE_DEVELOPER,
    find_user_by_role, normalise_stamp, now_iso, parse_tags,
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
        g.user = db.get_user(user_id)


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


def _throttle_key(username: str) -> str:
    """Brute-force counters are per username AND per client address.

    Keyed on both so one attacker hammering an account can't lock the real
    owner out from their own address, while a single address spraying many
    usernames still gets throttled.
    """
    return "%s|%s" % ((username or "").lower(), request.remote_addr or "-")


def _humanise_wait(seconds: int) -> str:
    if seconds < 60:
        return "%d seconds" % seconds
    minutes = max(1, round(seconds / 60))
    return "1 minute" if minutes == 1 else "%d minutes" % minutes


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user") is not None:
        return redirect(url_for("admin.dashboard"))

    error = None
    username = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        throttle_key = _throttle_key(username)

        # Refuse to even check the password while locked out, so a stolen
        # username can't be brute-forced by volume.
        locked = db.lockout_seconds(throttle_key)
        if locked:
            error = ("Too many failed attempts. Try again in %s."
                     % _humanise_wait(locked))
            return render_template("admin/login.html", error=error,
                                   username=username)

        user = db.verify_login(username, password)

        # One generic message for both branches so the form can't be used to
        # enumerate valid usernames.
        if user is None:
            wait = db.record_login_failure(throttle_key)
            error = "Incorrect username or password."
            if wait:
                error += (" Too many failed attempts — try again in %s."
                          % _humanise_wait(wait))
            current_app.logger.warning("Failed dashboard sign-in for %r", username)
        else:
            db.clear_login_failures(throttle_key)
            session.clear()
            session["user_id"] = user["id"]
            session.permanent = True
            db.touch_last_login(user["id"])
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
    stats = {
        "leads_total": db.count_leads(),
        "leads_new": db.count_leads("new"),
        "posts_published": db.count_posts("published"),
        "posts_drafts": db.count_posts("draft"),
    }
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        recent_leads=db.recent_leads(5),
        recent_posts=db.recent_posts(5),
    )


# --- Leads ------------------------------------------------------------------

@bp.route("/leads")
@login_required
def leads():
    """Paginated leads table with a status filter and a name/email/phone search."""
    status = request.args.get("status", "").strip().lower()
    query = request.args.get("q", "").strip()
    page = max(1, request.args.get("page", 1, type=int) or 1)

    rows, total, pages, page = db.search_leads(
        status=status, query=query, page=page, per_page=PER_PAGE)

    return render_template(
        "admin/leads.html",
        leads=rows, total=total, page=page, pages=pages,
        status=status, q=query, counts=db.lead_status_counts(),
        statuses=LEAD_STATUSES,
    )


@bp.route("/leads/<int:lead_id>")
@login_required
def lead_detail(lead_id):
    row = db.get_lead(lead_id)
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
    elif db.set_lead_status(lead_id, new_status):
        flash("Lead marked " + new_status + ".", "success")
    else:
        flash("That lead no longer exists.", "error")
    return redirect(request.form.get("back") or url_for("admin.leads"))


@bp.route("/leads/<int:lead_id>/delete", methods=["POST"])
@login_required
def lead_delete(lead_id):
    db.delete_lead(lead_id)
    flash("Lead deleted.", "success")
    return redirect(url_for("admin.leads"))


# --- Blog posts -------------------------------------------------------------

@bp.route("/posts")
@login_required
def posts():
    status = request.args.get("status", "").strip().lower()
    query = request.args.get("q", "").strip()
    rows = db.search_posts(status=status, query=query)
    return render_template(
        "admin/posts.html", posts=rows, status=status, q=query,
        counts=db.post_status_counts(), statuses=POST_STATUSES, total=len(rows),
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


def _editor_context(post, is_new, post_id=None):
    """Everything the editor template needs beyond the post itself."""
    categories, tags = db.known_taxonomy()
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

        stamp = now_iso()
        who = g.user["display_name"] or g.user["username"]
        post_id = db.create_post({
            **data,
            "slug": unique_slug(data["slug"] or data["title"]),
            "author": who,
            "author_name": data["author_name"] or who,
            "created_at": stamp,
            "updated_at": stamp,
            "published_at": data["published_at"] or (
                stamp if data["status"] == "published" else None),
            "views": 0,
        })
        flash("Post published." if data["status"] == "published"
              else "Draft saved.", "success")
        return redirect(url_for("admin.post_edit", post_id=post_id))

    return render_template("admin/post_form.html", **_editor_context(None, True))


@bp.route("/posts/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
def post_edit(post_id):
    row = db.get_post(post_id)
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

        db.update_post(post_id, {
            **data,
            "slug": unique_slug(data["slug"] or data["title"], post_id=post_id),
            "updated_at": now_iso(),
            "published_at": published_at,
        })
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
    store and nothing has to be saved first.
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
    db.delete_post(post_id)
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


def _validate_new_login(username, password, confirm, exclude_id=None):
    """Shared checks for creating a login or resetting its password.

    Returns an error string, or None when everything is acceptable.
    """
    if username is not None:
        if not USERNAME_RE.match(username or ""):
            return ("Choose a username of 3–32 characters: lowercase letters, "
                    "numbers, dots, dashes or underscores.")
        if db.username_taken(username, exclude_id=exclude_id):
            return "That username is already taken."

    if len(password or "") < MIN_PASSWORD:
        return "Choose a password of at least %d characters." % MIN_PASSWORD
    if password != confirm:
        return "The passwords don't match."
    return None


@bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    if request.method == "POST":
        action = request.form.get("action", "profile")

        if action == "profile":
            db.update_user(
                g.user["id"],
                display_name=request.form.get("display_name", "").strip(),
                email=request.form.get("email", "").strip(),
            )
            flash("Profile saved.", "success")

        elif action == "password":
            current = request.form.get("current_password", "")
            new = request.form.get("new_password", "")
            confirm = request.form.get("confirm_password", "")

            if not db.check_user_password(g.user, current):
                flash("Your current password is incorrect.", "error")
            elif len(new) < MIN_PASSWORD:
                flash("Choose a new password of at least %d characters." % MIN_PASSWORD,
                      "error")
            elif new != confirm:
                flash("The new passwords do not match.", "error")
            else:
                db.update_user(g.user["id"], password=new)
                flash("Password changed.", "success")

        return redirect(url_for("admin.account"))

    return render_template("admin/account.html", **_account_context())


def _account_context():
    """Everything the Account page needs, shaped by who is signed in."""
    is_developer = g.user["role"] == ROLE_DEVELOPER
    client = find_user_by_role(ROLE_CLIENT) if is_developer else None
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
    if find_user_by_role(ROLE_CLIENT) is not None:
        flash("A client account already exists.", "error")
        return redirect(url_for("admin.account"))

    username = request.form.get("username", "").strip().lower()
    display_name = request.form.get("display_name", "").strip()
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "")
    confirm = request.form.get("confirm_password", password)

    error = _validate_new_login(username, password, confirm)
    if error:
        flash(error, "error")
        return redirect(url_for("admin.account"))

    db.create_user(username, password, display_name=display_name or "Client",
                   email=email, role=ROLE_CLIENT)
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
    client = find_user_by_role(ROLE_CLIENT)
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
    if db.username_taken(username, exclude_id=client["id"]):
        flash("That username is already taken.", "error")
        return redirect(url_for("admin.account"))

    db.update_user(client["id"], username=username,
                   display_name=display_name or "Client", email=email)
    flash("Client account updated.", "success")
    return redirect(url_for("admin.account"))


@bp.route("/account/client/password", methods=["POST"])
@developer_required
def client_password():
    """Set a new password for the client without knowing the old one."""
    client = find_user_by_role(ROLE_CLIENT)
    if client is None:
        flash("There is no client account yet.", "error")
        return redirect(url_for("admin.account"))

    password = request.form.get("password", "")
    confirm = request.form.get("confirm_password", password)
    error = _validate_new_login(None, password, confirm)
    if error:
        flash(error, "error")
        return redirect(url_for("admin.account"))

    db.update_user(client["id"], password=password)
    current_app.logger.info("Reset password for client '%s'.", client["username"])
    session["new_credentials"] = {"username": client["username"],
                                 "password": password, "kind": "reset"}
    return redirect(url_for("admin.account"))


@bp.route("/account/client/delete", methods=["POST"])
@developer_required
def client_delete():
    """Remove the client login. Their posts and leads are untouched."""
    client = find_user_by_role(ROLE_CLIENT)
    if client is None:
        flash("There is no client account yet.", "error")
        return redirect(url_for("admin.account"))

    # Belt and braces: the decorator already blocks non-developers, and a
    # developer can never be the client row, but deleting yourself would lock
    # the dashboard out entirely.
    if client["id"] == g.user["id"]:
        flash("You can't delete the account you're signed in with.", "error")
        return redirect(url_for("admin.account"))

    db.delete_user(client["id"])
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


@bp.app_template_filter("time_ago")
def time_ago(value):
    """'2026-08-28 07:10:00' -> '2 hours ago'; older than a week -> 'Aug 12, 2026'.

    The dashboard's activity lists read as a feed, so recent rows want a
    relative stamp. Past a week that stops being useful ("47 days ago" tells you
    nothing), so it falls back to the same absolute date `pretty_date` renders.
    """
    if not value:
        return "—"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            then = datetime.strptime(str(value), fmt)
            break
        except ValueError:
            continue
    else:
        return value

    seconds = (datetime.now() - then).total_seconds()
    if seconds < 0:                       # a post scheduled for the future
        return pretty_date(value)
    if seconds < 90:
        return "Just now"
    minutes = seconds / 60
    if minutes < 60:
        return "%d minutes ago" % round(minutes)
    hours = minutes / 60
    if hours < 24:
        n = round(hours)
        return "1 hour ago" if n == 1 else "%d hours ago" % n
    days = int(hours // 24)
    if days == 1:
        return "Yesterday"
    if days < 7:
        return "%d days ago" % days
    return pretty_date(value)


@bp.app_template_filter("initials")
def initials(value, count=2):
    """'Marcus Delgado' -> 'MD'. Feeds the round avatars in the activity lists.

    Takes the first letter of the first `count` words, so it works on a person's
    name and on a post title alike.
    """
    words = re.findall(r"[A-Za-z0-9]+", str(value or ""))
    return "".join(word[0] for word in words[:count]).upper() or "?"


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
