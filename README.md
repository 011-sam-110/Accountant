# Tidy Books — MTD-readiness app for UK driving instructors

A mobile-only, installable **PWA** that takes a non-technical UK self-employed
driving instructor from "shoebox of receipts" to **Making Tax Digital (MTD)
readiness**. Scan a receipt → the app reads it and suggests a category → tap
"Looks right" → it's filed into clean, HMRC-shaped records. Built **in Python**
end-to-end, designed for **iPhone 12 Safari**, multi-user with isolated accounts.

Built to `architecture-plan.md` / `project-brief (1).md` (read those for the *why*).

## Stack

FastAPI (single ASGI app) · Jinja2 + HTMX + Alpine.js (server-rendered, no build
step) · hand-written CSS design system · SQLAlchemy 2.0 · reportlab/openpyxl for
exports. Targets Vercel + Neon Postgres + Cloudflare R2 + a vision-LLM + Resend/
Twilio in production.

### Runs locally with ZERO credentials

Every external dependency degrades to a local fallback, so the app runs end-to-end
on a laptop with an empty `.env`:

| Concern | Production | Local fallback |
|---|---|---|
| Database | Neon Postgres (pooled) | **SQLite** (`./local.db`) |
| Blob storage | Cloudflare R2 (presigned PUT) | **local filesystem** (`./local_storage`) via `/api/blob` |
| OCR | Gemini 2.5 Flash (OpenAI/Claude fallbacks) | **stub** (plausible data, exercises the full flow) |
| Email / SMS | Resend / Twilio | **console** (printed to the server log) |
| Login code | emailed OTP | shown on-screen (`DEV_SHOW_OTP=true`) |

Providers auto-select based on which env vars are present (see `app/config.py`).

## Run it

```bash
python -m venv .venv && . .venv/Scripts/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# Vendored already, but if public/vendor/*.js is missing, re-fetch htmx + alpine.
uvicorn api.index:app --reload
```

Open **http://127.0.0.1:8000** on a phone-sized viewport. A demo account
(`demo@example.com`, pre-seeded with two tax years of data) is created on first
run; sign in with any email — the 6-digit code is shown on screen in dev.

## Project layout

```
api/index.py        # the single ASGI entrypoint (Vercel Function); router wiring,
                    #   CSRF + security-header middleware, dev seed
app/
  config.py         # env settings + MTD thresholds/deadlines (config, not code)
  models.py db.py   # SQLAlchemy models + scoped Repo (the anti-IDOR backstop)
  security.py       # signed-cookie sessions, CSRF, OTP, get_current_user
  storage.py ocr.py notify.py   # provider-swappable: R2/local, vision-LLM/stub, Resend/console
  sa103.py tax.py   # SA103 box mapping + mandation engine + plain-English status
  export.py reminders.py
  routes/           # core, media, auth, capture, records, categories, status, export, cron
  templates/        # base.html + screens + HTMX partials
public/             # app.css, app.js, manifest.json, sw.js, vendored htmx/alpine
vercel.json         # FastAPI as one Function + /public CDN rewrite + daily cron
```

## Deploying to Vercel

`vercel.json` builds `api/index.py` with `@vercel/python` (with
`includeFiles: ["app/**"]` so the dynamically-imported routers **and** the Jinja
templates are bundled), serves `/public` from the CDN, and registers the daily
reminder cron (`0 7 * * *`).

**Deploy:** push to `master` (auto-deploys if the repo is linked) or run
`vercel` / `vercel --prod`.

**It boots with zero config.** On Vercel the filesystem is read-only except
`/tmp`, so with no env vars the SQLite DB + receipt storage fall back to `/tmp`
— the app runs immediately, but `/tmp` is **ephemeral** (data resets on cold
starts, not shared across instances). For a real deployment set:

| Variable | Why |
|---|---|
| `DATABASE_URL` | **Neon** pooled URL: `postgresql+psycopg://USER:PW@ep-xxx-pooler.REGION.aws.neon.tech/DB?sslmode=require`. Without it, data lives in ephemeral `/tmp`. |
| `SECRET_KEY` | Long random string — signs the session + CSRF cookies. |
| `ENV` = `production` | Marks prod (also makes cookies `Secure`). |
| `DEV_SHOW_OTP` = `false` | **Security:** otherwise the 6-digit sign-in code is shown on the login screen to anyone. |
| `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT`, `R2_BUCKET` | Cloudflare R2 — receipt photos persist. Without it, uploads fail on the read-only FS. |
| `GEMINI_API_KEY` (or `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`) | Real receipt OCR instead of the stub. |
| `RESEND_API_KEY` + a verified `EMAIL_FROM` | Emails the sign-in code + reminders for real (see logging-in below). |

#### Cloudflare R2 (receipt photos) — common gotchas

Receipts upload **straight from the phone to R2** via a presigned PUT, so two
things must *both* be true or photos fail silently and nothing reaches the bucket:

1. **Set the `R2_*` vars in the Vercel project** (Settings → Environment Variables),
   then redeploy — a local `.env` is never uploaded to Vercel. You can set either
   `R2_ENDPOINT` (`https://<account-id>.r2.cloudflarestorage.com`) **or** just
   `R2_ACCOUNT_ID` (the endpoint is derived from it). The R2 API **token must have
   read+write on the exact bucket named in `R2_BUCKET`** — a token scoped to a
   *different* bucket returns `AccessDenied`.
2. **Give the bucket a CORS policy** so the browser may PUT from your app's origin
   (Cloudflare → R2 → your bucket → Settings → CORS Policy):

   ```json
   [
     {
       "AllowedOrigins": ["https://your-app.vercel.app"],
       "AllowedMethods": ["GET", "PUT"],
       "AllowedHeaders": ["content-type"],
       "MaxAgeSeconds": 3600
     }
   ]
   ```

**Verify in one place:** sign in and open `/api/diag`. It reports the selected
`storage`, the endpoint/bucket it's pointed at, and runs a live server-side
write/read against R2 (`r2_test`). `ok: true` there means storage works and any
remaining upload failure is CORS; an error names the real cause.

Run Alembic migrations against the **direct** (non-pooled) Neon connection from
CI for schema changes; `create_all` runs at startup as an idempotent safety net.

### Logging in on the deployed app (without an email provider)

The sign-in flow emails a 6-digit code. Until you wire `RESEND_API_KEY`, the
"email" is printed to **stdout**, which appears in the **Vercel runtime logs**
(Dashboard → your deployment → *Logs*, or `vercel logs <deployment-url>`). Look
for a block like:

```
============================================================
[MTD EMAIL] -> you@example.com  |  Your Tidy Books sign-in code
------------------------------------------------------------
Your sign-in code is 559246. It expires in 15 minutes.
============================================================
```

Enter that code to sign in. (Quick alternative for a private demo: set
`DEV_SHOW_OTP=true` and the code is shown right on the login screen — but anyone
could then sign in as any email, so don't leave it on for real use.)

> Note: moving off the `functions` block dropped `maxDuration` to the Hobby
> default (10s). The stub and typical OCR calls fit; if real OCR ever times out,
> that's the lever to raise (Pro).

## Checks

- `python _smoke.py` — end-to-end integration test across every slice (asserts
  HTMX responses are clean fragments).
- `python run_review.py` — drives an iPhone-12-emulated Chromium through the whole
  app and screenshots each screen into `screenshots/` (needs `playwright install chromium`).
