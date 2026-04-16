# Dashboard Routes & UI — Detailed Documentation

> Back to @ARCHITECTURE.md for the system overview and module map.

---

## Overview

**What it does:** Provides a web UI at `localhost:5001` for reviewing SAR status, viewing reply threads, inspecting data schemas, managing portal submissions, sending subprocessor disclosure requests, and seeing LLM cost history.

**How it works:** The dashboard uses an app factory pattern. `dashboard/__init__.py` provides `create_app()` which sets up Flask, LoginManager, auth blueprints, the before_request hook, context processor, and template filter. `dashboard/shared.py` contains all shared helpers, constants, and data-loading functions. `dashboard/app.py` registers remaining route handlers on the app instance. Extracted blueprints: `costs_bp` (Phase 1), `settings_bp` (Phase 1), `api_bp` (Phase 1), `data_bp` (Phase 2), `monitor_bp` (Phase 2). `dashboard/services/monitor_runner.py` contains unified monitor functions used by both `monitor.py` CLI and `monitor_bp` routes. State files (`reply_state.json`, `sent_letters.json`, `companies.json`, `cost_log.json`) are read on every request — there is no in-memory state. This makes it safe to run while `monitor.py` is also running, at the cost of disk reads on every page load.

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

Two-panel layout with a `stream_panel()` Jinja2 macro rendering SAR and SP streams independently. `company_detail()` builds `sar_thread` and `sp_thread` as separate event lists (oldest first). `sp_all_msg_ids` (all SP reply IDs including `YOUR_REPLY`) is used to dedup SAR replies — if a message appears in the SP stream, it is excluded from SAR. Thread events have types: `sent` (outgoing letter), `reply` (company message), `your_reply` (user's manual Gmail reply or dashboard-sent follow-up). NON_GDPR replies are hidden entirely from the detail view (not dimmed). Links in reply messages are gated on tags: "Download data" requires `DATA_PROVIDED_*` or `FULFILLED_DELETION`; "Privacy portal" requires `WRONG_CHANNEL`, `DATA_PROVIDED_PORTAL`, `CONFIRMATION_REQUIRED`, or `MORE_INFO_REQUIRED`; "Confirm request" requires `CONFIRMATION_REQUIRED`. Portal URL in template uses `display_portal_url = ex.portal_url or portal_url` where `portal_url` comes from `_lookup_company(domain)`. WRONG_CHANNEL replies with a portal URL show a "Submit SAR via portal" button — `submitViaPortal()` JS shows live step-by-step progress ("Opening portal…", "Filling in your details…") and displays actionable results (success, reCAPTCHA blocked with manual instructions, or failure). A "View received data" button links to `/data/<domain>` on messages with data provision tags or attachments. Each stream panel includes a "Compose follow-up" collapsible form at the bottom of the thread for free-form replies. When `state.portal_submission` exists, a status bar appears above the thread: green for "submitted", blue for "manual needed" (with "Mark as submitted" form), yellow for "failed".

### Dashboard cards

Show a "View correspondence" button (no reply count) — styled `btn-outline-primary` when the company has at least one non-`NON_GDPR`, non-`YOUR_REPLY` reply, pale `btn-outline-secondary` otherwise. A "View data" button appears when `has_data` is true (status=COMPLETED with a DATA_PROVIDED tag).

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

Higher tiers supersede lower — e.g. DATA_PROVIDED hides REQUEST_ACCEPTED; WRONG_CHANNEL hides ACK. `_DISPLAY_NAMES` maps raw constants to user-friendly labels. `HUMAN_REVIEW` is in `_ACTION_TAGS` (state_manager.py) so it triggers ACTION_REQUIRED status.

---

## Company-Level Status

`compute_company_status(sar_status, sp_status, sp_sent)` in `state_manager.py` aggregates SAR and SP streams into one company-level badge shown as the primary badge on dashboard cards. 9 values, priority order (highest first):

| Priority | Value | Condition |
|----------|-------|-----------|
| 8 | `OVERDUE` | Any stream past GDPR deadline |
| 7 | `ACTION_REQUIRED` | Any stream needs user action |
| 6 | `STALLED` | Any stream is BOUNCED or ADDRESS_NOT_FOUND |
| 5 | `USER_REPLIED` | SAR=USER_REPLIED — user sent follow-up, awaiting company response |
| 4 | `DATA_RECEIVED` | SAR terminal (COMPLETED/DENIED); SP sent but not yet terminal |
| 3 | `FULLY_RESOLVED` | SAR terminal + (SP terminal OR SP not sent) |
| 2 | `IN_PROGRESS` | SAR is ACKNOWLEDGED, EXTENDED, PORTAL_SUBMITTED, or PORTAL_VERIFICATION |
| 1 | `SP_PENDING` | SAR=PENDING + SP sent + SP=PENDING |
| 0 | `PENDING` | Default — SAR pending, SP not sent |

Invariant: SP can only escalate; `sp_sent=False` never downgrades. `DATA_RECEIVED` ranks above `FULLY_RESOLVED` in sort urgency because the SP thread is still open. `_COMPANY_STATUS_PRIORITY` dict drives sort order. `COMPANY_LEVEL_STATUSES` list in `models.py` is the canonical list of 9 values.

---

## Known Limitations

The `/refresh` route blocks the HTTP response during the full monitor run — flagged for future async handling. Port 5001 is hardcoded. Authentication is handled by Flask-Login via `dashboard/__init__.py`'s `create_app()` — the before_request hook redirects unauthenticated users to the login page (except for `auth.*` and `static` endpoints).
