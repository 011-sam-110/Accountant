# Architecture Plan — Driving-Instructor Finance / MTD-Readiness App

> **Status:** Design document, v1. Synthesised from a 6-agent research swarm (stack/hosting, mobile UX, data model, OCR, tax engine, auth/security).
> **Companion doc:** `project-brief (1).md` (the product brief — read that first for the *why*).
> **Date:** 2026-06-02

---

## 0. What we're building (one paragraph)

A mobile-only **website** (installable PWA) that takes a non-technical UK self-employed driving instructor from "shoebox of receipts" to **Making Tax Digital (MTD) readiness**. He scans a receipt, the app reads it and suggests a category, he taps "Looks right," and it's filed into clean HMRC-shaped records. The app works out for itself whether/when MTD applies, reminds him of deadlines through a channel he'll actually see (email/SMS), and exports an organised pack for his accountant. Built **in Python**, hosted on **Vercel**, designed and tested for **iPhone 12 Safari only**, multi-user with fully isolated accounts. **Ease of use beats everything.**

---

## 1. Foundational decisions (the load-bearing choices)

Every subsystem below was researched against four hard constraints: **Python**, **Vercel**, **mobile-only iPhone 12**, **non-technical user**. These are the firm conclusions:

| Area | Decision | Why |
|---|---|---|
| **Backend framework** | **FastAPI** (single ASGI app) | Vercel's blessed Python backend; async I/O lets one request fan out to DB + R2 + OCR within the time budget; Pydantic validation protects a nervous user from bad input. |
| **Rendering** | **Server-rendered Jinja2 + HTMX + Alpine.js** | Honours "made in Python" end-to-end; tiny payloads on one phone; no React/build step; semantic HTML = free accessibility. HTMX swaps rendered partials for every flow. |
| **CSS** | Hand-written CSS with custom properties (**no Tailwind**) | ~7 screens, one device, one strict large-type/high-contrast design system — bespoke CSS is smaller and easier to enforce globally. |
| **Hosting** | **Vercel**, FastAPI as one Function at `api/index.py`, `vercel.json` for routing + daily cron | Single deployable; static assets from CDN via `public/`. |
| **Database** | **Neon serverless Postgres** (pooled endpoint) + **SQLModel/SQLAlchemy** + **Alembic** | Scales to zero (cheap at 2 users), built-in PgBouncer + HTTP driver for serverless. Supabase is the drop-in alternative. |
| **Blob storage** | **Cloudflare R2** (private bucket, EU jurisdiction) | Free egress, S3-compatible presigned URLs, encrypted at rest; you already run R2 elsewhere. |
| **OCR** | **Single vision-LLM call** (Gemini 2.5 Flash; GPT-4o-mini / Claude Haiku as swappable fallbacks) | Returns `{date, amount, vendor, category, confidence}` as structured JSON in one call — OCR *and* categorisation. Keeps the function tiny (no Tesseract/EasyOCR in the bundle). ~£0.0005/scan. |
| **Auth** | **Passwordless email 6-digit OTP** (passkey/Face ID as optional upgrade) | Login *is* recovery — directly satisfies "a forgotten password must never lock him out." No password to forget. |
| **Reminders** | **Vercel Cron → Python → Resend (email) + Twilio (SMS for high-stakes only)** | Date-based deadlines need a channel he'll see; **never** web-push (unreliable on iOS Safari). |
| **PDF export** | **reportlab** (pure-Python) — **not WeasyPrint** | WeasyPrint's native deps (Pango/Cairo) are fragile on Vercel's Lambda runtime. openpyxl + stdlib `csv` for the spreadsheets. |

### The three constraints that shape everything

1. **Vercel's 4.5 MB request-body cap** → receipt photos (2–5 MB) **must** upload **browser → R2 directly via presigned PUT**, never through the function.
2. **The ~500 MB function bundle limit + short execution time** → **no OCR engine in the bundle**; OCR is a network call to a hosted vision model.
3. **Serverless is stateless & fan-out** → **never** open a Postgres connection per invocation; always go through the pooled endpoint. Sessions live in a signed cookie, not server memory.

---

## 2. System at a glance

```
                       iPhone 12 / Safari (installed PWA)
                       Jinja2 HTML + HTMX + Alpine.js
                                   │
            ┌──────────────────────┼───────────────────────────┐
            │ (1) presigned PUT     │ (2) HTMX requests          │ (cron)
            ▼  photo                ▼  forms / partial swaps      │
   ┌─────────────────┐    ┌────────────────────────────┐   ┌────────────────┐
   │ Cloudflare R2   │◀──▶│  FastAPI on Vercel          │   │ Vercel Cron    │
   │ (private bucket)│    │  api/index.py (ASGI)        │◀──│ daily 08:00    │
   │ receipt images  │    │  routes · templates · auth  │   │ /api/cron/*    │
   └─────────────────┘    └──────┬───────────┬──────────┘   └────────────────┘
                                 │           │
                 ┌───────────────┘           └───────────────┐
                 ▼                                            ▼
        ┌──────────────────┐                       ┌───────────────────────┐
        │ Neon Postgres    │                       │ External APIs          │
        │ (pooled)         │                       │ • Vision LLM (OCR)     │
        │ users·entries·   │                       │ • Resend (email)       │
        │ categories·...   │                       │ • Twilio (SMS)         │
        └──────────────────┘                       └───────────────────────┘
```

**Project layout:**

```
mtd-app/
├── api/
│   └── index.py            # exports `app = FastAPI()` — the single entrypoint
├── app/
│   ├── routes/             # capture.py, entries.py, records.py, status.py, export.py, auth.py, cron.py
│   ├── templates/          # base.html + partials/_confirm.html, _entry_row.html, ...
│   ├── db.py               # SQLAlchemy engine (pooled) + per-user scoped repository
│   ├── storage.py          # R2 presigned PUT/GET helpers
│   ├── ocr.py              # vision-LLM interface (provider-swappable)
│   ├── tax.py              # mandation engine + tax-year/quarter date math
│   ├── reminders.py        # cron handler + idempotent send
│   ├── export.py           # reportlab PDF + openpyxl + zip → R2 → signed link
│   └── auth.py             # OTP, sessions, get_current_user dependency
├── public/                 # CDN-served: manifest.json, sw.js, icons/, app.css
├── migrations/             # Alembic
├── requirements.txt        # KEEP LEAN (fastapi, sqlalchemy, asyncpg/psycopg, httpx, jinja2, reportlab, openpyxl, boto3-lite)
└── vercel.json
```

`vercel.json`:
```json
{
  "functions": { "api/index.py": { "maxDuration": 60 } },
  "rewrites": [ { "source": "/((?!public/).*)", "destination": "/api" } ],
  "crons": [ { "path": "/api/cron/reminders", "schedule": "0 7 * * *" } ]
}
```

---

## 3. Stack & Vercel Hosting

The proposed foundation (FastAPI + Jinja2 + HTMX + Alpine.js, server-rendered PWA, Neon, R2) is **sound and endorsed**. Details and the load-bearing constraints below.

### Reality of Python on Vercel (2026 limits)

Vercel runs FastAPI as a **single Function** under **Fluid Compute**, speaking ASGI natively (no Mangum shim). Relevant limits:

| Limit | Hobby | Pro |
|---|---|---|
| Function max duration | 10s default → **60s** configurable | up to **800s** with Fluid Compute |
| Memory / CPU | fixed 2 GB / 1 vCPU | configurable |
| Bundle size (Python) | **500 MB** unzipped | 500 MB |
| Request/response body | **4.5 MB** | 4.5 MB |
| Cron frequency | **once/day, imprecise hour** | per-minute, precise |

Two numbers dominate the design: the **4.5 MB body cap** (→ direct-to-R2 uploads) and the **500 MB bundle limit** (→ no bundled OCR). Cold starts under Fluid Compute are reduced but still ~1–3s on an idle finance app — keep the dependency tree lean.

### Framework: FastAPI + Jinja2 + HTMX + Alpine.js (firm)

- **Next.js + Python API — rejected.** Relegates Python to a thin API, doubles the stack (two runtimes), forces an SPA + React bundle onto one mid-range phone. Wrong tool for a forms-over-data app.
- **Flask — viable, second choice.** Works, but sync I/O serialises the DB+R2+OCR fan-out, and Vercel/FastAPI integration is more first-class.
- **FastAPI + HTMX + Alpine — chosen.** Whole app is Python; server renders complete HTML; HTMX gives app-like partial swaps with no JS framework or build step; Alpine (~15 KB) covers the tiny local-state bits (number pad, toggles). **Strongly for HTMX** — a CRUD finance tracker is exactly "submit form → swap fragment," and all logic stays server-side (one source of truth, easy to keep HMRC-compliant).

### Where serverless hurts — and the fixes

| Pain | Mitigation |
|---|---|
| Background jobs / reminders | **Vercel Cron** → `/api/cron/*`. Hobby = daily & imprecise (fine for a morning send); go Pro or use an external pinger for finer timing. |
| Long-running OCR | **Run off-platform** (hosted vision API); never bundle the engine. Upload-first, then a separate `/api/scan` request. |
| Large file uploads | **Direct-to-R2 presigned PUT**; function only signs + records metadata. |
| Statelessness | Signed-cookie sessions; **pooled** Postgres endpoint. |
| No work after response | Write a "todo" row, process on next cron tick or provider webhook. |

### PWA delivery (iOS)

Ship `manifest.json` (`display: standalone`, 180×180 `apple-touch-icon`) + iOS meta tags. **Add-to-Home-Screen is manual and unprompted on iOS** → build a one-time in-app "Share → Add to Home Screen" hint (detect `navigator.standalone`). Service worker caches **only the app shell + static assets** (network-first for data; never cache financial figures); iOS evicts PWA storage after ~7 days idle and caps Cache API ~50 MB — treat the cache as disposable, Postgres is the system of record.

**Plan guidance:** start on **Hobby** to validate; the only forcing functions for **Pro** ($20/mo) are sub-daily/precise cron and OCR jobs >60s. Architect so that move is a config change.

---

## 4. Mobile Frontend & UX

Target device: **iPhone 12 only** (390×844 CSS px, notch + home indicator, iOS Safari ≥17). Design rule above all: **the fastest correct action is the default, and it is a tap, not a type.**

### Design system

- **Type scale (large):** base **18px** (never <16px — see iOS zoom). Display 34/700 for money; Title 26/700; Body-lg 20/600; Body 18; Meta 16 (floor). System font stack.
- **Colour/contrast (WCAG AA, AAA on body):** white bg, near-black text (~17:1); primary green `#1A7A3C`. **Money in/out never colour-only** — always `+`/`−` and a word. Status uses text+icon, not hue.
- **Spacing:** 8px grid; ≥12px between tappable rows.
- **Touch targets:** **56px** primary actions, **48px** everything tappable, list rows ≥64px. No tiny icon-only controls.
- **Bottom-anchored primary action** on every screen (thumb reach on an 844px phone), padded with `env(safe-area-inset-bottom)`.
- **Amounts = on-screen number pad** (`inputmode="decimal"`; custom big-button pad for the headline amount). **Date defaults to today** as a tap-to-change chip; native `<input type="date">` wheel for changes.
- **Smart defaults everywhere:** category pre-selected (OCR-suggested or most-used), last-used payment method, standard lesson price as a one-tap chip. Pickers, not free text.

### iOS Safari pitfalls (and fixes)

- **`100vh` lies** → use **`100svh`** for the shell (`height:100vh; height:100svh;`).
- **Input auto-zoom** if font <16px → all inputs ≥16px (we use 18). **Do not** disable pinch-zoom (WCAG fail).
- **Safe areas** → `viewport-fit=cover` + `env(safe-area-inset-*)` on fixed header/bar.
- **Camera** → `<input type=file accept="image/*" capture="environment">`; must fire from a **direct tap** (no `setTimeout`/`.then()` or iOS blocks it). Gallery fallback is unavoidable — word the button "Scan a receipt."
- **No reliable web push** → never build a flow that depends on a notification.
- Misc: `touch-action: manipulation`, subtle `-webkit-tap-highlight-color`, `enterkeyhint`.

### Navigation — feel like "two screens"

**3-item bottom tab bar**, **Capture** as the default/centre:

```
[ Capture (home) ]   [ Records ]   [ More ]
```

- **Capture** — big Scan button, Manual add expense, Quick-add income, one plain MTD status line. Never blank.
- **Records** — Tax year → Month → entries (money in/out), paid/unpaid students, edit/undo.
- **More** — Output/export, Status/Tax detail, Account. Keeps the two daily screens uncluttered. Confirm-entry is a transient swapped partial, not a tab.

### HTMX flows (the core pattern: POST → server returns a rendered partial → swap)

**Scan → Confirm → Save:**
```html
<form hx-post="/scan" hx-encoding="multipart/form-data"
      hx-target="#screen" hx-swap="innerHTML" hx-indicator="#scanning">
  <label class="btn-primary" for="receipt">📷 Scan a receipt</label>
  <input id="receipt" name="photo" type="file" accept="image/*"
         capture="environment" class="visually-hidden"
         onchange="this.form.requestSubmit()">
</form>
<div id="scanning" class="htmx-indicator">Reading your receipt…</div>
```
`/scan` returns the pre-filled **Confirm partial** whose bottom action is one button: **"Looks right — save it."** Saving returns a success partial ("Saved. That's logged for June.") with a prominent **Undo**.

**Inline edit + real undo in Records** — each row swaps itself into an edit form and back; the server keeps the prior value so Undo is genuine (soft-delete server-side). Use `hx-disabled-elt="this"` to block double-taps and a global `hx-indicator` for "Saving…" on cellular.

### Onboarding, empty states, "you can't break this"

- **First run (≤3 plain-language cards):** the promise, not a tutorial; one real setup question (confirm current tax year, pre-filled); drop them on Capture; then the Add-to-Home-Screen hint.
- **Never a blank page** — empty Records: "No entries yet. Tap Scan a receipt to add your first one," with the action right there.
- **Reversible everything** (save/edit/delete all undoable; deletes soft), **one-tap confirm**, **no error walls** ("We couldn't read that photo clearly — you can type the amount instead," with the form right below), persistent bottom-bar escape hatches, calm ledger-style confirmation copy.

### PWA / offline

Manifest locks **portrait**; SW caches the shell so launching from the home screen never shows a Safari error. **Do not promise offline data entry** — at most, hold an entry locally and retry the POST on reconnect ("Saved on your phone — we'll send it when you're back online"), knowing iOS may evict it. Server is the record of truth.

---

## 5. Data Model & Database

Two hard constraints shape it: serverless connections can't persist; **per-user isolation** and **closed-year immutability** are correctness requirements.

### The serverless connection problem (critical)

Postgres caps connections; Vercel spins up many isolated instances. **Naive connection-per-invocation exhausts `max_connections`**; caching a global connection **leaks across requests**. **Fix:** connect via Neon's **`-pooler` (PgBouncer, transaction mode)** for app traffic, or the **Neon HTTP driver** for one-shot reads; keep the SQLAlchemy pool tiny (1–2) and disable prepared-statement caching under transaction pooling. Use a **separate direct (non-pooled) connection only for Alembic migrations**.

### Schema (core tables)

- **`app_user`** — `id (uuid)`, `email (citext unique)`, `display_name`, `recovery_email`, `auth_provider`, `created_at`.
- **`sa103_box`** *(global, seeded once, immutable)* — the **hidden fixed layer**: `code (pk)`, `box_number`, `kind (income|expense)`, `label`. Acts as an enum-as-data so HMRC form changes are a seed migration, not an `ALTER TYPE`.
- **`category`** *(per user, the **visible** layer)* — `id`, `user_id`, **`sa103_code` FK → sa103_box** (the mapping that keeps exports correct), `label` (renamable, cosmetic only), `kind`, `display_order`, `is_favourite`, `is_hidden`, `is_deleted`.
- **`student`** — `id`, `user_id`, `name`, `is_archived`.
- **`entry`** *(the core transaction)* — `id`, `user_id`, `entry_type`, `entry_date (DATE — drives tax year)`, **`amount_minor (BIGINT pennies)`**, `currency`, `category_id`, `notes`, `student_id?`, `is_paid?` (income only), `tax_year_id?`, `is_locked`, `created_at`, `updated_at`. Indexed `(user_id, entry_date)`, `(user_id, category_id)`, `(user_id, tax_year_id)`.
- **`receipt`** — `id`, `user_id`, `entry_id`, `r2_key`, `content_type`, `byte_size`, `uploaded_at`. **DB stores the key, never a public URL.**
- **`tax_year`** — `id`, `user_id`, `start_date (6 Apr)`, `end_date (5 Apr)`, `label ('2026/27')`, `status (open|closed)`, `closed_at`, **`snapshot (JSONB)`** (frozen totals + per-box subtotals), `snapshot_hash`.
- **`audit_event`** *(append-only)* — `id (bigserial)`, `user_id`, `entity`, `entity_id`, `action (create|update|delete|merge|close_year)`, `diff (JSONB)`, `at`.

### Money, categories, isolation, locking

- **Money = `BIGINT` pennies, never floats** (IEEE-754 drift can't reconcile a tax return). Convert pounds⇄pennies only at the UI boundary.
- **Two-layer categories:** `entry → category → sa103_box`. **Rename** = update `category.label` only (mapping untouched → exports unaffected). **Merge A→B** = reassign entries to B + soft-delete A + audit (allowed only when SA103 boxes match). **Delete blocked while entries exist** (FK `RESTRICT`; soft-delete otherwise).
- **Isolation (defence in depth):** `user_id` FK on every tenant table + **a single scoped repository layer that requires `user_id` on every query** + composite indexes leading with `user_id`. **Optional Postgres RLS** (`SET LOCAL app.current_user_id` per transaction + `USING (user_id = current_setting(...))`) as the database-level backstop.
- **Closed-year immutability:** closing computes totals into `tax_year.snapshot` (the figures the accountant receives — they do **not** recompute from live rows), sets `status=closed`, stamps entries `is_locked=true`. Edits to locked entries are rejected at the **app layer and a DB `BEFORE UPDATE/DELETE` trigger**. Closed-year reports read the frozen snapshot, so later relabelling can't rewrite history.

### Seeding & migrations

Default user categories (Lesson/tuition fees, Other income, Car/van & travel, Vehicle costs, Phone & internet, Advertising & marketing, Professional/franchise fees, Training & CPD, Accountancy/admin & bank charges, Other) are inserted **per user at account creation**, each pre-linked to its `sa103_code`. **Alembic runs out-of-band from CI** (never at request time), against the **direct** connection, using **expand-contract** (backward-compatible) migrations; validate on a Neon branch first.

---

## 6. OCR & Receipt Capture

The heart of the app. Golden rule: **the photo is never lost and the user always reaches the confirm screen, even when OCR fails completely.**

### Engine: single vision-LLM call (firm)

Compared (a) vision LLM in one call, (b) dedicated OCR API + a second LLM pass, (c) bundled Tesseract/EasyOCR. **Winner: (a) Gemini 2.5 Flash** (GPT-4o-mini / Claude Haiku as swappable fallbacks behind a thin interface):
- **One call** returns `{date, amount, vendor, suggested_category, per-field confidence}` as schema-enforced JSON — OCR *and* categorisation. (b) and (c) only get raw text and still need an LLM pass.
- **(c) is infeasible** on Vercel — EasyOCR ~500 MB blows the bundle; Tesseract gives raw text only and weakest accuracy on phone photos of crumpled UK receipts.
- **Cost ~£0.0004–0.0008/scan**; for 2 users, pennies/month. Frontier VLMs beat traditional OCR on faded/handwritten receipts and can reason "this is the total, not the subtotal/VAT."

### Pipeline (upload-first, two requests)

1. **Capture** via `<input type=file accept="image/*" capture="environment">`.
2. **Client-side downscale/compress** (canvas → ~1,500px longest edge, JPEG ~0.7 → 150–400 KB) — saves data, tokens, cost.
3. **Direct presigned PUT to R2** (browser → R2; free egress; avoids the 4.5 MB function cap). CORS allows PUT from the app origin.
4. **`POST /api/scan`** with the R2 key → function sends the image to the vision LLM (**Fluid Compute + `maxDuration` ~30s** so the 2–6s call + cold start never trips the limit).
5. **Validate/normalise** JSON (amount→pennies, date→ISO, category→fixed SA set).
6. **Confirm screen pre-fill** — render immediately in a **skeleton state** (thumbnail already visible, shimmer on the four fields, "Reading your receipt…"), fields fade in when `/api/scan` returns.
7. **Save** → `POST /api/entries` writes the entry linked to the R2 key. Nothing persists until this tap.

### The gentle failure path

Confidence is **per field** (`high | low | missing`). The photo is already in R2, so it's never lost; OCR only ever *pre-fills*.

| Scenario | Confirm-screen behaviour |
|---|---|
| Full success | All fields filled; one tap "Looks right." |
| Partial (e.g. amount missing) | Good fields filled; missing field highlighted: "Couldn't read the amount — tap to type it." |
| Low confidence | Field filled but flagged (amber, "Please check"). |
| Total failure / timeout | Same screen, fields empty, thumbnail shown, category defaults to "Other": "We kept your photo but couldn't read it — just fill in the details below." |

**Required to save: amount + date** (vendor/category have safe defaults). Forgiving validation accepts `£12.50`, `12,50`, common UK date formats; prefers the largest "total"-labelled amount and flags ambiguity as `low`.

### Prompt / schema (abbreviated)

Use the provider's **structured-output mode** with the category constrained to a fixed enum (`car_van_travel`, `vehicle_costs`, `phone_internet`, `advertising_marketing`, `professional_franchise_fees`, `training_cpd`, `accountancy_admin_bank`, `other`). System prompt frames it as a UK driving-instructor SA103 assistant with explicit category guidance (fuel→car_van_travel, MOT/servicing→vehicle_costs, etc.) and the rules: ISO dates, GBP number, **never invent — null + "missing" if unreadable**, choose the final total. Example output:
```json
{ "date":"2026-05-28","amount":61.40,"vendor":"Shell",
  "suggested_category":"car_van_travel",
  "confidence":{"date":"high","amount":"high","vendor":"high","category":"high"} }
```

### Image storage (R2)

Private bucket; key layout `userId/YYYY/MM/<uuid>.jpg` (+ optional `_thumb`). All access via **short-lived presigned URLs** minted after auth (PUT to upload, GET to display) — never public URLs. DB stores only the key. **Retention ~6 years** (HMRC). At 2 users you live inside R2's free tier for years.

---

## 7. MTD Tax Engine, Reminders & Export

This is the compliance brain. **Tax facts verified against HMRC / professional sources (June 2026).**

### Verified facts (refinements to the brief)

1. **Quarterly *deadlines* are the 7th** (periods end on the 5th): **7 Aug / 7 Nov / 7 Feb / 7 May**. Final Declaration **31 Jan** (~10 months after year-end).
2. **Qualifying income = gross turnover** (self-employment + property), **before expenses**; excludes PAYE/dividends/savings. For this user, simply gross fees.
3. **Mandation is tested on the *previous* return** (~two tax years before): 2026/27 mandation uses the **2024/25** return vs £50k; 2027/28 uses 2025/26 vs £30k; 2028/29 uses 2026/27 vs £20k.
4. **Once mandated, you stay mandated** (exemptions out of scope for v1 — flag, don't automate). First-year (2026/27) quarterly soft-landing on penalty points, but the **Final Declaration is not protected** — treat all deadlines as hard.

### Mandation engine

Store one record per user per tax year (`gross_qualifying_income`, `is_final`) plus a live running total for the year in progress. **Mandation is only *confirmed* by an `is_final` prior-year figure; the in-progress year drives early warnings only** — never tell a non-technical user he "must file quarterly" when he legally needn't yet. The engine watches **both** the completed year (determines mandation) and the in-progress year (early warning), via the phased thresholds £50k(2026)/£30k(2027)/£20k(2028) keyed to the *mandation year*.

States: `UnderThreshold → Approaching (>80% YTD) → OnTrackToBeMandated → MandatedNextYear → Mandated(active)`.

### Date math

UK tax year 6 Apr → 5 Apr (`current_tax_year_start`). Default to **standard quarters**; calendar quarters are an advanced toggle with identical deadlines (little benefit). Obligations for year *y*: Q1 due 7 Aug, Q2 7 Nov, Q3 7 Feb(+1), Q4 7 May(+1), Final 31 Jan(+2). Dates are fixed (no weekend rolling) and reminders fire ahead, so it's moot.

### Plain-English status

One reassuring sentence per state, e.g. *"You're under the £50,000 line, so a simple yearly summary is all HMRC needs for now."* / *"Heads up: from 6 April you'll need to send HMRC an update every 3 months instead of one yearly return. We'll handle the reminders — nothing changes today."* Copy lives in one mapping module keyed by `(state, nearest_obligation, days_until)`.

### Reminders (Vercel Cron → Python → email/SMS)

Single **daily cron** (`0 7 * * *` UTC ≈ 08:00 London; accept ≤1h DST drift) hits `/api/cron/reminders`, which computes who needs reminding today. Windows: quarterly `T-21, T-7, T-1, T-0` + overdue `+1, +3, +7`; final `T-30, T-14, T-7, T-1, T-0`.

**Idempotency is the core safety requirement** — store a `ReminderLog` unique on a deterministic `dedupe_key = user:obligation:milestone`; `INSERT … ON CONFLICT DO NOTHING`, and **commit the "sent" row only after the provider call succeeds** so failures retry next day. **Resend** for email (primary; Postmark equivalent alt), **Twilio** for SMS reserved for high-stakes (`T-1`, `T-0`, overdue, Final). Default email-on, SMS-off but strongly nudge adding a mobile number. **Never web-push.** Alert the operator if a `T-0` send fails.

### Export (accountant handover .zip)

User selects a tax year → `.zip` with **PDF summary + Excel + CSV + receipt images**, SA103-aligned.
- **PDF: `reportlab`** (pure-Python, deterministic on Lambda) — **not WeasyPrint** (native Pango/Cairo deps are fragile on Vercel).
- **Excel: `openpyxl`; CSV: stdlib `csv`** (skip pandas — heavy cold start).
- **The real risk is receipt-image size.** Don't stream a big zip as the HTTP response: **build the zip → upload to R2 → email a time-limited signed download link** ("Your accountant pack is ready — download (expires in 7 days)"). Stream-copy receipts from R2 without re-encoding; if a pack risks the time budget, move generation to a queued/background job and notify via the same email channel.

### SA103 mapping (config-driven)

App categories map to SA103F boxes (e.g. Car/van & travel → Box 20; Advertising → Box 24; Phone & office → Box 25; Professional/accountancy → Box 30; Other/Training → Box 31; gross income → Box 15/16). **Note:** the two research agents cited slightly different box numbers (the short **SA103S** collapses expenses into one total; the full **SA103F** numbering shifts year to year). **Resolution: store the category→box mapping in config, not code**, and confirm the exact boxes against the current-year SA103F before first export — and ideally ask the accountant for their preferred format (per brief §7 action item).

### Phased HMRC path

- **Phase 1 (v1):** records + mandation awareness + reminders + SA103-aligned accountant `.zip`. No HMRC connection.
- **Phase 2:** clean machine-readable export the accountant's *recognised* MTD software imports (preserves the "digital link" chain).
- **Phase 3:** direct HMRC API — Developer Hub recognition, OAuth2, fraud-prevention headers, quarterly + final endpoints. Major regulated undertaking; the data model already mirrors HMRC's period/box structure so it's an integration layer, not a rewrite.

---

## 8. Auth, Security & Privacy

The non-negotiable: **a forgotten password must never lock a non-technical user out of his own tax records.** That drives everything toward passwordless.

### Auth model: passwordless email 6-digit OTP (firm)

Email is both the **login identifier and the recovery mechanism** — login *is* recovery, so there's no separate password to forget or recovery maze to build. **Code, not magic link**, because this is a one-device mobile flow (links can open the wrong browser/webview), iOS autofills one-time codes (`autocomplete="one-time-code"`), and codes are a familiar "bank code" pattern.

**Day-1 flow:** enter email → "Send me a code" → enter the 6 digits (often autofilled) → in. **Recovery is the identical flow.** **Passkeys (Face ID)** offered as an optional speed-up after a few logins, with **email-OTP always retained as the recovery-safe fallback**. **SMS OTP rejected as primary** (SIM-swap risk, regulatory headwind, cost) — kept only as a possible backup delivery channel.

### Sessions on stateless serverless

**Signed/encrypted HttpOnly cookie** (JWT or `itsdangerous`, secret from Vercel env) carrying `{user_id, session_id, exp}`, plus a tiny Postgres `sessions` table for revocation ("log out everywhere", post-recovery invalidation). No Redis at this scale. Cookie flags: `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`. **Long rolling sessions (30–60 days)** with a **"Keep me signed in" default-on** — a nervous user must never feel "locked out of his taxes." **CSRF:** `SameSite=Lax` + double-submit token (`fastapi-csrf-protect`), attached globally via HTMX `hx-headers` on `<body>`.

### Recovery flow (= login) + safeguards

Enter email → code emailed → enter code → in. Codes are **single-use, hashed at rest, ~10–15 min expiry**. **Rate-limit** (~1/60s, ~5/hour per email/IP), **attempt-limit** code entry (~5 tries), **no account enumeration** (identical response and timing whether or not the email exists), invalidate prior codes on new request/use. Optional reassuring "you just signed in" email.

### Data isolation (anti-IDOR)

- A FastAPI **`get_current_user()` dependency** is the only source of caller identity.
- **Never trust a client ID for ownership** — every query scoped `WHERE id = :id AND user_id = :current_user_id`; cross-user IDs return **404, not 403**.
- **Scoped repository layer** injects `user_id` automatically so no one writes an unscoped query.
- R2 keys namespaced by user; presigned URLs minted **only after** verifying ownership.
- **Optional Postgres RLS** as the database-level net (per-request `SET LOCAL`).

### Secrets, privacy, hardening

- **Secrets in Vercel env, marked "Sensitive"**, scoped per environment, least-privilege (R2 token scoped to the one bucket; DB role DML-only). Load via Pydantic `BaseSettings` (fail loud on missing). Never log secrets or presigned URLs.
- **UK-GDPR:** HTTPS + HSTS in transit; Postgres + **R2 (EU jurisdiction)** encrypted at rest; private bucket, presigned-only; **~6-year retention** (don't auto-delete tax records); self-service **export** and **delete** (delete hard-removes DB rows + the user's R2 prefix); plain-English one-screen privacy notice; only essential auth/CSRF cookies → no cookie banner.
- **Hardening:** security headers (tight CSP — HTMX is HTML-driven so inline scripts are avoidable; `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`); Pydantic validation + Jinja autoescape; **file-upload validation** (max ~10 MB, allowlist + magic-byte check, **re-encode via Pillow to strip EXIF/GPS**, random server-side key); **Postgres-backed rate limiting** on auth (serverless instances don't share memory; Cloudflare Turnstile if abuse appears); constant-time code comparison.

---

## 9. Phased delivery roadmap

| Phase | Scope | Exit criteria |
|---|---|---|
| **0 — Skeleton** | FastAPI on Vercel (`api/index.py`), Neon connected (pooled), Alembic baseline, base Jinja layout + design system CSS, PWA manifest + SW shell. | App deploys; "hello" page renders on a real iPhone 12; DB migration runs from CI. |
| **1 — Auth + isolation** | Email-OTP login/recovery, signed-cookie sessions, `get_current_user`, scoped repository, the two seeded accounts. | Both users log in, recover via code, and cannot see each other's data (IDOR-tested). |
| **2 — Capture core** | Camera input, client compress, presigned R2 upload, `/api/scan` vision-LLM extraction, skeleton **Confirm** screen, save. Gentle-failure path. | Scan→confirm→save in seconds; failed OCR still reaches a usable confirm screen; photo never lost. |
| **3 — Records + categories** | Tax year→month→entries ledger, money in/out, manual add, quick-add income, paid/unpaid students, inline edit + real undo, customisable categories over fixed SA103 mapping. | Full CRUD; rename/merge/delete rules enforced; closed-year locking works. |
| **4 — Tax engine + reminders** | Mandation engine, status line + Status screen, tax-year/quarter math, daily cron + Resend (+ Twilio for high-stakes), idempotent send log. | Correct status for seeded scenarios; deadline reminders dispatch once, verifiably. |
| **5 — Export** | reportlab PDF + openpyxl/CSV + receipt bundle → zip → R2 → emailed signed link. | Accountant pack generated for a full tax year, downloadable, SA103-aligned. |
| **6 — Polish + harden** | Onboarding cards, Add-to-Home-Screen hint, empty states, security headers, file-upload hardening, privacy notice, export/delete-my-data. | Security review passes; first-receipt-in-a-minute validated on a real iPhone 12. |
| **Later** | Phase 2/3 HMRC submission; auto-categorisation learning; wife's profile specifics + account-switching UX; offline queue. | Deliberate decisions once core is proven. |

---

## 10. Consolidated risk register

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | **4.5 MB body cap** blocks photo uploads | High | Direct-to-R2 presigned PUT (designed in, not a workaround). |
| 2 | **OCR can't fit the bundle / time limit** | High | Hosted vision API, never bundled; upload-first + separate `/api/scan` + Fluid Compute. |
| 3 | **Serverless DB connection exhaustion** | High | Mandatory pooled endpoint / HTTP driver; tiny client pool; direct conn only for migrations. |
| 4 | **Missed deadline reminder** (breaks the core promise) | High | At-least-once idempotent send, commit-after-send retry, dual-channel on T-0, operator alert on T-0 failure. |
| 5 | **IDOR / cross-tenant leak** | High | `get_current_user` + scoped repository + 404-not-403 + optional RLS net. |
| 6 | **OCR mis-extraction** frustrates the user | Medium | The whole Confirm screen exists to make a wrong read a 2-tap fix; nothing saves without a tap. |
| 7 | **WeasyPrint native deps** break exports | Medium | Use reportlab (pure-Python); openpyxl/csv not pandas. |
| 8 | **Large receipt zips** exceed serverless limits | Medium | Build → R2 → signed link; queue generation if needed. |
| 9 | **Email deliverability** = single point of failure for auth | Medium | Reputable provider + SPF/DKIM/DMARC + dedicated domain; ~15-min code window + resend; SMS contingency. |
| 10 | **iOS PWA fragility** (manual install, evictable cache, weak push) | Medium | Guided install hint; Postgres as source of truth; cron email reminders as the reliable channel. |
| 11 | **HMRC rule drift** (thresholds, dates, SA103 boxes) | Medium | Keep thresholds, deadlines, and box mapping in **config**; confirm SA103F boxes + ask the accountant before first export. |
| 12 | **Vercel env-var exposure** (per 2026 breach) | Medium | Mark secrets Sensitive; per-env least-privilege keys; rotation drill. |
| 13 | **Hobby cron daily-only/imprecise** | Low | Acceptable for a morning send; budget for Pro if timely/sub-daily needed. |
| 14 | **Cold starts** on an idle finance app | Low | Lean dependency tree + Fluid Compute; ~1–3s first hit is tolerable. |

---

## 11. Open questions (carried from the brief, to resolve before/early in build)

- **Accountant's preferred export format** — ask for a sample so the SA103 mapping lands exactly right (brief §7).
- **Confirm exact SA103F box numbers** for the current tax year (the one cross-agent discrepancy; config-driven so low-risk).
- **Vision-LLM provider final pick** (Gemini Flash recommended) and whether to add a second provider for failover.
- **Phase 2 vs Phase 3** decision for HMRC submission (deliberate, post-v1).
- **Wife's profile specifics + account-switching UX** (parked).
- **Web-push** — treat as a bonus only, never a dependency.

---

*Plan synthesised from six parallel research agents. Each subsystem section is buildable as written; the phased roadmap (§9) is the recommended execution order.*
