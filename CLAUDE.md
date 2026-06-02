# CLAUDE.md — Tidy Books

Operating guide for Claude Code agents in this repo. Optimised for **throughput with
a quality gate**: every change is built, self-reviewed, hardened, then opened as a PR.

---

## 1. What this is (30-second orientation)

A mobile-only installable **PWA** that takes a UK self-employed driving instructor from
"shoebox of receipts" to **Making Tax Digital (MTD) readiness**. Python end-to-end,
server-rendered, **no build step**.

**Stack:** FastAPI (single ASGI app) · Jinja2 + HTMX + Alpine.js · hand-written CSS ·
SQLAlchemy 2.0 · reportlab/openpyxl. Prod targets Vercel + Neon Postgres + Cloudflare R2
+ a vision-LLM + Resend/Twilio. **Runs locally with an empty `.env`** — every external
dependency degrades to a local fallback (SQLite / local filesystem / OCR stub / console).

Read `README.md` for run instructions and `architecture-plan.md` + `project-brief (1).md`
for the *why*. Provider selection lives in `app/config.py`.

### ⭐ The one rule above all others: this is for non-technical users
The target user is a **technically illiterate** self-employed driving instructor. **Simplicity
and an effortless user journey are the single most important property of this app — more than
features, more than cleverness.** Every change is judged first by: *would someone who has never
heard of "MTD", "SA103", or "OCR" understand exactly what to do next, with zero thought?*
- One clear primary action per screen. No jargon in the UI — plain English, never tax/tech terms.
- No dead ends, no ambiguous states, no decisions the user shouldn't have to make.
- Fewer taps, fewer screens, fewer choices. If a step can be removed or automated, remove it.
- A feature that adds power but adds confusion is a **net negative** — push back on it.

### Layout (don't relearn this each time)
```
api/index.py        # single ASGI entrypoint (Vercel Function): router wiring, CSRF +
                    #   security-header middleware, dev seed
app/
  config.py         # env settings + MTD thresholds/deadlines — config, NOT code
  models.py db.py   # SQLAlchemy models + scoped Repo (the anti-IDOR backstop)
  security.py       # signed-cookie sessions, CSRF, OTP, get_current_user
  storage.py ocr.py notify.py   # provider-swappable: R2/local, vision-LLM/stub, Resend/console
  sa103.py tax.py   # SA103 box mapping + mandation engine + plain-English status
  export.py reminders.py templating.py util.py
  routes/           # core, media, auth, capture, records, categories, status, export, cron
  templates/        # base.html + screens + HTMX partials
public/             # app.css, app.js, manifest.json, sw.js, vendored htmx/alpine
```

---

## 2. The workflow — build → review → improve → review → PR

**Every non-trivial task runs through this loop. Do not skip the reviews.**

```
  ┌─ build ──→ review ──→ improve ──→ review ──→ (improve…) ──→ PR ─┐
  └──────────── repeat review↔improve until the gate is green ──────┘
```

1. **Build** — implement the smallest correct version of the task in an isolated worktree (§3).
2. **Review** — critique your own diff against the §5 checklist. Write the findings down
   (in your response, as a short numbered list). Be adversarial: assume the code is wrong.
3. **Improve** — fix every issue the review found. Don't defer; if you defer, say why.
4. **Review again** — re-run the checklist on the *new* diff. New code can introduce new bugs.
5. **Improve again** *(as needed)* — loop review↔improve until a review produces **zero**
   must-fix findings. Two clean passes minimum before you open the PR.
6. **PR** — push the branch and open a PR (§4). The PR description *is* the final review summary.

**When to short-circuit:** trivial one-liners (typo, comment, version bump) may go straight
build → single review → PR. Anything touching auth, the scoped `Repo`, money/tax maths,
SA103 mapping, migrations, or a route handler always runs the full loop.

**Stop conditions:** if two improve cycles can't clear a finding, stop and surface it to the
user with options rather than churning. Don't loop more than ~4 review↔improve rounds.

---

## 3. Worktrees — every task gets its own subtree

Work in an **isolated git worktree** ("subtree"), never directly on `master`. This keeps the
main checkout clean, lets the local dev server / `local.db` stay untouched, and makes the PR
diff exactly your change.

```bash
# from the repo root
git worktree add ../tb-<short-task-slug> -b feat/<short-task-slug> master
cd ../tb-<short-task-slug>
python -m venv .venv && . .venv/Scripts/activate   # bash on Windows; PS: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

- Branch naming: `feat/…`, `fix/…`, `chore/…`, `docs/…`.
- One worktree per task. Do all build/review/improve cycles inside it.
- **Tear down when the PR is open:** `git worktree remove ../tb-<slug>` (from root). Don't
  leave stale worktrees behind.
- Never commit local dev artifacts — `.env`, `*.db`, `local_storage/`, `screenshots/` are
  gitignored; keep it that way.

> If Claude Code's native worktree tooling is available in your session, prefer it — the
> intent ("isolate this task") matters more than the exact command.

---

## 4. Opening the PR

Remote is GitHub (`origin` → `011-sam-110/Accountant`). Use the `gh` CLI.

```bash
git push -u origin feat/<slug>
gh pr create --base master --title "<imperative summary>" --body-file -   # body = your review summary
```

PR body must contain:
- **What & why** — one paragraph, link the brief/plan section if relevant.
- **Review summary** — the findings from your final review pass and how each was resolved.
- **Checks run** — paste the `_smoke.py` result (and `run_review.py` if UI changed).
- **Risk notes** — anything touching auth/tenant-scoping/money/migrations, called out explicitly.

Only push and open the PR **after** the review gate is green. Don't push WIP to `origin`.
Don't merge — leave the PR for the user to review unless they say otherwise.

---

## 5. Review checklist (the quality gate)

Run this against your own diff at every **review** step. A finding here is a **must-fix**.

**Simplicity / user journey (judge this FIRST — see ⭐ in §1):**
- [ ] Could a technically illiterate user complete the journey with zero confusion? Walk it.
- [ ] No jargon in any user-facing copy (no "MTD", "SA103", "OCR", "OTP", error codes, etc.).
- [ ] One obvious primary action per screen; no dead ends or ambiguous states; no avoidable taps,
      screens, or decisions. A change that adds confusion is rejected even if it "works".

**Tenant isolation / security (highest priority — this app is multi-user):**
- [ ] Every DB query goes through the **scoped `Repo`** (`app/db.py`). No raw cross-user
      access — the Repo is the anti-IDOR backstop; bypassing it is a security bug.
- [ ] State-changing routes are CSRF-protected and require `get_current_user` (`app/security.py`).
- [ ] No secret, OTP, or other user's data leaks into a response or log. `DEV_SHOW_OTP`
      stays opt-in. Security headers/middleware in `api/index.py` not weakened.
- [ ] User input is validated; file uploads go through `storage.py`, not ad-hoc paths.

**Correctness:**
- [ ] Money/tax maths and **SA103 box mapping** (`sa103.py`, `tax.py`) are correct and unit-sane.
      MTD thresholds/deadlines come from `config.py`, never hard-coded inline.
- [ ] HTMX endpoints return **clean fragments** (no nested `<!doctype>` / shell) — `_smoke.py`
      asserts this; respect it.
- [ ] Works with the **empty-`.env` local fallbacks** — don't introduce a hard dependency on a
      cloud provider. Provider choice stays driven by `config.py`.

**Quality:**
- [ ] Matches surrounding style: server-rendered, no build step, no new JS framework, no new
      heavy dependency without justification. Reuse `util.py` / `templating.py` helpers.
- [ ] Smallest correct change; no dead code, no unrelated churn, no debug prints left in.
- [ ] Touched behaviour is covered by `_smoke.py` (extend it if you added a slice).

---

## 6. Checks (must pass before PR)

```bash
python _smoke.py                 # end-to-end integration across every slice; asserts clean HTMX fragments
# UI changes only:
uvicorn api.index:app --reload   # then, in another shell:
python run_review.py             # iPhone-12 Chromium walkthrough → screenshots/  (needs: playwright install chromium)
```

`_smoke.py` is the non-negotiable gate — it spins up the app on a temp SQLite DB and exercises
login → capture → records → categories → status → export. If you add or change a slice, add the
assertion to `_smoke.py` in the same PR.

---

## 7. Conventions & guardrails

- **Python 3.13.** Windows dev host, PowerShell default shell — but code must stay
  cross-platform (it runs on Vercel/Linux). Use `pathlib`, never hard-code path separators.
- **No build step, ever.** htmx/alpine are vendored in `public/vendor/`. Server-rendered Jinja
  + HTMX partials are the pattern; don't add a bundler or SPA framework.
- **`config.py` is config, not code** — thresholds, deadlines, provider keys. New tunables go
  there, read from env with a sane local default.
- **Migrations:** Alembic against the *direct* (non-pooled) Neon URL; `create_all` at startup is
  the idempotent safety net. A schema change is a high-risk PR — call it out.
- **Don't commit:** `.env`, `*.db`, `local_storage/`, `screenshots/`, `node_modules/` (all gitignored).
- Commit only when work is review-clean. End commit messages with the Co-Authored-By trailer.
- This repo handles real tax data — when unsure about money, tax rules, or tenant scoping,
  stop and ask rather than guess.
