# CLAUDE.md — GDPR Agent

Automated GDPR Subject Access Request pipeline: Gmail scan → contact resolution → SAR send → reply monitoring → subprocessor discovery.

See @ARCHITECTURE.md for full module documentation, data models, and route reference.

## Commands

```bash
# Tests
.venv/bin/pytest tests/unit/ -q
.venv/bin/pytest tests/unit/test_resolver.py -v
.venv/bin/pytest tests/unit/test_resolver.py::TestContactResolver::test_cache_hit -v

# Pipeline
python run.py                                          # Full run
python run.py --dry-run                                # Preview only
python run.py --portal-only                            # Portal-method companies only

# Portal automation
python test_portal.py --list-portals
python test_portal.py --domain glassdoor.com --dry-run
python test_portal.py --domain glassdoor.com

# Monitor & Dashboard
python monitor.py [--account EMAIL] [--verbose]
python dashboard/app.py                                # Flask on :5001
```

## Architecture (summary)

Five-stage pipeline: Scanner → Resolver → Composer/Sender → Monitor → Subprocessors

- **Dashboard uses an app factory pattern.** `dashboard/__init__.py` contains `create_app()` (107 lines — Flask setup, LoginManager, all blueprint registration, before_request hook). `dashboard/shared.py` (579 lines) contains all shared helpers, constants, and the context processor. `dashboard/app.py` is just the entry point (33 lines — creates the app and runs it). **All routes are in 10 blueprints** under `dashboard/blueprints/`: `pipeline_bp.py` (832), `company_bp.py` (568), `transfers_bp.py` (385), `data_bp.py` (285), `portal_bp.py` (239), `dashboard_bp.py` (204), `monitor_bp.py` (90), `costs_bp.py` (89), `api_bp.py` (78), `settings_bp.py` (67). **Service modules** under `dashboard/services/`: `monitor_runner.py` (807 — unified monitor functions used by both `monitor.py` CLI and `monitor_bp`), `graph_data.py` (395), `jurisdiction.py` (235). Total dashboard: ~5,558 lines across all Python files. Do not add new routes without discussion.
- All LLM calls go through `contact_resolver/cost_tracker.py` — call `record_llm_call()` AFTER JSON extraction so `found=` reflects the actual result.
- Portal automation uses Playwright with stealth scripts in `portal_submitter/`.
- Playwright ≥1.58: `page.accessibility.snapshot()` is removed. Use `page.locator("body").aria_snapshot()`.

## Critical Gotchas — Claude Gets These Wrong

### sent_letters.json vs subprocessor_requests.json are separate
SP letters must NEVER be written to `sent_letters.json` — use `send_letter(record=False)`. If SP letters leak in, `promote_latest_attempt()` corrupts SAR state (wrong thread_id, lost replies).

### Always use `_load_all_states(account)`, not `load_state()`
`_load_all_states()` (in `dashboard/shared.py`) merges reply_state.json with sent_letters.json via `promote_latest_attempt()`. Using `load_state()` directly undercounts companies.

### Portal status persistence goes to reply_state.json, NEVER sent_letters.json
`save_portal_submission()` writes to reply_state.json. Getting this wrong corrupts `promote_latest_attempt()`.

### `_lookup_company(domain)` does deep-merge
`_lookup_company()` (in `dashboard/shared.py`) merges `data/companies.json` with `data/dataowners_overrides.json`. Override contact fields win when non-empty. Used by company_detail, portal_submit, and mark_portal_submitted routes.

### `write_subprocessors()` must create stubs for unknown domains
Never skip-on-missing. Without stubs, subprocessors silently don't persist for domains only in reply_state.json.

### Mock Anthropic responses need real `.usage` integers
`response.usage.input_tokens` and `output_tokens` must be integers, not MagicMock auto-attributes — or cost recording crashes.

### Gmail send tokens can be revoked independently of readonly
Symptoms: letters show "ready" forever, send task completes with 0 sent, no error. Diagnosis: `check_send_token_valid(email)` or `/pipeline/reauth-send`.

### `extracted` field URLs can be false positives
`data_link` and `portal_url` may contain misclassified URLs. Templates gate link display on reply tags — don't trust `extracted` URLs without checking tags.

### OAuth service cache has 5-minute TTL
`clear_service_cache()` exists for tests. OAuth call logger appends to `user_data/oauth_calls.log` (TSV, append-only — never truncate).

## Key Constraints

- `data/companies.json` — public contact info only, committed to repo, no PII
- `user_data/` — gitignored, contains OAuth tokens and sent letters
- LLM is last resort — free lookup paths always attempted first
- Portal field mappings cached 90 days in `CompanyRecord.portal_field_mapping`
- CAPTCHA files in `user_data/captcha_pending/` — cleaned on solution or 5min timeout
- Ketch portals (Zendesk) always fail reCAPTCHA v3 headless — `needs_manual=True`, no workaround
- Login-required portals (Google, Apple, Meta, Amazon, Facebook, Twitter/X) → manual instructions

## Testing

All tests in `tests/unit/` use DI or `unittest.mock` — no real network calls. `ContactResolver` accepts injectable `http_get`, `llm_search`, `privacy_scrape`.

## Known Tech Debt

- Dashboard route test coverage is partial — most blueprint routes lack dedicated tests
- GitHub API rate limit (60/hour) blocks 500+ company runs — needs `GITHUB_TOKEN`
- Ketch portal reCAPTCHA v3 — no automated workaround

## LLM Cost Projections (500+ companies, cold cache)

- Resolver: ~$12.50 cold, ~$1 warm
- Subprocessors: ~$15–25 cold, free within 30-day TTL
- Classifier: ~$5 per monitor cycle
- Portal analyzer: ~$0.02/company, cached 90 days
