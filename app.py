"""Florida Chicken Coops — Flask application.

Multi-page marketing site. The shared header/footer live in Jinja partials
(templates/partials/) and are pulled into every page via template inheritance
(templates/base.html). Navigation data is defined here ONCE and injected into
all templates through a context processor, so labels/links change in one place.

Only the Home page exists so far. The other nav links point at their intended
URLs and will resolve once we build each page (add a route + a template). The
active nav link is derived from the request path, so it keeps working as pages
come online — no nav changes required.
"""

import os
import html
import logging
import re
from datetime import datetime, timedelta

import requests
from flask import Flask, abort, render_template, request, redirect, url_for
from dotenv import load_dotenv

import db
from admin import bp as admin_bp

# Load Resend / recipient settings from a local .env file (never committed).
load_dotenv()

app = Flask(__name__)

app.config.update(
    # --- Sessions (the admin dashboard signs users in with a signed cookie) ---
    # Set a long random FLASK_SECRET_KEY in .env for production: without it the
    # fallback below changes on every restart, which logs everyone out.
    SECRET_KEY=os.getenv("FLASK_SECRET_KEY") or os.urandom(32),
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Sent over HTTPS only once you're behind TLS — enable via .env in prod.
    SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "0").strip().lower()
    in ("1", "true", "yes"),

    # Largest request body we accept — the ceiling for a featured-image upload.
    # Flask raises 413 past this, which the editor's dropzone reports inline.
    MAX_CONTENT_LENGTH=int(os.getenv("MAX_UPLOAD_MB", "8")) * 1024 * 1024,

    # --- Resend (HTTPS email API that sends the notification — no SMTP) ---
    # send_contact_email() POSTs to Resend's REST API over HTTPS, which sidesteps
    # VPS port-25/SMTP blocks and gives inbox-grade deliverability (SPF/DKIM are
    # handled by Resend once flchickencoops.com is verified in their dashboard).
    RESEND_API_KEY=os.getenv("RESEND_API_KEY", ""),
    # Must be an address on a domain you've verified in Resend. The display-name
    # form is allowed: "Florida Chicken Coops <quotes@flchickencoops.com>".
    RESEND_FROM=os.getenv(
        "RESEND_FROM", "Florida Chicken Coops <quotes@flchickencoops.com>"
    ),

    # --- Recipients of every form submission ---
    # Falls back to the business inbox if CONTACT_RECEIVER is unset.
    CONTACT_RECEIVER=os.getenv("CONTACT_RECEIVER", "mitchell@flchickencoops.com"),
    CONTACT_CC=os.getenv("CONTACT_CC", ""),  # comma-separated, optional

    # --- reCAPTCHA ---
    # Disabled for now per request. Flip ENABLE_RECAPTCHA=1 in .env and fill the
    # keys to turn verification back on without touching this file.
    ENABLE_RECAPTCHA=os.getenv("ENABLE_RECAPTCHA", "0").strip().lower()
    in ("1", "true", "yes"),
    RECAPTCHA_SECRET=os.getenv("RECAPTCHA_SECRET", ""),
    RECAPTCHA_SITE_KEY=os.getenv("RECAPTCHA_SITE_KEY", ""),
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create the SQLite schema (and seed the dashboard login) before serving, then
# mount the /admin dashboard.
db.init_db(app)
app.register_blueprint(admin_bp)

# Human-readable labels for the coop-size <select> values, used in the email.
SIZE_LABELS = {
    "small": "Small — 8′ × 6′",
    "medium": "Medium — 12′ × 6′",
    "large": "Large — 16′ × 6′",
    "custom": "Custom build",
    "unsure": "Not sure yet",
    "": "Not specified",
}

# --- Single source of truth for site chrome ---------------------------------
# Plain URL paths for now (not url_for) because most target pages don't exist
# yet. Swap to url_for once a page has its own route, if you prefer.

NAV_LINKS = [
    {"label": "Home",         "url": "/"},
    {"label": "Our Coops",    "url": "/our-coops"},
    {"label": "How We Build", "url": "/how-we-build"},
    {"label": "Gallery",      "url": "/gallery"},
    {"label": "About",        "url": "/about"},
    {"label": "FAQs",         "url": "/faqs"},
    {"label": "Service Area", "url": "/service-area"},
]

FOOTER_COLUMNS = [
    {
        "heading": "Products",
        "links": [
            {"label": "Our Coops",     "url": "/our-coops"},
            {"label": "Custom Builds", "url": "/our-coops#custom-builds"},
        ],
    },
    {
        "heading": "Quick Links",
        "links": [
            {"label": "About",        "url": "/about"},
            {"label": "How We Build", "url": "/how-we-build"},
            {"label": "FAQs",         "url": "/faqs"},
            {"label": "Gallery",      "url": "/gallery"},
        ],
    },
    {
        "heading": "Service Area",
        "links": [
            {"label": "All of Florida",  "url": "/service-area"},
            {"label": "Request a Quote", "url": "/contact"},
        ],
    },
]

CONTACT = {
    "cta": {"label": "Contact Us", "url": "/contact"},
    "email": "mitchell@FlChickenCoops.com",
    "phone": "(305) 431-6505",
    "phone_href": "tel:+13054316505",
    # Click-to-chat link (wa.me) using the business number in international
    # format, with a prefilled inquiry message customers can edit before sending.
    "whatsapp_href": (
        "https://wa.me/13054316505"
        "?text=Hi%20Florida%20Chicken%20Coops%2C%20I%27d%20like%20to"
        "%20request%20a%20quote%20for%20a%20chicken%20coop."
    ),
}


# Block-level tags that mean "this body is already HTML" (TinyMCE output).
_BLOCK_TAG_RE = re.compile(
    r"<\s*(p|div|h[1-6]|ul|ol|li|blockquote|pre|table|figure|section)\b", re.I)


@app.template_filter("post_html")
def post_html(value):
    """Render a post body.

    The editor is TinyMCE, so a saved body is normally already valid HTML and
    gets passed through untouched. Bodies that are plain text (older posts, or
    a plain-text import) still get blank-line-to-paragraph conversion, so both
    kinds render correctly and neither ends up double-wrapped.

    Author markup is intentionally NOT escaped — the editor sits behind a
    login, so only trusted content reaches this filter.
    """
    from markupsafe import Markup

    if not value:
        return ""
    text = str(value)
    if _BLOCK_TAG_RE.search(text):
        return Markup(text)

    blocks = [b.strip() for b in text.replace("\r\n", "\n").split("\n\n")]
    return Markup("".join(
        "<p>{}</p>".format(b.replace("\n", "<br />")) for b in blocks if b
    ))



@app.context_processor
def inject_chrome():
    """Make shared chrome data available to every template."""
    return {
        "nav_links": NAV_LINKS,
        "footer_columns": FOOTER_COLUMNS,
        "contact": CONTACT,
        "current_year": datetime.now().year,
    }


# --- Form delivery ----------------------------------------------------------

def verify_recaptcha(token: str) -> bool:
    """Verify a reCAPTCHA v2 token with Google.

    reCAPTCHA is currently DISABLED (ENABLE_RECAPTCHA defaults to off), so this
    short-circuits to True and the form submits without a challenge. To turn it
    on later: set ENABLE_RECAPTCHA=1 and the RECAPTCHA_* keys in .env, then add
    the widget to the form in templates/contact.html.
    """
    if not app.config["ENABLE_RECAPTCHA"]:
        return True

    secret = app.config["RECAPTCHA_SECRET"].strip()
    if not secret:
        logger.warning("ENABLE_RECAPTCHA is on but RECAPTCHA_SECRET is unset — skipping.")
        return True

    try:
        resp = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={"secret": secret, "response": token.strip()},
            timeout=5,
        )
        return bool(resp.json().get("success", False))
    except Exception as exc:
        logger.error("reCAPTCHA verification failed: %s", exc)
        return False


def send_contact_email(name, email, phone, zip_code, size, notes) -> bool:
    """Email a contact/quote-request submission to the business. Returns success.

    Delivery goes through Resend's HTTPS API (no SMTP). Needs RESEND_API_KEY and
    a RESEND_FROM address on a domain verified in the Resend dashboard.
    """
    cfg = app.config

    if not cfg["RESEND_API_KEY"]:
        logger.error("RESEND_API_KEY not configured — cannot send email.")
        return False

    receiver = cfg["CONTACT_RECEIVER"]
    cc_list = [addr.strip() for addr in cfg["CONTACT_CC"].split(",") if addr.strip()]

    size_label = SIZE_LABELS.get(size, size or "Not specified")
    submitted_at = datetime.now().strftime("%B %d, %Y at %I:%M %p")

    # Escape user-supplied values before dropping them into the HTML email so a
    # submission can't inject markup into the recipient's inbox.
    e_name, e_email, e_phone, e_zip, e_size, e_notes = (
        html.escape(v) for v in (name, email, phone, zip_code, size_label, notes)
    )

    html_body = f"""\
    <h2 style="font-family:Georgia,serif;color:#2b2b2b;">New Quote Request</h2>
    <p style="color:#666;font-size:13px;">Submitted {submitted_at}</p>
    <table cellpadding="6" style="font-family:Arial,sans-serif;font-size:14px;border-collapse:collapse;">
      <tr><td><strong>Name</strong></td><td>{e_name}</td></tr>
      <tr><td><strong>Email</strong></td><td>{e_email}</td></tr>
      <tr><td><strong>Phone</strong></td><td>{e_phone}</td></tr>
      <tr><td><strong>Zip Code</strong></td><td>{e_zip}</td></tr>
      <tr><td><strong>Coop Size</strong></td><td>{e_size}</td></tr>
      <tr><td valign="top"><strong>Notes</strong></td><td>{e_notes or "—"}</td></tr>
    </table>
    """
    text_body = (
        f"New Quote Request (submitted {submitted_at})\n\n"
        f"Name:      {name}\n"
        f"Email:     {email}\n"
        f"Phone:     {phone}\n"
        f"Zip Code:  {zip_code}\n"
        f"Coop Size: {size_label}\n"
        f"Notes:     {notes or '—'}\n"
    )
    payload = {
        "from": cfg["RESEND_FROM"],
        "to": [receiver],
        "subject": f"New Quote Request from {name} — Florida Chicken Coops",
        "html": html_body,
        "text": text_body,
    }
    if cc_list:
        payload["cc"] = cc_list
    # Let the business reply straight to the customer.
    if email:
        payload["reply_to"] = email

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {cfg['RESEND_API_KEY']}"},
            json=payload,
            timeout=15,
        )
        if resp.status_code >= 400:
            logger.error("Resend API error %s: %s", resp.status_code, resp.text)
            return False
        logger.info("Contact email sent to %s via Resend", [receiver] + cc_list)
        return True
    except Exception as exc:
        logger.error("Resend send failed: %s", exc)
        return False


# --- Routes -----------------------------------------------------------------
# Add one view + template per page as the designs arrive.

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/our-coops")
def our_coops():
    return render_template("our-models.html")


@app.route("/how-we-build")
def how_we_build():
    return render_template("how-we-build.html")


@app.route("/gallery")
def gallery():
    return render_template("gallery.html")


@app.route("/about")
def about():
    return render_template("about-us.html")


@app.route("/faqs")
def faqs():
    return render_template("faq.html")


@app.route("/service-area")
def service_area():
    return render_template("service-area.html")


@app.route("/service-area-2")
def service_area_2():
    return render_template("service-area-2.html")


@app.route("/service-area-3")
def service_area_3():
    return render_template("service-area-3.html")


@app.route("/service-area-4")
def service_area_4():
    return render_template("service-area-4.html")


@app.route("/contact", methods=["GET", "POST"])
def contact():
    """Render the quote-request form and email each submission to the business."""
    if request.method != "POST":
        return render_template("contact.html")

    # 1. reCAPTCHA (currently disabled — verify_recaptcha returns True).
    if not verify_recaptcha(request.form.get("g-recaptcha-response", "")):
        return render_template("contact.html", error=True)

    # 2. Collect and lightly validate the required fields.
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    phone = request.form.get("phone", "").strip()
    zip_code = request.form.get("zip", "").strip()
    size = request.form.get("size", "").strip()
    notes = request.form.get("notes", "").strip()

    if not all([name, email, phone, zip_code]):
        logger.warning("Contact form submitted with missing required fields.")
        return render_template("contact.html", error=True)

    # 3. Store the lead BEFORE emailing, so a Resend outage can never lose a
    #    submission — it still shows up in the dashboard either way. The form
    #    may declare its own source (e.g. a landing page); default to "contact".
    source = request.form.get("source", "contact").strip().lower() or "contact"
    lead_id = None
    try:
        lead_id = db.create_lead(name, email, phone, zip_code, size, notes,
                                 source=source)
    except Exception as exc:
        logger.error("Could not store lead in the dashboard: %s", exc)

    # 4. Send the notification email. On failure, keep the form and warn — the
    #    lead itself is already saved above.
    if not send_contact_email(name, email, phone, zip_code, size, notes):
        return render_template("contact.html", error=True)

    if lead_id is not None:
        try:
            conn = db.get_db()
            conn.execute("UPDATE leads SET emailed = 1 WHERE id = ?", (lead_id,))
            conn.commit()
        except Exception as exc:
            logger.error("Could not flag lead %s as emailed: %s", lead_id, exc)

    # 5. Success — redirect to the dedicated thank-you page. Using Post/Redirect/
    #    Get means a browser refresh lands on /thank-you instead of re-submitting.
    return redirect(url_for("thank_you"))


# --- Blog (public side of the dashboard's Blog Posts section) ----------------
# Lives at /blogs/ to match the permalink shown in the post editor. Not linked
# from the main nav yet — add {"label": "Blog", "url": "/blogs"} to NAV_LINKS
# above whenever you want it in the header.

@app.route("/blogs")
def blog():
    """List every published post, newest first."""
    posts = db.get_db().execute(
        """SELECT * FROM posts WHERE status = 'published'
           ORDER BY COALESCE(published_at, updated_at) DESC, id DESC"""
    ).fetchall()
    return render_template("blog.html", posts=posts)


@app.route("/blogs/<slug>")
def blog_post(slug):
    """A single published post. Drafts 404 until they're published."""
    conn = db.get_db()
    post = conn.execute(
        "SELECT * FROM posts WHERE slug = ? AND status = 'published'", (slug,)
    ).fetchone()
    if post is None:
        abort(404)

    # Count the read. Best-effort: a failure here must never break the page.
    try:
        conn.execute("UPDATE posts SET views = views + 1 WHERE id = ?",
                     (post["id"],))
        conn.commit()
    except Exception as exc:
        logger.warning("Could not increment views for %s: %s", slug, exc)

    return render_template("blog_post.html", post=post)


# Old /blog/... URLs kept alive so nothing that was already shared 404s.
@app.route("/blog")
def blog_legacy():
    return redirect(url_for("blog"), code=301)


@app.route("/blog/<slug>")
def blog_post_legacy(slug):
    return redirect(url_for("blog_post", slug=slug), code=301)


@app.route("/thank-you")
def thank_you():
    """Confirmation page shown after a successful quote-request submission."""
    return render_template("thank-you.html")


if __name__ == "__main__":
    app.run(debug=True)
