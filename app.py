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
import smtplib
import logging
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import Flask, render_template, request, redirect, url_for
from dotenv import load_dotenv

# Load SMTP / recipient settings from a local .env file (never committed).
load_dotenv()

app = Flask(__name__)

app.config.update(
    # --- SMTP (outgoing mail server that sends the notification) ---
    SMTP_HOST=os.getenv("SMTP_HOST", "smtp.gmail.com"),
    SMTP_PORT=int(os.getenv("SMTP_PORT", "465")),
    SMTP_USER=os.getenv("SMTP_USER", ""),
    SMTP_PASS=os.getenv("SMTP_PASS", ""),

    # --- Recipients of every form submission ---
    # Falls back to the public contact address if CONTACT_RECEIVER is unset.
    CONTACT_RECEIVER=os.getenv("CONTACT_RECEIVER", "carvel@smashtoday.com"),
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
        import requests  # imported lazily so it's only needed when enabled

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
    """Email a contact/quote-request submission to the business. Returns success."""
    cfg = app.config

    if not cfg["SMTP_USER"] or not cfg["SMTP_PASS"]:
        logger.error("SMTP credentials not configured — cannot send email.")
        return False

    receiver = cfg["CONTACT_RECEIVER"] or cfg["SMTP_USER"]
    cc_list = [addr.strip() for addr in cfg["CONTACT_CC"].split(",") if addr.strip()]
    all_recipients = [receiver] + cc_list

    size_label = SIZE_LABELS.get(size, size or "Not specified")
    submitted_at = datetime.now().strftime("%B %d, %Y at %I:%M %p")

    # Escape user-supplied values before dropping them into the HTML email so a
    # submission can't inject markup into the recipient's inbox.
    e_name, e_email, e_phone, e_zip, e_size, e_notes = (
        html.escape(v) for v in (name, email, phone, zip_code, size_label, notes)
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"New Quote Request from {name} — Florida Chicken Coops"
    msg["From"] = cfg["SMTP_USER"]
    msg["To"] = receiver
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    # Let the business reply straight to the customer.
    if email:
        msg["Reply-To"] = email

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
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP_SSL(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15) as server:
            server.login(cfg["SMTP_USER"], cfg["SMTP_PASS"])
            server.sendmail(cfg["SMTP_USER"], all_recipients, msg.as_string())
        logger.info("Contact email sent to %s", all_recipients)
        return True
    except Exception as exc:
        logger.error("SMTP send failed: %s", exc)
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

    # 3. Send the notification email. On failure, keep the form and warn.
    if not send_contact_email(name, email, phone, zip_code, size, notes):
        return render_template("contact.html", error=True)

    # 4. Success — redirect to the dedicated thank-you page. Using Post/Redirect/
    #    Get means a browser refresh lands on /thank-you instead of re-submitting.
    return redirect(url_for("thank_you"))


@app.route("/thank-you")
def thank_you():
    """Confirmation page shown after a successful quote-request submission."""
    return render_template("thank-you.html")


if __name__ == "__main__":
    app.run(debug=True)
