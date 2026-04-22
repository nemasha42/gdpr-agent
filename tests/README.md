# Test Suite

> Back to @ARCHITECTURE.md for the system overview.

---

## Structure

All tests live in `tests/unit/`. No integration test directories, no end-to-end test scripts, no external fixture files (all test data is inline). Runner: `pytest` — run with `.venv/bin/pytest tests/unit/ -q`.

Files follow `test_{module_name}.py`. Test classes: `Test{Concept}`. Test functions: `test_{scenario}`.

All test data is inline. LLM mock responses use `_make_text_response()` helpers with integer `.usage` attributes (not MagicMock auto-attributes — required for cost arithmetic). **No test makes a real API call** — Gmail, GitHub, Anthropic, and Playwright are all mocked. GitHub API mocks omit `X-RateLimit-Remaining`, so the rate-limit warning path is untested.

---

## Coverage Map

| Module | Test file | Coverage |
|--------|-----------|----------|
| `scanner/inbox_reader.py` | `test_inbox_reader.py` | Good — pagination, max_results, missing headers, empty inbox |
| `scanner/service_extractor.py` | `test_service_extractor.py` | Good — confidence levels, dedup, alias grouping, date ranges |
| `scanner/company_normalizer.py` | `test_company_normalizer.py` | Good — TLD handling, subdomain stripping, alias table |
| `contact_resolver/resolver.py` | `test_resolver.py` | Excellent — all 5 steps, staleness, cache write-back, overrides, datarequests |
| `contact_resolver/llm_searcher.py` | `test_llm_searcher.py` | Good — JSON extraction, validate_and_build, cost tracking, API error |
| `contact_resolver/privacy_page_scraper.py` | `test_privacy_page_scraper.py` | Good — 4-URL fallback, email/portal extraction, classification |
| `contact_resolver/cost_tracker.py` | within `test_llm_searcher.py` | Partial — session/persistent log, cost calc; `record_resolver_result`, `set_llm_limit` untested separately |
| `letter_engine/composer.py` | `test_letter_engine.py` | Good — email/portal/postal template, variable substitution |
| `letter_engine/sender.py` | `test_letter_engine.py` | Good — Y/N/EOF handling, dry-run, Gmail dispatch mocked |
| `letter_engine/tracker.py` | `test_letter_engine.py` | Good — record_sent, get_log with empty/corrupt file |
| `reply_monitor/classifier.py` | `test_reply_classifier.py` | Excellent — all 18 tags, NON_GDPR pre-pass, URL extraction, LLM fallback |
| `reply_monitor/fetcher.py` | `test_reply_fetcher.py` | Good — thread fetch, search fallback, body extraction, attachment detection |
| `reply_monitor/attachment_handler.py` | `test_attachment_handler.py` | Good — ZIP cataloging, JSON/CSV key extraction, category guessing |
| `reply_monitor/state_manager.py` | `test_reply_state_manager.py` | Good — all 7 statuses, priority ordering, deadline, per-account isolation |
| `reply_monitor/schema_builder.py` | `test_schema_builder.py` | Good — empty export, corrupt ZIP, malformed JSON, dynamic truncation |
| `reply_monitor/link_downloader.py` | `test_link_downloader.py` | Good — DownloadResult, filename parsing, too-large, 404 expiry |
| `dashboard/shared.py` | `test_snippet_clean.py` | Good — `_clean_snippet()`, `_is_human_friendly()`, `_dedup_reply_rows()` |
| `dashboard/blueprints/portal_bp.py` | `test_portal_submit_route.py` | Good — URL resolution, overrides, rejection, persistence lifecycle |
| `dashboard/` (UI health) | `test_ui_health.py` | Good — templates, static JS, service modules, cross-references |
| `portal_submitter/` | `test_portal_submitter.py` | Good — models, platform detection, OTP, form analysis, CAPTCHA, full workflow |
| `auth/gmail_oauth.py` | `test_oauth_refactor.py` | Good — service cache, OAuth call logging, getProfile skip |
| `run.py` | `test_run.py` | Partial — sent/skipped counts, `--max-llm-calls`; credentials.json path untested |
| `dashboard/blueprints/dashboard_bp.py` | `test_dashboard.py` | Partial — `/`, `/refresh` covered; `/cards` untested |
| `dashboard/blueprints/company_bp.py` | `test_dashboard.py` | Partial — `/company/<domain>` covered |

---

## Untested Modules

| Module | Risk |
|--------|------|
| `dashboard/blueprints/pipeline_bp.py` | All pipeline routes — no test coverage |
| `dashboard/blueprints/data_bp.py` | `/data/<domain>`, `/scan/<domain>`, `/download/<domain>` — no coverage |
| `dashboard/blueprints/transfers_bp.py` | `/transfers/*` — no coverage |
| `monitor.py` (CLI entry point) | Argument parsing, account selection, summary printing — invisible regressions |
| `config/settings.py` | Tested implicitly only |
| LLM classifier `_llm_cache` | Dedup cache never verified to suppress second call |
| `dataowners_overrides.json` | Schema not validated by any test — silent skip on malformed entry |
