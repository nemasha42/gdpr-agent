# Open Recommendations — Audit Review

> **Date:** 2026-04-18
> **Sources reviewed:** `codereview.md`, `REFACTOR_PLAN.md`, `plan_replies.md`, `ARCHITECTURE.md` (known issues), `CLAUDE.md` (tech debt), and 8 plan/spec documents under `docs/superpowers/`
> **Scope:** Identify all unimplemented concerns and recommendations, evaluate validity and necessity

---

## What Was Already Completed

Before listing open items, it is worth noting the scale of work already done:

- **Blueprint refactoring** (REFACTOR_PLAN.md, IMPLEMENTATION_GUIDE.md, ORCHESTRATION_PLAYBOOK.md) — fully complete. 10 blueprints, shared.py, app factory, services layer, all confirmed in current codebase.
- **29 P1/P2/P3 bug fixes** from codereview.md — all applied.
- **Multiuser support** (docs/superpowers 2026-04-12) — implemented: `auth_routes.py`, `user_model.py`, `admin_routes.py`, `sse.py`.
- **Portal automation** (docs/superpowers 2026-04-12) — implemented: all 7 `portal_submitter/` modules.
- **Reply portal verification + auto-submit** (docs/superpowers 2026-04-13) — implemented: `url_verifier.py`, classifier fixes, junk URL filter.
- **Ketch portal support + multi-step navigation** (docs/superpowers 2026-04-13) — implemented: `portal_navigator.py`, ketch detection in `platform_hints.py`.
- **Past-attempts dedup bug fix** (REFACTOR_PLAN.md) — confirmed in `monitor_runner.py` lines 94-99.
- **Domain-search fallback** (plan_replies.md Part 1) — implemented in `fetcher.py`.
- **Dashboard compose-reply form** (plan_replies.md Part 4) — implemented in `company_bp.py`.
- **`suggested_reply` field** (plan_replies.md Part 3) — implemented in classifier + models.

**Note:** The 8 plan/spec documents under `docs/superpowers/` still show "PENDING" status despite all features being implemented. See item #10 below.

---

## Open Recommendations

### 1. Dashboard Route Test Coverage

- **Source:** codereview.md (still open), ARCHITECTURE.md Section 9 (P3)
- **What:** Many blueprint routes lack dedicated tests. Untested: all pipeline routes (`pipeline_bp.py`, 832 lines), data routes (`data_bp.py`, 285 lines), transfers routes (`transfers_bp.py`, 385 lines), most API routes (`api_bp.py`, 78 lines). Only `test_dashboard.py` and `test_portal_submit_route.py` cover a handful of routes.
- **Why it was proposed:** Template rendering errors or logic bugs are only discovered during live use. No regression safety net for 1,500+ lines of route code.
- **Assessment: MEDIUM-HIGH PRIORITY**
  - This is the biggest testing gap in the project. `pipeline_bp.py` alone is 832 lines with SSE scan streams, background tasks, and OAuth pre-flight checks — all untested.
  - However, adding route tests requires substantial mocking (Flask test client, authenticated sessions, mock state files). The ROI per test is low unless you are actively changing these routes.
  - **Recommendation:** Write tests before modifying pipeline or transfer routes. Do not add tests retroactively for stable code that is not being changed.

---

### 2. Dashboard `/refresh` Blocks HTTP Response

- **Source:** ARCHITECTURE.md Section 9 (P3)
- **What:** The `/refresh` route runs the full monitor inline and blocks the HTTP response until completion. The browser hangs with a spinning indicator for minutes.
- **Why it was proposed:** Poor UX — no progress feedback, browser may timeout on slow runs.
- **Assessment: MEDIUM PRIORITY**
  - This is a real UX issue, especially now that multiuser is implemented — one user's refresh blocks the Flask process for everyone.
  - The pattern for background tasks with SSE already exists in the codebase (pipeline scan uses it).
  - Workaround exists: run `python monitor.py` from CLI instead of clicking refresh.
  - **Recommendation:** Convert to background task + SSE when multiuser is actively used. Low urgency for single-user use.

---

### 3. `GITHUB_TOKEN` Support for API Rate Limit

- **Source:** codereview.md (still open), ARCHITECTURE.md Section 9 (P3)
- **What:** GitHub API calls in `contact_resolver/resolver.py` are unauthenticated (60 req/hour limit). The code warns when rate limit is low but does not use a token. Adding `GITHUB_TOKEN` to `.env` would raise the limit to 5,000 req/hour.
- **Why it was proposed:** At 500+ companies, unauthenticated GitHub API is exhausted mid-run. Step 3 (datarequests.org lookup) silently fails for remaining companies.
- **Assessment: MEDIUM PRIORITY — implement when scaling past ~50 companies**
  - Implementation is trivial: add `GITHUB_TOKEN` to `config/settings.py`, pass `Authorization: Bearer` header in `_fetch_dir_listing()`.
  - For warm-cache runs where most companies are resolved from cache, the GitHub limit is rarely hit.
  - **Recommendation:** Implement when you plan to do full 500+ company cold runs. Not needed for current scale.

---

### 4. Monitor CLI Test Coverage

- **Source:** ARCHITECTURE.md Section 9, codereview.md (still open)
- **What:** `monitor.py` has zero test coverage. No `test_monitor.py` exists. The CLI entry point (argument parsing, account selection, summary table printing, auto-download orchestration) is entirely untested.
- **Why it was proposed:** Regressions in the monitor CLI are invisible until a live run.
- **Assessment: MEDIUM PRIORITY — partially mitigated**
  - The core monitoring logic lives in `dashboard/services/monitor_runner.py` (807 lines), which is tested indirectly through blueprint routes.
  - What is untested is glue code: argparse, account selection from tokens directory, the summary table printer. These rarely change.
  - Regressions would show up immediately on the next manual `python monitor.py` run.
  - **Recommendation:** Write tests if modifying `monitor.py` itself. The risk is low because the actual logic is in `monitor_runner.py`.

---

### 5. Three New Classifier Tags

- **Source:** plan_replies.md (Part 2)
- **What:** Three proposed regex tags for the reply classifier:
  - `COMPLAINT_RIGHTS_MENTIONED` — company mentions supervisory authority rights (implying they consider the matter closed)
  - `PROCESSING_FEE_REQUESTED` — company asks for payment (illegal under GDPR Art. 12 unless excessive)
  - `THIRD_PARTY_PROCESSOR` — company says they are a processor, not a controller, and deflects
- **Why it was proposed:** Without dedicated tags, these replies land in `HUMAN_REVIEW`, requiring manual reading.
- **Assessment: LOW PRIORITY — skip for now**
  - These are rare edge cases. In 500+ SARs, you would see maybe 5-10 fee requests and fewer processor-deflections.
  - `HUMAN_REVIEW` already catches them — the user just manually reads the reply. The incremental automation gain is small.
  - Adding tags requires changes in 3 files (`models.py`, `classifier.py`, `state_manager.py`) plus ACTION_HINTS in `shared.py`, and the tag sets must stay in sync.
  - **Recommendation:** Track how many HUMAN_REVIEW replies fall into these three buckets. If it exceeds ~10% of HUMAN_REVIEW volume, implement. Otherwise the effort is not justified.

---

### 6. LLM Prompt `next_step` Field

- **Source:** plan_replies.md (Part 3)
- **What:** Have the LLM classifier return a `next_step` field alongside tags, describing what the user should do next (context-specific guidance rather than generic hints).
- **Why it was proposed:** Would give per-reply actionable guidance beyond the static ACTION_HINTS.
- **Assessment: LOW PRIORITY — `suggested_reply` already covers the useful case**
  - `suggested_reply` IS implemented and generates draft follow-up text for action tags. This is the part that actually saves user time.
  - `next_step` would be a shorter, less actionable version of what `suggested_reply` already provides.
  - Adding it means more LLM output tokens per classification (~$0.002 extra per call).
  - The fixed ACTION_HINTS in `shared.py` already cover the "what to do" question for each tag type.
  - **Recommendation:** Not needed. The combination of `suggested_reply` + ACTION_HINTS covers this use case.

---

### 7. Resolver Concurrency (ThreadPoolExecutor)

- **Source:** ARCHITECTURE.md Section 9 (P3)
- **What:** The 5-step resolver chain in `resolver.py` is sequential per domain and across domains. Proposed: wrap with `ThreadPoolExecutor` for I/O-bound parallelization.
- **Why it was proposed:** A 500-company cold run is slow because each domain resolves sequentially (HTTP scrapes, LLM calls).
- **Assessment: LOW PRIORITY — premature optimization**
  - The resolver caches aggressively. After the first cold run, subsequent runs hit cache for 90%+ of domains.
  - Parallelizing introduces complexity: rate-limit accounting for GitHub API becomes concurrent, cost tracker needs locking, error handling gets harder to debug.
  - The LLM step (Step 5) has its own rate limits — parallelizing 50 concurrent Haiku calls may trigger Anthropic's rate limiter.
  - **Recommendation:** Skip unless cold-run time is actually a pain point. If it runs overnight, sequential is fine.

---

### 8. Monitor Reply Dedup Cache Persistence

- **Source:** ARCHITECTURE.md Section 9 (P3)
- **What:** `_llm_cache` in `classifier.py` is in-memory only and resets between runs. Identical auto-replies processed in separate `monitor.py` runs each trigger an LLM call.
- **Why it was proposed:** Saves ~$0.01 per duplicate classification. Some companies send identical auto-replies to every SAR.
- **Assessment: LOW PRIORITY — minimal cost impact**
  - At $0.01 per LLM call and maybe 20-30 duplicates across runs, the total waste is ~$0.30 per monitor cycle.
  - Implementing persistent cache means writing to disk, handling staleness, and adding a purge mechanism.
  - The in-session dedup already works — running `monitor.py` once catches all duplicates within that run.
  - **Recommendation:** Not worth implementing unless you are running monitor.py many times per day and seeing high LLM costs from re-classification.

---

### 9. Ketch Portal reCAPTCHA v3 Headless Failure

- **Source:** codereview.md (still open), ARCHITECTURE.md Section 9 (P2)
- **What:** Ketch portals (used by Zendesk) always fail reCAPTCHA v3 in headless Playwright. Falls back to manual with `needs_manual=True`.
- **Why it was proposed:** Ketch portals represent ~15-20% of GDPR portals. All require manual submission.
- **Assessment: KNOWN LIMITATION — no actionable fix**
  - reCAPTCHA v3 scores are based on browser fingerprinting. Headless Playwright fails by design — Google detects automation signals.
  - No stealth plugin reliably bypasses reCAPTCHA v3 (unlike v2 checkbox).
  - The current behavior is correct: detect the failure, fall back to manual instructions.
  - Documented as "no known workaround" in CLAUDE.md, which is accurate.
  - **Recommendation:** No action. Would only change if Google modifies reCAPTCHA v3 scoring or Ketch switches to a different CAPTCHA — neither is in your control.

---

### 10. Stale Plan/Spec Documents

- **Source:** `docs/superpowers/plans/` and `docs/superpowers/specs/` (8 files from 2026-04-12 and 2026-04-13)
- **What:** All 8 plan/spec documents show "PENDING" or "DESIGN" status but the features they describe have been fully implemented.
- **Why it matters:** Anyone reading these docs — including future Claude sessions — would think the work has not been done, potentially wasting time re-reading plans for features that already exist.
- **Assessment: TRIVIAL — cleanup when convenient**
  - Options: update each with a "COMPLETED" header, move to an `archive/` directory, or delete.
  - **Recommendation:** Add a one-line `> Status: COMPLETED — implemented 2026-04-13` to each file header.

---

## Summary

| # | Item | Source | Priority | Action |
|---|------|--------|----------|--------|
| 1 | Dashboard route tests | codereview, ARCH | Medium-High | Write tests before modifying untested routes |
| 2 | `/refresh` backgrounding | ARCHITECTURE.md | Medium | Convert when multiuser is actively used |
| 3 | GITHUB_TOKEN support | codereview, ARCH | Medium | Implement when scaling past ~50 companies |
| 4 | Monitor CLI tests | codereview, ARCH | Medium | Write tests if modifying monitor.py |
| 5 | 3 new classifier tags | plan_replies.md | Low | Skip — rare edge cases covered by HUMAN_REVIEW |
| 6 | LLM `next_step` field | plan_replies.md | Low | Skip — `suggested_reply` + ACTION_HINTS cover it |
| 7 | Resolver concurrency | ARCHITECTURE.md | Low | Skip — premature optimization |
| 8 | LLM cache persistence | ARCHITECTURE.md | Low | Skip — ~$0.30/cycle waste, not worth the complexity |
| 9 | Ketch reCAPTCHA v3 | codereview, ARCH | Known limit | No fix available — current behavior is correct |
| 10 | Stale plan docs | docs/superpowers/ | Trivial | Mark as COMPLETED when convenient |

**Bottom line:** Nothing is on fire. The highest-value work is **dashboard route tests (#1)** when modifying those routes, and **`/refresh` backgrounding (#2)** for multiuser UX. Items #5-#8 can be safely deferred indefinitely. Item #9 has no fix. Item #10 is housekeeping.
