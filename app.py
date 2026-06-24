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

from datetime import datetime

from flask import Flask, render_template, request

app = Flask(__name__)

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
    "email": "mitchell@floridachickencoops.com",
    "phone": "(954) 324-3466",
    "phone_href": "tel:+19543243466",
    # Click-to-chat link (wa.me) using the business number in international
    # format, with a prefilled inquiry message customers can edit before sending.
    "whatsapp_href": (
        "https://wa.me/19543243466"
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
    # Form delivery (email / CRM) isn't wired up yet — a POST is simply
    # acknowledged with a thank-you. Hook this branch into your mail service
    # (or a database) when you're ready to receive submissions.
    submitted = request.method == "POST"
    return render_template("contact.html", submitted=submitted)


if __name__ == "__main__":
    app.run(debug=True)
