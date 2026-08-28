# fl-chicken-coop

Florida Chicken Coops — Flask marketing site plus an admin dashboard for leads
and blog posts.

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env      # then fill in the values you need
python app.py             # http://127.0.0.1:5000
```

## Where the data lives

There is no database. The dashboard only holds three small collections, so they
are plain JSON files, created automatically on first boot:

```
instance/data/users.json      dashboard logins (password hashes only)
instance/data/leads.json      contact-form submissions
instance/data/posts.json      blog posts, draft and published
instance/data/security.json   failed sign-in counters
instance/secret_key           session-signing key, generated once
```

`instance/` is gitignored and sits outside `static/`, so nothing there is
reachable by a URL and a deploy never overwrites live data. Set `DATABASE_PATH`
in `.env` to move the folder.

**Back it up by copying that one folder.** To restore, copy it back.

If an older `instance/fcc.db` (SQLite) is present, its users, leads and posts
are imported automatically the first time the app boots on this version. The
`.db` file is left in place afterwards as a backup — nothing re-imports it.

## Admin dashboard

`/admin` — sign in at `/admin/login`.

| Section | Path | What it does |
| --- | --- | --- |
| Dashboard | `/admin/` | Lead + post counts, five most recent of each |
| Leads | `/admin/leads` | Search, filter by status, paginate, view, set status, delete |
| Blog Posts | `/admin/posts` | Create, edit, publish/unpublish, delete |
| Account | `/admin/account` | Display name, email, change password |

Sign-in is rate limited: five failed attempts for the same username from the
same address triggers a lockout that grows from 1 minute to an hour. The
counters live in the store, so they are shared by every worker process and
survive a restart. Clear them with `python manage.py unlock`.

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
development login only. Before the site goes live, change the password from
**Account → Change password** — or run `python manage.py reset smashteam`.

Seeding only runs for a username that does not exist yet. Once the password has
been changed, `ADMIN_PASSWORD` in `.env` and the value in `db.py` are both dead
letters, and the only way back in is the CLI below.

### Locked out? Fix it from the server shell

Run these on the deployed machine, from the project folder:

```bash
python manage.py users                       # who can sign in, and when they last did
python manage.py reset smashteam             # prompts for a new password
python manage.py reset smashteam --generate  # prints a strong one instead
python manage.py create newname --role developer
python manage.py delete oldname
python manage.py unlock                      # clear brute-force lockouts
```

Only the hash is ever written, so a password can be replaced but never read
back.

One more production setting lives in `.env`: `SESSION_COOKIE_SECURE=1`, once
the site is served over HTTPS.

## How the pieces fit

| File | Role |
| --- | --- |
| `app.py` | Marketing routes, contact form + Resend email, nav/footer data, `/blog` |
| `db.py` | The whole data layer: JSON store (`users`, `leads`, `posts`), locking, atomic writes, seeding |
| `manage.py` | Account CLI — list, reset password, create, delete, unlock |
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

Contact-form submissions are written to `leads.json` *before* the notification
email is attempted, so a Resend outage can't lose a lead.

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
