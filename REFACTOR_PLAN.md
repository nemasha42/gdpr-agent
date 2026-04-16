# Refactor Plan: `dashboard/app.py` → Flask Blueprints

**Generated:** 2026-04-16
**Status:** Analysis complete — awaiting review before implementation
**File under analysis:** `dashboard/app.py` (3,099 lines)

---

## Table of Contents

1. [Route Map](#step-1-route-map)
2. [Dependency Graph](#step-2-dependency-graph)
3. [Three-Way Monitor Comparison](#step-3-three-way-monitor-comparison)
4. [Shared Utilities Inventory](#step-4-shared-utilities-inventory)
5. [Blueprint Split Proposal](#step-5-blueprint-split-proposal)
6. [Services Layer Proposal](#step-6-services-layer-proposal)
7. [Execution Order](#step-7-execution-order)
8. [Risk Register](#step-8-risk-register)

---

## Step 1: Route Map

37 routes across 9 logical groups:

### Dashboard (2 routes)
| Method | URL | Line | Description |
|--------|-----|------|-------------|
| GET | `/` | 563 | Main dashboard — account selector, company cards sorted by urgency |
| GET | `/cards` | 1145 | Two-tab listing: companies with data vs. without |

### Company Detail (8 routes)
| Method | URL | Line | Description |
|--------|-----|------|-------------|
| GET | `/company/<domain>` | 603 | Full two-panel reply thread (SAR + SP streams) |
| POST | `/company/<domain>/send-followup` | 830 | Send auto-generated draft reply to SAR thread |
| POST | `/company/<domain>/dismiss-followup` | 867 | Dismiss a pending SAR draft |
| POST | `/company/<domain>/send-sp-followup` | 883 | Send auto-generated draft reply to SP thread |
| POST | `/company/<domain>/dismiss-sp-followup` | 921 | Dismiss a pending SP draft |
| POST | `/company/<domain>/compose-reply` | 937 | Send free-form reply in SAR thread |
| POST | `/company/<domain>/compose-sp-reply` | 990 | Send free-form reply in SP thread |
| POST | `/company/<domain>/mark-portal-submitted` | 2190 | Manually mark portal submission as completed |

### Data (4 routes)
| Method | URL | Line | Description |
|--------|-----|------|-------------|
| GET | `/data/<domain>` | 1042 | Data card — catalog, schema, company info |
| GET | `/scan/<domain>` | 1252 | Scan received folder, LLM-analyze, persist catalog |
| GET | `/download/<domain>` | 1339 | Trigger link_downloader, update state, redirect |
| GET | `/reextract` | 1380 | Re-fetch Gmail bodies for missing data_link fields |

### Monitor (1 route)
| Method | URL | Line | Description |
|--------|-----|------|-------------|
| GET | `/refresh` | 1395 | Run SAR + SP monitors inline, re-extract links |

### Costs (1 route)
| Method | URL | Line | Description |
|--------|-----|------|-------------|
| GET | `/costs` | 1795 | LLM cost history table and cost calculator |

### Transfers (5 routes)
| Method | URL | Line | Description |
|--------|-----|------|-------------|
| GET | `/transfers` | 1858 | Data transfer map with D3.js graph |
| POST | `/transfers/fetch` | 1934 | Start background subprocessor fetch task |
| POST | `/transfers/request-letter/<domain>` | 1949 | Send single SP disclosure request |
| POST | `/transfers/request-all` | 2006 | Background task: send all disclosure requests |
| GET | `/api/transfers/task` | 2021 | Poll subprocessor task progress |

### Portal (5 routes)
| Method | URL | Line | Description |
|--------|-----|------|-------------|
| POST | `/portal/submit/<domain>` | 2069 | Start background portal submission |
| GET | `/portal/status/<domain>` | 2166 | Poll portal submission progress |
| POST | `/portal/verify/<domain>` | 2211 | Mark portal verification as passed |
| GET | `/captcha/<domain>` | 2230 | Display pending CAPTCHA for user solving |
| POST | `/captcha/<domain>` | 2253 | Submit user CAPTCHA solution |

### Pipeline (9 routes)
| Method | URL | Line | Description |
|--------|-----|------|-------------|
| GET | `/pipeline` | 2702 | Pipeline dashboard — scan/resolve/send progress |
| POST | `/pipeline/add-account` | 2794 | Authenticate new Gmail account + start scan |
| POST | `/pipeline/scan` | 2812 | Start background inbox scan task |
| POST | `/pipeline/resolve` | 2839 | Start background contact resolution task |
| GET | `/pipeline/review` | 2863 | Review resolved companies before sending |
| POST | `/pipeline/manual-contact` | 2916 | Save user-supplied contact for bounced company |
| POST | `/pipeline/approve` | 2959 | Save letter approval/rejection decisions |
| GET | `/pipeline/reauth-send` | 2974 | Re-run OAuth for gmail.send scope |
| POST | `/pipeline/send` | 2994 | Start background letter sending task |

### API & Settings (4 routes)
| Method | URL | Line | Description |
|--------|-----|------|-------------|
| GET | `/api/task/<task_id>` | 3024 | Generic task progress polling |
| GET | `/api/scan/status` | 3032 | Scan-specific progress + inbox stats |
| GET | `/api/body/<domain>/<message_id>` | 2031 | Fetch Gmail message body on demand |
| GET | `/settings/export` | 3059 | Download zip of user's data directory |
| POST | `/settings/delete-account` | 3082 | Delete user account and all data |

---

## Step 2: Dependency Graph

### What `app.py` imports

| Module | What's imported | Used by |
|--------|----------------|---------|
| `flask` | Flask, Response, flash, jsonify, redirect, render_template, request, url_for, g | Everything |
| `flask_login` | LoginManager, current_user, login_required | Auth setup, `_inject_user` |
| `letter_engine.tracker` | `get_log`, `_SUBPROCESSOR_REQUESTS_PATH` | Dashboard, cards, transfers, pipeline, monitor |
| `reply_monitor.classifier` | `_ACTION_DRAFT_TAGS`, `classify`, `generate_reply_draft`, `reextract_data_links` | Monitor, reextract |
| `reply_monitor.state_manager` | `_SUBPROCESSOR_STATE_PATH`, `_COMPANY_STATUS_PRIORITY`, `compute_company_status`, `compute_status`, `days_remaining`, `deadline_from_sent`, `domain_from_sent_record`, `load_state`, `promote_latest_attempt`, `save_state`, `set_portal_status`, `status_sort_key`, `verify_portal`, `_ACTION_TAGS`, `update_state`, `save_portal_submission` | Almost every route |
| `reply_monitor.models` | `ReplyRecord` | Monitor logic |
| `reply_monitor.fetcher` | `fetch_replies_for_sar`, `_extract_body` | Monitor, reextract, api_body |
| `reply_monitor.attachment_handler` | `handle_attachment`, `_catalog_csv`, `_catalog_json`, `_catalog_zip` | Monitor, scan_folder |
| `reply_monitor.link_downloader` | `download_data_link` | download_data, auto_download |
| `reply_monitor.schema_builder` | `build_schema`, `build_schema_from_body` | scan_folder, auto_analyze |
| `dashboard.user_model` | `_safe_email`, `load_user`, `user_data_dir`, `_safe_email_to_address`, `delete_user` | Auth, path helpers, export, delete |
| `dashboard.auth_routes` | `auth_bp` | Blueprint registration |
| `dashboard.admin_routes` | `admin_bp` | Blueprint registration |
| `dashboard.scan_state` | `load_scan_state`, `save_scan_state`, `get_all_accounts`, `_safe_key` | Pipeline routes |
| `dashboard.tasks` | `start_task`, `get_task`, `find_running_task`, `update_task_progress` | Pipeline + transfers background tasks |
| `dashboard.services.graph_data` | `build_graph_data` | Transfers route only |
| `auth.gmail_oauth` | `get_gmail_service`, `get_gmail_send_service`, `check_send_token_valid` | Monitor, pipeline, reextract, api_body |
| `contact_resolver.resolver` | `ContactResolver`, `write_subprocessors` | Portal, pipeline, transfers |
| `contact_resolver.models` | `CompanyRecord`, `Contact`, `Flags`, `SubprocessorRecord` | Pipeline, transfers |
| `contact_resolver.cost_tracker` | `load_persistent_log`, `set_llm_limit` | Costs, pipeline resolve |
| `contact_resolver.subprocessor_fetcher` | `fetch_subprocessors`, `is_stale` | Transfers background task |
| `letter_engine.composer` | `compose`, `compose_subprocessor_request` | Portal, pipeline, transfers |
| `letter_engine.sender` | `send_letter`, `send_thread_reply` | Company followups, pipeline send, transfers |
| `letter_engine.models` | `SARLetter` | Portal submit |
| `portal_submitter` | `submit_portal` | Portal submit background task |
| `scanner.inbox_reader` | `fetch_new_emails`, `get_inbox_total` | Pipeline scan |
| `scanner.service_extractor` | `extract_services` | Pipeline scan |

### What imports from `app.py`

| File | What's imported |
|------|----------------|
| `tests/unit/test_snippet_clean.py` | `_clean_snippet`, `_dedup_reply_rows`, `_is_human_friendly` |
| `tests/unit/test_portal_submit_route.py` | `_lookup_company` (3 locations) |
| `tests/unit/test_api_body.py` | `app` (Flask app object) |
| `tests/unit/test_dashboard.py` | `dashboard.app` as `app_module` |

---

## Step 3: Three-Way Monitor Comparison

Three copies of Gmail reply monitoring logic exist:

### 1. `monitor.py` → `main()` (lines 85–326) — CLI, full-featured

**Capabilities:**
- Full `fetch_replies_for_sar` loop with `promote_latest_attempt`
- Attachment handling with schema enrichment (`build_schema` on attachments)
- Inline data schema building (`build_schema_from_body` for DATA_PROVIDED_INLINE)
- Portal verification via `url_verifier.verify_if_needed()` + auto-submit via `_try_portal_submit()`
- Reply draft generation with `generate_reply_draft`
- Auto-dismiss stale drafts on YOUR_REPLY
- Past-attempts dedup for `existing_ids` (includes archived reply IDs)
- `--reprocess` mode: re-classify existing replies
- `--draft-backfill` mode: generate drafts for existing replies
- Verbose terminal output (summary table, per-reply details)
- `_handle_bounce_retries()`: re-resolve + auto-send for bounced companies
- `_auto_download_data_links()`: download DATA_PROVIDED_LINK replies

### 2. `dashboard/app.py` → `_run_monitor_for_account()` (lines 1474–1590) — Inline SAR

**Capabilities:**
- `fetch_replies_for_sar` loop with `promote_latest_attempt`
- Basic attachment handling (no schema enrichment)
- Reply draft generation
- Auto-dismiss stale drafts on YOUR_REPLY
- Post-monitor: `_auto_download_data_links()` and `_auto_analyze_inline_data()`

**Missing vs. monitor.py:**
- No portal verification or auto-submit
- No schema enrichment on attachments
- No bounce retries
- No reprocess/draft-backfill modes
- **BUG: Missing past-attempts dedup for `existing_ids`** — only deduplicates against `state.replies`, not `state.past_attempts[].replies`. This means archived replies from older attempts can be re-fetched and re-classified.

### 3. `dashboard/app.py` → `_run_subprocessor_monitor_for_account()` (lines 1593–1701) — Inline SP

**Capabilities:**
- `fetch_replies_for_sar` loop with `promote_latest_attempt`
- Reply draft generation
- Auto-dismiss stale drafts on YOUR_REPLY
- Correctly includes `past_attempts` in `existing_ids` dedup

**Missing vs. monitor.py SP section:**
- No attachment handling at all
- No verbose output

### Summary of Differences

| Feature | `monitor.py` | app.py SAR | app.py SP |
|---------|:---:|:---:|:---:|
| Basic reply fetch + classify | Yes | Yes | Yes |
| past_attempts dedup | Yes | **NO (BUG)** | Yes |
| Attachment handling | Yes + schema | Yes (basic) | No |
| Inline data schema | Yes | Yes (post-monitor) | No |
| Portal verification | Yes | No | N/A |
| Portal auto-submit | Yes | No | N/A |
| Bounce retries | Yes | No | N/A |
| Draft generation | Yes | Yes | Yes |
| Auto-dismiss on YOUR_REPLY | Yes | Yes | Yes |
| Reprocess mode | Yes | No | No |
| Draft backfill | Yes | No | No |
| Verbose output | Yes | No | No |

---

## Step 4: Shared Utilities Inventory

### Helper Functions (used by multiple route groups)

| Function | Line | Used by |
|----------|------|---------|
| `_clean_snippet(text)` | 58 | `company_detail()`, tests |
| `_is_human_friendly(text)` | 75 | Tests only (not called in production) |
| `_current_data_dir()` | 145 | Almost every route + all background tasks |
| `_current_state_path()` | 153 | Dashboard, company, data, monitor, portal, transfers |
| `_current_sp_state_path()` | 157 | Dashboard, company, cards, monitor, transfers |
| `_current_tokens_dir()` | 161 | Monitor, pipeline, portal, company followups, reextract |
| `_current_sp_requests_path()` | 165 | Dashboard, cards, transfers |
| `_dedup_reply_rows(sar_rows, sp_rows)` | 365 | `company_detail()`, tests |
| `_get_accounts()` | 378 | Dashboard, company, data, cards, transfers, refresh, reextract, pipeline |
| `_build_card(domain, state, status)` | 404 | Dashboard, cards |
| `_lookup_company(domain)` | 528 | Company detail, data card, portal submit, mark-portal, cards, transfers |
| `_load_all_states(account)` | 2311 | Dashboard, cards, company, portal, transfers, pipeline |
| `_load_companies_db()` | 2303 | Pipeline (sync flags, resolve, send), transfers fetch |
| `_get_all_accounts()` | 2290 | Pipeline routes only |
| `_sync_scan_state_flags(account, state)` | 2337 | Pipeline routes only |
| `_token_exists(account, kind)` | 2446 | Pipeline routes only |
| `_send_token_valid(account)` | 2452 | Pipeline routes only |
| `_reextract_missing_links(account)` | 1417 | Refresh, reextract routes |
| `_run_monitor_for_account(account)` | 1474 | Refresh route |
| `_run_subprocessor_monitor_for_account(...)` | 1593 | Refresh route |
| `_auto_download_data_links(...)` | 1704 | Monitor, refresh (post-monitor) |
| `_auto_analyze_inline_data(...)` | 1735 | Monitor, refresh (post-monitor) |
| `is_stale_dict(sp_dict, ttl_days)` | 2687 | Transfers fetch task |

### Constants (used by multiple route groups)

| Constant | Line | Used by |
|----------|------|---------|
| `_STATUS_COLOUR` | 191 | Context processor → all templates |
| `_TAG_COLOUR` | 211 | Context processor → all templates |
| `_DISPLAY_NAMES` | 237 | Context processor → all templates |
| `_TIER_TERMINAL/ACTION/PROGRESS/NOISE` | 267–279 | `_effective_tags()` |
| `_effective_tags(raw_tags)` | 282 | `_build_card()` |
| `_ACTION_HINTS` | 302 | `_build_card()` |
| `_TERMINAL_STATUSES` | 189 | `_build_card()` |
| `REQUEST_TYPES` | 172 | Context processor (exposed to templates) |
| `_CONFIDENCE_RANK` | 2287 | Pipeline routes only |
| `_COMPANIES_PATH` | 90 | `_lookup_company()`, `_load_companies_db()`, transfers |
| `_portal_tasks` | 2066 | Portal routes only (in-memory state) |

### Context Processor & Template Filter

| Function | Line | Scope |
|----------|------|-------|
| `_inject_globals()` | 315 | All templates — provides `status_colour`, `tag_colour`, `tag_names`, etc. |
| `_flag_emoji_filter()` | 347 | Jinja filter — transfers template only |

### Cross-Group Dependencies Matrix

| Utility | Dashboard | Company | Data | Monitor | Costs | Transfers | Portal | Pipeline | API/Settings |
|---------|:---------:|:-------:|:----:|:-------:|:-----:|:---------:|:------:|:--------:|:------------:|
| `_current_data_dir()` | X | X | X | X | | X | X | X | X |
| `_current_state_path()` | X | X | X | X | | X | X | | |
| `_current_sp_state_path()` | X | X | | X | | X | | | |
| `_current_tokens_dir()` | | X | | X | | X | | X | |
| `_get_accounts()` | X | X | X | X | | X | | X | |
| `_build_card()` | X | | | | | | | | |
| `_lookup_company()` | | X | X | | | X | X | | |
| `_load_all_states()` | X | | | | | X | X | X | |
| `_effective_tags()` | X | | X | | | | | | |
| Constants (colours, tags) | X | X | X | | | X | X | X | |

---

## Step 5: Blueprint Split Proposal

### Proposed Structure

```
dashboard/
    __init__.py            ← app factory (create_app())
    app.py                 ← shrinks to ~50 lines: imports create_app, if __name__
    shared.py              ← shared helpers, constants, context processor
    auth_routes.py         ← (existing, no change)
    admin_routes.py        ← (existing, no change)
    blueprints/
        __init__.py
        dashboard_bp.py    ← GET /, GET /cards
        company_bp.py      ← GET /company/<domain>, all followup/compose routes
        data_bp.py         ← GET /data/<domain>, /scan/<domain>, /download/<domain>
        monitor_bp.py      ← GET /refresh, GET /reextract
        costs_bp.py        ← GET /costs
        transfers_bp.py    ← GET /transfers, /transfers/fetch, /request-letter, /request-all, /api/transfers/task
        portal_bp.py       ← POST /portal/submit, /portal/status, /portal/verify, /captcha/*
        pipeline_bp.py     ← GET /pipeline, all /pipeline/* routes
        api_bp.py          ← GET /api/task, /api/scan/status, /api/body
        settings_bp.py     ← GET /settings/export, POST /settings/delete-account
    services/
        monitor_runner.py  ← unified monitor logic (replaces 3 copies)
        graph_data.py      ← (existing, no change)
        jurisdiction.py    ← (existing, no change)
```

### Blueprint Details

#### 1. `shared.py` — Shared Utilities Module (NOT a Blueprint)

**Moves here:**
- Path helpers: `_current_data_dir()`, `_current_state_path()`, `_current_sp_state_path()`, `_current_tokens_dir()`, `_current_sp_requests_path()`
- Account helpers: `_get_accounts()`, `_get_all_accounts()`
- Data helpers: `_build_card()`, `_lookup_company()`, `_load_all_states()`, `_load_companies_db()`, `_dedup_reply_rows()`
- Display helpers: `_clean_snippet()`, `_is_human_friendly()`, `_effective_tags()`
- All constants: `_STATUS_COLOUR`, `_TAG_COLOUR`, `_DISPLAY_NAMES`, `_TERMINAL_STATUSES`, `_ACTION_HINTS`, `REQUEST_TYPES`, tier frozensets, `_COMPANIES_PATH`
- Context processor: `_inject_globals()`
- Template filter: `_flag_emoji_filter()`

**Why a module, not a Blueprint:** These are pure functions and constants with no routes. Blueprints should only contain routes. Shared logic belongs in a plain module that all Blueprints import from.

**Import changes:** All Blueprints will `from dashboard.shared import ...` instead of accessing `app.py` globals.

**Test impact:** `test_snippet_clean.py` changes from `from dashboard.app import _clean_snippet` → `from dashboard.shared import _clean_snippet`. Same for `test_portal_submit_route.py` (`_lookup_company`).

---

#### 2. `dashboard_bp.py` — Dashboard Blueprint

**URL prefix:** `/` (no prefix)

**Routes:**
| Route | Function | Lines |
|-------|----------|-------|
| GET `/` | `dashboard()` | 563–600 |
| GET `/cards` | `cards_listing()` | 1145–1249 |

**Dependencies:**
- `shared.py`: `_get_accounts()`, `_build_card()`, `_load_all_states()`, `_lookup_company()`, `_effective_tags()`, `_STATUS_COLOUR`, `_COMPANY_STATUS_PRIORITY`
- `reply_monitor.state_manager`: `compute_status`, `compute_company_status`, `load_state`
- `letter_engine.tracker`: `get_log`
- `dashboard.scan_state`: `load_scan_state`

**Breakage risks:** Low. Pure read-only routes with no state mutation. `_build_card()` is the most complex dependency but it moves to `shared.py` intact.

---

#### 3. `company_bp.py` — Company Detail Blueprint

**URL prefix:** `/company`

**Routes:**
| Route | Function | Lines |
|-------|----------|-------|
| GET `/company/<domain>` | `company_detail()` | 603–827 |
| POST `/company/<domain>/send-followup` | `send_followup()` | 830–864 |
| POST `/company/<domain>/dismiss-followup` | `dismiss_followup()` | 867–880 |
| POST `/company/<domain>/send-sp-followup` | `send_sp_followup()` | 883–918 |
| POST `/company/<domain>/dismiss-sp-followup` | `dismiss_sp_followup()` | 921–934 |
| POST `/company/<domain>/compose-reply` | `compose_reply()` | 937–1039 |
| POST `/company/<domain>/compose-sp-reply` | `compose_sp_reply()` | 990–1039 |
| POST `/company/<domain>/mark-portal-submitted` | `mark_portal_submitted()` | 2190–2210 |

**Note:** `mark-portal-submitted` is under `/company/` URL even though it's portal-related. Keep it here because the URL structure and template context match the company detail page.

**Dependencies:**
- `shared.py`: `_get_accounts()`, `_current_state_path()`, `_current_sp_state_path()`, `_current_tokens_dir()`, `_current_sp_requests_path()`, `_lookup_company()`, `_dedup_reply_rows()`, `_clean_snippet()`, `_STATUS_COLOUR`, `_TAG_COLOUR`
- `reply_monitor.state_manager`: `load_state`, `save_state`, `compute_status`, `compute_company_status`, `days_remaining`, `save_portal_submission`
- `letter_engine.sender`: `send_thread_reply`
- `letter_engine.tracker`: `get_log`
- `reply_monitor.models`: `ReplyRecord`

**Breakage risks:** Medium. `company_detail()` is ~225 lines and builds complex template context. The `url_for("company_detail", ...)` references appear in many other routes (portal, data, followups) and will need updating to `url_for("company.company_detail", ...)`.

---

#### 4. `data_bp.py` — Data Card Blueprint

**URL prefix:** `/` (mixed URLs: `/data/`, `/scan/`, `/download/`)

**Routes:**
| Route | Function | Lines |
|-------|----------|-------|
| GET `/data/<domain>` | `data_card()` | 1042–1142 |
| GET `/scan/<domain>` | `scan_folder()` | 1252–1336 |
| GET `/download/<domain>` | `download_data()` | 1339–1377 |

**Dependencies:**
- `shared.py`: `_get_accounts()`, `_load_all_states()`, `_lookup_company()`, `_current_state_path()`
- `reply_monitor.attachment_handler`: `_catalog_csv`, `_catalog_json`, `_catalog_zip`
- `reply_monitor.link_downloader`: `download_data_link`
- `reply_monitor.schema_builder`: `build_schema`
- `reply_monitor.models`: `AttachmentCatalog`, `FileEntry`

**Breakage risks:** Low. Self-contained routes with clear data flow.

---

#### 5. `monitor_bp.py` — Monitor Blueprint

**URL prefix:** `/`

**Routes:**
| Route | Function | Lines |
|-------|----------|-------|
| GET `/refresh` | `refresh()` | 1395–1414 |
| GET `/reextract` | `reextract()` | 1380–1392 |

**Moves here (or to services/monitor_runner.py):**
- `_run_monitor_for_account()` (1474–1590)
- `_run_subprocessor_monitor_for_account()` (1593–1701)
- `_reextract_missing_links()` (1417–1471)
- `_auto_download_data_links()` (1704–1793)
- `_auto_analyze_inline_data()` (1735–1793)

**Dependencies:**
- `shared.py`: `_get_accounts()`, `_current_state_path()`, `_current_sp_state_path()`, `_current_tokens_dir()`, `_current_data_dir()`, `_current_sp_requests_path()`
- `auth.gmail_oauth`: `get_gmail_service`
- `reply_monitor.*`: classifier, fetcher, attachment_handler, link_downloader, schema_builder, state_manager
- `letter_engine.tracker`: `get_log`

**Breakage risks:** Medium. The monitor functions are the most complex internal logic. Moving them requires careful handling of the `_auto_download_data_links` and `_auto_analyze_inline_data` helpers which are called both from the monitor and from the reextract route.

---

#### 6. `costs_bp.py` — Costs Blueprint

**URL prefix:** `/costs`

**Routes:**
| Route | Function | Lines |
|-------|----------|-------|
| GET `/costs` | `costs()` | 1795–1855 |

**Dependencies:**
- `contact_resolver.cost_tracker`: `load_persistent_log`

**Breakage risks:** Very low. Completely self-contained, no shared state, no mutations.

---

#### 7. `transfers_bp.py` — Transfers Blueprint

**URL prefix:** `/transfers`

**Routes:**
| Route | Function | Lines |
|-------|----------|-------|
| GET `/transfers` | `transfers()` | 1858–1931 |
| POST `/transfers/fetch` | `transfers_fetch()` | 1934–1946 |
| POST `/transfers/request-letter/<domain>` | `transfers_request_letter()` | 1949–2003 |
| POST `/transfers/request-all` | `transfers_request_all()` | 2006–2018 |
| GET `/api/transfers/task` | `transfers_task_status()` | 2021–2028 |

**Also moves here:**
- `_fetch_all_subprocessors()` (2570–2612) — background task
- `_send_all_disclosure_requests()` (2615–2684) — background task
- `is_stale_dict()` (2687–2695)

**Dependencies:**
- `shared.py`: `_get_accounts()`, `_load_all_states()`, `_current_sp_state_path()`, `_current_sp_requests_path()`, `_current_tokens_dir()`, `_current_data_dir()`, `_COMPANIES_PATH`
- `dashboard.services.graph_data`: `build_graph_data`
- `dashboard.tasks`: `start_task`, `get_task`, `find_running_task`, `update_task_progress`
- `reply_monitor.state_manager`: `compute_status`, `load_state`
- `letter_engine.tracker`: `get_log`, `record_subprocessor_request`
- `contact_resolver.*`: `CompanyRecord`, `ContactResolver`, `fetch_subprocessors`, `write_subprocessors`
- `letter_engine.*`: `compose_subprocessor_request`, `send_letter`

**Breakage risks:** Medium. Background task functions reference `_current_data_dir()` and `_current_tokens_dir()` which rely on Flask's `g` object. Background threads don't have a request context — need to capture paths before spawning threads (already done in current code via closure, but verify).

---

#### 8. `portal_bp.py` — Portal Blueprint

**URL prefix:** `/portal`

**Routes:**
| Route | Function | Lines |
|-------|----------|-------|
| POST `/portal/submit/<domain>` | `portal_submit()` | 2069–2163 |
| GET `/portal/status/<domain>` | `portal_status()` | 2166–2187 |
| POST `/portal/verify/<domain>` | `portal_verify()` | 2211–2227 |
| GET `/captcha/<domain>` | `captcha_show()` | 2230–2250 |
| POST `/captcha/<domain>` | `captcha_solve()` | 2253–2273 |

**Also moves here:**
- `_portal_tasks` dict (in-memory state, line 2066)

**Dependencies:**
- `shared.py`: `_lookup_company()`, `_load_all_states()`, `_current_state_path()`, `_current_data_dir()`
- `contact_resolver.resolver`: `ContactResolver`
- `letter_engine.composer`: `compose`
- `letter_engine.models`: `SARLetter`
- `portal_submitter`: `submit_portal`
- `reply_monitor.state_manager`: `save_portal_submission`, `set_portal_status`, `verify_portal`, `save_state`, `load_state`

**Breakage risks:** Medium. The `_portal_tasks` in-memory dict is module-level state. It must be importable from a single location — if multiple modules import it, they must all reference the same dict object. The background thread in `portal_submit()` also calls `_current_data_dir()` inside a thread with no request context — must capture the path before spawning.

---

#### 9. `pipeline_bp.py` — Pipeline Blueprint

**URL prefix:** `/pipeline`

**Routes:**
| Route | Function | Lines |
|-------|----------|-------|
| GET `/pipeline` | `pipeline()` | 2702–2791 |
| POST `/pipeline/add-account` | `pipeline_add_account()` | 2794–2809 |
| POST `/pipeline/scan` | `pipeline_scan()` | 2812–2836 |
| POST `/pipeline/resolve` | `pipeline_resolve()` | 2839–2860 |
| GET `/pipeline/review` | `pipeline_review()` | 2863–2913 |
| POST `/pipeline/manual-contact` | `pipeline_manual_contact()` | 2916–2956 |
| POST `/pipeline/approve` | `pipeline_approve()` | 2959–2971 |
| GET `/pipeline/reauth-send` | `pipeline_reauth_send()` | 2974–2991 |
| POST `/pipeline/send` | `pipeline_send()` | 2994–3017 |

**Also moves here:**
- `_sync_scan_state_flags()` (2337–2443) — 107 lines, pipeline-only
- `_token_exists()` (2446–2449)
- `_send_token_valid()` (2452–2455)
- `_CONFIDENCE_RANK` (2287)
- Background tasks: `_do_scan()` (2462–2508), `_do_resolve()` (2511–2535), `_do_send()` (2538–2567)

**Dependencies:**
- `shared.py`: `_get_accounts()`, `_get_all_accounts()`, `_load_all_states()`, `_load_companies_db()`, `_current_data_dir()`, `_current_state_path()`, `_current_tokens_dir()`
- `dashboard.scan_state`: `load_scan_state`, `save_scan_state`, `get_all_accounts`, `_safe_key`
- `dashboard.tasks`: `start_task`, `get_task`, `find_running_task`, `update_task_progress`
- `auth.gmail_oauth`: `get_gmail_service`, `get_gmail_send_service`, `check_send_token_valid`
- `scanner.*`: `fetch_new_emails`, `get_inbox_total`, `extract_services`
- `contact_resolver.*`: `ContactResolver`, `CompanyRecord`, `cost_tracker`
- `letter_engine.*`: `compose`, `send_letter`

**Breakage risks:** High. Pipeline is the most complex Blueprint with the most background tasks. `_sync_scan_state_flags()` alone is 107 lines. Background tasks reference per-user path helpers that depend on Flask's `g` — must capture paths synchronously before spawning threads.

---

#### 10. `api_bp.py` — API Blueprint

**URL prefix:** `/api`

**Routes:**
| Route | Function | Lines |
|-------|----------|-------|
| GET `/api/task/<task_id>` | `api_task()` | 3024–3029 |
| GET `/api/scan/status` | `api_scan_status()` | 3032–3048 |
| GET `/api/body/<domain>/<message_id>` | `api_body()` | 2031–2058 |

**Dependencies:**
- `shared.py`: `_get_accounts()`, `_current_tokens_dir()`, `_current_data_dir()`
- `dashboard.tasks`: `get_task`, `find_running_task`
- `dashboard.scan_state`: `load_scan_state`
- `auth.gmail_oauth`: `get_gmail_service`
- `reply_monitor.fetcher`: `_extract_body`

**Breakage risks:** Low. Stateless JSON endpoints.

---

#### 11. `settings_bp.py` — Settings Blueprint

**URL prefix:** `/settings`

**Routes:**
| Route | Function | Lines |
|-------|----------|-------|
| GET `/settings/export` | `export_data()` | 3059–3079 |
| POST `/settings/delete-account` | `delete_account()` | 3082–3099 |

**Dependencies:**
- `shared.py`: `_current_data_dir()`
- `dashboard.user_model`: `_safe_email`, `delete_user`
- `flask_login`: `logout_user`

**Breakage risks:** Very low. Self-contained, minimal dependencies.

---

### `url_for()` Impact

Every `url_for("function_name")` call must become `url_for("blueprint.function_name")`. Affected locations:

| Current `url_for` target | New target | Occurrences |
|--------------------------|-----------|-------------|
| `url_for("dashboard")` | `url_for("main.dashboard")` | ~10 (portal, refresh, company, etc.) |
| `url_for("company_detail", ...)` | `url_for("company.company_detail", ...)` | ~15 (followups, portal, data) |
| `url_for("data_card", ...)` | `url_for("data.data_card", ...)` | ~3 (scan, download) |
| `url_for("transfers", ...)` | `url_for("transfers.transfers", ...)` | ~5 (fetch, request) |
| `url_for("pipeline", ...)` | `url_for("pipeline.pipeline", ...)` | ~8 (scan, resolve, send) |
| `url_for("pipeline_review", ...)` | `url_for("pipeline.pipeline_review", ...)` | ~3 |
| `url_for("captcha_show", ...)` | `url_for("portal.captcha_show", ...)` | ~1 |

**Templates are also affected.** Every `url_for()` call in Jinja templates must be updated. Grep templates for `url_for(` to build the full list before implementation.

---

## Step 6: Services Layer Proposal

### `dashboard/services/monitor_runner.py` — Unified Monitor

**Purpose:** Replace the three copies of monitor logic with one shared implementation.

**Functions:**
```python
def run_sar_monitor(
    account: str,
    *,
    state_path: Path,
    tokens_dir: Path,
    data_dir: Path,
    sp_requests_path: Path,
    verbose: bool = False,
    reprocess: bool = False,
    draft_backfill: bool = False,
) -> tuple[Any, str]:
    """Run SAR reply monitor for one account. Returns (gmail_service, email)."""

def run_sp_monitor(
    account: str,
    *,
    state_path: Path,
    tokens_dir: Path,
    data_dir: Path,
    sp_requests_path: Path,
    sp_state_path: Path,
    service=None,
    email: str = "",
    verbose: bool = False,
) -> None:
    """Run subprocessor reply monitor for one account."""

def auto_download_data_links(account: str, states: dict, api_key: str | None) -> None:
    """Download DATA_PROVIDED_LINK replies that have a URL but no catalog."""

def auto_analyze_inline_data(account: str, states: dict, api_key: str | None) -> None:
    """Analyze DATA_PROVIDED_INLINE replies without schema."""
```

**Key design decisions:**
- All paths passed explicitly (no `g` dependency) — safe to call from CLI, web, and background threads
- `verbose` flag controls terminal output (True for CLI, False for web)
- Includes past-attempts dedup fix (currently missing from app.py SAR monitor)
- `monitor.py` CLI becomes a thin wrapper: parses args, calls these functions
- `dashboard/blueprints/monitor_bp.py` calls these functions with paths from `shared.py`

**Migration path:**
1. Create `monitor_runner.py` with unified logic from `monitor.py` (the most complete version)
2. Update `monitor.py` to import and call `monitor_runner.py`
3. Update `monitor_bp.py` to import and call `monitor_runner.py`
4. Delete duplicate logic from both

### `dashboard/services/graph_data.py` — No Change

Already properly separated. Used only by `transfers()`.

### `dashboard/services/jurisdiction.py` — No Change

Already properly separated. Used only by `graph_data.py`.

### Why NOT a `services/llm_client.py`

LLM calls are already properly encapsulated:
- `contact_resolver/llm_searcher.py` — resolver LLM calls
- `reply_monitor/classifier.py` — classification LLM calls
- `reply_monitor/schema_builder.py` — schema analysis LLM calls
- `portal_submitter/form_analyzer.py` — form analysis LLM calls
- `contact_resolver/cost_tracker.py` — cost tracking

There's no shared LLM logic in `app.py` that needs extraction. Adding a `services/llm_client.py` would add a layer of indirection with no benefit.

---

## Step 7: Execution Order

Ordered from lowest risk (fewest dependencies, least complex) to highest risk.

### Phase 0: Preparation (prerequisite for all phases)

**0.1: Create `dashboard/shared.py`**
- Move all shared helpers, constants, and context processor
- Update `app.py` to import from `shared.py`
- Update test imports
- **No Blueprint creation yet** — this is a pure extraction that keeps `app.py` working

**0.2: Create app factory `dashboard/__init__.py`**
- Add `create_app()` function
- Move Flask app creation, login manager, before_request, secret key setup
- `app.py` shrinks to: `from dashboard import create_app; app = create_app(); if __name__: app.run()`
- **Critical:** All existing `from dashboard.app import app` references must still work

**Risk:** LOW — pure mechanical moves, no logic changes.

### Phase 1: Leaf Blueprints (no other Blueprint depends on them)

**1.1: `costs_bp.py`** (1 route, ~60 lines)
- Zero shared state, zero mutations, zero cross-references
- Only imports `cost_tracker`
- No `url_for()` references from other routes
- **Risk:** VERY LOW

**1.2: `settings_bp.py`** (2 routes, ~40 lines)
- Minimal dependencies, self-contained
- No `url_for()` references from other routes
- **Risk:** VERY LOW

**1.3: `api_bp.py`** (3 routes, ~55 lines)
- Stateless JSON endpoints
- `url_for("api_task")` referenced from JS only — update once
- **Risk:** LOW

### Phase 2: Data and Monitor Blueprints

**2.1: `data_bp.py`** (3 routes, ~300 lines)
- References to `url_for("data_card")` from company detail and download routes — all within this Blueprint
- Cross-refs: `company_detail.html` has "View data" links → update template
- **Risk:** LOW-MEDIUM

**2.2: Create `services/monitor_runner.py`** (unified monitor logic, ~350 lines)
- Write the unified implementation
- Update `monitor.py` CLI to use it
- **Test thoroughly** — this is logic-critical
- **Risk:** MEDIUM (most important to get right)

**2.3: `monitor_bp.py`** (2 routes + monitor functions, ~350 lines)
- `refresh` and `reextract` routes
- Uses `services/monitor_runner.py` instead of inline functions
- **Risk:** MEDIUM

### Phase 3: Core Blueprints

**3.1: `portal_bp.py`** (5 routes + in-memory state, ~210 lines)
- `_portal_tasks` dict must live in exactly one module
- Background thread path capture must be verified
- `url_for("captcha_show")` from captcha_solve — within Blueprint
- `mark-portal-submitted` stays in company_bp but redirects here
- **Risk:** MEDIUM

**3.2: `transfers_bp.py`** (5 routes + 2 background tasks, ~350 lines)
- Background tasks that reference per-user paths
- `url_for("transfers")` referenced from transfer action routes — all within Blueprint
- **Risk:** MEDIUM

**3.3: `company_bp.py`** (8 routes, ~440 lines)
- `company_detail()` is the most complex route (225 lines)
- `url_for("company_detail", ...)` referenced from ~15 locations across other Blueprints and templates
- **Risk:** MEDIUM-HIGH (highest `url_for` breakage surface)

### Phase 4: Dashboard and Pipeline

**4.1: `dashboard_bp.py`** (2 routes, ~200 lines)
- `url_for("dashboard")` referenced from many routes (portal, refresh, captcha, auth)
- `_build_card()` already in shared.py at this point
- **Risk:** MEDIUM (many `url_for` references but simple logic)

**4.2: `pipeline_bp.py`** (9 routes + 3 background tasks + sync_flags, ~750 lines)
- Largest Blueprint by far
- Most background tasks, most complex state management
- `_sync_scan_state_flags()` is 107 lines of bounce detection logic
- **Extract last** because it has the most internal dependencies and is least likely to break other Blueprints (pipeline URLs are only referenced from pipeline templates)
- **Risk:** HIGH (complexity, background tasks, thread safety)

### Phase 5: Cleanup

**5.1: Update all templates**
- Grep all `.html` files for `url_for(` and update Blueprint prefixes
- Update `base.html` navbar links

**5.2: Update all test imports**
- `test_snippet_clean.py` → `from dashboard.shared import ...`
- `test_portal_submit_route.py` → `from dashboard.shared import _lookup_company`
- `test_api_body.py` → `from dashboard import create_app`
- `test_dashboard.py` → update module import

**5.3: Verify `app.py` is minimal**
- Should be ~20 lines: create_app + `if __name__`

---

## Step 8: Risk Register

### Critical Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| `url_for()` breakage in templates | Broken links throughout UI | Grep all templates before each Blueprint extraction; run full test suite + manual click-through |
| Background thread path capture | Thread crash (no request context) | Capture all paths from `g`/`request` before `Thread.start()`, pass as explicit args |
| `_portal_tasks` dict split across modules | Portal status polling breaks | Keep dict in `portal_bp.py`, verify single import point |
| Monitor logic regression | Missed replies, duplicate processing | Extract unified service first (Phase 2.2), write tests for edge cases (past_attempts dedup) |
| App factory breaks existing imports | `from dashboard.app import app` fails | Keep `app.py` as thin wrapper that re-exports `app` |
| `_load_all_states()` thread safety | Corrupted state on concurrent requests | Already not thread-safe; document as known tech debt, not introduced by refactor |

### Bugs to Fix During Refactor

| Bug | Location | Fix |
|-----|----------|-----|
| Missing `past_attempts` dedup in SAR monitor | `_run_monitor_for_account()` line 1520 | Fix in unified `monitor_runner.py` — add `for pa in state.past_attempts: for r in pa.get("replies", []): existing_ids.add(r["gmail_message_id"])` |

### Test Strategy

- Run `pytest tests/unit/ -q` after each Blueprint extraction
- Manual smoke test: load dashboard, click through each page
- Check all `url_for()` routes by visiting every page
- Verify background tasks still run (portal submit, subprocessor fetch, pipeline scan/resolve/send)

---

## Size Estimates

| Module | Approx. Lines | From `app.py` Lines |
|--------|:---:|---|
| `shared.py` | ~350 | 54–84, 145–167, 172–358, 365–556, 2287–2288, 2303–2334 |
| `dashboard_bp.py` | ~200 | 563–600, 1145–1249 |
| `company_bp.py` | ~440 | 603–827, 830–1039, 2190–2210 |
| `data_bp.py` | ~300 | 1042–1377 |
| `monitor_bp.py` | ~50 | 1380–1414 (logic → services/) |
| `services/monitor_runner.py` | ~350 | 1417–1793, unified with monitor.py |
| `costs_bp.py` | ~65 | 1795–1855 |
| `transfers_bp.py` | ~350 | 1858–2028, 2570–2695 |
| `portal_bp.py` | ~210 | 2060–2273 |
| `pipeline_bp.py` | ~750 | 2280–3017, 2337–2567 |
| `api_bp.py` | ~55 | 2031–2058, 3024–3048 |
| `settings_bp.py` | ~45 | 3055–3099 |
| `__init__.py` (app factory) | ~60 | 86–142, 315–358 |
| `app.py` (final) | ~20 | Entry point only |
| **Total** | **~3,245** | 3,099 original + structure overhead |
