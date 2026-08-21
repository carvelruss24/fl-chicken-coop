# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Florida Chicken Coops: a server-rendered Flask marketing site (10 public pages)
plus an admin dashboard at `/admin` for leads and blog posts. No build step, no
JS framework, no ORM, no bundler — Jinja templates, hand-written CSS, vanilla JS.
Keep it that way unless asked otherwise; adding a toolchain is a bigger decision
than it looks.

## Commands

```bash
pip install -r requirements.txt
cp .env.example .env          # then fill in values
python app.py                 # dev server, http://127.0.0.1:5000, debug=True
gunicorn app:app              # production (gunicorn is in requirements.txt)
```

There is **no test suite and no linter configured**. Playwright *is* installed
(`_shot.py` uses it), so the practical way to verify a change is to boot the app
and drive it:

```bash
python _shot.py out.png 390   # args: outfile, viewport width
```

`_shot.py` hardcodes the home page on port `5057`, so it only shoots `/` — edit
its `url` or write a throwaway Playwright script for anything else.

When verifying non-trivially, prefer a throwaway database over the real one:
set `DATABASE_PATH` to a temp file and use `app.test_client()`, which exercises
routes without a live server. Stub `app.send_contact_email` so tests don't hit
Resend.

## Architecture

### Shared chrome is defined once, in Python

`NAV_LINKS`, `FOOTER_COLUMNS`, and `CONTACT` (phone, email, WhatsApp deep link)
live in `app.py` and reach every template through the `inject_chrome()` context
processor. Change a phone number or nav label there, never in a template.
`NAV_LINKS` uses plain URL strings, not `url_for` — several entries predate
their routes. The active nav link is derived from `request.path`, so it keeps
working as pages come online.

### Route names and template names deliberately disagree

The URLs are marketing-driven; the templates keep their original design names.
Don't "fix" these:

| Route | Template |
| --- | --- |
| `/our-coops` | `our-models.html` |
| `/about` | `about-us.html` |
| `/faqs` | `faq.html` |

`/service-area-2`, `-3`, `-4` are alternate layouts of the same page kept for
comparison, not live navigation.

### Two independent design systems

| | Marketing site | Dashboard |
| --- | --- | --- |
| CSS | `static/css/style.css` (+ `reset.css`) | `static/css/admin.css` |
| Shell | `templates/base.html` + `templates/partials/` | `templates/admin/base.html` |
| Prefix | BEM-ish per component (`.page-hero__title`) | everything `ad-` |
| Fonts | Playfair Display for display type, system sans for body | system sans only |

The dashboard does **not** extend the marketing `base.html` and does not load
`style.css` — that isolation is intentional (no GTM, no marketing scripts, and
neither stylesheet can break the other). Don't merge them.

`style.css` is one long file organised as a banner-commented section per
component (`SITE HEADER`, `PAGE HERO`, `CTA`, `BLOG`, …) with design tokens as
custom properties in `:root`. Add a new section in the same style rather than a
new file. Brand palette: gold `--color-gold: #c5a06a`, dark brown
`--color-dark: #2c2416`, cream `--color-cream: #f7f3ec`.

### Data layer (`db.py`)

Plain `sqlite3`, three tables — `users`, `leads`, `posts`. The database is
created at `instance/fcc.db` (gitignored, override with `DATABASE_PATH`), and
`init_db(app)` is idempotent: it runs `CREATE TABLE IF NOT EXISTS` and seeds the
dashboard login on every boot, so a fresh clone works immediately. Connections
are per-request via `get_db()` on `flask.g`.

`db.py` reads its env vars **lazily**, inside functions, because `app.py`
imports it before `load_dotenv()` runs. Keep new config reads inside functions
for the same reason, and use `os.getenv(k, "").strip() or DEFAULT` — a key
present-but-blank in `.env` would otherwise beat the default.

### Dashboard (`admin.py`)

One blueprint at `/admin`, gated by a `@login_required` decorator over a signed
session cookie; `before_app_request` puts the current user on `g.user`. No
Flask-Login. Sections: dashboard, leads (search/filter/paginate/status/delete),
posts (CRUD + draft↔published), account (profile + password change).

- Sidebar highlighting works via a top-level `{% set active_nav = 'leads' %}` in
  each child template — Jinja evaluates that before the parent renders.
- `admin/base.html` exposes `{% block topbar %}` (whole action bar) and
  `{% block scripts %}`; the editor overrides both. The identity chip lives in
  `admin/partials/identity.html` so both topbars share it.
- New columns go in `db.MIGRATIONS`, never into `SCHEMA` — `CREATE TABLE IF NOT
  EXISTS` is a no-op on an existing database. `apply_migrations()` adds only
  what's missing and runs on every boot.
- Template filters `pretty_date`, `source_label` (in `admin.py`) and `post_html`
  (in `app.py`) are registered app-wide, so public templates can use them too.
- `post_html` renders author HTML unescaped by design (the editor is behind a
  login). Don't feed it untrusted input.
- Login failures return one generic message for both unknown-user and
  bad-password, and `_safe_next()` rejects absolute URLs. Preserve both.

### Leads are stored before the email is sent

`contact()` writes the lead to SQLite *first*, then attempts the Resend API, then
flags `emailed`. A Resend outage must never lose a submission. If email fails the
form still re-renders with `error=True` — the lead is safe in the dashboard.
Email goes over Resend's HTTPS API, never SMTP (VPS port-25 blocks).

### Two account roles

`users.role` is `developer` or `client` (`db.ROLES`).

- **developer** — the seeded agency login. `init_db()` promotes the seeded
  username to this role on *every* boot, so a database that predates the column
  (where it defaulted to `client`) upgrades itself and the roles can't drift.
  It sees its own profile plus a panel to provision and manage the client login.
- **client** — the business's own login. Identical access to leads and posts; it
  only ever sees its own profile, and never the developer's username.

There is at most **one** client account — that constraint is what makes the
"no client account exists yet" empty state meaningful. `client_create` refuses a
second one.

Everything that touches another login sits behind `@developer_required`
(`admin.py`), which bounces a client with a flash rather than a bare 403, since
the only way to reach those URLs is by typing them. Don't add a client-editing
route without it.

Passwords are generated by `db.suggest_password()` and mirrored by the same
algorithm in `static/js/admin-account.js` (`generate()`), both avoiding
look-alike characters (`l I O 0 1`) so a password survives being read aloud.
**If you change the rules, change both.** A newly created or reset password is
put in the session under `new_credentials`, rendered once, and popped — only the
hash is ever stored, so there is no way to show it again.

Deleting the client removes the login only; posts, leads, and uploads are
deliberately left alone. A deleted user's live session resolves to `g.user =
None` on the next request and is redirected to the login, which is the intended
behaviour.

### Password fields

`static/js/password-toggle.js` is the single show/hide implementation, loaded by
both the sign-in page and the dashboard shell. Markup contract: a button with
`data-toggle-pw="<input id>"`, optionally containing
`[data-pw-icon="show"]` / `[data-pw-icon="hide"]` SVGs (it falls back to
swapping its own Show/Hide text).

It swaps icons with `setAttribute('hidden')`, **not** `el.hidden = …`, because
`hidden` is defined on `HTMLElement`: assigning it to an `<svg>` sets a JS
expando that never reaches the attribute, so the icons silently never change.
Don't "simplify" it back to the property.

The sign-in page deliberately has no visible heading or strapline (the logo is
the branding) and never displays the seeded credential — the old debug-only hint
was removed. The `<h1>` is kept as `.ad-sr` for document structure.

### Account page layout

`templates/admin/account.html` uses one grid (`.ac-layout`: main column +
340px identity rail) for the *whole* page, so every card shares the same left
and right edge. Adding a card outside `.ac-main` reintroduces the mismatched
right edge that fix removed.

Related settings are grouped as `.ac-section` blocks inside one card rather than
a card per control — the client panel is one card with details / reset password /
danger-zone sections. Two more house rules the page depends on:

- `.ad-grid2 > .ad-field { align-content: start; }` — without it, a field with a
  hint makes the row taller and its hint-less neighbour stretches, so the two
  inputs sit visibly out of line.
- `.ad-card__head > .ad-pill { align-self: flex-start; }` under 768px — the
  header becomes a column there, which otherwise stretches a status pill into a
  full-width bar.

Creating or resetting the client password deliberately does **not** flash: the
handover card that renders next is the confirmation, and both together read as a
duplicate.

### The post editor

`templates/admin/post_form.html` + `static/css/admin-editor.css` +
`static/js/admin-editor.js` are a self-contained editor built to match a
supplied design: a main column (title / overview / rich text) beside a settings
rail with **Post Settings** and **SEO Settings** tabs.

- **TinyMCE 7 is vendored**, not loaded from a CDN: `static/vendor/tinymce/`
  holds the minified subset (theme, model, icons, oxide skin, and only the
  plugins the toolbar uses). Self-hosted means no API key and no
  "domain is not registered" banner. It is **GPL-2.0-or-later** — see
  `static/vendor/tinymce/LICENSE.md`. Re-vendor with `npm install tinymce@7`
  and copy the same paths.
- **Fields are reused, not duplicated.** `excerpt` is the SEO tab's Meta
  Description *and* the blog-index card text. `cover_image` is the Featured
  Image *and* the Open Graph image. `published_at` is the Publish date, `body`
  the Content, `slug` the URL Slug. Only genuinely new things got columns
  (`overview`, `caption`, `author_name`, `author_avatar`, `category`, `tags`,
  `seo_title`, `views`, `read_time`).
- **The buttons beat the Status select.** `action=draft` always stores a draft
  and `action=publish` always publishes, so a mis-synced select can never
  publish something by accident. Keep that precedence.
- **Preview posts the live form** to `/admin/posts/preview` with
  `formtarget="_blank"`, which renders `blog_post.html` from unsaved input and
  writes nothing. Don't "fix" it into a save-then-view.
- `read_time` is computed on save by `db.read_time_minutes()` at 200 wpm; the
  JS mirrors the same rate live so the field doesn't disagree with the server.
- One upload endpoint, `POST /admin/uploads`, serves the featured image, the
  avatar, and TinyMCE's own image button. It returns `{"location": url}`
  because that's the shape `images_upload_handler` wants. Extensions are
  allow-listed (no SVG — it can carry script), names are randomised, and files
  land in `static/uploads/YYYY/MM/` (gitignored except `.gitkeep`).
- `<input type="datetime-local">` cannot show a placeholder, so the Publish
  date field renders as `type="text"` and the JS swaps it on focus. Submitted
  values go through `db.normalise_stamp()` — without it the browser's
  `2026-07-04T09:30` reaches the page unparsed by `pretty_date`.

### Blog

`/blogs` and `/blogs/<slug>` are the public face of the dashboard's Blog Posts
section; only `status = 'published'` rows are visible, drafts 404. `/blog/...`
301-redirects to `/blogs/...` so previously shared links survive. The section is
intentionally absent from `NAV_LINKS` — adding it is the client's call.

`post_html` passes a body straight through when it already contains block tags
(TinyMCE output) and only does blank-line-to-paragraph conversion otherwise, so
neither editor content nor legacy plain-text posts get double-wrapped.

## Responsive rules

Mobile-first, and "fully responsive" here means *no horizontal page scroll at
any width* — verified 320px→1920px. Dashboard breakpoints:

- **≥1024px** fixed 260px sidebar
- **<1024px** sidebar becomes an off-canvas drawer (hamburger, scrim, Esc to close)
- **768–1439px** tables hide `.ad-col-optional` columns and tighten cell padding
- **≤767px** tables reflow into stacked cards driven by each cell's `data-label`

Two non-obvious fixes that must not be reverted:

1. `.ad-tablewrap` needs `contain: paint`. `overflow-x: auto` alone clips the
   paint but the *root* still gains scrollable width, sliding the whole
   dashboard sideways on tablets.
2. In the stacked-card layout, rows and cells get `display: block` with **no**
   `width: 100%`. An explicit 100% is measured before the row's own margins and
   pushes every card past the card edge.

The editor adds its own breakpoints: the settings rail sits beside the main
column above 1200px and stacks under it below, and the action bar moves its
buttons to their own full-width row under 768px.

Two more traps worth keeping in mind:

**`pattern` attributes compile in RegExp `v` mode**, where `-` is a syntax
character inside a character class. `pattern="[a-z0-9._-]{3,32}"` throws and
silently disables client-side validation — it must be written `[a-z0-9._\-]`.
The server-side `USERNAME_RE` in `admin.py` is plain Python `re` and does not
need the escape, so the two patterns legitimately differ by one backslash.

And: **`[hidden] { display: none !important; }`** in both
dashboard stylesheets. Several elements are toggled with `el.hidden = true`
while CSS also gives them a `display` (inline-flex/grid/block), which outranks
the UA stylesheet's `[hidden]` rule — without the override the property flips
but the element stays on screen.

Also: dashboard inputs go to 16px under 768px (smaller sizes make iOS Safari
zoom the viewport), and `style.css:131` notes that every ancestor of
`.site-header` must stay `overflow: visible` or the sticky header breaks.

## Credentials and secrets

The seeded dashboard login (`smashteam` / `SmashInteractiveAgency!2026`) is
committed in `db.py` at the client's request — it is a development credential.
`README.md` documents rotating it before launch. `.env` holds the real secrets
(Resend key, `FLASK_SECRET_KEY`, `SESSION_COOKIE_SECURE`) and is gitignored;
`.env.example` documents every key.

reCAPTCHA is wired but disabled (`ENABLE_RECAPTCHA=0`); `verify_recaptcha()`
short-circuits to `True`. Re-enabling needs the widget added to
`templates/contact.html`.

## Gotchas

- Flask caches templates when `debug=False`. A preview server started with
  `debug=False` will not pick up template edits — restart it.
- Images live in `static/img/<page-name>/` (`home/`, `about-us/`, `faq/`, …)
  with shared assets like `logo.svg` and `smash-logo.png` at the top level.
- `.gitignore` ignores `*.png` (for `_shot.py` screenshots) but re-includes
  `static/img/**/*.png`. Without that exception, real site imagery saved as PNG
  is silently left out of commits and 404s on deploy.
- The topbar shows the Smash Interactive wordmark in place of the initials badge
  when `static/img/smash-logo.{svg,png,webp}` exists — see `SMASH_LOGO_FILES`
  and `inject_smash_logo()` in `admin.py`. It falls back to initials if the file
  is missing, so neither state breaks. The mark is a ~1.4:1 stacked lockup, and
  the 68px topbar caps it at 40px tall (≈57px wide); a single-line horizontal
  version of the logo would read better at that size.
- Bash heredocs in this repo's Windows/Git-Bash environment have failed on large
  Python payloads; use the Write tool for whole files and a short `python - <<'PY'`
  patch script (or Edit) for surgical changes.
