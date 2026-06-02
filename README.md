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

## Deploy (Vercel)

Set the production env vars (DATABASE_URL pooled Neon, R2_*, a vision-LLM key,
RESEND_API_KEY, SECRET_KEY, `DEV_SHOW_OTP=false`) in the Vercel project, run
Alembic migrations against the **direct** Neon connection from CI, and deploy.
`vercel.json` already wires the single Function, the `/public` CDN rewrite, and
the daily reminder cron (`0 7 * * *`).

## Checks

- `python _smoke.py` — end-to-end integration test across every slice (asserts
  HTMX responses are clean fragments).
- `python run_review.py` — drives an iPhone-12-emulated Chromium through the whole
  app and screenshots each screen into `screenshots/` (needs `playwright install chromium`).
