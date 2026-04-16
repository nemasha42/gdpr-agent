# Orchestrated Refactoring: Master Prompt & Approach

## How This Works

You give Claude Code **one master prompt**. It drives through all phases
sequentially, using subagents (via `claude -p` CLI) for each worktree
extraction. The master session acts as an orchestrator — it creates
worktrees, delegates the actual coding to subagents, runs tests, merges,
and moves on.

### Why subagents?

Each worktree extraction is a self-contained task: read the plan, move
code, update imports, register Blueprint. A subagent gets a focused prompt,
does the work, and exits. The master session then validates the result
(runs tests, checks line counts, greps for leftover issues) before merging.

This is better than doing everything in one long session because:
- Each subagent starts fresh — no context window bloat from earlier phases
- If a subagent makes a mess, the master can discard the worktree and retry
- The master session stays focused on orchestration and validation

### Architecture

```
Master Claude Code session (orchestrator)
  │
  ├─ Phase 0: works directly on main (no subagent — small enough)
  │
  ├─ Worktree 1: claude -p "..." in ../gdpr-agent-leaf-blueprints/
  │   └─ validates → merges → removes worktree
  │
  ├─ Worktree 2: claude -p "..." in ../gdpr-agent-monitor/
  │   └─ validates → ⚠️ CHECKPOINT → merges → removes worktree
  │
  ├─ Worktree 3: claude -p "..." in ../gdpr-agent-data/
  │   └─ validates → merges → removes worktree
  │
  ├─ Worktree 4: claude -p "..." in ../gdpr-agent-portal-transfers/
  │   └─ validates → merges → removes worktree
  │
  ├─ Worktree 5: claude -p "..." in ../gdpr-agent-company-dash/
  │   └─ validates → ⚠️ CHECKPOINT → merges → removes worktree
  │
  ├─ Worktree 6: claude -p "..." in ../gdpr-agent-pipeline/
  │   └─ validates → ⚠️ CHECKPOINT → merges → removes worktree
  │
  └─ Phase 5: cleanup directly on main
```

### Checkpoints

Three mandatory stop-and-report moments where the orchestrator
pauses and tells you what happened before proceeding:

1. **After Worktree 2** (monitor unification) — this changes logic,
   not just structure. You should review the unified monitor_runner.py
   and confirm the dedup fix is correct.

2. **After Worktree 5** (company + dashboard) — highest url_for
   breakage surface. The orchestrator runs grep validation, but a
   quick manual click-through of the dashboard and a company page
   would catch anything grep misses.

3. **After Worktree 6** (pipeline) — app.py should now be ~20 lines.
   You should verify the pipeline flow works before final cleanup.

Between all other worktrees, the orchestrator auto-merges if tests pass.

---

## The Master Prompt

Copy this entire block and paste it into Claude Code as a single prompt.

```
I need you to orchestrate the full gdpr-agent refactoring described in
REFACTOR_PLAN.md and IMPLEMENTATION_GUIDE.md. You are the orchestrator.
You will work through each phase sequentially, using subagents for the
worktree extractions.

## Your workflow for each worktree:

1. Create the git worktree and branch
2. Use `claude -p` with the `--model claude-opus-4-20250514` flag to spawn
   a subagent in that worktree directory, passing it a detailed prompt
3. After the subagent completes, validate the result:
   a. Run `pytest tests/unit/ -q` in the worktree
   b. Run validation greps (check for orphaned url_for, check app.py
      line count is shrinking, check new Blueprint files exist)
   c. If tests fail: report the failure and STOP
   d. If tests pass: merge to main, remove worktree, continue
4. At checkpoints: report what was done and STOP for my review

## Validation commands to run after EVERY worktree merge:

```bash
# Tests must pass
pytest tests/unit/ -q

# No bare url_for references to extracted routes (add to this list as
# you extract each Blueprint)
grep -rn 'url_for("dashboard"' dashboard/templates/ dashboard/blueprints/ || echo "OK: no bare dashboard refs"
grep -rn 'url_for("company_detail"' dashboard/templates/ dashboard/blueprints/ || echo "OK: no bare company_detail refs"

# app.py is shrinking
wc -l dashboard/app.py

# New files exist
ls -la dashboard/blueprints/
ls -la dashboard/services/
```

## Phase 0 — Do directly on main (no subagent needed, small enough)

Do Steps 0.1 and 0.2 from IMPLEMENTATION_GUIDE.md yourself:

Step 0.1: Create dashboard/shared.py. Move all shared helpers, constants,
and context processor from app.py. The complete list is in REFACTOR_PLAN.md
Step 4 and Step 5 (shared.py section). In app.py, replace moved items with
imports from shared.py. Update any test imports that reference moved functions.

Step 0.2: Create app factory in dashboard/__init__.py. Add create_app()
that sets up Flask, login manager, before_request, registers existing
blueprints. Shrink app.py to ~5 lines: import create_app, call it,
if __name__ block.

Run tests. Commit: "Phase 0: extract shared.py + app factory"

---

## Worktree 1 — feature/leaf-blueprints

```bash
git worktree add ../gdpr-agent-leaf-blueprints -b feature/leaf-blueprints
```

Subagent prompt (run from the worktree directory):

"Read REFACTOR_PLAN.md Step 5 sections 6, 10, 11 and IMPLEMENTATION_GUIDE.md
Steps 1.1, 1.2, 1.3.

Create dashboard/blueprints/ directory with __init__.py.

Extract three leaf Blueprints:

1. dashboard/blueprints/costs_bp.py — Blueprint('costs', __name__), move
   costs() route (~line 1795). Only dep: cost_tracker.load_persistent_log.
   Register in create_app().

2. dashboard/blueprints/settings_bp.py — Blueprint('settings', __name__,
   url_prefix='/settings'), move export_data() and delete_account().
   Register in create_app().

3. dashboard/blueprints/api_bp.py — Blueprint('api', __name__,
   url_prefix='/api'), move api_task(), api_scan_status(), api_body().
   Register in create_app(). Grep templates and JS for url_for('api_task')
   → update to url_for('api.api_task').

Delete moved functions from app.py. Run pytest tests/unit/ -q.
Update CLAUDE.md and ARCHITECTURE.md."

After subagent completes: validate, merge, remove worktree. Continue.

---

## Worktree 2 — feature/monitor-unification ⚠️ CHECKPOINT AFTER

```bash
git worktree add ../gdpr-agent-monitor -b feature/monitor-unification
```

Subagent prompt:

"Read REFACTOR_PLAN.md Step 3 (Three-Way Monitor Comparison) and Step 6
(Services Layer) very carefully. Also IMPLEMENTATION_GUIDE.md Steps 2.2, 2.3.

Step 2.2: Create dashboard/services/monitor_runner.py with unified monitor.

Start from monitor.py (lines 85–326) — it's the most complete version.
Four functions: run_sar_monitor(), run_sp_monitor(), auto_download_data_links(),
auto_analyze_inline_data(). Signatures in REFACTOR_PLAN.md Step 6.

Requirements:
- ALL paths as explicit args — no Flask g
- verbose flag (True=CLI, False=web)
- FIX THE BUG: past-attempts dedup:
  for pa in state.past_attempts:
      for r in pa.get('replies', []):
          existing_ids.add(r['gmail_message_id'])
- Include all features from monitor.py column in the comparison table

Update monitor.py CLI to call monitor_runner instead of its own logic.

Step 2.3: Create dashboard/blueprints/monitor_bp.py.
Move refresh() and reextract() routes. Wire them to monitor_runner functions.
Delete old _run_monitor_for_account() and _run_subprocessor_monitor_for_account()
from app.py. Register Blueprint. Run pytest. Update CLAUDE.md, ARCHITECTURE.md."

After subagent completes: validate and run tests.

**⚠️ CHECKPOINT: STOP HERE.** Report to me:
- Test results
- The contents of dashboard/services/monitor_runner.py (show me the file)
- Confirm the dedup fix is present
- Line count of app.py (should have shrunk significantly)

Wait for my approval before merging.

---

## Worktree 3 — feature/data-blueprint

```bash
git worktree add ../gdpr-agent-data -b feature/data-blueprint
```

Subagent prompt:

"Read REFACTOR_PLAN.md Step 5 section 4 and IMPLEMENTATION_GUIDE.md Step 2.1.

Create dashboard/blueprints/data_bp.py — Blueprint('data', __name__), no
url_prefix. Move data_card() (~line 1042), scan_folder() (~line 1252),
download_data() (~line 1339). Deps from shared.py: _get_accounts(),
_load_all_states(), _lookup_company(), _current_state_path(). Grep templates
for url_for('data_card'), url_for('scan_folder'), url_for('download_data')
→ prefix with 'data.'. Check company_detail.html for View data links.
Register in create_app(). Delete from app.py. Run pytest.
Update CLAUDE.md, ARCHITECTURE.md."

After subagent completes: validate, merge, remove worktree. Continue.

---

## Worktree 4 — feature/portal-transfers

```bash
git worktree add ../gdpr-agent-portal-transfers -b feature/portal-transfers
```

Subagent prompt:

"Read REFACTOR_PLAN.md Step 5 sections 7 and 8, and IMPLEMENTATION_GUIDE.md
Steps 3.1, 3.2.

Step 3.1: Create dashboard/blueprints/portal_bp.py — Blueprint('portal',
__name__). Move 5 routes + _portal_tasks dict (module-level, single import
point). CRITICAL: in portal_submit(), all g-dependent calls must happen
BEFORE Thread.start(). Capture paths, pass as args to thread function.
Update url_for('captcha_show') → url_for('portal.captcha_show').

Step 3.2: Create dashboard/blueprints/transfers_bp.py — Blueprint('transfers',
__name__, url_prefix='/transfers'). Move 5 routes + _fetch_all_subprocessors(),
_send_all_disclosure_requests(), is_stale_dict(). Same thread-context rule:
verify all paths captured before Thread.start(). Update url_for references.

Register both in create_app(). Delete from app.py. Run pytest.
Update CLAUDE.md, ARCHITECTURE.md."

After subagent completes: validate, merge, remove worktree. Continue.

---

## Worktree 5 — feature/company-dashboard-blueprints ⚠️ CHECKPOINT AFTER

```bash
git worktree add ../gdpr-agent-company-dash -b feature/company-dashboard-blueprints
```

Subagent prompt:

"Read REFACTOR_PLAN.md Step 5 sections 2 and 3, plus the url_for() Impact
table. IMPLEMENTATION_GUIDE.md Steps 3.3, 4.1.

Step 3.3: Create dashboard/blueprints/company_bp.py — Blueprint('company',
__name__). Move all 8 routes including mark-portal-submitted. company_detail()
is ~225 lines, move carefully. CRITICAL: grep entire codebase for
url_for('company_detail') and url_for(\"company_detail\") — ~15 occurrences.
Update ALL to url_for('company.company_detail'). Check already-extracted
blueprints too.

Step 4.1: Create dashboard/blueprints/dashboard_bp.py — Blueprint('main',
__name__). Move dashboard() and cards_listing(). Grep for url_for('dashboard')
— ~10 occurrences. Update to url_for('main.dashboard'). Check base.html navbar.

Register both. Delete from app.py. Run pytest.
Update CLAUDE.md, ARCHITECTURE.md."

After subagent completes: validate.

**⚠️ CHECKPOINT: STOP HERE.** Report to me:
- Test results
- Results of: grep -rn 'url_for("company_detail"' dashboard/ (should find ZERO bare refs)
- Results of: grep -rn 'url_for("dashboard"' dashboard/ (should find ZERO bare refs)
- Line count of app.py

Wait for my approval before merging.

---

## Worktree 6 — feature/pipeline-blueprint ⚠️ CHECKPOINT AFTER

```bash
git worktree add ../gdpr-agent-pipeline -b feature/pipeline-blueprint
```

Subagent prompt:

"Read REFACTOR_PLAN.md Step 5 section 9 and Step 8 (Risk Register) very
carefully. IMPLEMENTATION_GUIDE.md Step 4.2.

Create dashboard/blueprints/pipeline_bp.py — Blueprint('pipeline', __name__,
url_prefix='/pipeline'). Move all 9 routes + _sync_scan_state_flags() (107
lines, move intact, don't refactor internals) + _token_exists() +
_send_token_valid() + _CONFIDENCE_RANK + background tasks _do_scan(),
_do_resolve(), _do_send().

CRITICAL: Audit all three background tasks. Every call to _current_data_dir(),
_current_tokens_dir(), current_user, or any Flask g-dependent function inside
a thread function is a bug. Capture in the route handler, pass as args.

app.py should be ~20 lines after this. If anything is left besides
create_app import and if __name__, identify where it belongs and move it.

Update url_for('pipeline') → url_for('pipeline.pipeline') etc. These should
mostly be in pipeline templates. Verify with grep.

Register. Delete from app.py. Run pytest. Update CLAUDE.md, ARCHITECTURE.md."

After subagent completes: validate.

**⚠️ CHECKPOINT: STOP HERE.** Report to me:
- Test results
- Full contents of app.py (should be ~20 lines)
- Line count of every file in dashboard/blueprints/
- Any functions still in app.py that shouldn't be

Wait for my approval before merging.

---

## Phase 5 — Cleanup on main

After final merge, do this yourself (no subagent needed):

1. Run: grep -rn 'url_for(' dashboard/templates/ | grep -v '\.' | head -20
   (finds any url_for without a Blueprint prefix — should be zero)

2. Update test imports per IMPLEMENTATION_GUIDE.md Step 5.2:
   - test_snippet_clean.py → from dashboard.shared
   - test_portal_submit_route.py → from dashboard.shared
   - test_api_body.py → from dashboard import create_app
   - test_dashboard.py → update module import

3. Run pytest one final time.

4. Report final stats:
   - wc -l dashboard/app.py
   - wc -l dashboard/shared.py
   - wc -l dashboard/blueprints/*.py
   - wc -l dashboard/services/monitor_runner.py

Commit: "Phase 5: final cleanup, all Blueprint extractions complete"

Done. The refactoring is complete.
```

---

## How to launch

Paste the master prompt above into Claude Code in your gdpr-agent repo root.
Make sure REFACTOR_PLAN.md and IMPLEMENTATION_GUIDE.md are both in the repo.

The orchestrator will work through everything and stop at the three
checkpoints for your review. Total hands-on time for you: ~15 minutes
across three review points, instead of hours of manual driving.

---

## If something goes wrong

The orchestrator validates after each worktree. If tests fail:

- **In worktrees 1 or 3** (low risk): likely a missed import. Tell the
  orchestrator to fix it and re-run tests.
- **In worktree 2** (monitor): the subagent may have gotten the unification
  wrong. Review the diff, provide specific corrections.
- **In worktrees 4–6** (thread context): likely a g-in-thread bug. Tell the
  orchestrator which function has the issue.
- **Merge conflict**: the orchestrator should show you the conflict. For
  this refactoring, conflicts will almost always be in app.py where two
  branches deleted different functions — accept both deletions.

If a worktree is unsalvageable:
```bash
git worktree remove ../gdpr-agent-<name> --force
git branch -D feature/<name>
```
Then re-run that worktree from the master prompt.
