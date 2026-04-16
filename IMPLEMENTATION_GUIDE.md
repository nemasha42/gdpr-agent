# GDPR-Agent Refactoring: Granular Implementation Guide

**Based on:** `REFACTOR_PLAN.md` (2026-04-16)
**Purpose:** Step-by-step walkthrough of every change, with plain-language explanations and technical context.

---

## How This Guide Works

Each step has three parts:

- **What you're doing** — plain English, no jargon
- **Why it matters** — what breaks if you skip this, or what improves after you do it
- **Technical detail** — exact files, functions, and architecture references from the refactoring plan

The steps follow the plan's phased execution order: preparation first, then easiest extractions, then progressively harder ones.

---

## PHASE 0: PREPARATION

*Goal: Set up the foundation without changing any behavior. After Phase 0, your app works exactly like before — but the plumbing is ready for the split.*

---

### Step 0.1 — Create `dashboard/shared.py` (the shared toolbox)

**What you're doing:**
You're creating a new file that will hold all the helper functions and constants that multiple parts of your app need. Think of it as a shared toolbox — instead of every Blueprint rummaging through the giant `app.py` to find the wrench it needs, they'll all go to `shared.py`.

**Why it matters:**
Right now, functions like `_lookup_company()` and `_current_data_dir()` live inside `app.py`. When you later split `app.py` into Blueprints, multiple Blueprints will need these functions. If they're still in `app.py`, you'd have circular imports (file A imports from file B which imports from file A — Python hates this). Moving them to neutral ground prevents that.

**Technical detail:**

The refactoring plan identifies these categories to move (from Step 4 and Step 5):

**Path helpers** (these figure out where the current user's data lives on disk):
- `_current_data_dir()` (line 145) — used by almost every route
- `_current_state_path()` (line 153) — used by 7 route groups
- `_current_sp_state_path()` (line 157) — used by 4 route groups
- `_current_tokens_dir()` (line 161) — used by 5 route groups
- `_current_sp_requests_path()` (line 165) — used by 3 route groups

**Account helpers** (these discover which Gmail accounts are connected):
- `_get_accounts()` (line 378) — used by 8 route groups
- `_get_all_accounts()` (line 2290) — used by pipeline routes only but also useful elsewhere

**Data helpers** (these load and transform application data):
- `_build_card()` (line 404) — builds the card UI elements for the dashboard
- `_lookup_company()` (line 528) — finds a company's contact info by domain name
- `_load_all_states()` (line 2311) — loads SAR/SP tracking state for a Gmail account
- `_load_companies_db()` (line 2303) — loads the master companies database
- `_dedup_reply_rows()` (line 365) — removes duplicate email replies from the display

**Display helpers** (formatting for the UI):
- `_clean_snippet()` (line 58) — trims and cleans email preview text
- `_is_human_friendly()` (line 75) — checks if text is readable (currently test-only)
- `_effective_tags()` (line 282) — picks the most relevant status tags to show

**All constants** — color maps, display names, status categories:
- `_STATUS_COLOUR` (line 191), `_TAG_COLOUR` (line 211), `_DISPLAY_NAMES` (line 237)
- `_TERMINAL_STATUSES` (line 189), `_ACTION_HINTS` (line 302)
- `REQUEST_TYPES` (line 172)
- Tier frozensets: `_TIER_TERMINAL`, `_TIER_ACTION`, `_TIER_PROGRESS`, `_TIER_NOISE` (lines 267–279)
- `_COMPANIES_PATH` (line 90)

**Context processor and template filter:**
- `_inject_globals()` (line 315) — a Flask hook that makes color maps and display names available to every HTML template
- `_flag_emoji_filter()` (line 347) — a Jinja template filter for country flag emojis

The plan states: *"These are pure functions and constants with no routes. Blueprints should only contain routes. Shared logic belongs in a plain module that all Blueprints import from."*

**What to do, concretely:**
1. Create the file `dashboard/shared.py`
2. Copy (don't move yet) each function and constant listed above into it
3. Add the necessary imports at the top of `shared.py` (Flask's `g`, `request`, `current_user`, and the external modules these functions call)
4. In `app.py`, replace each moved function's body with an import from `shared.py` — e.g., `from dashboard.shared import _lookup_company`
5. Run tests: `pytest tests/unit/ -q`
6. Verify the app still works: start it and click through every page

**Size estimate from the plan:** ~350 lines.

---

### Step 0.2 — Create the App Factory in `dashboard/__init__.py`

**What you're doing:**
Right now, when Python loads `dashboard/app.py`, it immediately creates the Flask application as a side effect of importing the file. You're changing this so the app is created inside a function called `create_app()`. This function lives in `dashboard/__init__.py`.

**Why it matters:**
The app factory pattern is how Flask expects you to organize larger applications. It solves two problems:
1. **Testing** — you can create a fresh app for each test with different settings
2. **Circular imports** — when Blueprints need to register themselves with the app, but the app also needs to know about Blueprints, having a function that builds everything in the right order avoids the chicken-and-egg problem

**What is `__init__.py`?**
In Python, `__init__.py` is a special file that turns a folder into a "package" (an importable module). When someone writes `from dashboard import create_app`, Python looks inside `dashboard/__init__.py` for `create_app`. Your `dashboard/` folder may already have an `__init__.py` — you're adding the factory function to it.

**Technical detail:**

The plan says to move these things from `app.py` into `create_app()`:
- Flask app creation (`app = Flask(__name__)`)
- Secret key configuration
- Login manager setup (Flask-Login, which handles user sessions)
- The `before_request` hook (code that runs before every request, like loading the current user)
- Blueprint registration (connecting `auth_bp`, `admin_bp`, and later all the new Blueprints)

After this step, `app.py` shrinks to roughly:

```python
from dashboard import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
```

The plan emphasizes: *"Critical: All existing `from dashboard.app import app` references must still work"* — meaning `app.py` still exports the `app` object, it just gets it from the factory now.

**What to do, concretely:**
1. Open `dashboard/__init__.py`
2. Write a `create_app()` function that does everything `app.py` currently does at module level (create Flask, configure it, register Blueprints)
3. In `app.py`, replace all that setup code with `from dashboard import create_app; app = create_app()`
4. Make sure `app` is still importable from `dashboard.app` (the test files depend on this)
5. Run tests, smoke-test the app

**Size estimate from the plan:** ~60 lines for the factory.

**Risk level from the plan:** LOW — *"pure mechanical moves, no logic changes."*

---

## PHASE 1: LEAF BLUEPRINTS

*Goal: Extract the simplest, most isolated pieces first. These have minimal dependencies and nothing else depends on them. This builds confidence and establishes the pattern you'll repeat.*

---

### Step 1.1 — Extract `costs_bp.py` (the costs page)

**What you're doing:**
Taking the single `/costs` route out of `app.py` and putting it in its own file. This page shows LLM (large language model) spending history — how much the AI calls have cost.

**Why it matters:**
This is the simplest possible Blueprint extraction. It has one route, zero shared state, zero mutations (it only reads data, never writes), and no other route links to it. It's a confidence builder — if you can do this, you can do any of them.

**Technical detail:**

The plan says: *"Zero shared state, zero mutations, zero cross-references. Only imports `cost_tracker`. No `url_for()` references from other routes."*

From the Route Map (Step 1):
| Method | URL | Line | Description |
|--------|-----|------|-------------|
| GET | `/costs` | 1795 | LLM cost history table and cost calculator |

This route only depends on:
- `contact_resolver.cost_tracker`: `load_persistent_log` — reads the cost log file

**What to do, concretely:**
1. Create `dashboard/blueprints/` directory and an empty `__init__.py` inside it
2. Create `dashboard/blueprints/costs_bp.py`
3. At the top, create the Blueprint: `costs_bp = Blueprint("costs", __name__)`
4. Move the `costs()` function (lines 1795–1855 of `app.py`) into this file
5. Change the route decorator from `@app.route("/costs")` to `@costs_bp.route("/costs")`
6. Add the import for `cost_tracker`
7. In `dashboard/__init__.py`'s `create_app()`, register this Blueprint: `app.register_blueprint(costs_bp)`
8. Delete the `costs()` function from `app.py`
9. Check if any template has `url_for("costs")` — if so, update to `url_for("costs.costs")`
10. Run tests

**Size estimate from the plan:** ~65 lines.

**Risk level from the plan:** VERY LOW.

**What is a Blueprint, mechanically?**
A Blueprint is a Python object you create with `Blueprint("name", __name__)`. You attach routes to it with `@blueprint.route(...)` instead of `@app.route(...)`. Then you register it with the app using `app.register_blueprint(blueprint)`. Flask connects the routes to the main app at registration time.

---

### Step 1.2 — Extract `settings_bp.py` (export and delete account)

**What you're doing:**
Moving the two settings routes — data export (download a zip of your data) and account deletion — into their own file.

**Why it matters:**
Like costs, this is self-contained. These routes don't affect or depend on anything else in the app. Extracting it continues to shrink `app.py` with no risk.

**Technical detail:**

From the Route Map:
| Method | URL | Line | Description |
|--------|-----|------|-------------|
| GET | `/settings/export` | 3059 | Download zip of user's data directory |
| POST | `/settings/delete-account` | 3082 | Delete user account and all data |

Dependencies:
- `shared.py`: `_current_data_dir()`
- `dashboard.user_model`: `_safe_email`, `delete_user`
- `flask_login`: `logout_user`

The plan notes: *"Minimal dependencies, self-contained. No `url_for()` references from other routes."*

**What to do, concretely:**
1. Create `dashboard/blueprints/settings_bp.py`
2. Define the Blueprint with URL prefix `/settings`: `settings_bp = Blueprint("settings", __name__, url_prefix="/settings")`
3. Move `export_data()` (lines 3059–3079) and `delete_account()` (lines 3082–3099)
4. Update route decorators (e.g., `@settings_bp.route("/export")` — the `/settings` prefix is automatic)
5. Add imports from `shared.py` and `user_model`
6. Register in `create_app()`
7. Delete from `app.py`
8. Run tests

**What is a URL prefix?**
When you create a Blueprint with `url_prefix="/settings"`, every route inside it automatically gets `/settings` prepended. So a route defined as `/export` becomes `/settings/export` in the actual app. This keeps your Blueprint code cleaner and makes the URL structure obvious.

**Size estimate from the plan:** ~45 lines.

**Risk level from the plan:** VERY LOW.

---

### Step 1.3 — Extract `api_bp.py` (JSON API endpoints)

**What you're doing:**
Moving three API endpoints into their own file. These are routes that return JSON data (not HTML pages) — they're called by JavaScript in the browser to poll for progress updates or fetch email content.

**Why it matters:**
API endpoints are stateless (they don't remember anything between calls) and return data, not pages. They're clean to extract. One small complication: JavaScript code in your templates calls `url_for("api_task")`, and that will need updating.

**Technical detail:**

From the Route Map:
| Method | URL | Line | Description |
|--------|-----|------|-------------|
| GET | `/api/task/<task_id>` | 3024 | Generic task progress polling |
| GET | `/api/scan/status` | 3032 | Scan-specific progress + inbox stats |
| GET | `/api/body/<domain>/<message_id>` | 2031 | Fetch Gmail message body on demand |

Dependencies:
- `shared.py`: `_get_accounts()`, `_current_tokens_dir()`, `_current_data_dir()`
- `dashboard.tasks`: `get_task`, `find_running_task`
- `dashboard.scan_state`: `load_scan_state`
- `auth.gmail_oauth`: `get_gmail_service`
- `reply_monitor.fetcher`: `_extract_body`

The plan notes: *"Stateless JSON endpoints. `url_for('api_task')` referenced from JS only — update once."*

**What to do, concretely:**
1. Create `dashboard/blueprints/api_bp.py`
2. Define: `api_bp = Blueprint("api", __name__, url_prefix="/api")`
3. Move the three route functions
4. Update route decorators
5. **Search all `.html` template files** for `url_for("api_task")` and update to `url_for("api.api_task")`
6. Also search for any JavaScript that hardcodes `/api/task/` or `/api/scan/status` — these don't need changing since the actual URLs stay the same
7. Register in `create_app()`
8. Delete from `app.py`
9. Run tests

**Size estimate from the plan:** ~55 lines.

**Risk level from the plan:** LOW.

---

## PHASE 2: DATA AND MONITOR BLUEPRINTS

*Goal: Extract the data-viewing routes and — critically — unify the three copies of monitor logic into one.*

---

### Step 2.1 — Extract `data_bp.py` (data viewing, scanning, downloading)

**What you're doing:**
Moving three routes that deal with viewing, scanning, and downloading the data companies have sent you in response to your GDPR requests. The "data card" shows what data a company has provided, the "scan" route processes new attachments, and "download" fetches data from links in emails.

**Why it matters:**
These routes are mostly self-contained — they read state and trigger downloads/scans, but they don't affect other parts of the app. The one cross-reference is that the company detail page has "View data" links pointing to these routes.

**Technical detail:**

From the Route Map:
| Method | URL | Line | Description |
|--------|-----|------|-------------|
| GET | `/data/<domain>` | 1042 | Data card — catalog, schema, company info |
| GET | `/scan/<domain>` | 1252 | Scan received folder, LLM-analyze, persist catalog |
| GET | `/download/<domain>` | 1339 | Trigger link_downloader, update state, redirect |

Dependencies from the plan:
- `shared.py`: `_get_accounts()`, `_load_all_states()`, `_lookup_company()`, `_current_state_path()`
- `reply_monitor.attachment_handler`: `_catalog_csv`, `_catalog_json`, `_catalog_zip`
- `reply_monitor.link_downloader`: `download_data_link`
- `reply_monitor.schema_builder`: `build_schema`
- `reply_monitor.models`: `AttachmentCatalog`, `FileEntry`

The plan notes: *"Cross-refs: `company_detail.html` has 'View data' links → update template."*

**What to do, concretely:**
1. Create `dashboard/blueprints/data_bp.py`
2. Define the Blueprint (no URL prefix since URLs are mixed: `/data/`, `/scan/`, `/download/`)
3. Move `data_card()` (lines 1042–1142), `scan_folder()` (lines 1252–1336), `download_data()` (lines 1339–1377)
4. Update imports
5. **Search templates** for `url_for("data_card")` → update to `url_for("data.data_card")`
6. Search for `url_for("scan_folder")` and `url_for("download_data")` → update similarly
7. Register, delete from `app.py`, run tests

**Size estimate from the plan:** ~300 lines.

**Risk level from the plan:** LOW-MEDIUM.

---

### Step 2.2 — Create `services/monitor_runner.py` (unified monitor logic) ⚠️ CRITICAL

**What you're doing:**
This is the most important step in the entire refactoring. You're taking the three different copies of "check Gmail for new replies" logic and writing one authoritative version that replaces all three.

**Why it matters:**
The plan's Three-Way Monitor Comparison (Step 3) found that:
- `monitor.py` (the CLI tool) has the most complete implementation with 12 features
- `app.py`'s SAR monitor (web version) is missing 6 features and **has a bug** — it doesn't deduplicate against archived replies from past attempts
- `app.py`'s SP (subprocessor) monitor is missing 4 features

The plan says: *"Replace the three copies of monitor logic with one shared implementation."*

The bug specifically: *"BUG: Missing past-attempts dedup for `existing_ids` — only deduplicates against `state.replies`, not `state.past_attempts[].replies`. This means archived replies from older attempts can be re-fetched and re-classified."*

**Technical detail:**

The new file `dashboard/services/monitor_runner.py` will contain four functions:

```python
def run_sar_monitor(account, *, state_path, tokens_dir, data_dir,
                    sp_requests_path, verbose=False, reprocess=False,
                    draft_backfill=False):
    """Run SAR reply monitor for one account."""

def run_sp_monitor(account, *, state_path, tokens_dir, data_dir,
                   sp_requests_path, sp_state_path, service=None,
                   email="", verbose=False):
    """Run subprocessor reply monitor for one account."""

def auto_download_data_links(account, states, api_key):
    """Download DATA_PROVIDED_LINK replies that have a URL but no catalog."""

def auto_analyze_inline_data(account, states, api_key):
    """Analyze DATA_PROVIDED_INLINE replies without schema."""
```

**Key design decision from the plan:** *"All paths passed explicitly (no `g` dependency) — safe to call from CLI, web, and background threads."*

This is important. Flask's `g` object is a per-request storage space — it only exists while a web request is being handled. Background threads and CLI scripts don't have a request, so `g` doesn't exist there. By making all file paths explicit function arguments, this code works everywhere.

**What to do, concretely:**
1. Create `dashboard/services/monitor_runner.py`
2. Start from `monitor.py`'s implementation (lines 85–326) since it's the most complete
3. Refactor it so all paths are passed as arguments instead of computed internally
4. Add the `verbose` flag to control terminal output (True for CLI, False for web)
5. **Fix the bug**: add past-attempts deduplication — the plan gives the exact fix: `for pa in state.past_attempts: for r in pa.get("replies", []): existing_ids.add(r["gmail_message_id"])`
6. Update `monitor.py` CLI to import and call these new functions instead of having its own copy
7. **Test extensively** — run the monitor through CLI, verify it still processes replies correctly
8. Don't update `app.py` yet — that happens in the next step

**Migration path from the plan:**
1. Create `monitor_runner.py` with unified logic from `monitor.py` (the most complete version)
2. Update `monitor.py` to import and call `monitor_runner.py`
3. (Later) Update `monitor_bp.py` to import and call `monitor_runner.py`
4. Delete duplicate logic from both

**Size estimate from the plan:** ~350 lines.

**Risk level from the plan:** MEDIUM — *"most important to get right."*

---

### Step 2.3 — Extract `monitor_bp.py` (refresh and re-extract routes)

**What you're doing:**
Moving the two monitoring-related web routes into their own Blueprint. These routes trigger the "check for new replies" and "re-extract download links" actions from the web dashboard.

**Why it matters:**
Now that you have `monitor_runner.py`, the web routes become thin wrappers — they just figure out the right file paths from the current user's session, call the unified monitor functions, and redirect back to the dashboard.

**Technical detail:**

From the Route Map:
| Method | URL | Line | Description |
|--------|-----|------|-------------|
| GET | `/refresh` | 1395 | Run SAR + SP monitors inline, re-extract links |
| GET | `/reextract` | 1380 | Re-fetch Gmail bodies for missing data_link fields |

The plan also lists helper functions that move here or to `monitor_runner.py`:
- `_run_monitor_for_account()` (lines 1474–1590) → replaced by `monitor_runner.run_sar_monitor()`
- `_run_subprocessor_monitor_for_account()` (lines 1593–1701) → replaced by `monitor_runner.run_sp_monitor()`
- `_reextract_missing_links()` (lines 1417–1471) → stays in this Blueprint or moves to services
- `_auto_download_data_links()` (lines 1704–1793) → now in `monitor_runner`
- `_auto_analyze_inline_data()` (lines 1735–1793) → now in `monitor_runner`

The plan notes: *"The monitor functions are the most complex internal logic. Moving them requires careful handling of the `_auto_download_data_links` and `_auto_analyze_inline_data` helpers which are called both from the monitor and from the reextract route."*

**What to do, concretely:**
1. Create `dashboard/blueprints/monitor_bp.py`
2. Define the Blueprint (no URL prefix)
3. Move `refresh()` and `reextract()` routes
4. Replace the old inline monitor calls with calls to `monitor_runner.run_sar_monitor()` and `monitor_runner.run_sp_monitor()`
5. Pass paths explicitly: get them from `shared.py` helpers, which read from Flask's `g`
6. Delete the old `_run_monitor_for_account()` and `_run_subprocessor_monitor_for_account()` from `app.py`
7. Register, run tests, smoke-test by clicking "Refresh" in the dashboard

**Size estimate from the plan:** ~50 lines for the Blueprint (logic is now in `monitor_runner.py`).

**Risk level from the plan:** MEDIUM.

---

## PHASE 3: CORE BLUEPRINTS

*Goal: Extract the more interconnected pieces — portal, transfers, and company detail. These have more cross-references and some tricky technical challenges.*

---

### Step 3.1 — Extract `portal_bp.py` (portal submission and CAPTCHA handling)

**What you're doing:**
Moving the routes that handle submitting GDPR requests through company web portals (instead of email). This includes the CAPTCHA flow — when a portal shows a CAPTCHA, your app pauses and lets you solve it manually.

**Why it matters:**
This Blueprint has a specific technical challenge: the `_portal_tasks` dictionary. This is an in-memory Python dictionary that tracks which portal submissions are currently running. Background threads write to it, and the status-polling route reads from it. It must exist in exactly one place.

**Technical detail:**

From the Route Map:
| Method | URL | Line | Description |
|--------|-----|------|-------------|
| POST | `/portal/submit/<domain>` | 2069 | Start background portal submission |
| GET | `/portal/status/<domain>` | 2166 | Poll portal submission progress |
| POST | `/portal/verify/<domain>` | 2211 | Mark portal verification as passed |
| GET | `/captcha/<domain>` | 2230 | Display pending CAPTCHA for user solving |
| POST | `/captcha/<domain>` | 2253 | Submit user CAPTCHA solution |

Also moves here:
- `_portal_tasks` dict (line 2066) — in-memory tracking of running portal submissions

The plan warns: *"The `_portal_tasks` in-memory dict is module-level state. It must be importable from a single location — if multiple modules import it, they must all reference the same dict object. The background thread in `portal_submit()` also calls `_current_data_dir()` inside a thread with no request context — must capture the path before spawning."*

**What is the background thread problem?**
When someone clicks "Submit to portal," your app starts a background thread to do the actual submission (which might take a while — filling forms, uploading documents, etc.). The web request returns immediately with "submission started." But that background thread doesn't have access to Flask's `g` object (which stores the current user's data directory). So you must grab the path *before* starting the thread and pass it in:

```python
# WRONG — crashes because g doesn't exist in the thread
def portal_submit(domain):
    thread = Thread(target=do_submit, args=(domain,))
    thread.start()  # do_submit will try to use g.data_dir and fail

# RIGHT — capture the path first, pass it explicitly
def portal_submit(domain):
    data_dir = _current_data_dir()  # grab while we still have the request
    thread = Thread(target=do_submit, args=(domain, data_dir))
    thread.start()  # do_submit uses the passed-in data_dir
```

**What to do, concretely:**
1. Create `dashboard/blueprints/portal_bp.py`
2. Define: `portal_bp = Blueprint("portal", __name__)`
3. Move `_portal_tasks` dict into this file at module level
4. Move all five route functions
5. For `portal_submit()`: verify that all paths from `g`/`request` are captured *before* the `Thread.start()` call — if not, fix this
6. Update any references to `url_for("captcha_show")` → `url_for("portal.captcha_show")`
7. Note: `mark-portal-submitted` stays in `company_bp` (different URL structure) but may reference portal routes
8. Register, delete from `app.py`, run tests
9. **Critical test:** actually start a portal submission and verify the progress polling still works

**Size estimate from the plan:** ~210 lines.

**Risk level from the plan:** MEDIUM.

---

### Step 3.2 — Extract `transfers_bp.py` (data transfer tracking and subprocessor requests)

**What you're doing:**
Moving the routes that handle the "data transfers" view — a D3.js graph showing which companies share your data with which subprocessors — plus the routes for fetching subprocessor information and sending disclosure requests.

**Why it matters:**
Like portal, this Blueprint has background tasks (fetching subprocessor lists, sending bulk disclosure requests). The same thread-context rules apply. It also has the most external dependencies of the medium-complexity Blueprints.

**Technical detail:**

From the Route Map:
| Method | URL | Line | Description |
|--------|-----|------|-------------|
| GET | `/transfers` | 1858 | Data transfer map with D3.js graph |
| POST | `/transfers/fetch` | 1934 | Start background subprocessor fetch task |
| POST | `/transfers/request-letter/<domain>` | 1949 | Send single SP disclosure request |
| POST | `/transfers/request-all` | 2006 | Background task: send all disclosure requests |
| GET | `/api/transfers/task` | 2021 | Poll subprocessor task progress |

Also moves here:
- `_fetch_all_subprocessors()` (lines 2570–2612) — background task function
- `_send_all_disclosure_requests()` (lines 2615–2684) — background task function
- `is_stale_dict()` (lines 2687–2695) — helper to check if cached data is old

**What is D3.js?**
D3 is a JavaScript library for data visualization. The transfers page uses it to render an interactive graph/network diagram showing data flows between companies and their subprocessors. The D3 code lives in the HTML template and calls the `/transfers` route for its data — the Blueprint move doesn't affect the JavaScript, only the Python that prepares the data.

The plan notes about background tasks: *"Background task functions reference `_current_data_dir()` and `_current_tokens_dir()` which rely on Flask's `g` object. Background threads don't have a request context — need to capture paths before spawning threads (already done in current code via closure, but verify)."*

**What to do, concretely:**
1. Create `dashboard/blueprints/transfers_bp.py`
2. Define: `transfers_bp = Blueprint("transfers", __name__)`
3. Move the five route functions plus the two background task functions and `is_stale_dict()`
4. Verify all background task functions receive paths as arguments (not from `g`)
5. Update `url_for("transfers")` references → `url_for("transfers.transfers")`
6. Register, delete from `app.py`, run tests
7. **Test:** trigger a subprocessor fetch and verify the progress polling works

**Size estimate from the plan:** ~350 lines.

**Risk level from the plan:** MEDIUM.

---

### Step 3.3 — Extract `company_bp.py` (company detail page and reply management)

**What you're doing:**
Moving the most-referenced routes in your app: the company detail page (which shows the full email thread with a company) and all the follow-up/reply actions (send draft, dismiss draft, compose free-form reply).

**Why it matters:**
This is the highest-risk extraction so far because `url_for("company_detail", domain=...)` appears in approximately 15 places across other Blueprints and templates. Every one of those needs updating.

**Technical detail:**

From the Route Map — 8 routes:
| Method | URL | Line | Description |
|--------|-----|------|-------------|
| GET | `/company/<domain>` | 603 | Full two-panel reply thread (SAR + SP streams) |
| POST | `/company/<domain>/send-followup` | 830 | Send auto-generated draft reply |
| POST | `/company/<domain>/dismiss-followup` | 867 | Dismiss a pending SAR draft |
| POST | `/company/<domain>/send-sp-followup` | 883 | Send auto-generated draft reply to SP thread |
| POST | `/company/<domain>/dismiss-sp-followup` | 921 | Dismiss a pending SP draft |
| POST | `/company/<domain>/compose-reply` | 937 | Send free-form reply in SAR thread |
| POST | `/company/<domain>/compose-sp-reply` | 990 | Send free-form reply in SP thread |
| POST | `/company/<domain>/mark-portal-submitted` | 2190 | Mark portal submission as completed |

The plan notes: *"`company_detail()` is the most complex route (225 lines)"* and *"`url_for('company_detail', ...)` referenced from ~15 locations across other Blueprints and templates"* — making this the *"highest `url_for` breakage surface."*

The plan also notes about `mark-portal-submitted`: *"under `/company/` URL even though it's portal-related. Keep it here because the URL structure and template context match the company detail page."*

**What to do, concretely:**
1. Create `dashboard/blueprints/company_bp.py`
2. Define: `company_bp = Blueprint("company", __name__)`
3. Move all eight route functions
4. **This is the critical part:** grep (search) the entire codebase for `url_for("company_detail"` — update every occurrence to `url_for("company.company_detail"`
5. Also grep all `.html` templates for the same pattern
6. Check other Blueprints you've already extracted (portal, data, transfers) — they may have redirects to company detail
7. Register, delete from `app.py`, run tests
8. **Smoke test every page** — click through portal, data, transfers, dashboard to verify all "View company" links work

**Size estimate from the plan:** ~440 lines.

**Risk level from the plan:** MEDIUM-HIGH.

---

## PHASE 4: DASHBOARD AND PIPELINE

*Goal: Extract the main dashboard and the pipeline — the two most visible and most complex pieces.*

---

### Step 4.1 — Extract `dashboard_bp.py` (main dashboard and cards listing)

**What you're doing:**
Moving the main landing page (the dashboard showing all your GDPR requests sorted by urgency) and the cards listing page (two-tab view of companies) into their own Blueprint.

**Why it matters:**
`url_for("dashboard")` is referenced from many other routes as the "go back to home" destination — portal, refresh, captcha, auth all redirect here. You need to update all of them.

**Technical detail:**

From the Route Map:
| Method | URL | Line | Description |
|--------|-----|------|-------------|
| GET | `/` | 563 | Main dashboard — account selector, company cards sorted by urgency |
| GET | `/cards` | 1145 | Two-tab listing: companies with data vs. without |

The plan notes: *"`url_for('dashboard')` referenced from many routes (portal, refresh, captcha, auth)"* but *"simple logic"* — these are read-only pages that just load data and render templates.

`_build_card()` is already in `shared.py` at this point (moved in Step 0.1).

**What to do, concretely:**
1. Create `dashboard/blueprints/dashboard_bp.py`
2. Define: `dashboard_bp = Blueprint("main", __name__)` — note the name is "main" to keep `url_for` references intuitive
3. Move `dashboard()` (lines 563–600) and `cards_listing()` (lines 1145–1249)
4. Grep all files for `url_for("dashboard")` → update to `url_for("main.dashboard")`
5. Check `base.html` navbar — the "Home" link almost certainly uses `url_for("dashboard")`
6. Register, delete from `app.py`, run tests

**Size estimate from the plan:** ~200 lines.

**Risk level from the plan:** MEDIUM (many `url_for` references but simple logic).

---

### Step 4.2 — Extract `pipeline_bp.py` (the full SAR pipeline) ⚠️ HIGHEST RISK

**What you're doing:**
Moving the entire pipeline — the workflow that scans your Gmail, discovers companies, resolves their contact information, generates GDPR letters, and sends them. This is the largest and most complex Blueprint.

**Why it matters:**
The plan saves this for last because it has the most internal dependencies, the most background tasks, and the most complex state management. It also contains `_sync_scan_state_flags()`, a 107-line function that detects bounced emails and tracks pipeline state.

The plan says: *"Extract last because it has the most internal dependencies and is least likely to break other Blueprints (pipeline URLs are only referenced from pipeline templates)."*

**Technical detail:**

From the Route Map — 9 routes:
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

Also moves here:
- `_sync_scan_state_flags()` (lines 2337–2443) — 107 lines of bounce detection logic
- `_token_exists()` (lines 2446–2449) — checks if a Gmail OAuth token exists
- `_send_token_valid()` (lines 2452–2455) — checks if the "send email" token is still valid
- `_CONFIDENCE_RANK` constant (line 2287)
- Three background task functions: `_do_scan()`, `_do_resolve()`, `_do_send()`

The plan lists extensive dependencies — this Blueprint touches almost every module in the project:
- `shared.py`, `scan_state`, `tasks`, `gmail_oauth`, `scanner.*`, `contact_resolver.*`, `letter_engine.*`

**What to do, concretely:**
1. Create `dashboard/blueprints/pipeline_bp.py`
2. Define: `pipeline_bp = Blueprint("pipeline", __name__, url_prefix="/pipeline")`
3. Move all nine route functions
4. Move `_sync_scan_state_flags()`, `_token_exists()`, `_send_token_valid()`, `_CONFIDENCE_RANK`
5. Move the three background task functions: `_do_scan()`, `_do_resolve()`, `_do_send()`
6. **For each background task:** verify all paths from `g`/`request` are captured before `Thread.start()`
7. Update `url_for("pipeline")` → `url_for("pipeline.pipeline")` and similar for all pipeline routes
8. These should mostly be in pipeline templates only (self-contained), but verify
9. Register, delete from `app.py`, run tests
10. **Critical tests:**
    - Start a scan and verify progress polling
    - Start a resolve and verify it completes
    - Test the review page
    - Test letter sending (if possible in a safe environment)

**Size estimate from the plan:** ~750 lines (the largest Blueprint).

**Risk level from the plan:** HIGH — *"complexity, background tasks, thread safety."*

---

## PHASE 5: CLEANUP

*Goal: Tie up loose ends, verify everything, and confirm `app.py` is minimal.*

---

### Step 5.1 — Audit all templates for `url_for()` references

**What you're doing:**
Doing a final sweep of every HTML template to make sure all `url_for()` calls use the correct Blueprint-prefixed names.

**Why it matters:**
Any missed `url_for()` reference will cause a `BuildError` at runtime — the page will crash when Flask tries to generate a URL for a route name that no longer exists.

**What to do, concretely:**
1. Run: `grep -rn 'url_for(' dashboard/templates/` — this finds every `url_for` call in every template
2. For each one, verify the route name includes the Blueprint prefix (e.g., `"main.dashboard"` not `"dashboard"`)
3. Pay special attention to `base.html` — the navbar links affect every page
4. Check JavaScript in templates too — some `url_for()` calls may be inside `<script>` tags

---

### Step 5.2 — Update all test imports

**What you're doing:**
Your test files import specific functions from `app.py`. Those functions have moved. Update the imports.

**Technical detail from the plan:**

| Test file | Old import | New import |
|-----------|-----------|------------|
| `test_snippet_clean.py` | `from dashboard.app import _clean_snippet` | `from dashboard.shared import _clean_snippet` |
| `test_portal_submit_route.py` | `from dashboard.app import _lookup_company` | `from dashboard.shared import _lookup_company` |
| `test_api_body.py` | `from dashboard.app import app` | `from dashboard import create_app` |
| `test_dashboard.py` | `import dashboard.app as app_module` | Update module import path |

**What to do, concretely:**
1. Update each test file's imports as listed above
2. For `test_api_body.py`, you may need to change the test setup to call `create_app()` and use the returned app
3. Run: `pytest tests/unit/ -q`
4. All tests should pass

---

### Step 5.3 — Verify `app.py` is minimal

**What you're doing:**
Confirming that `app.py` has been fully emptied out and now just serves as an entry point.

**Technical detail:**

The plan says: *"Should be ~20 lines: create_app + `if __name__`."*

The final `app.py` should look approximately like:

```python
from dashboard import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
```

**What to do, concretely:**
1. Open `app.py` — it should have no route functions, no helper functions, no constants
2. If anything is left, identify which Blueprint it should belong to and move it
3. Verify the app starts and every page works

---

### Step 5.4 — Full regression test

**What you're doing:**
A complete test of every feature to make sure nothing was broken during the refactoring.

**The plan's test strategy:**
- Run `pytest tests/unit/ -q` after each Blueprint extraction
- Manual smoke test: load dashboard, click through each page
- Check all `url_for()` routes by visiting every page
- Verify background tasks still run (portal submit, subprocessor fetch, pipeline scan/resolve/send)

**What to do, concretely:**
1. Run all unit tests
2. Start the app and visit every page in this order:
   - Dashboard (`/`) — verify cards load
   - Cards listing (`/cards`) — verify both tabs work
   - Click into a company detail (`/company/example.com`) — verify thread loads
   - Click "View data" on a company — verify data card loads
   - Visit costs (`/costs`) — verify cost table loads
   - Visit transfers (`/transfers`) — verify D3 graph renders
   - Visit pipeline (`/pipeline`) — verify progress indicators show
   - Visit settings — verify export link works
3. If you have a test environment, trigger:
   - A monitor refresh
   - A portal submission
   - A pipeline scan
4. Check browser console for JavaScript errors (broken `url_for` in JS would show here)

---

## SUMMARY: STEP COUNT AND RISK MAP

| Step | Name | ~Lines | Risk | Key Challenge |
|------|------|--------|------|---------------|
| 0.1 | Create `shared.py` | 350 | LOW | Getting all imports right |
| 0.2 | Create app factory | 60 | LOW | Keeping `app` importable |
| 1.1 | Extract `costs_bp` | 65 | VERY LOW | First Blueprint — establishing the pattern |
| 1.2 | Extract `settings_bp` | 45 | VERY LOW | Straightforward |
| 1.3 | Extract `api_bp` | 55 | LOW | Update JS `url_for` references |
| 2.1 | Extract `data_bp` | 300 | LOW-MED | Template cross-references |
| 2.2 | Create `monitor_runner` | 350 | MEDIUM | Unifying 3 copies, fixing the dedup bug |
| 2.3 | Extract `monitor_bp` | 50 | MEDIUM | Wiring up the new unified service |
| 3.1 | Extract `portal_bp` | 210 | MEDIUM | `_portal_tasks` dict, thread context |
| 3.2 | Extract `transfers_bp` | 350 | MEDIUM | Background tasks, thread context |
| 3.3 | Extract `company_bp` | 440 | MED-HIGH | ~15 `url_for` references to update |
| 4.1 | Extract `dashboard_bp` | 200 | MEDIUM | Many `url_for("dashboard")` references |
| 4.2 | Extract `pipeline_bp` | 750 | HIGH | Most complex, most background tasks |
| 5.1 | Audit templates | — | LOW | Thoroughness required |
| 5.2 | Update test imports | — | LOW | Mechanical |
| 5.3 | Verify minimal `app.py` | — | LOW | Confirmation |
| 5.4 | Full regression test | — | — | Finding anything missed |

**Total: 17 steps across 6 phases.**

The original 3,099-line `app.py` becomes ~3,245 lines across ~14 files — slightly more total lines due to import statements and Blueprint boilerplate, but dramatically more organized and maintainable.
