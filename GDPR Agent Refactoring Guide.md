# Step-by-Step Refactoring Execution Guide

---

## Terminal Setup

You need **2 terminal windows** (or tabs), both starting in the same place:

```
Terminal 1 — "WORK"     → where Claude Code runs
Terminal 2 — "CONTROL"  → where you do git operations, merges, worktree management
```

Open both now:

```bash
# Both terminals:
cd /Users/nemasha/MyProjects/gdpr-agent
source .venv/bin/activate
```

Terminal 2 stays in `/Users/nemasha/MyProjects/gdpr-agent` (main repo) at all times. Terminal 1 will move between the main repo and worktree directories depending on the stage.

---

## Claude Code Configuration

Before Stage 1, set this up once in Terminal 1:

```bash
cd /Users/nemasha/MyProjects/gdpr-agent
claude config set model claude-opus-4-20250514
```

**Settings for all stages:**

| Setting | Value | Why |
|---------|-------|-----|
| Model | Opus | Every stage, no exceptions — you have credits |
| Effort | High | Every stage, no exceptions |
| Compact window | **70%** (raise from your current 50%) | You're moving 200–750 line functions. At 50% Claude Code starts compacting mid-stage and forgets function bodies it just read. 70% keeps more context alive. |

To change compact window: it's in Claude Code settings, or:
```bash
claude config set compactAfterTokenPercentage 70
```

**One stage per Claude Code session.** After each stage finishes, exit Claude Code (`/exit` or Ctrl+C), do your git operations in Terminal 2, then start a fresh `claude` session for the next stage. Fresh context every time.

---

## Your Baseline

From your test run:
- **655 passed, 1 pre-existing failure** (`test_portal_submitter::test_successful_submission`)
- Ignore that one failure throughout — it's not ours

---

## Pre-Stage Checklist (do this before EVERY stage)

```bash
# Terminal 2:
cd /Users/nemasha/MyProjects/gdpr-agent
source .venv/bin/activate
git status          # should be clean
git log --oneline -3  # know where you are
```

---

## STAGE 1: Phase 0 — Preparation

**Where:** directly on main
**Terminal 1:** stays in `/Users/nemasha/MyProjects/gdpr-agent`

### Launch Claude Code

```bash
# Terminal 1:
cd /Users/nemasha/MyProjects/gdpr-agent
source .venv/bin/activate
claude
```

### Paste this prompt:

```
Before running any command, always activate the virtualenv:
source /Users/nemasha/MyProjects/gdpr-agent/.venv/bin/activate

Read REFACTOR_PLAN.md and IMPLEMENTATION_GUIDE.md thoroughly before starting.

CRITICAL CODE RULES:
- NEVER write placeholder comments like "# ... rest of the code", "# existing code here", "# same as before", "# remaining implementation", "# TODO"
- NEVER truncate a function body. Every function must be copied in FULL — every single line.
- NEVER use pass as a stand-in for real implementation
- After creating each new file, verify with: python -c "from dashboard.shared import _lookup_company" (adjust import as needed)

We're doing Phase 0: Preparation. Two sub-steps, both on main.

**Step 0.1 — Create dashboard/shared.py**

Move ALL shared helpers, constants, and context processor from dashboard/app.py into a new dashboard/shared.py.

Use REFACTOR_PLAN.md Step 4 "Shared Utilities Inventory" as your exact checklist — both the "Helper Functions" table and "Constants" table. Also move _inject_globals() context processor and _flag_emoji_filter() template filter.

For each item:
1. Copy the complete function/constant to shared.py with all its imports
2. In app.py, replace the moved item with an import from dashboard.shared
3. Do NOT change any function bodies — this is a pure extraction

Update any test files that import moved functions from dashboard.app.

After completing 0.1, run:
- pytest tests/unit/ -q (expect 655 passed, 1 known failure)
- wc -l dashboard/shared.py (should be approximately 350 lines — if under 250, you missed items)
- wc -l dashboard/app.py (should have shrunk significantly)
- /find-placeholders

**Step 0.2 — Create app factory in dashboard/__init__.py**

Add a create_app() function that:
- Creates the Flask app
- Configures secret key
- Sets up LoginManager and login_user_loader
- Sets up before_request hook
- Registers existing blueprints (auth_bp, admin_bp)
- Registers the context processor from shared.py
- Returns app

Shrink app.py to approximately:
```python
from dashboard import create_app
app = create_app()
if __name__ == "__main__":
    app.run(debug=True)
```

Critical: `from dashboard.app import app` must still work — tests depend on this.

After completing 0.2, run:
- pytest tests/unit/ -q (655 passed, 1 known failure)
- wc -l dashboard/app.py
- /find-placeholders

Report all results to me.
```

### After Claude Code finishes — in Terminal 1:

```
/find-placeholders
```

If clean, then in Claude Code:

```
Update CLAUDE.md and ARCHITECTURE.md to reflect the new shared.py module and app factory pattern in dashboard/__init__.py.
```

### Exit Claude Code, commit in Terminal 2:

```bash
# Terminal 2:
cd /Users/nemasha/MyProjects/gdpr-agent
git add -A
git commit -m "Phase 0: extract shared.py + app factory" --no-verify
pytest tests/unit/ -q    # verify 655 passed
```

Write down: `app.py is now ____ lines, shared.py is ____ lines`

---

## STAGE 2: Worktree 1 — Leaf Blueprints

**Where:** worktree

### Create worktree in Terminal 2:

```bash
# Terminal 2:
cd /Users/nemasha/MyProjects/gdpr-agent
git worktree add /Users/nemasha/MyProjects/gdpr-agent-leaf-blueprints -b feature/leaf-blueprints
```

### Launch Claude Code in Terminal 1:

```bash
# Terminal 1:
cd /Users/nemasha/MyProjects/gdpr-agent-leaf-blueprints
source /Users/nemasha/MyProjects/gdpr-agent/.venv/bin/activate
claude
```

### Paste this prompt:

```
Before running any command, always activate the virtualenv:
source /Users/nemasha/MyProjects/gdpr-agent/.venv/bin/activate

Read REFACTOR_PLAN.md and IMPLEMENTATION_GUIDE.md.

CRITICAL CODE RULES:
- NEVER write placeholder comments like "# ...", "# rest of the code", "# same as before", "# TODO"
- NEVER truncate a function body. Every function must be copied in FULL.
- After creating each new file, verify it imports cleanly with python -c

We're doing Phase 1: Leaf Blueprints — three extractions.

First, create the directory dashboard/blueprints/ with an empty __init__.py.

**Step 1.1 — Extract costs_bp.py**
(REFACTOR_PLAN.md Step 5, section 6 — costs Blueprint)

- Create dashboard/blueprints/costs_bp.py
- Blueprint("costs", __name__)
- Move the costs() route from app.py (line ~1795, approximately 60 lines)
- Only dependency: contact_resolver.cost_tracker.load_persistent_log
- Add @login_required decorator if the original had it
- Register in create_app() in dashboard/__init__.py
- Delete the moved function from app.py
- Verify: python -c "from dashboard.blueprints.costs_bp import costs_bp"

**Step 1.2 — Extract settings_bp.py**
(REFACTOR_PLAN.md Step 5, section 11 — settings Blueprint)

- Create dashboard/blueprints/settings_bp.py
- Blueprint("settings", __name__, url_prefix="/settings")
- Move export_data() and delete_account() from app.py — COMPLETE functions, every line
- Register in create_app()
- Delete from app.py
- Verify: python -c "from dashboard.blueprints.settings_bp import settings_bp"

**Step 1.3 — Extract api_bp.py**
(REFACTOR_PLAN.md Step 5, section 10 — API Blueprint)

- Create dashboard/blueprints/api_bp.py
- Blueprint("api", __name__, url_prefix="/api")
- Move api_task(), api_scan_status(), api_body() from app.py — COMPLETE functions
- Register in create_app()
- IMPORTANT: grep all templates for url_for("api_task") or url_for('api_task') → update to url_for("api.api_task"). Same for api_scan_status and api_body.
- Also check JavaScript inside <script> tags in templates
- Delete from app.py
- Verify: python -c "from dashboard.blueprints.api_bp import api_bp"

After all three, run:
- pytest tests/unit/ -q (expect 655 passed, 1 known failure)
- wc -l dashboard/blueprints/costs_bp.py dashboard/blueprints/settings_bp.py dashboard/blueprints/api_bp.py
- wc -l dashboard/app.py
- /find-placeholders

Report all results to me.
```

### After Claude Code finishes — in Terminal 1:

```
/find-placeholders
```

If clean:

```
Update CLAUDE.md and ARCHITECTURE.md to document the new dashboard/blueprints/ directory and the three leaf Blueprints.
```

### Exit Claude Code, merge in Terminal 2:

```bash
# Terminal 2:
cd /Users/nemasha/MyProjects/gdpr-agent-leaf-blueprints
git add -A
git commit -m "Phase 1: extract costs, settings, api leaf Blueprints" --no-verify

cd /Users/nemasha/MyProjects/gdpr-agent
git merge feature/leaf-blueprints
pytest tests/unit/ -q    # verify 655 passed
git worktree remove /Users/nemasha/MyProjects/gdpr-agent-leaf-blueprints
```

Write down: `app.py is now ____ lines`

---

## STAGE 3: Worktree 2 — Monitor Unification ⚠️ MOST IMPORTANT

**Where:** worktree

### Create worktree in Terminal 2:

```bash
# Terminal 2:
cd /Users/nemasha/MyProjects/gdpr-agent
git worktree add /Users/nemasha/MyProjects/gdpr-agent-monitor -b feature/monitor-unification
```

### Launch Claude Code in Terminal 1:

```bash
# Terminal 1:
cd /Users/nemasha/MyProjects/gdpr-agent-monitor
source /Users/nemasha/MyProjects/gdpr-agent/.venv/bin/activate
claude
```

### Paste this prompt:

```
Before running any command, always activate the virtualenv:
source /Users/nemasha/MyProjects/gdpr-agent/.venv/bin/activate

Read these sections of REFACTOR_PLAN.md CAREFULLY before writing any code:
- Step 3: Three-Way Monitor Comparison (the comparison table is critical)
- Step 6: Services Layer Proposal (the function signatures are mandatory)
Also read IMPLEMENTATION_GUIDE.md Steps 2.2 and 2.3.

CRITICAL CODE RULES:
- NEVER write placeholder comments like "# ...", "# rest of the code", "# same as before", "# TODO"
- NEVER truncate a function body. Every function must be copied in FULL.
- monitor_runner.py should be approximately 350 lines. If it's under 200, you've used placeholders.

**Step 2.2 — Create dashboard/services/monitor_runner.py**

Create the directory dashboard/services/ if it doesn't exist, with __init__.py.

This is the most important file in the entire refactoring. You are unifying three copies of monitor logic into one authoritative version.

Create four functions with these EXACT signatures (from REFACTOR_PLAN.md Step 6):

```python
def run_sar_monitor(account, *, state_path, tokens_dir, data_dir,
                    sp_requests_path, verbose=False, reprocess=False,
                    draft_backfill=False):

def run_sp_monitor(account, *, state_path, tokens_dir, data_dir,
                   sp_requests_path, sp_state_path, service=None,
                   email="", verbose=False):

def auto_download_data_links(account, states, api_key):

def auto_analyze_inline_data(account, states, api_key):
```

Start from monitor.py's main() implementation (lines 85–326) — it is the most complete version. Include ALL features from the monitor.py column in the comparison table (REFACTOR_PLAN.md Step 3):
- Basic reply fetch + classify
- past_attempts dedup
- Attachment handling WITH schema enrichment
- Inline data schema building
- Portal verification
- Portal auto-submit
- Bounce retries
- Draft generation
- Auto-dismiss on YOUR_REPLY
- Reprocess mode
- Draft backfill
- Verbose output (controlled by verbose flag)

FIX THE BUG — add past-attempts dedup to run_sar_monitor:
```python
for pa in state.past_attempts:
    for r in pa.get("replies", []):
        existing_ids.add(r["gmail_message_id"])
```

KEY DESIGN: All paths passed as explicit arguments. No Flask g dependency. verbose flag = True for CLI, False for web. This makes the functions callable from CLI, web routes, and background threads.

Then update monitor.py CLI to import and call these functions instead of having its own copy. monitor.py becomes a thin wrapper that parses command-line args and calls monitor_runner functions.

**Step 2.3 — Create dashboard/blueprints/monitor_bp.py**

- Blueprint("monitor", __name__)
- Move refresh() and reextract() routes from app.py
- These routes call monitor_runner functions, passing paths from shared.py helpers
- Move _reextract_missing_links() to this Blueprint or to monitor_runner
- DELETE the old _run_monitor_for_account() and _run_subprocessor_monitor_for_account() from app.py — they are replaced by monitor_runner
- Register the Blueprint in create_app()

After both steps, run:
- pytest tests/unit/ -q (expect 655 passed, 1 known failure)
- wc -l dashboard/services/monitor_runner.py (should be ~350 lines)
- wc -l dashboard/app.py
- /find-placeholders
- /audit-errors

Show me the full results, plus confirm the dedup fix is present in run_sar_monitor.
```

### After Claude Code finishes — review carefully:

This is the one stage where you should actually read the output. Check:
1. Did it say 655 passed?
2. Is `monitor_runner.py` around 350 lines (not 100)?
3. Did it confirm the dedup fix?

If you want to see the fix yourself:

```
Show me the lines in dashboard/services/monitor_runner.py where existing_ids is built, including the past_attempts loop.
```

If everything looks good:

```
/find-placeholders
/audit-errors
Update CLAUDE.md and ARCHITECTURE.md to document the new services layer, monitor_runner.py, and the fact that monitor.py CLI and the web dashboard now share the same implementation.
```

### Exit Claude Code, merge in Terminal 2:

```bash
# Terminal 2:
cd /Users/nemasha/MyProjects/gdpr-agent-monitor
git add -A
git commit -m "Phase 2: unified monitor service + monitor Blueprint" --no-verify

cd /Users/nemasha/MyProjects/gdpr-agent
git merge feature/monitor-unification
pytest tests/unit/ -q    # verify 655 passed
git worktree remove /Users/nemasha/MyProjects/gdpr-agent-monitor
```

Write down: `app.py is now ____ lines, monitor_runner.py is ____ lines`

---

## STAGE 4: Worktree 3 — Data Blueprint

**Where:** worktree

### Create worktree in Terminal 2:

```bash
# Terminal 2:
cd /Users/nemasha/MyProjects/gdpr-agent
git worktree add /Users/nemasha/MyProjects/gdpr-agent-data -b feature/data-blueprint
```

### Launch Claude Code in Terminal 1:

```bash
# Terminal 1:
cd /Users/nemasha/MyProjects/gdpr-agent-data
source /Users/nemasha/MyProjects/gdpr-agent/.venv/bin/activate
claude
```

### Paste this prompt:

```
Before running any command, always activate the virtualenv:
source /Users/nemasha/MyProjects/gdpr-agent/.venv/bin/activate

Read REFACTOR_PLAN.md Step 5 section 4 (data_bp) and IMPLEMENTATION_GUIDE.md Step 2.1.

CRITICAL CODE RULES:
- NEVER write placeholder comments. NEVER truncate function bodies.
- data_bp.py should be approximately 300 lines. If under 200, you've dropped code.

**Step 2.1 — Extract data_bp.py**

Create dashboard/blueprints/data_bp.py:
- Blueprint("data", __name__) — no url_prefix (URLs are /data/, /scan/, /download/)
- Move data_card() (~line 1042), scan_folder() (~line 1252), download_data() (~line 1339) from app.py — EVERY LINE of each function
- Dependencies from shared.py: _get_accounts(), _load_all_states(), _lookup_company(), _current_state_path()
- External deps: reply_monitor.attachment_handler, link_downloader, schema_builder, models

Before moving, run these greps and update ALL matches:
- grep -rn 'url_for("data_card"' dashboard/ dashboard/templates/
- grep -rn "url_for('data_card'" dashboard/ dashboard/templates/
- grep -rn 'url_for("scan_folder"' dashboard/ dashboard/templates/
- grep -rn 'url_for("download_data"' dashboard/ dashboard/templates/
Update each to use "data." prefix: url_for("data.data_card"), etc.

Check company_detail.html specifically for "View data" links.

Register in create_app(). Delete moved functions from app.py.

After done, run:
- pytest tests/unit/ -q (expect 655 passed, 1 known failure)
- wc -l dashboard/blueprints/data_bp.py (should be ~300 lines)
- wc -l dashboard/app.py
- /find-placeholders

Report all results.
```

### After Claude Code finishes:

```
/find-placeholders
Update CLAUDE.md and ARCHITECTURE.md.
```

### Exit Claude Code, merge in Terminal 2:

```bash
# Terminal 2:
cd /Users/nemasha/MyProjects/gdpr-agent-data
git add -A
git commit -m "Extract data Blueprint (data_card, scan_folder, download_data)" --no-verify

cd /Users/nemasha/MyProjects/gdpr-agent
git merge feature/data-blueprint
pytest tests/unit/ -q
git worktree remove /Users/nemasha/MyProjects/gdpr-agent-data
```

---

## STAGE 5: Worktree 4 — Portal + Transfers

**Where:** worktree

### Create worktree in Terminal 2:

```bash
# Terminal 2:
cd /Users/nemasha/MyProjects/gdpr-agent
git worktree add /Users/nemasha/MyProjects/gdpr-agent-portal-transfers -b feature/portal-transfers
```

### Launch Claude Code in Terminal 1:

```bash
# Terminal 1:
cd /Users/nemasha/MyProjects/gdpr-agent-portal-transfers
source /Users/nemasha/MyProjects/gdpr-agent/.venv/bin/activate
claude
```

### Paste this prompt:

```
Before running any command, always activate the virtualenv:
source /Users/nemasha/MyProjects/gdpr-agent/.venv/bin/activate

Read REFACTOR_PLAN.md Step 5 sections 7 (transfers_bp) and 8 (portal_bp). Also read IMPLEMENTATION_GUIDE.md Steps 3.1 and 3.2.

CRITICAL CODE RULES:
- NEVER write placeholder comments. NEVER truncate function bodies.
- portal_bp.py should be approximately 210 lines, transfers_bp.py approximately 350 lines.

**Step 3.1 — Extract portal_bp.py**

Create dashboard/blueprints/portal_bp.py:
- Blueprint("portal", __name__)
- Move all 5 routes from app.py: portal_submit, portal_status, portal_verify, captcha_show, captcha_solve
- Move _portal_tasks dict to this file at module level — it MUST live here as the single source of truth

CRITICAL THREAD SAFETY AUDIT: In portal_submit(), find every call to _current_data_dir(), _current_state_path(), _current_tokens_dir(), current_user, or any Flask g/request-dependent function. If ANY of these are called INSIDE the thread function (the function passed to Thread(target=...)), that is a bug. Fix by:
1. Calling the function in the route handler (before Thread.start())
2. Saving the result to a local variable
3. Passing that variable as an argument to the thread function

Update url_for references: captcha_show → portal.captcha_show, etc.
Register in create_app(). Delete from app.py.

**Step 3.2 — Extract transfers_bp.py**

Create dashboard/blueprints/transfers_bp.py:
- Blueprint("transfers", __name__, url_prefix="/transfers")
- Move 5 routes + _fetch_all_subprocessors() + _send_all_disclosure_requests() + is_stale_dict()
  (REFACTOR_PLAN.md Step 5, section 7 has the full list)

SAME THREAD SAFETY AUDIT: _fetch_all_subprocessors() and _send_all_disclosure_requests() must not call g-dependent functions inside the thread. Capture paths before Thread.start(), pass as args.

Update url_for references. Register in create_app(). Delete from app.py.

After both, run:
- pytest tests/unit/ -q (expect 655 passed, 1 known failure)
- wc -l dashboard/blueprints/portal_bp.py (should be ~210)
- wc -l dashboard/blueprints/transfers_bp.py (should be ~350)
- wc -l dashboard/app.py
- /find-placeholders
- /audit-errors

Report all results.
```

### After Claude Code finishes:

```
/find-placeholders
/audit-errors
Update CLAUDE.md and ARCHITECTURE.md.
```

### Exit Claude Code, merge in Terminal 2:

```bash
# Terminal 2:
cd /Users/nemasha/MyProjects/gdpr-agent-portal-transfers
git add -A
git commit -m "Extract portal + transfers Blueprints" --no-verify

cd /Users/nemasha/MyProjects/gdpr-agent
git merge feature/portal-transfers
pytest tests/unit/ -q
git worktree remove /Users/nemasha/MyProjects/gdpr-agent-portal-transfers
```

---

## STAGE 6: Worktree 5 — Company + Dashboard ⚠️ HIGHEST URL_FOR RISK

**Where:** worktree

### Create worktree in Terminal 2:

```bash
# Terminal 2:
cd /Users/nemasha/MyProjects/gdpr-agent
git worktree add /Users/nemasha/MyProjects/gdpr-agent-company-dash -b feature/company-dashboard-blueprints
```

### Launch Claude Code in Terminal 1:

```bash
# Terminal 1:
cd /Users/nemasha/MyProjects/gdpr-agent-company-dash
source /Users/nemasha/MyProjects/gdpr-agent/.venv/bin/activate
claude
```

### Paste this prompt:

```
Before running any command, always activate the virtualenv:
source /Users/nemasha/MyProjects/gdpr-agent/.venv/bin/activate

Read REFACTOR_PLAN.md Step 5 sections 2 (dashboard_bp) and 3 (company_bp), PLUS the url_for() Impact table at the end of Step 5. Also read IMPLEMENTATION_GUIDE.md Steps 3.3 and 4.1.

CRITICAL CODE RULES:
- NEVER write placeholder comments. NEVER truncate function bodies.
- company_bp.py should be approximately 440 lines. company_detail() alone is ~225 lines — copy EVERY line.
- dashboard_bp.py should be approximately 200 lines.

**Step 3.3 — Extract company_bp.py**

BEFORE writing any code, run these greps and save the output:
```bash
grep -rn 'url_for("company_detail"' dashboard/
grep -rn "url_for('company_detail'" dashboard/
grep -rn 'url_for("company_detail"' dashboard/templates/
grep -rn "url_for('company_detail'" dashboard/templates/
```

Create dashboard/blueprints/company_bp.py:
- Blueprint("company", __name__)
- Move all 8 routes (REFACTOR_PLAN.md Step 5, section 3) — including mark-portal-submitted
- company_detail() is the most complex route at ~225 lines. Move it COMPLETELY.
- Update EVERY url_for("company_detail") occurrence found by grep → url_for("company.company_detail")
- Check already-extracted blueprints (portal_bp, data_bp, transfers_bp, monitor_bp) for references too
- Register in create_app(). Delete from app.py.

**Step 4.1 — Extract dashboard_bp.py**

Run these greps first:
```bash
grep -rn 'url_for("dashboard"' dashboard/
grep -rn "url_for('dashboard'" dashboard/
```

Create dashboard/blueprints/dashboard_bp.py:
- Blueprint("main", __name__) — name is "main" so url_for becomes url_for("main.dashboard")
- Move dashboard() and cards_listing() (REFACTOR_PLAN.md Step 5, section 2)
- Update EVERY url_for("dashboard") → url_for("main.dashboard")
- Check base.html navbar — the Home link almost certainly uses url_for("dashboard")
- Register in create_app(). Delete from app.py.

After both, run:
- pytest tests/unit/ -q (expect 655 passed, 1 known failure)
- wc -l dashboard/blueprints/company_bp.py (should be ~440)
- wc -l dashboard/blueprints/dashboard_bp.py (should be ~200)
- wc -l dashboard/app.py

Then verify no bare url_for references remain:
```bash
grep -rn 'url_for("company_detail"' dashboard/
grep -rn "url_for('company_detail'" dashboard/
grep -rn 'url_for("dashboard"' dashboard/
grep -rn "url_for('dashboard'" dashboard/
```
All of these should return ZERO results (except possibly inside create_app Blueprint registration).

- /find-placeholders

Report all results including the grep output.
```

### After Claude Code finishes — verify the greps yourself:

This is the one where a missed `url_for` breaks links. Check the grep results Claude shows you. If any bare references remain, tell it to fix them.

```
/find-placeholders
Update CLAUDE.md and ARCHITECTURE.md.
```

### Exit Claude Code, merge in Terminal 2:

```bash
# Terminal 2:
cd /Users/nemasha/MyProjects/gdpr-agent-company-dash
git add -A
git commit -m "Extract company + dashboard Blueprints" --no-verify

cd /Users/nemasha/MyProjects/gdpr-agent
git merge feature/company-dashboard-blueprints
pytest tests/unit/ -q
git worktree remove /Users/nemasha/MyProjects/gdpr-agent-company-dash
```

---

## STAGE 7: Worktree 6 — Pipeline ⚠️ HIGHEST RISK

**Where:** worktree

### Create worktree in Terminal 2:

```bash
# Terminal 2:
cd /Users/nemasha/MyProjects/gdpr-agent
git worktree add /Users/nemasha/MyProjects/gdpr-agent-pipeline -b feature/pipeline-blueprint
```

### Launch Claude Code in Terminal 1:

```bash
# Terminal 1:
cd /Users/nemasha/MyProjects/gdpr-agent-pipeline
source /Users/nemasha/MyProjects/gdpr-agent/.venv/bin/activate
claude
```

### Paste this prompt:

```
Before running any command, always activate the virtualenv:
source /Users/nemasha/MyProjects/gdpr-agent/.venv/bin/activate

Read REFACTOR_PLAN.md Step 5 section 9 (pipeline_bp) and Step 8 (Risk Register) VERY carefully. Also read IMPLEMENTATION_GUIDE.md Step 4.2.

CRITICAL CODE RULES:
- NEVER write placeholder comments. NEVER truncate function bodies.
- pipeline_bp.py should be approximately 750 lines. This is the LARGEST Blueprint.
- _sync_scan_state_flags() is 107 lines — move ALL 107 lines intact, do not summarize, do not abbreviate.
- If pipeline_bp.py is under 500 lines, you have dropped code. Go back and verify every function.

**Step 4.2 — Extract pipeline_bp.py**

Create dashboard/blueprints/pipeline_bp.py:
- Blueprint("pipeline", __name__, url_prefix="/pipeline")
- Move ALL 9 routes (REFACTOR_PLAN.md Step 5, section 9 has the complete list)
- Move these pipeline-only functions INTACT:
  - _sync_scan_state_flags() — all 107 lines, do NOT refactor internals
  - _token_exists()
  - _send_token_valid()
  - _CONFIDENCE_RANK constant
  - Background tasks: _do_scan(), _do_resolve(), _do_send() — each one in FULL

CRITICAL THREAD SAFETY AUDIT: All three background tasks (_do_scan, _do_resolve, _do_send) must not call g-dependent functions inside the thread:
- _current_data_dir() inside a thread? → BUG
- _current_tokens_dir() inside a thread? → BUG
- current_user inside a thread? → BUG
Capture all values in the route handler BEFORE Thread.start(), pass as args.

Update url_for references (should mostly be in pipeline templates — verify with grep).
Register in create_app(). Delete from app.py.

After this, app.py should be approximately 20 lines or less:
```python
from dashboard import create_app
app = create_app()
if __name__ == "__main__":
    app.run(debug=True)
```

If anything else remains in app.py besides this pattern, identify where it belongs and move it.

Run:
- pytest tests/unit/ -q (expect 655 passed, 1 known failure)
- wc -l dashboard/blueprints/pipeline_bp.py (should be ~750)
- cat dashboard/app.py (show me the FULL contents — it should be tiny now)
- /find-placeholders
- /audit-errors

Report all results.
```

### After Claude Code finishes — review carefully:

Check:
1. Is `pipeline_bp.py` around 750 lines?
2. Is `app.py` now ~20 lines?
3. Did 655 tests pass?

```
/find-placeholders
/audit-errors
Update CLAUDE.md and ARCHITECTURE.md with the final architecture — document every Blueprint, the services layer, and the new project structure.
```

### Exit Claude Code, merge in Terminal 2:

```bash
# Terminal 2:
cd /Users/nemasha/MyProjects/gdpr-agent-pipeline
git add -A
git commit -m "Extract pipeline Blueprint — refactoring complete" --no-verify

cd /Users/nemasha/MyProjects/gdpr-agent
git merge feature/pipeline-blueprint
pytest tests/unit/ -q
git worktree remove /Users/nemasha/MyProjects/gdpr-agent-pipeline
```

---

## STAGE 8: Final Cleanup

**Where:** directly on main

### Launch Claude Code in Terminal 1:

```bash
# Terminal 1:
cd /Users/nemasha/MyProjects/gdpr-agent
source .venv/bin/activate
claude
```

### Paste this prompt:

```
Before running any command, always activate the virtualenv:
source /Users/nemasha/MyProjects/gdpr-agent/.venv/bin/activate

Read IMPLEMENTATION_GUIDE.md Steps 5.1 through 5.4.

Final cleanup after all Blueprint extractions.

**Step 5.1 — Audit ALL templates for bare url_for()**

Run:
```bash
grep -rn 'url_for(' dashboard/templates/ | grep -vE '\.(main|company|data|monitor|costs|transfers|portal|pipeline|api|settings|auth|admin)\.' | grep -v 'static'
```
Any results are bugs — url_for references that weren't updated to include a Blueprint prefix. Fix every one found.

**Step 5.2 — Check test imports**

Run:
```bash
grep -rn 'from dashboard.app import' tests/
grep -rn 'import dashboard.app' tests/
```
Update any that reference moved functions:
- _clean_snippet, _dedup_reply_rows, _is_human_friendly → import from dashboard.shared
- _lookup_company → import from dashboard.shared
- app → from dashboard import create_app (update test setup accordingly)

**Step 5.3 — Final validation**

Run:
- /find-placeholders
- /audit-errors
- /check-secrets
- pytest tests/unit/ -q (expect 655 passed, 1 known failure)

**Step 5.4 — Report final stats**

```bash
echo "=== app.py ==="
wc -l dashboard/app.py
echo "=== shared.py ==="
wc -l dashboard/shared.py
echo "=== blueprints ==="
wc -l dashboard/blueprints/*.py
echo "=== services ==="
wc -l dashboard/services/monitor_runner.py
echo "=== total ==="
find dashboard/blueprints dashboard/services dashboard/shared.py dashboard/app.py -name '*.py' | xargs wc -l | tail -1
```

Show me all results.
```

### After Claude Code finishes:

```
Update CLAUDE.md and ARCHITECTURE.md with final line counts and the complete project structure.
```

### Exit Claude Code, final commit in Terminal 2:

```bash
# Terminal 2:
cd /Users/nemasha/MyProjects/gdpr-agent
git add -A
git commit -m "Phase 5: final cleanup, Blueprint refactoring complete" --no-verify
```

---

## Quick Reference Card

| Stage | What | Where | Worktree name | Key risk |
|-------|------|-------|---------------|----------|
| 1 | Phase 0: shared.py + factory | main | — | Getting imports right |
| 2 | Leaf Blueprints (costs, settings, api) | worktree | gdpr-agent-leaf-blueprints | First extraction pattern |
| 3 | Monitor unification + Blueprint | worktree | gdpr-agent-monitor | Bug fix, logic correctness |
| 4 | Data Blueprint | worktree | gdpr-agent-data | Template cross-refs |
| 5 | Portal + Transfers | worktree | gdpr-agent-portal-transfers | Thread safety |
| 6 | Company + Dashboard | worktree | gdpr-agent-company-dash | ~25 url_for updates |
| 7 | Pipeline | worktree | gdpr-agent-pipeline | 750 lines, 3 bg tasks |
| 8 | Final cleanup | main | — | Thoroughness |

**Every stage:** Opus, High effort, 70% compact window, fresh Claude Code session.

**Every stage ends with:** /find-placeholders → CLAUDE.md update → exit Claude Code → commit in Terminal 2 → merge if worktree → pytest.

---

## If Something Goes Wrong

**Tests fail after merge:**
```bash
# See what the merge broke
pytest tests/unit/ -q --tb=short
# If it's a simple import error, fix in Terminal 2 and amend the commit
# If it's complex, open Claude Code and ask it to fix the specific error
```

**Worktree merge has conflicts:**
Almost always in app.py where two branches deleted different functions. Accept both deletions.
```bash
git merge feature/xxx
# If conflict:
git diff --name-only --diff-filter=U    # see conflicted files
# Edit the file, accept both deletions
git add dashboard/app.py
git commit
```

**Claude Code truncated a function (placeholder found):**
Don't accept the work. In Claude Code, tell it:
```
/find-placeholders found issues. The function X in file Y is truncated.
Go back to dashboard/app.py, find the complete function, and copy it
in full to the Blueprint file. Every line.
```

**Need to abandon a worktree:**
```bash
cd /Users/nemasha/MyProjects/gdpr-agent
git worktree remove /Users/nemasha/MyProjects/gdpr-agent-xxx --force
git branch -D feature/xxx
```
Then restart that stage from scratch.

**Got disconnected mid-stage:**
```bash
cd /Users/nemasha/MyProjects/gdpr-agent-xxx   # the worktree
git status                                      # see what was done
git diff --stat                                 # see what changed
```
If most of the work was done, commit it and continue manually. If it's a mess, abandon and restart.
