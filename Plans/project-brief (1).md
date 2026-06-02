# Project Brief — Driving Instructor Finance App

*(working title: TBD)*

**One-liner:** A simple, hand-holding mobile web app that takes a UK sole trader from "shoebox of paper receipts" to **Making Tax Digital readiness** — capturing income and expenses as they happen, keeping HMRC-compliant digital records, working out for itself whether MTD applies, and producing clean output for the accountant or for HMRC. Built specifically for people who aren't confident with technology and are used to doing this on paper — so **ease of use comes before everything else**.

---

## 1. The goal

MTD for Income Tax is a real, growing compliance burden, and the people most exposed to it are often the least comfortable with software. This app exists to **absorb that complexity** so the user doesn't have to understand it.

The user just captures money in and money out. The app quietly does the hard part: keeps records in the shape HMRC requires, tracks whether the user has crossed an MTD threshold, tells them in plain English what that means for them and when, and generates the right output — whether that's a tidy package for the accountant (today) or, eventually, submissions to HMRC itself.

**Success looks like:** a non-technical sole trader stays fully compliant without ever needing to learn what "MTD" or "quarterly update" technically means — the app handles it and explains it simply. He records his first receipt within a minute of opening the app for the first time, and never feels he might break something.

## 2. Who it's for & context

- **Primary user:** a UK self-employed driving instructor (sole trader, Self Assessment) who is **not technically confident** and is used to paper records.
- **Device:** mobile-first, designed and tested for iPhone 12. It's a website, not a native app. Designed for **legibility and large touch targets** — the user may not have sharp eyesight or a steady tap.
- **Other users:** his wife will have her **own separate account** with isolated records. Multi-user from the start; her specifics parked for later.
- **Jurisdiction:** UK. Tax year **6 April → 5 April**. Records and exports follow UK Self Assessment / MTD conventions.

## 3. Design principles (non-negotiable)

Ease of use and customisation are the spine of this product, so they lead the list:

1. **Simple above all.** Minimal buttons. Every screen earns its place. The fastest correct action is always the default.
2. **Smooth journey.** Capturing an entry takes seconds from the home screen. Favour taps over typing — pickers, last-used values, a number pad for amounts — because typing on a phone is friction.
3. **Sensible defaults; never a blank page.** The app works fully out of the box. Customisation is something the user grows into, never a setup chore standing between him and his first receipt.
4. **Forgiving.** Nothing is permanent. Easy edit, easy undo, and a clear "you can't break this" reassurance throughout — because the user is nervous about technology.
5. **Nothing saved without confirmation.** OCR and auto-sorting are conveniences, never the final word. But confirmation is usually a single tap, not a form: the extracted details and a suggested category come pre-filled; the user accepts or edits.
6. **Plain language, no jargon.** Tax concepts are translated into clear, reassuring everyday language — and this extends to every error message, empty state and reminder, not just the tax bits. The app tells the user what to do and when, not what the rules are called.
7. **Legible and reachable.** Large text, high contrast, big touch targets. Accessibility *is* ease of use for this user.
8. **The app does the thinking.** Threshold checks, deadlines, categorisation, compliance — handled in the background, surfaced only when the user needs to act.
9. **Customisable without ever breaking compliance.** His categories, his structure, his labels — but each maps invisibly to the right tax box underneath, so customising can never threaten the clean export (see §4 and §7).

## 4. What it does

### First run (onboarding)
The first five minutes decide whether a paper-based user stays. So:
- A short, plain-language welcome — no tax vocabulary.
- **Driving-instructor categories pre-loaded** (see §7) so there's nothing to set up before the first entry.
- A guided **first scan** that walks him through camera → confirm → saved, so he sees the whole loop once with help.
- Customisation is introduced *later*, gently, once he's comfortable — never as a wall on day one.

### Capture (the heart of the app)
- Prominent **Scan** button → camera → OCR reads the receipt.
- Equally easy **Manual entry** for things without a receipt.
- Lightweight **income quick-add** for recording student/pupil payments.
- Minimal typing throughout: number pad for amounts, date defaulting to today, last-used values offered.

### Auto-sort with one-tap confirmation
- After a scan, the app extracts date, amount and vendor and **suggests** a category.
- A **confirmation screen** shows the extracted info and suggested category, all pre-filled, so the common case is a single **"Looks right"** tap; the user can edit any field before it saves. Always.
- **Gentle failure path.** When OCR can't read a receipt, the photo is kept and the user is dropped onto the same confirm screen with a friendly prompt ("Couldn't read the amount — tap to type it") — never a dead end, never a lost photo.

### Custom categories — customisable, but compliance-safe
The design tension here is real: the user wants *his* categories, but the export has to map cleanly onto the tax return. We resolve it by separating two layers:
- **Visible layer (fully his):** he can rename categories, reorder them, mark favourites for quick access, and hide ones he never uses.
- **Hidden layer (fixed):** each category is linked to the correct UK Self Assessment expense type underneath. Renaming the label never changes the tax mapping, so exports stay correct no matter how he customises.

Rules that protect his records when he edits later:
- **Rename** is cosmetic — underlying tax mapping unchanged.
- **Merge** reassigns the affected entries to the surviving category.
- **Delete** is blocked while entries exist (or requires reassigning them first), so nothing is orphaned.
- **Closed tax years are locked** — customising now never rewrites a year already handed to the accountant.

Defaults ship pre-aligned to UK Self Assessment expense types (see §7) so records and exports map straight onto the tax return from day one.

### MTD threshold awareness (core to the mission)
The app must **work out the user's MTD status by itself** and explain it simply:
- It tracks **gross qualifying income** (turnover before expenses) per tax year.
- It compares against the phased thresholds — **£50,000 (from April 2026)**, **£30,000 (from April 2027)**, **£20,000 (from April 2028)** — and works out whether and when MTD applies. (Note: HMRC mandates you based on the qualifying income on your *previous* return, so the app watches both the completed year and the year in progress.)
- It surfaces a clear, plain-English status, e.g. *"You're under the threshold — a yearly summary is fine for now"* or *"You've gone over £50k — from the next tax year you'll need to send HMRC an update every 3 months. We'll handle that for you."*
- When MTD applies, it tracks the **quarterly deadlines** and the year-end final declaration, and reminds the user in good time.
- **Reminders reach him through a channel he'll actually see** (e.g. email/SMS). We do **not** rely on web-push notifications, which are unreliable on iOS Safari — a deadline reminder the user never sees would break the core promise.

### Records view (the ledger)
- **Tax year** (2024/25, 2025/26, 2026/27…) → **Month** → **entries**, split into money in / money out.
- For income: track **which students have paid** (paid / outstanding) per month.
- Entries are **editable after saving** — tap to correct an amount, recategorise, or delete — in keeping with the "forgiving" principle. Edits to a closed (handed-over) tax year are locked.

### Output
Two purposes, designed to share one foundation:
- **Accountant handover (v1):** select data → **.zip** of organised **PDF** (clean summary) + **Excel/CSV** (raw data), with receipt images bundled.
- **HMRC-ready data (the ambition):** the same digital records, structured to feed MTD quarterly updates and the final declaration (see §8 for the path).

### Profiles / accounts
- Multiple users, each with fully isolated data. (Switching UX deferred.)
- **Painless account recovery is in scope, not optional.** A forgotten password must never lock the user out of his own tax records — recovery has to be simple enough for a non-technical user to do alone.

## 5. Screen architecture & journey

```
[ First run ]        ←—  one-time: welcome, pre-loaded categories, guided first scan
        │
        ▼
[ Home / Capture ]   ←—  default landing screen thereafter
   • big Scan button
   • Manual add (expense)
   • Quick-add income
   • a small, plain-language MTD status line ("You're on track")
        │
        ▼
[ Confirm entry ]    ←—  pre-filled extracted data + suggested category; one-tap accept or edit, save
        │
        ▼
[ Records ]          ←—  Tax year → Month → entries; paid/unpaid tracking; edit/undo entries
        │
        ▼
[ Output ]           ←—  accountant .zip now; HMRC submission later
        │
[ Status / Tax ]     ←—  plain-English MTD status, thresholds, deadlines, reminders
[ Account ]          ←—  login, recovery, switch user (switching parked)
```

Still feels like **two core screens** (Capture, Records), with output, status and account behind them.

## 6. Data model (first sketch)

- **User** — owns everything below; isolated per user.
- **Entry** — one transaction. Type: *expense* or *income*. Fields: date, amount, category, notes, linked receipt (optional); for income: related student + paid/outstanding. **Editable after save; keeps created/edited timestamps** for an honest record trail.
- **Category** — user-defined **label, order, favourite/hidden flags** (the visible, customisable layer); each maps to a **fixed Self Assessment expense type** underneath (the layer that keeps exports compliant).
- **Student / Pupil** — optional, for paid-tracking.
- **Receipt image** — stored and linked to its entry.
- **TaxYear summary** — derived totals (gross income, expenses) used to compute MTD status and deadlines. A **closed tax year is an immutable snapshot** — later customisation doesn't alter it.

## 7. Records & accountant export — UK default

Until the accountant provides their preferred format, design to standard **Self Assessment (SA103) self-employment** conventions. Suggested default categories for a driving instructor:

**Income:** Lesson / tuition fees · Other income

**Expenses** (grouped to map onto Self Assessment expense boxes):
Car, van & travel (fuel, mileage) · Vehicle costs (insurance, servicing, repairs, dual-control maintenance) · Phone & internet · Advertising & marketing · Professional / franchise fees (DVSA, ADI registration, franchise) · Training & CPD · Accountancy, admin & bank charges · Other allowable business expenses

These ship pre-loaded so the user never faces a blank category list. He can rename, reorder, favourite or hide them freely — **the visible label is his, but the Self Assessment box each one maps to is fixed**, so however he customises, the export still lands in the right place on the return.

> **Action item:** ask the accountant for a sample of how they like receiving data so the export maps straight onto the return. Sensible default now, easy to adjust.

## 8. MTD compliance strategy (the core ambition)

There are **two layers** to being "MTD compliant", and they're very different in size. Being clear about this protects the project.

**Layer 1 — Compliant digital record-keeping (build now).**
MTD requires a digital record of *every* transaction (date, amount, category) kept in software, in the right categories. This app does exactly that. On its own, this makes the app legitimate **record-keeping software** — a recognised role in HMRC's model.

**Layer 2 — Submitting to HMRC (the bigger step).**
Actually sending the quarterly updates and final declaration means connecting directly to HMRC's API, which is a **regulated process**, not just a feature. To get there, software must:
- register on HMRC's **Developer Hub** and test against their **sandbox**;
- implement **OAuth 2.0** so the user authorises it via their Government Gateway login;
- send **fraud-prevention header** data (a legal requirement);
- meet HMRC's **minimum functionality standards** and pass a **Production Approvals Checklist** before going live.
HMRC calls passing software "recognised" / "compatible" (it doesn't endorse or rank products).

**The phased path this points to:**
- **Phase 1 (v1):** Build the app as compliant record-keeping + threshold awareness + clean accountant export. Ships real value immediately, no recognition needed.
- **Phase 2:** Because HMRC explicitly allows a *combination* of products joined by "digital links," the app can hand its digital records to the accountant's (or a third party's) recognised filing software — compliant submission without building the API integration ourselves.
- **Phase 3 (full ambition):** Pursue HMRC recognition and integrate the MTD API so the app submits quarterly updates and the final declaration end-to-end. This is the largest undertaking (regulatory, security, ongoing maintenance) and should be a deliberate decision once the core product is proven.

> The key reassurance: every phase builds on the same foundation. Phase 1's records *are* the data Phases 2 and 3 submit, so we never throw work away.

## 9. Parked for later

- Tech stack, hosting, and **which OCR engine** (client-side vs API; iOS Safari camera quirks; offline capability).
- How auto-categorisation guesses (rules, vendor matching, smarter suggestions) — and whether it learns from corrections.
- Exact rules for the threshold/deadline engine (HMRC's mandation timing, opt-in/opt-out edge cases).
- The decision on Phase 2 vs Phase 3 for HMRC submission.
- The wife's profile specifics and account-*switching* UX. (Account *recovery* is in scope now — see §4.)
- Receipt image storage and limits.

## 10. Out of scope for v1

- Direct HMRC submission (a deliberate later phase — see §8, not abandoned).
- Bank feeds / automatic transaction import.
- VAT (separate MTD regime; only relevant above £90k turnover).
- Multi-currency.
- Anything beyond a single sole trader's income and expenses.
