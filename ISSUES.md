# Known Issues & Tech Debt

> Back to @ARCHITECTURE.md for the system overview.

29 issues found and fixed during code review (2026-03-16) across llm_searcher, sender, cost_tracker, resolver, classifier, state_manager, link_downloader, schema_builder, privacy_page_scraper, portal_submitter, monitor, run.py. Error logging audit (2026-04-18): 32 bare `except:` / `except Exception:` blocks across 13 files now log the exception.

---

## Open Issues

| Priority | Location | Issue |
|---|---|---|
| P2 | `portal_submitter/submitter.py` | Ketch portals always fail reCAPTCHA v3 in headless Playwright — falls back to manual. No known workaround. |
| P3 | GitHub API authentication | No `GITHUB_TOKEN` support — rate limit is 60 req/hour unauthenticated. Exhausted at 500+ companies. |
| P3 | Resolver concurrency | 5-step chain is sequential per domain and across domains. Steps 1-4 could be parallelised with `ThreadPoolExecutor`. |
| P3 | Dashboard `/refresh` | Blocks HTTP response during full monitor run. Should use a background thread. |
| P3 | Monitor reply dedup cache | `_llm_cache` in `classifier.py` resets between runs. Identical auto-replies in separate runs each trigger an LLM call. |
| P3 | Dashboard test coverage | Only pure helper functions and a few routes are tested. Most blueprint routes lack dedicated tests. |
| P3 | `monitor.py` | Zero test coverage for the CLI entry point. |

---

## Architectural Pressure Points

These are not bugs but structural concerns that increase friction as the codebase grows.

### `dashboard/shared.py` — grab-bag module

Contains path helpers, data loaders, context processors, snippet cleaning, and reply dedup in one file. Code that changes for the same reason should live together — shared.py changes for almost any dashboard reason. **Candidate for split:** path/config helpers, data loaders, display helpers.

### `pipeline_bp.py` — oversized blueprint

Handles scan, resolve, review, send, and SSE streaming. Five distinct sub-flows in one file. **Candidate for split:** scan routes, review/send routes, SSE routes.

### `monitor_runner.py` — large service module

Unified monitor functions used by both CLI and dashboard at ~890 lines. **Candidate for split:** core monitor logic, portal reply domain resolution, re-extraction helpers.

### JSON file concurrency

`reply_state.json`, `sent_letters.json`, and `cost_log.json` are read/written by multiple processes (CLI pipeline, monitor, dashboard) without file locking. Race conditions are unlikely at current usage but will surface under concurrent dashboard + monitor runs. **Options:** `fcntl.flock()` wrapper, or migrate state to SQLite.

### Silent failure observability

Multiple fallback sites silently degrade: missing `ANTHROPIC_API_KEY`, Playwright not installed, GitHub rate limit exhausted, LLM returning unparseable JSON. Each logs locally but there is no unified channel. **Candidate:** Single `degraded_calls.log` (TSV, append-only) written by all silent fallback paths.

### `user_data/` mixed tiers

Tokens and screenshots (replaceable cache) share a directory with `reply_state.json` and `cost_log.json` (irreplaceable state). A careless `rm -rf user_data/tokens/` could become `rm -rf user_data/`. **Candidate:** Split into `user_data/cache/` (tokens, screenshots) and `user_data/state/` (reply_state, cost_log, sent_letters).
