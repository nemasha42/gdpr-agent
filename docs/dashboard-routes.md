# Dashboard Routes & UI — Detailed Documentation

> Back to @ARCHITECTURE.md for the system overview and module map.

---

## Overview

**What it does:** Provides a web UI at `localhost:5001` for reviewing SAR status, viewing reply threads, inspecting data schemas, managing portal submissions, sending subprocessor disclosure requests, and seeing LLM cost history.

**How it works:** The dashboard uses an app factory pattern. `dashboard/__init__.py` provides `create_app()` (107 lines) which sets up Flask, LoginManager, registers all 10 route blueprints, the before_request hook, context processor, and template filter. `dashboard/shared.py` (573 lines) contains all shared helpers, constants, and data-loading functions. `dashboard/app.py` is the entry point (33 lines — creates the app and runs it). All routes are in 10 blueprints under `dashboard/blueprints/` (2,832 lines total): `pipeline_bp` (831), `company_bp` (578), `transfers_bp` (385), `data_bp` (285), `portal_bp` (239), `dashboard_bp` (190), `monitor_bp` (90), `costs_bp` (89), `api_bp` (78), `settings_bp` (67). Service modules under `dashboard/services/` (1,520 lines total): `monitor_runner.py` (890), `graph_data.py` (395), `jurisdiction.py` (235). State files (`reply_state.json`, `sent_letters.json`, `companies.json`, `cost_log.json`) are read on every request — there is no in-memory state. This makes it safe to run while `monitor.py` is also running, at the cost of disk reads on every page load.

**Important:** Always use `_load_all_states(account)` (in `dashboard/shared.py`) — not `load_state()` — for any route that displays company counts or cards. `_load_all_states()` merges reply_state.json with sent_letters.json via `promote_latest_attempt()` so recently-sent letters appear immediately without waiting for a monitor run. Using `load_state()` directly undercounts by missing companies sent since the last monitor run.

**`_lookup_company(domain)`** (in `dashboard/shared.py`) merges `data/companies.json` (handles nested `{"companies": {...}}` structure) with `data/dataowners_overrides.json`. Override contact fields are deep-merged (non-empty values win). Used by `company_detail()` to provide `portal_url` template var and by `portal_submit`/`mark_portal_submitted` routes.

---

## Route Reference

### Core routes

- `GET /` — all companies dashboard
- `GET /company/<domain>` — reply thread detail
- `GET /data/<domain>` — data catalog viewer
- `GET /cards` — companies with/without data (Data Cards view)
- `GET /costs` — LLM cost history
- `GET /transfers` — subprocessor data transfer map + D3.js graph
- `GET /pipeline` — scan/resolve/send pipeline
- `GET /pipeline/review` — letter review & approve
- `GET /pipeline/reauth-send` — re-authorize gmail.send OAuth
- `POST /refresh` — runs monitor + re-extracts missing links, saves to reply_state.json

### Portal automation routes

- `POST /portal/submit/<domain>?account=EMAIL&portal_url=URL` — starts background portal submission. Accepts `portal_url` query param for WRONG_CHANNEL companies whose `preferred_method` is not "portal"; falls back to resolver then `dataowners_overrides.json`. Returns 409 if already running. Syncs portal status to `CompanyState` via `set_portal_status()`.
- `GET /portal/status/<domain>` — polls task progress. Returns **flat JSON** with `status`, `success`, `needs_manual`, `portal_status`, `confirmation_ref`, `error` — NOT nested under a `result` key. JS must read `sd.success` not `sd.result.success`.
- `POST /portal/verify/<domain>` — marks portal verification as passed. Restarts 30-day deadline via `verify_portal()`. Returns JSON with updated `portal_status`, `deadline`, `portal_verified_at`.
- `POST /company/<domain>/mark-portal-submitted` — manual marking after user fills portal form themselves. Persists `portal_submission.status="submitted"` to reply_state.json.
- `GET /captcha/<domain>` — displays CAPTCHA screenshot + solution form
- `POST /captcha/<domain>` — accepts user CAPTCHA solution, resumes portal submission

### Background task routes

- `POST /transfers/fetch` — starts subprocessor fetch task
- `GET /api/transfers/task` — polls task progress
- `POST /transfers/request-letter/<domain>` — sends SP disclosure request for one company (falls back to SAR `to_email` when no privacy/dpo email)
- `POST /transfers/request-all` — background task, sends to all companies with email contact and no prior request, tracked in `subprocessor_requests.json`; also uses SAR email fallback

### Compose routes

- `POST /company/<domain>/compose-reply` — sends SAR follow-up email, creates YOUR_REPLY record, auto-dismisses pending action drafts
- `POST /company/<domain>/compose-sp-reply` — sends SP follow-up email

---

## UI Components

### Navbar (`base.html`)

Centered tab navigation (Dashboard, Pipeline, Data Cards, Costs, Transfers) with `active_tab` highlighting. Account selector and action buttons in `{% block nav_extra %}`. Logout is a small `btn-outline-secondary` button in the right-side control group.

### Transfer Graph (`/transfers`)

D3.js v7 force-directed visualization of subprocessor data flows. `dashboard/services/graph_data.py` builds graph JSON (nodes + edges + stats) from subprocessor rows and company records, with configurable depth (1–6 layers, default 4 via `?depth=N` query param). `dashboard/services/jurisdiction.py` provides GDPR adequacy assessment — classifies countries as EU/EEA, adequate (DPF, bilateral), or third-country for risk coloring. `dashboard/static/js/transfer-graph.js` renders the graph with zoom controls, coverage donut, and depth selector.

### Data Cards (`cards.html`)

Account selector dropdown in `nav_extra`. Cards show a `Wrong channel` warning badge (yellow border + badge) when `is_wrong_channel` is true. Two sections: "With data" and "Without data" with tab navigation.

### Company detail (`company_detail.html`)

Two-panel layout with a `stream_panel()` Jinja2 macro rendering SAR and SP streams independently. `company_detail()` builds `sar_thread` and `sp_thread` as separate event lists (oldest first). `sp_all_msg_ids` (all SP reply IDs including `YOUR_REPLY`) is used to dedup SAR replies — if a message appears in the SP stream, it is excluded from SAR. Thread events have types: `sent` (outgoing letter), `reply` (company message), `your_reply` (user's manual Gmail reply or dashboard-sent follow-up). NON_GDPR replies are hidden entirely from the detail view (not dimmed). Links in reply messages are gated on tags: "Download data" requires `DATA_PROVIDED_*` or `FULFILLED_DELETION`; "Privacy portal" requires `WRONG_CHANNEL`, `DATA_PROVIDED_PORTAL`, `CONFIRMATION_REQUIRED`, or `MORE_INFO_REQUIRED`; "Confirm request" requires `CONFIRMATION_REQUIRED`. Portal URL in template uses `display_portal_url = ex.portal_url or portal_url` where `portal_url` comes from `_lookup_company(domain)`. WRONG_CHANNEL replies with a portal URL show a "Submit SAR via portal" button — `submitViaPortal()` JS shows live step-by-step progress ("Opening portal…", "Filling in your details…") and displays actionable results (success, reCAPTCHA blocked with manual instructions, or failure). WRONG_CHANNEL replies also display `extracted.wrong_channel_instructions` — a short phrase describing what the company says to do (e.g. "submit via our privacy portal"). WRONG_CHANNEL action drafts include an "I submitted via portal" button that sets `reply_review_status="portal_submitted"` and dismisses the draft without sending an email. A "View received data" button links to `/data/<domain>` on messages with data provision tags or attachments. Each stream panel includes a "Compose follow-up" collapsible form at the bottom of the thread for free-form replies. When `state.portal_submission` exists, a status bar appears above the thread: green for "submitted", blue for "manual needed" (with "Mark as submitted" form), yellow for "failed". A "Privacy Policy" link appears below the company header when `privacy_policy_url` is available from the company record.

### Dashboard cards

Each card shows three elements: **status pictograms**, **contact address**, and **action buttons**.

**Status pictograms** — a row of small badges (`.picto-row > .picto`) indicating which request streams exist: `SAR` (always present), `SUB` (subprocessor disclosure sent), `DEL` (deletion request). Each pictogram is color-coded by its stream's status color.

**Contact address** — the `to_email` used for the SAR, shown below the company name. Styled with strikethrough (`text-decoration: line-through`) only when the SAR status is `STALLED` (bounce/address exhausted); normal text otherwise.

**Action buttons** — "View correspondence" button colored by SAR status (`btn-outline-{{ status_colour }}`) instead of the previous binary primary/secondary. A "View data" button appears when `has_data` is true (status=DONE with a DATA_PROVIDED tag).

### Snippet display

Raw Gmail snippets often contain encoding artifacts (HTML entities, MIME quoted-printable, URL encoding). `_clean_snippet(text)` in `dashboard/shared.py` decodes these at display time — raw data in `reply_state.json` is never modified. Applied in `company_detail()` for SAR replies, past-attempt replies, and SP replies. `_is_human_friendly(text)` is the paired test predicate; it is not called in production routes.

### Draft reply guard

`has_pending_draft` (used to show the "Draft reply ready" badge on cards) requires three conditions: `reply_review_status == "pending"`, a non-empty `suggested_reply`, **and** at least one tag in `_ACTION_DRAFT_TAGS` (imported from `reply_monitor.classifier`). The tag guard prevents stale `"pending"` state on AUTO_ACKNOWLEDGE or other non-action replies from showing a false-positive badge. `company_detail.html` applies the same guard (`r.has_action_draft`) before rendering the draft form. When a YOUR_REPLY is detected by the monitor, all pending action drafts for that company are auto-dismissed. Both `monitor.py` and the dashboard's inline monitors apply this auto-dismiss logic.

### LLM summary

When `classifier.py` falls back to Claude Haiku, it also populates `extracted["summary"]` — a ≤15-word plain-English sentence. `company_detail.html` shows this in italic instead of the raw snippet when present. Summary is only set on the LLM path (~10–20% of replies).

---

## Tag Display

`_effective_tags(all_tags)` in `dashboard/shared.py` applies tier-based supersession for cards:

| Tier | Type | Tags |
|------|------|------|
| 1 | Terminal | DATA_PROVIDED_*, REQUEST_DENIED, NO_DATA_HELD, NOT_GDPR_APPLICABLE, FULFILLED_DELETION |
| 2 | Action | WRONG_CHANNEL, IDENTITY_REQUIRED, CONFIRMATION_REQUIRED, MORE_INFO_REQUIRED, HUMAN_REVIEW |
| 3 | Progress | REQUEST_ACCEPTED, IN_PROGRESS, EXTENDED |
| 4 | Informational | AUTO_ACKNOWLEDGE, BOUNCE_* |
| — | Always hidden | OUT_OF_OFFICE, NON_GDPR (unless only tag) |

Higher tiers supersede lower — e.g. DATA_PROVIDED hides REQUEST_ACCEPTED; WRONG_CHANNEL hides ACK. `_DISPLAY_NAMES` maps raw constants to user-friendly labels. `HUMAN_REVIEW` is in `_ACTION_TAGS` (state_manager.py) so it triggers ACTION_NEEDED status.

---

## Status System

The status system has 2 layers: 21 reply tags (unchanged) → 7 unified statuses used by both SAR and SP streams. The company-level status layer has been removed — SAR status is the primary badge, SP is shown as a secondary badge using the same 7-status vocabulary. For the complete evaluation logic, tag sets, and edge cases, see the **Status resolution logic** section in @docs/reply-monitor.md.

| Priority | Status | Color | Meaning |
|----------|--------|-------|---------|
| 7 | `OVERDUE` | danger (red) | Past 30-day GDPR deadline, no terminal resolution |
| 6 | `ACTION_NEEDED` | warning (yellow) | Company asked for something — user must act |
| 5 | `STALLED` | danger (red) | Delivery failed, can't reach company |
| 4 | `REPLIED` | primary (blue) | User sent follow-up, awaiting response |
| 3 | `IN_PROGRESS` | info (teal) | Company acknowledged / working on it |
| 2 | `WAITING` | primary (blue) | Request sent, no response yet |
| 1 | `DONE` | success/secondary | Terminal — resolved one way or another |

DONE sub-labels (via `compute_done_reason()`): "Data received", "Deletion confirmed", "Denied" (secondary), "No data held" (secondary), "Not applicable" (secondary). `_STATUS_PRIORITY` dict in `state_manager.py` drives sort order. `REQUEST_STATUSES` list in `models.py` is the canonical list of 7 values.

---

## Known Limitations

The `/refresh` route blocks the HTTP response during the full monitor run — flagged for future async handling. Port 5001 is hardcoded. Authentication is handled by Flask-Login via `dashboard/__init__.py`'s `create_app()` — the before_request hook redirects unauthenticated users to the login page (except for `auth.*` and `static` endpoints).
