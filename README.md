# fl-chicken-coop

Florida Chicken Coops — Flask marketing site plus an admin dashboard for leads
and blog posts.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in the values you need
python app.py             # http://127.0.0.1:5000
```

The SQLite database is created automatically on first boot at
`instance/fcc.db` (gitignored). Set `DATABASE_PATH` in `.env` to move it.

## Admin dashboard

`/admin` — sign in at `/admin/login`.

| Section | Path | What it does |
| --- | --- | --- |
| Dashboard | `/admin/` | Lead + post counts, five most recent of each |
| Leads | `/admin/leads` | Search, filter by status, paginate, view, set status, delete |
| Blog Posts | `/admin/posts` | Create, edit, publish/unpublish, delete |
| Account | `/admin/account` | Display name, email, change password |

### Accounts

Two roles:

| Role | Who | Can |
| --- | --- | --- |
| `developer` | Smash Interactive (the seeded login) | Everything, plus create/rename/reset/delete the client login |
| `client` | Florida Chicken Coops | Leads, blog posts, and their own profile |

Sign in as the developer and open **Account** — if no client login exists yet
you'll be offered **Create the client account**, with a username and a generated
password prefilled. The password is shown once on the next screen so you can
copy and hand it over; only its hash is stored, so it can't be shown again
(reset it instead). Deleting the client login leaves all posts, leads, and
uploads intact.

**Seeded development login** (created on first boot, in `db.py`):

```
Username: smashteam
Password: SmashInteractiveAgency!2026
```

The sign-in page does not display it (it used to show a debug-only hint; that
was removed). Because the credential is committed in this repo, treat it as a
development login only. Before the site goes live, either change the password from
**Account → Change password**, or set `ADMIN_USERNAME` / `ADMIN_PASSWORD` in
`.env` and delete `instance/fcc.db` so a different login is seeded instead.

Two other production settings live in `.env`: `FLASK_SECRET_KEY` (a stable
random value — without it every restart signs everyone out) and
`SESSION_COOKIE_SECURE=1` once the site is served over HTTPS.

## How the pieces fit

| File | Role |
| --- | --- |
| `app.py` | Marketing routes, contact form + Resend email, nav/footer data, `/blog` |
| `db.py` | SQLite schema (`users`, `leads`, `posts`), connection handling, seeding |
| `admin.py` | The `/admin` blueprint: auth, leads, posts, account |
| `templates/admin/` | Dashboard templates (own shell, not the marketing `base.html`) |
| `static/css/admin.css` | Dashboard stylesheet — standalone, fully responsive |
| `static/js/admin.js` | Sidebar drawer, delete confirmations, slug auto-fill |

### The post editor

`/admin/posts/new` is a full editing screen:

- **Rich text** via TinyMCE 7 (self-hosted in `static/vendor/tinymce/`, so no
  API key and no CDN call). Images dropped into the body upload automatically.
- **Post Settings** — status, publish date, live read-time estimate, view
  count, drag-and-drop featured image with caption, author name + avatar,
  category and Enter-to-add tags.
- **SEO Settings** — SEO title and meta description with character counters, URL
  slug, Open Graph image, and a live Google-style result preview.
- **Preview** opens the post as the public page would render it, using whatever
  is on screen — nothing is saved first.
- **Save Draft** always stores a draft; **Create Post / Publish** always
  publishes, whatever the Status select says.

Uploads land in `static/uploads/YYYY/MM/` (gitignored) and are capped by
`MAX_UPLOAD_MB` in `.env`, default 8 MB. Allowed types: PNG, JPG, WEBP, GIF.

TinyMCE is GPL-2.0-or-later (`static/vendor/tinymce/LICENSE.md`). If the project
needs a non-GPL licence, TinyMCE sells one, or the editor can be swapped for an
MIT-licensed alternative.

The dashboard topbar shows the Smash Interactive wordmark
(`static/img/smash-logo.png`) in place of the round initials badge. Saving it as
`.svg`, `.png`, or `.webp` all work; if the file is absent the badge falls back
to the account's initials.

Contact-form submissions are written to the `leads` table *before* the
notification email is attempted, so a Resend outage can't lose a lead.

The blog has a public side at `/blogs` and `/blogs/<slug>`; only `published`
posts appear there, and `/blog/...` redirects to `/blogs/...`. "Blog" is in the
header nav (and the mobile menu) via `NAV_LINKS` in `app.py`.

Contact details — phone, email and the WhatsApp link — are defined once in the
`CONTACT` dict in `app.py` and injected into every template, so they change in
one place. `CONTACT_RECEIVER` (where form submissions are emailed) is separate
and lives in `.env`; an `.env` value overrides the code default.

## Responsive behaviour

The dashboard is built mobile-first around three breakpoints:

- **≥1024px** — fixed 260px sidebar, full data tables.
- **768–1023px** — sidebar becomes an off-canvas drawer (hamburger + scrim,
  closes on Esc, scrim tap, or nav tap).
- **768–1439px** — tables drop their lowest-priority columns and tighten cell
  padding rather than scrolling sideways.
- **≤767px** — every table reflows into stacked cards (driven by each cell's
  `data-label`), toolbars go full width, and inputs are 16px so iOS Safari
  doesn't zoom the viewport.
