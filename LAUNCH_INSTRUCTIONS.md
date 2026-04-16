# Exact Launch Instructions

---

## Step 1: Put the reference files in your repo

Download all three files from this conversation and copy them into your repo root.

```bash
cd /Users/nemasha/MyProjects/gdpr-agent

cp ~/Downloads/REFACTOR_PLAN.md .
cp ~/Downloads/IMPLEMENTATION_GUIDE.md .
cp ~/Downloads/ORCHESTRATION_PLAYBOOK.md .

git add REFACTOR_PLAN.md IMPLEMENTATION_GUIDE.md ORCHESTRATION_PLAYBOOK.md
git commit -m "docs: add refactoring reference documents"
```

If the files landed somewhere other than Downloads, adjust the `cp` paths — but they must end up in `/Users/nemasha/MyProjects/gdpr-agent/`.

---

## Step 2: Verify your Claude Code setup

```bash
cd /Users/nemasha/MyProjects/gdpr-agent
claude config list
```

Confirm you see:
- `model`: `claude-opus-4-20250514` (or however your Opus default shows)
- `skipDangerousModePermissionPrompt`: `true`

If model isn't Opus:
```bash
claude config set model claude-opus-4-20250514
```

---

## Step 3: Make sure main is clean

```bash
cd /Users/nemasha/MyProjects/gdpr-agent
git status
```

If anything is uncommitted, commit or stash it now. Everything branches from this point.

```bash
pytest tests/unit/ -q
```

Note the number of passing tests. Write it down. This is your baseline.

---

## Step 4: Launch Claude Code

```bash
cd /Users/nemasha/MyProjects/gdpr-agent
claude
```

---

## Step 5: Paste the master prompt

Copy everything between the two `---PROMPT START---` and `---PROMPT END---` markers below. Paste it into Claude Code as a single message.

---PROMPT START---

Read these three files before doing anything:
- REFACTOR_PLAN.md
- IMPLEMENTATION_GUIDE.md
- ORCHESTRATION_PLAYBOOK.md

You are orchestrating a full refactoring of dashboard/app.py into Flask Blueprints. You will work through 8 stages sequentially. For each stage you will do the work, validate it, and either continue or stop at a checkpoint for my review.

Rules that apply to EVERY stage:
- Never change logic — only move code, unless explicitly told to fix a bug
- After every stage, run: pytest tests/unit/ -q
- After every stage, run: wc -l dashboard/app.py (it should shrink each time)
- If pytest fails, stop and tell me what failed. Do not continue.
- Commit after each stage with a descriptive message
- Use the exact function lists and line numbers from REFACTOR_PLAN.md

CRITICAL — NO PLACEHOLDERS, NO SHORTCUTS, NO ABBREVIATIONS IN CODE:
- NEVER write `# ... rest of the code`, `# existing code here`, `# same as before`, `# remaining implementation`, `# TODO`, or any similar placeholder comment
- NEVER truncate a function body. Every function you move must be copied in FULL — every single line, every condition, every edge case. If a function is 225 lines (like company_detail), you write all 225 lines.
- NEVER use `pass` as a stand-in for real implementation
- NEVER summarize code with comments like `# handle the other cases similarly`
- NEVER skip imports — every file must have complete, working imports at the top
- When you create a new Blueprint file, it must be immediately runnable — not a skeleton that needs filling in later
- If you are moving a function and feel tempted to abbreviate it, that is your signal to copy it completely instead
- After creating each new file, verify it by running: python -c "import dashboard.blueprints.XXXX" (replace XXXX) to confirm it parses without errors
- The line count of all new files combined should approximately equal the line count removed from app.py. If it doesn't, you've dropped code somewhere.

VALIDATION after every stage — run all of these before committing:
```bash
# 1. Tests pass
pytest tests/unit/ -q

# 2. app.py is shrinking
wc -l dashboard/app.py

# 3. New files are substantial (not stubs)
wc -l dashboard/shared.py dashboard/blueprints/*.py dashboard/services/*.py 2>/dev/null

# 4. Placeholder scan — run the slash command:
/find-placeholders

# 5. Every new Python file imports cleanly
find dashboard/blueprints dashboard/services -name '*.py' ! -name '__init__.py' -exec python -c "import importlib, sys; sys.path.insert(0,'.'); importlib.import_module('{}'.replace('/','.')replace('.py',''))" \;
```
If /find-placeholders finds ANYTHING, fix it immediately — replace with the actual complete code from app.py. Do not commit until /find-placeholders is clean.

SLASH COMMANDS — use these at the right moments:
- `/find-placeholders` — run after completing EVERY stage, before committing. Non-negotiable.
- `/audit-errors` — run after stages that involve network calls, file I/O, or background threads. That means: Stage 3 (monitor), Stage 5 (portal + transfers), Stage 7 (pipeline).
- `/check-secrets` — run before every merge back to main.
- `/pre-merge` — run before every merge back to main, after /check-secrets.

The full sequence before each merge is:
1. /find-placeholders → fix anything found
2. pytest tests/unit/ -q → must be green
3. /audit-errors (if applicable to this stage)
4. git add -A && git commit
5. cd /Users/nemasha/MyProjects/gdpr-agent
6. /check-secrets
7. /pre-merge
8. git merge <branch>

---

STAGE 1: Phase 0 — Preparation (do directly, no worktree)

Step 0.1: Create dashboard/shared.py

Move ALL shared helpers, constants, and context processor from app.py into dashboard/shared.py.

The exact inventory is in REFACTOR_PLAN.md Step 4 "Shared Utilities Inventory" — use the "Helper Functions (used by multiple route groups)" table and "Constants (used by multiple route groups)" table as your checklist. Also move the context processor _inject_globals() and template filter _flag_emoji_filter().

For each item: copy to shared.py with its imports, then replace in app.py with an import from dashboard.shared.

Do not change any function bodies. This is a pure extraction.

Update any test files that import moved functions from dashboard.app — change to import from dashboard.shared.

Run pytest. Run wc -l dashboard/app.py.

Size check: shared.py should be approximately 350 lines. If it's under 250, you've missed functions from the inventory. Go back to REFACTOR_PLAN.md Step 4 and check every row in both tables.

Step 0.2: Create app factory in dashboard/__init__.py

Add create_app() that:
- Creates Flask app
- Configures secret key
- Sets up LoginManager and login_user_loader
- Sets up before_request hook
- Registers auth_bp and admin_bp
- Registers the context processor from shared.py
- Returns app

Shrink app.py to:
```python
from dashboard import create_app
app = create_app()
if __name__ == "__main__":
    app.run(debug=True)
```

Keep `app` importable from dashboard.app (tests need this).

Run the full validation sequence:
1. /find-placeholders — fix anything found
2. pytest tests/unit/ -q
3. wc -l dashboard/app.py
4. wc -l dashboard/shared.py

Commit: "Phase 0: extract shared.py + app factory"

Tell me the test count, app.py line count, and shared.py line count, then CONTINUE to Stage 2.

---

STAGE 2: Worktree 1 — Leaf Blueprints

```bash
git worktree add /Users/nemasha/MyProjects/gdpr-agent-leaf-blueprints -b feature/leaf-blueprints
```

Work in /Users/nemasha/MyProjects/gdpr-agent-leaf-blueprints/ for this stage.

Create dashboard/blueprints/ directory with empty __init__.py.

Step 1.1: Create dashboard/blueprints/costs_bp.py
- Blueprint("costs", __name__)
- Move costs() route (REFACTOR_PLAN.md Step 5, section 6)
- Only dependency: contact_resolver.cost_tracker.load_persistent_log
- Register in create_app()
- Delete from app.py

Step 1.2: Create dashboard/blueprints/settings_bp.py
- Blueprint("settings", __name__, url_prefix="/settings")
- Move export_data() and delete_account() (REFACTOR_PLAN.md Step 5, section 11)
- Register in create_app()
- Delete from app.py

Step 1.3: Create dashboard/blueprints/api_bp.py
- Blueprint("api", __name__, url_prefix="/api")
- Move api_task(), api_scan_status(), api_body() (REFACTOR_PLAN.md Step 5, section 10)
- Register in create_app()
- Grep templates for url_for("api_task") or url_for('api_task') → update to url_for("api.api_task")
- Also check JS in templates for these references
- Delete from app.py

Run validation in the worktree:
1. /find-placeholders — fix anything found
2. pytest tests/unit/ -q
3. wc -l dashboard/blueprints/costs_bp.py dashboard/blueprints/settings_bp.py dashboard/blueprints/api_bp.py

Commit in the worktree.

Then merge back to main:
```bash
cd /Users/nemasha/MyProjects/gdpr-agent
/check-secrets
/pre-merge
git merge feature/leaf-blueprints
git worktree remove /Users/nemasha/MyProjects/gdpr-agent-leaf-blueprints
pytest tests/unit/ -q
```

Tell me the test count and app.py line count, then CONTINUE to Stage 3.

---

STAGE 3: Worktree 2 — Monitor Unification

```bash
cd /Users/nemasha/MyProjects/gdpr-agent
git worktree add /Users/nemasha/MyProjects/gdpr-agent-monitor -b feature/monitor-unification
```

Work in /Users/nemasha/MyProjects/gdpr-agent-monitor/ for this stage.

Step 2.2: Create dashboard/services/monitor_runner.py

This is the most important step in the entire refactoring. Read REFACTOR_PLAN.md Step 3 (Three-Way Monitor Comparison) and Step 6 (Services Layer Proposal) completely.

Create four functions with these exact signatures (from REFACTOR_PLAN.md Step 6):

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

Start from monitor.py's implementation (the most complete). Include ALL features from the monitor.py column of the comparison table in REFACTOR_PLAN.md Step 3.

FIX THE BUG in run_sar_monitor — add past-attempts dedup:
```python
for pa in state.past_attempts:
    for r in pa.get("replies", []):
        existing_ids.add(r["gmail_message_id"])
```

All paths as explicit arguments. No Flask g dependency. verbose flag controls terminal output.

Then update monitor.py CLI to call these functions instead of its own copy. monitor.py becomes a thin wrapper.

Size check: monitor_runner.py should be approximately 350 lines. If it's under 200, you've almost certainly used placeholders or dropped logic. Go back and compare against monitor.py line by line.

Step 2.3: Create dashboard/blueprints/monitor_bp.py
- Blueprint("monitor", __name__)
- Move refresh() and reextract() routes
- These call monitor_runner functions, passing paths from shared.py helpers
- Move _reextract_missing_links() to this file or to monitor_runner
- Delete _run_monitor_for_account() and _run_subprocessor_monitor_for_account() from app.py
- Register in create_app()

Run validation in the worktree:
1. /find-placeholders — fix anything found
2. /audit-errors — this stage involves file I/O and network calls (Gmail API)
3. pytest tests/unit/ -q
4. wc -l dashboard/services/monitor_runner.py (should be ~350 lines)

Commit in the worktree.

⚠️ CHECKPOINT — STOP HERE. Show me:
1. pytest results
2. The full contents of dashboard/services/monitor_runner.py
3. Confirm the past-attempts dedup fix is present in run_sar_monitor
4. wc -l dashboard/app.py
5. How monitor.py now calls monitor_runner
6. /find-placeholders results (should be clean)

Wait for me to say "continue" before merging.

---

STAGE 4: Worktree 3 — Data Blueprint

After I approve Stage 3, merge and clean up:
```bash
cd /Users/nemasha/MyProjects/gdpr-agent
/check-secrets
/pre-merge
git merge feature/monitor-unification
git worktree remove /Users/nemasha/MyProjects/gdpr-agent-monitor
git worktree add /Users/nemasha/MyProjects/gdpr-agent-data -b feature/data-blueprint
```

Work in /Users/nemasha/MyProjects/gdpr-agent-data/ for this stage.

Step 2.1: Create dashboard/blueprints/data_bp.py
- Blueprint("data", __name__) — no url_prefix
- Move data_card(), scan_folder(), download_data() (REFACTOR_PLAN.md Step 5, section 4)
- Grep templates for url_for("data_card"), url_for("scan_folder"), url_for("download_data") → add "data." prefix
- Check company_detail.html for "View data" links
- Register in create_app()
- Delete from app.py

Run validation in the worktree:
1. /find-placeholders — fix anything found
2. pytest tests/unit/ -q
3. wc -l dashboard/blueprints/data_bp.py (should be ~300 lines)

Commit in the worktree.

Then merge back to main:
```bash
cd /Users/nemasha/MyProjects/gdpr-agent
/check-secrets
/pre-merge
git merge feature/data-blueprint
git worktree remove /Users/nemasha/MyProjects/gdpr-agent-data
pytest tests/unit/ -q
```

Tell me the test count and app.py line count, then CONTINUE to Stage 5.

---

STAGE 5: Worktree 4 — Portal + Transfers

```bash
cd /Users/nemasha/MyProjects/gdpr-agent
git worktree add /Users/nemasha/MyProjects/gdpr-agent-portal-transfers -b feature/portal-transfers
```

Work in /Users/nemasha/MyProjects/gdpr-agent-portal-transfers/ for this stage.

Step 3.1: Create dashboard/blueprints/portal_bp.py
- Blueprint("portal", __name__)
- Move all 5 routes (REFACTOR_PLAN.md Step 5, section 8)
- Move _portal_tasks dict to this file at module level
- AUDIT portal_submit(): find every call to _current_data_dir(), _current_state_path(), _current_tokens_dir(), current_user, or any other Flask g/request-dependent function. If ANY of these are called inside the thread function (after Thread.start), that is a bug. Fix by capturing the value in the route handler and passing it as an argument.
- Update url_for references: captcha_show → portal.captcha_show, etc.
- Register in create_app()
- Delete from app.py

Step 3.2: Create dashboard/blueprints/transfers_bp.py
- Blueprint("transfers", __name__, url_prefix="/transfers")
- Move 5 routes + _fetch_all_subprocessors() + _send_all_disclosure_requests() + is_stale_dict() (REFACTOR_PLAN.md Step 5, section 7)
- SAME AUDIT for background tasks: _fetch_all_subprocessors() and _send_all_disclosure_requests() must not call g-dependent functions inside the thread
- Update url_for references
- Register in create_app()
- Delete from app.py

Size checks: portal_bp.py should be approximately 210 lines, transfers_bp.py approximately 350 lines. If either is significantly smaller, functions were truncated.

Run validation in the worktree:
1. /find-placeholders — fix anything found
2. /audit-errors — this stage has background threads, network calls, and file I/O
3. pytest tests/unit/ -q
4. wc -l dashboard/blueprints/portal_bp.py dashboard/blueprints/transfers_bp.py

Commit in the worktree.

Then merge back to main:
```bash
cd /Users/nemasha/MyProjects/gdpr-agent
/check-secrets
/pre-merge
git merge feature/portal-transfers
git worktree remove /Users/nemasha/MyProjects/gdpr-agent-portal-transfers
pytest tests/unit/ -q
```

Tell me the test count and app.py line count, then CONTINUE to Stage 6.

---

STAGE 6: Worktree 5 — Company + Dashboard

```bash
cd /Users/nemasha/MyProjects/gdpr-agent
git worktree add /Users/nemasha/MyProjects/gdpr-agent-company-dash -b feature/company-dashboard-blueprints
```

Work in /Users/nemasha/MyProjects/gdpr-agent-company-dash/ for this stage.

Step 3.3: Create dashboard/blueprints/company_bp.py
- Blueprint("company", __name__)
- Move all 8 routes (REFACTOR_PLAN.md Step 5, section 3)
- company_detail() is ~225 lines. Copy it COMPLETELY — every line of template context building, every conditional, every variable. Do not abbreviate.
- Size check: company_bp.py should be approximately 440 lines. If it's under 300, you've truncated functions.
- BEFORE moving anything, run these greps and save the output:
  grep -rn 'url_for("company_detail"' dashboard/
  grep -rn "url_for('company_detail'" dashboard/
  grep -rn 'url_for("company_detail"' dashboard/templates/
  grep -rn "url_for('company_detail'" dashboard/templates/
- Update EVERY occurrence to use "company.company_detail"
- Check already-extracted blueprints (portal_bp, data_bp, transfers_bp, monitor_bp) for references too
- Register in create_app()
- Delete from app.py

Step 4.1: Create dashboard/blueprints/dashboard_bp.py
- Blueprint("main", __name__)
- Move dashboard() and cards_listing() (REFACTOR_PLAN.md Step 5, section 2)
- Run these greps:
  grep -rn 'url_for("dashboard"' dashboard/
  grep -rn "url_for('dashboard'" dashboard/
- Update EVERY occurrence to url_for("main.dashboard")
- Check base.html navbar
- Register in create_app()
- Delete from app.py

Run validation in the worktree:
1. /find-placeholders — fix anything found
2. pytest tests/unit/ -q
3. wc -l dashboard/blueprints/company_bp.py dashboard/blueprints/dashboard_bp.py

Commit in the worktree.

⚠️ CHECKPOINT — STOP HERE. Show me:
1. pytest results
2. /find-placeholders results (should be clean)
3. Results of: grep -rn 'url_for("company_detail"' dashboard/ (should be ZERO)
4. Results of: grep -rn "url_for('company_detail'" dashboard/ (should be ZERO)
5. Results of: grep -rn 'url_for("dashboard"' dashboard/ (should be ZERO — except inside create_app registration which is fine)
6. wc -l dashboard/app.py
7. wc -l dashboard/blueprints/company_bp.py (should be ~440)

Wait for me to say "continue" before merging.

---

STAGE 7: Worktree 6 — Pipeline

After I approve Stage 6, merge and clean up:
```bash
cd /Users/nemasha/MyProjects/gdpr-agent
/check-secrets
/pre-merge
git merge feature/company-dashboard-blueprints
git worktree remove /Users/nemasha/MyProjects/gdpr-agent-company-dash
git worktree add /Users/nemasha/MyProjects/gdpr-agent-pipeline -b feature/pipeline-blueprint
```

Work in /Users/nemasha/MyProjects/gdpr-agent-pipeline/ for this stage.

Step 4.2: Create dashboard/blueprints/pipeline_bp.py
- Blueprint("pipeline", __name__, url_prefix="/pipeline")
- Move all 9 routes (REFACTOR_PLAN.md Step 5, section 9)
- Move _sync_scan_state_flags() INTACT — all 107 lines, do not refactor its internals, do not summarize, do not abbreviate
- Move _token_exists(), _send_token_valid(), _CONFIDENCE_RANK
- Move background tasks: _do_scan(), _do_resolve(), _do_send() — each one in full
- AUDIT all three background tasks for g-dependent calls inside threads. Fix any found.
- Update url_for references (should be mostly in pipeline templates)
- Register in create_app()
- Delete from app.py
- Size check: pipeline_bp.py should be approximately 750 lines. This is the largest Blueprint. If it's under 500, you've dropped code. Go back and verify every function was moved completely.

After this, app.py should be approximately:
```python
from dashboard import create_app
app = create_app()
if __name__ == "__main__":
    app.run(debug=True)
```

If anything else remains in app.py, identify where it belongs and move it.

Run validation in the worktree:
1. /find-placeholders — fix anything found
2. /audit-errors — this stage has background threads, network calls, and file I/O
3. pytest tests/unit/ -q
4. wc -l dashboard/blueprints/pipeline_bp.py (should be ~750)

Commit in the worktree.

⚠️ CHECKPOINT — STOP HERE. Show me:
1. pytest results
2. /find-placeholders results (should be clean)
3. /audit-errors results
4. cat dashboard/app.py (full contents — should be ~20 lines or less)
5. ls -la dashboard/blueprints/
6. wc -l dashboard/blueprints/*.py dashboard/services/monitor_runner.py dashboard/shared.py

Wait for me to say "continue" before merging.

---

STAGE 8: Phase 5 — Final Cleanup

After I approve Stage 7, merge and clean up:
```bash
cd /Users/nemasha/MyProjects/gdpr-agent
/check-secrets
/pre-merge
git merge feature/pipeline-blueprint
git worktree remove /Users/nemasha/MyProjects/gdpr-agent-pipeline
```

Step 5.1: Run this and fix any bare url_for references:
```bash
grep -rn 'url_for(' dashboard/templates/ | grep -vE '\.(main|company|data|monitor|costs|transfers|portal|pipeline|api|settings|auth|admin)\.' | grep -v 'static'
```
Any results (except static file references) are bugs — fix them.

Step 5.2: Check test imports:
```bash
grep -rn 'from dashboard.app import' tests/
grep -rn 'import dashboard.app' tests/
```
Update any that reference moved functions to import from dashboard.shared or dashboard instead.

Step 5.3: Run the full final validation:
1. /find-placeholders — one last sweep across everything
2. /audit-errors — final sweep for error handling gaps
3. /check-secrets — final sweep before the last commit
4. pytest tests/unit/ -q

Step 5.4: Show me final stats:
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

Commit: "Phase 5: final cleanup, refactoring complete"

Update CLAUDE.md and ARCHITECTURE.md with the final architecture.

Done. Report everything to me.

---PROMPT END---

---

## Step 6: What happens next

After you paste the prompt, Claude Code starts working through Stage 1.

It runs autonomously through Stages 1 and 2, then **stops at the first checkpoint** (after Stage 3 — the monitor unification). You'll see it show you the pytest results, the new monitor_runner.py file, and the dedup fix.

**At each checkpoint, what to do:**

Look at what Claude Code is showing you. Check:
- Did pytest pass with the same count as your baseline?
- Does the code look reasonable?
- For Stage 3 specifically: is the dedup fix actually there?

If everything looks good, type:

```
continue
```

If something looks wrong, describe the problem and Claude Code will fix it before proceeding.

**There are exactly 3 checkpoints** where you need to respond:
1. After Stage 3 (monitor unification) — review monitor_runner.py
2. After Stage 6 (company + dashboard) — review url_for grep results
3. After Stage 7 (pipeline) — review final app.py

Everything else is automatic.

---

## Step 7: After it's all done

Once Stage 8 completes and Claude Code reports the final stats:

1. Start your app and click through every page
2. Try triggering a monitor refresh
3. Try the pipeline flow if you have a test Gmail account
4. Check browser console for JavaScript errors

If everything works, you're done. The 3,099-line god module is now ~14 focused files.

---

## If you get disconnected

Claude Code sessions can time out or disconnect on long operations. If this happens:

1. Check which stage completed last:
```bash
cd /Users/nemasha/MyProjects/gdpr-agent
git log --oneline -10
```

2. Check for any open worktrees:
```bash
git worktree list
```

3. Open a new Claude Code session:
```bash
cd /Users/nemasha/MyProjects/gdpr-agent
claude
```

And paste:
```
Read REFACTOR_PLAN.md, IMPLEMENTATION_GUIDE.md, and ORCHESTRATION_PLAYBOOK.md.

We were in the middle of the refactoring orchestration. Check git log
to see which stages are done. Check git worktree list for any open
worktrees. Resume from wherever we left off, following the same
stage-by-stage process described in ORCHESTRATION_PLAYBOOK.md.
```

Claude Code will pick up where things stopped.

---

## Emergency rollback

If the whole thing goes sideways and you want to start over:

```bash
cd /Users/nemasha/MyProjects/gdpr-agent

# Remove all worktrees
git worktree list | grep -v 'bare' | tail -n +2 | awk '{print $1}' | xargs -I{} git worktree remove {} --force

# Delete all feature branches
git branch | grep 'feature/' | xargs git branch -D

# Reset main to before the refactoring
git log --oneline -20   # find the commit before "Phase 0"
git reset --hard <that-commit-hash>
```

Nothing is lost — your original app.py is in git history.
