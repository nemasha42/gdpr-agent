# Project Backlog — Consolidated & Deduplicated

> **Last updated:** 2026-04-19
> **Sources merged:** `ISSUES.md`, `code-review/open-recommendations.md`, `docs/error-handling-audit.md`, `docs/customer-naming-audit.md`, `plan_replies.md`, `codereview.md`, `REFACTOR_PLAN.md`, architecture review suggestions, `CLAUDE.md` tech debt notes, `tests/README.md` coverage gaps
>
> This is the single authoritative list of open work. Do not duplicate items across other files.
> For completed work, see the **Completed** section at the bottom.

---

## Priority Guide

| Label | Meaning |
|-------|---------|
| **P1** | Actively causes incorrect behavior or data loss risk |
| **P2** | Real UX issue or blocks scaling; fix when touching the area |
| **P3** | Nice-to-have improvement; defer until needed |
| **Skip** | Evaluated and deliberately not worth doing |
| **No fix** | Known limitation with no available workaround |

---

## 1. Testing & Quality

### 1.1 Dashboard route test coverage
**Priority:** P2 — fix when modifying untested routes
**Sources:** `codereview.md`, `ISSUES.md`, `open-recommendations.md` #1, `tests/README.md`

All pipeline routes (`pipeline_bp.py`, 831 lines), data routes (`data_bp.py`, 285 lines), transfers routes (`transfers_bp.py`, 385 lines), and most API routes (`api_bp.py`) have zero test coverage. Only `test_dashboard.py` and `test_portal_submit_route.py` cover a handful of routes. Template rendering errors and logic bugs are only discovered during live use.

**Recommendation:** Write tests before modifying any of these routes. Do not retroactively add tests for stable routes that are not being changed — the ROI is low without active development. When adding tests, use Flask test client with `create_app()` and mock state files.

**Specific gaps:**
- `pipeline_bp.py` — SSE scan streams, background tasks, OAuth pre-flight checks
- `data_bp.py` — `/data/<domain>`, `/scan/<domain>`, `/download/<domain>`
- `transfers_bp.py` — `/transfers/*` routes
- `dashboard_bp.py` — `/cards`, `/reextract`
- `api_bp.py` — `/api/body/<domain>/<id>`

---

### 1.2 Monitor CLI test coverage
**Priority:** P3 — fix if modifying `monitor.py`
**Sources:** `ISSUES.md`, `open-recommendations.md` #4, `tests/README.md`

`monitor.py` has zero test coverage. The CLI entry point (argument parsing, account selection, summary table printing, auto-download orchestration) is entirely untested. Core monitoring logic lives in `dashboard/services/monitor_runner.py` which is tested indirectly through blueprint routes. What is untested is glue code that rarely changes.

**Recommendation:** Write tests if modifying `monitor.py` itself. Risk is low because the actual logic is in `monitor_runner.py`.

---

### 1.3 LLM classifier `_llm_cache` dedup verification
**Priority:** P3
**Source:** `tests/README.md`

The in-session cache that prevents re-classifying identical auto-replies (`_llm_cache` in `classifier.py`) has no test verifying it actually suppresses the second API call.

**Recommendation:** Add a test that calls `classify()` twice with the same `(from_addr, subject)` and asserts the LLM mock is called only once.

---

### 1.4 `dataowners_overrides.json` schema validation test
**Priority:** P3
**Source:** `tests/README.md`

The hand-curated override entries are not validated by any test. A malformed entry (e.g. wrong `source` literal) would cause `CompanyRecord.model_validate()` to raise `ValidationError` caught silently, skipping that company in Step 2.

**Recommendation:** Add a parametrized test that loads every entry from `dataowners_overrides.json` and calls `CompanyRecord.model_validate()`.

---

## 2. Architecture & Code Organization

### 2.1 Split `shared.py` into focused modules
**Priority:** P3 — do when making substantial dashboard changes
**Sources:** `ISSUES.md` (pressure point), architecture review

`dashboard/shared.py` (520 lines) contains path helpers, data loaders, context processors, snippet cleaning, and reply dedup. Code that changes for the same reason should live together — `shared.py` changes for almost any dashboard reason.

**Candidate split:**
- `dashboard/paths.py` — path helpers, data directory resolution
- `dashboard/loaders.py` — `_load_all_states()`, `_lookup_company()`, JSON file reading
- `dashboard/display.py` — `_clean_snippet()`, `_is_human_friendly()`, `_dedup_reply_rows()`, `_effective_tags()`

**Recommendation:** Do this split when you are already making multiple changes to `shared.py` in one session, so the refactor pays for itself.

---

### 2.2 Split `pipeline_bp.py` into focused blueprints
**Priority:** P3 — do when adding pipeline features
**Sources:** `ISSUES.md` (pressure point), architecture review

`pipeline_bp.py` (831 lines) handles scan, resolve, review, send, and SSE streaming — five distinct sub-flows in one file.

**Candidate split:**
- `scan_bp.py` — scan page, SSE scan stream
- `review_bp.py` — letter review, approve, send routes
- Keep pipeline_bp.py for shared routes like `/pipeline` landing page

**Recommendation:** Split when adding new pipeline functionality. The current file works but is hard to navigate.

---

### 2.3 Split `monitor_runner.py` into focused modules
**Priority:** P3
**Sources:** `ISSUES.md` (pressure point), architecture review

`dashboard/services/monitor_runner.py` (890 lines) handles core monitor logic, portal reply domain resolution, and re-extraction helpers — three distinct concerns used by both CLI and dashboard.

**Candidate split:**
- `monitor_runner.py` — core monitor loop
- `portal_domains.py` — `_get_portal_sender_domains()` and platform resolution
- `reextraction.py` — `_reextract_missing_links()` helpers

---

### 2.4 Extract top-level service layer
**Priority:** P3
**Source:** Architecture review

CLI tools (`run.py`, `monitor.py`) and dashboard blueprints share business logic but currently import from each other in ad-hoc ways. A top-level `services/` directory would provide a clean shared interface.

**Recommendation:** Not needed until a third consumer (e.g. a REST API or scheduled job runner) is added. Current imports work fine for two consumers.

---

### 2.5 Separate `user_data/` into cache vs state tiers
**Priority:** P3
**Sources:** `ISSUES.md` (pressure point), architecture review

Tokens and screenshots (replaceable cache) share a directory with `reply_state.json` and `cost_log.json` (irreplaceable state). A careless `rm -rf user_data/tokens/` could become `rm -rf user_data/`.

**Candidate structure:**
- `user_data/cache/` — tokens, screenshots, captcha files
- `user_data/state/` — reply_state.json, cost_log.json, sent_letters.json, subprocessor_requests.json

**Recommendation:** Do this when you next need to make changes to the `user_data/` structure. Low urgency for single-user use.

---

## 3. Infrastructure & Reliability

### 3.1 Background `/refresh` route
**Priority:** P2 — fix when multiuser is actively used
**Sources:** `ISSUES.md` P3, `open-recommendations.md` #2

The `/refresh` route runs the full monitor inline and blocks the HTTP response until completion. The browser hangs for minutes. With multiuser support, one user's refresh blocks Flask for everyone.

**Recommendation:** Convert to background task + SSE (the pattern already exists in the codebase for pipeline scan). Low urgency for single-user use. Workaround: run `python monitor.py` from CLI.

---

### 3.2 `GITHUB_TOKEN` support for API rate limit
**Priority:** P2 — implement when scaling past ~50 companies
**Sources:** `ISSUES.md` P3, `open-recommendations.md` #3, `codereview.md`

GitHub API calls in `resolver.py` are unauthenticated (60 req/hour limit). At 500+ companies, Step 3 (datarequests.org lookup) silently fails for remaining companies.

**Implementation:** Add `GITHUB_TOKEN: str = ""` to `config/settings.py`, pass `Authorization: Bearer {token}` header in `_fetch_dir_listing()` when non-empty. Raises limit to 5,000 req/hour.

**Recommendation:** Implement when planning full 500+ company cold runs. Not needed for warm-cache runs.

---

### 3.3 JSON file concurrency (file locking)
**Priority:** P3
**Sources:** `ISSUES.md` (pressure point), architecture review

`reply_state.json`, `sent_letters.json`, and `cost_log.json` are read/written by multiple processes (CLI pipeline, monitor, dashboard) without file locking. Race conditions are unlikely at current usage but will surface under concurrent dashboard + monitor runs.

**Options:** `fcntl.flock()` wrapper around all JSON read/write operations, or migrate state to SQLite with proper transactions.

**Recommendation:** Defer until concurrent access causes actual data corruption. SQLite migration is the cleaner long-term fix but requires changes across every file that reads/writes state.

---

### 3.4 Unified silent failure observability
**Priority:** P3
**Sources:** `ISSUES.md` (pressure point), architecture review

Multiple fallback sites silently degrade (missing API keys, Playwright not installed, GitHub rate limit, LLM unparseable JSON). Each logs locally but there is no unified channel.

**Proposal:** Single `degraded_calls.log` (TSV, append-only) written by all silent fallback paths. Columns: timestamp, module, dependency, fallback_action, context.

**Recommendation:** Implement when debugging cross-module degradation. The per-module `print()` logging added in the error handling audit (2026-04-18) provides basic coverage.

---

### 3.5 Monitor reply dedup cache persistence
**Priority:** Skip
**Sources:** `ISSUES.md` P3, `open-recommendations.md` #8

`_llm_cache` in `classifier.py` resets between runs. Identical auto-replies in separate runs each trigger an LLM call. At $0.01/call and ~20-30 duplicates, the waste is ~$0.30/cycle.

**Recommendation:** Not worth implementing. The in-session dedup already works. The complexity of persistent cache (disk writes, staleness, purging) far exceeds the $0.30/cycle savings.

---

### 3.6 Resolver concurrency (ThreadPoolExecutor)
**Priority:** Skip
**Sources:** `ISSUES.md` P3, `open-recommendations.md` #7

The 5-step resolver chain is sequential. Parallelizing introduces complexity: rate-limit accounting, cost tracker locking, harder error debugging. After the first cold run, cache hits are 90%+.

**Recommendation:** Skip unless cold-run time is a measured pain point. If it runs overnight, sequential is fine.

---

## 4. Features & Enhancements

### 4.1 Three new classifier tags
**Priority:** P3 — implement if HUMAN_REVIEW volume is high
**Sources:** `plan_replies.md` Part 2, `open-recommendations.md` #5

Three proposed regex tags:
- `COMPLAINT_RIGHTS_MENTIONED` — company mentions supervisory authority / right to complain
- `PROCESSING_FEE_REQUESTED` — company asks for payment (illegal under GDPR Art. 12 unless excessive)
- `THIRD_PARTY_PROCESSOR` — company says they are a processor, not a controller

**Changes required (3 files must stay in sync):**
1. `reply_monitor/models.py` — add to `REPLY_TAGS`
2. `reply_monitor/classifier.py` — add regex rules to `_RULES`
3. `reply_monitor/state_manager.py` — add to `_ACTION_TAGS`
4. `dashboard/shared.py` — add `_ACTION_HINTS` entries
5. `tests/unit/test_reply_classifier.py` — add test cases

**Recommendation:** Track how many HUMAN_REVIEW replies fall into these three buckets. If >10% of HUMAN_REVIEW volume, implement. Otherwise `HUMAN_REVIEW` catches them adequately.

---

### 4.2 `privacy_policy_url` population gap
**Priority:** P3
**Source:** `CLAUDE.md` tech debt

`Contact.privacy_policy_url` is populated by the privacy page scraper (Step 4) but not by LLM searcher (Step 5) or datarequests.org (Step 3). Companies resolved via those paths have no privacy policy link in the dashboard.

**Recommendation:** Add `privacy_policy_url` to the LLM searcher prompt schema and to the datarequests.org field mapping. Low effort, improves dashboard completeness.

---

## 5. Known Limitations (no fix available)

### 5.1 Ketch portal reCAPTCHA v3 headless failure
**Sources:** `ISSUES.md` P2, `open-recommendations.md` #9

Ketch portals (used by Zendesk, ~15-20% of GDPR portals) always fail reCAPTCHA v3 in headless Playwright. Google detects automation signals by design. No stealth plugin reliably bypasses v3.

**Current behavior is correct:** detect the failure, fall back to manual instructions with `needs_manual=True`. Would only change if Google modifies v3 scoring or Ketch switches CAPTCHA providers.

---

## 6. Documentation & Housekeeping

### 6.1 Mark stale plan/spec docs as COMPLETED
**Priority:** Trivial
**Sources:** `open-recommendations.md` #10

9 plan/spec documents under `docs/superpowers/` show "PENDING" or "DESIGN" status but all features are implemented:
- `docs/superpowers/plans/2026-04-12-multiuser.md`
- `docs/superpowers/specs/2026-04-12-multiuser-design.md`
- `docs/superpowers/plans/2026-04-12-portal-automation.md`
- `docs/superpowers/specs/2026-04-12-portal-automation-design.md`
- `docs/superpowers/specs/2026-04-13-reply-portal-verification-design.md`
- `docs/superpowers/plans/2026-04-13-reply-portal-verification.md`
- `docs/superpowers/specs/2026-04-13-ketch-portal-support-design.md`
- `docs/superpowers/plans/2026-04-13-ketch-portal-support.md`
- `docs/superpowers/plans/2026-04-18-customer-naming-cleanup.md`

**Action:** Add `> Status: COMPLETED` to each file header, or move to `docs/superpowers/archive/`.

---

### 6.2 Remove obsolete plan documents from project root
**Priority:** Trivial
**Source:** Project root file audit

Several root-level planning documents are for completed work and clutter the project root:
- `REFACTOR_PLAN.md` — blueprint refactoring (COMPLETE)
- `plan_replies.md` — reply monitor enhancements (Parts 1/3/4 COMPLETE, Parts 2/5 tracked in 4.1 above)
- `codereview.md` — 29 issues all fixed
- `LAUNCH_INSTRUCTIONS.md`, `IMPLEMENTATION_GUIDE.md`, `ORCHESTRATION_PLAYBOOK.md`, `GDPR Agent Refactoring Guide.md` — all for completed blueprint refactoring

**Action:** Move to `docs/archive/` or delete. The relevant open items from each are captured in this backlog.

---

## Summary Table

| # | Item | Priority | Category | Action |
|---|------|----------|----------|--------|
| 1.1 | Dashboard route tests | P2 | Testing | Write before modifying untested routes |
| 1.2 | Monitor CLI tests | P3 | Testing | Write if modifying monitor.py |
| 1.3 | LLM cache dedup test | P3 | Testing | Add single test |
| 1.4 | Overrides schema test | P3 | Testing | Add parametrized test |
| 2.1 | Split shared.py | P3 | Architecture | Do during substantial dashboard changes |
| 2.2 | Split pipeline_bp.py | P3 | Architecture | Do when adding pipeline features |
| 2.3 | Split monitor_runner.py | P3 | Architecture | Do when modifying monitor service |
| 2.4 | Top-level service layer | P3 | Architecture | Defer until third consumer needed |
| 2.5 | user_data/ tier split | P3 | Architecture | Do when restructuring user_data |
| 3.1 | /refresh backgrounding | P2 | Infrastructure | Convert when multiuser is active |
| 3.2 | GITHUB_TOKEN support | P2 | Infrastructure | Implement for 500+ company runs |
| 3.3 | JSON file locking | P3 | Infrastructure | Defer until concurrency issues appear |
| 3.4 | Unified degraded log | P3 | Infrastructure | Implement when debugging cross-module |
| 3.5 | LLM cache persistence | Skip | Infrastructure | Not worth the complexity |
| 3.6 | Resolver concurrency | Skip | Infrastructure | Premature optimization |
| 4.1 | 3 new classifier tags | P3 | Features | Implement if HUMAN_REVIEW >10% |
| 4.2 | privacy_policy_url gap | P3 | Features | Add to LLM/datarequests steps |
| 5.1 | Ketch reCAPTCHA v3 | No fix | Limitation | No action available |
| 6.1 | Stale plan docs status | Trivial | Housekeeping | Mark COMPLETED |
| 6.2 | Remove root plan files | Trivial | Housekeeping | Archive or delete |

---

## Completed Work (reference)

These items appeared in one or more source documents and have been fully resolved:

| Item | Source | Completed |
|------|--------|-----------|
| Blueprint refactoring (10 blueprints + shared.py + services) | `REFACTOR_PLAN.md` | 2026-04 |
| 29 P1/P2/P3 code review bug fixes | `codereview.md` | 2026-03-16 |
| Error handling audit (32 bare except blocks across 13 files) | `docs/error-handling-audit.md` | 2026-04-18 |
| Customer naming cleanup (25 renames + 4 regex + 9 comments) | `docs/customer-naming-audit.md` | 2026-04-18 |
| Multiuser support (auth_routes, user_model, admin_routes) | `docs/superpowers/` plans | 2026-04-12 |
| Portal automation (7 portal_submitter modules) | `docs/superpowers/` plans | 2026-04-12 |
| Reply portal verification + auto-submit (url_verifier) | `docs/superpowers/` plans | 2026-04-13 |
| Ketch portal support + multi-step navigation | `docs/superpowers/` plans | 2026-04-13 |
| Domain-search fallback for missed replies | `plan_replies.md` Part 1 | 2026-04 |
| LLM `suggested_reply` field | `plan_replies.md` Part 3 | 2026-04 |
| Dashboard compose-reply form | `plan_replies.md` Part 4 | 2026-04 |
| Past-attempts dedup bug fix | `REFACTOR_PLAN.md` | 2026-04 |
| ARCHITECTURE.md rewrite (383→220 lines + split-out docs) | Architecture review | 2026-04-19 |
| Transfer map depth bug fix (cross-link dedup) | Live bug report | 2026-04-19 |
| Subprocessor fetcher audit (wave/TTL logic, filters) | Live audit | 2026-04-19 |
