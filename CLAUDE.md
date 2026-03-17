# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all unit tests
python -m pytest tests/unit/ -q

# Run a single test file
python -m pytest tests/unit/test_resolver.py -v

# Run a single test by name
python -m pytest tests/unit/test_resolver.py::TestContactResolver::test_cache_hit -v

# Full pipeline (real Gmail + sending)
python run.py

# Full pipeline — preview only, nothing sent
python run.py --dry-run

# Smoke test Phase 4 letter engine interactively
python test_phase4.py --dry-run

# Monitor Gmail for SAR replies (prints summary table)
python monitor.py [--account EMAIL] [--verbose]

# Web dashboard (Flask, port 5001)
python dashboard/app.py
```

## Architecture

The pipeline runs in four stages:

```
Gmail inbox → service detection → contact resolution → SAR letter send
```

**Stage 1 — Scan** (`scanner/`): `inbox_reader.py` fetches email headers only (no body, `gmail.readonly` scope). `service_extractor.py` classifies senders as HIGH/MEDIUM/LOW confidence services and deduplicates by domain. `company_normalizer.py` maps raw domains to display names (strips noise subdomains, handles co.uk/com.au, hardcoded exceptions for t.co → Twitter/X etc.).

**Stage 2 — Resolve** (`contact_resolver/`): `resolver.py` runs a 5-step chain per domain, stopping at first success:
1. Local cache `data/companies.json` — TTLs: datarequests/overrides=180d, scrape/llm=90d
2. `data/dataowners_overrides.json` — hand-curated records
3. datarequests.org via GitHub API — open-source GDPR DB, matches on domain root + company name words, verifies against company's `runs` array
4. `privacy_page_scraper.py` — tries `/privacy-policy`, `/privacy`, `/legal/privacy`, `/gdpr`; regex-extracts `privacy@`/`dpo@` emails and DSAR portal URLs
5. `llm_searcher.py` — Claude Haiku with `web_search_20250305` tool, `max_uses=1`, ~$0.025/call

All successful lookups are written back to `data/companies.json`. `cost_tracker.py` records every LLM call and prints a summary table at end of run.

**Stage 3 — Compose** (`letter_engine/`): `composer.py` fills `templates/sar_email.txt` or `templates/sar_postal.txt` based on `CompanyRecord.contact.preferred_method`. `sender.py` prints a preview, prompts Y/N, then sends via Gmail API (`gmail.send` scope) or prints instructions for portal/postal. `tracker.py` logs sent letters to `user_data/sent_letters.json` and records the Gmail thread ID for reply tracking.

**Stage 4 — Monitor** (`reply_monitor/`): After letters are sent, `monitor.py` polls Gmail for replies and updates `user_data/reply_state.json`. Key modules:
- `fetcher.py` — fetches new messages in each SAR's Gmail thread
- `classifier.py` — classifies each reply with one or more tags (e.g. `REQUEST_ACCEPTED`, `IDENTITY_REQUIRED`, `DATA_PROVIDED_LINK`, `BOUNCE_PERMANENT`, `NON_GDPR`, ~20 total). Uses regex first, falls back to Claude Haiku. Extracts all data download URLs into `data_links` list (not just the first). Includes body-level detection for self-service deflections (`_RE_BODY_WRONG_CHANNEL`) and Zendesk-format linked attachments (`_RE_ZENDESK_ATTACHMENT_A/B`).
- `attachment_handler.py` — downloads and catalogs email attachments (zip/json/csv) to `user_data/received/<domain>/`
- `link_downloader.py` — downloads data export links using Playwright (handles Cloudflare-protected pages)
- `schema_builder.py` — LLM-powered analysis of received data exports to produce a structured schema
- `state_manager.py` — loads/saves per-account state, computes SAR status (`PENDING`, `ACKNOWLEDGED`, `ACTION_REQUIRED`, `COMPLETED`, `OVERDUE`, `DENIED`, `BOUNCED`, `EXTENDED`)

**Dashboard** (`dashboard/app.py`): Flask web UI on port 5001. Routes: `/` (all companies), `/company/<domain>` (reply thread), `/data/<domain>` (data catalog), `/cards` (with/without data listing), `/costs` (LLM cost history), `/refresh` (runs monitor inline).

**Auth** (`auth/gmail_oauth.py`): Centralised OAuth2 logic. Tokens are stored per-account in `user_data/tokens/{email}_readonly.json` and `{email}_send.json`. Auto-migrates legacy flat `token.json`/`token_send.json` on first run.

**Config** (`config/settings.py`): Pydantic `Settings` model loaded from `.env` at project root. Fields: `GOOGLE_CLIENT_ID/SECRET`, `ANTHROPIC_API_KEY`, `USER_FULL_NAME`, `USER_EMAIL`, `USER_ADDRESS_*`, `GDPR_FRAMEWORK`.

## Key constraints

- `data/companies.json` stores public contact info only — committed to repo, never contains PII
- `user_data/` is gitignored — contains OAuth tokens and the sent letters log
- Gmail OAuth tokens are stored per-account in `user_data/tokens/` as `{email}_readonly.json` and `{email}_send.json` (legacy flat `token.json`/`token_send.json` auto-migrated on first run)
- LLM is last resort only — free lookup paths always attempted first
- `record_llm_call()` must be called **after** JSON extraction so `found=` reflects the actual result

## Data models

`contact_resolver/models.py` is the source of truth. `CompanyRecord` is the central type — flows from resolver through composer into `SARLetter`. `preferred_method` on `Contact` drives which template and dispatch path is used (`email` / `portal` / `postal`).

## Testing

All tests in `tests/unit/` use dependency injection or `unittest.mock` — no real network, Gmail, or Anthropic calls. `ContactResolver` accepts injectable `http_get`, `llm_search`, and `privacy_scrape` callables. Mock Anthropic responses must set `response.usage.input_tokens` and `response.usage.output_tokens` as integers (not MagicMock auto-attributes) or cost recording will fail.

## Known Issues / Tech Debt

Issues discovered in code review (2026-03-16). Fixed items marked ✓.

| Priority | File | Issue | Status |
|----------|------|-------|--------|
| P1 | `contact_resolver/llm_searcher.py` | ✓ Greedy regex in `_extract_json()` — use `json.JSONDecoder().raw_decode()` instead | Fixed |
| P1 | `contact_resolver/llm_searcher.py` | ✓ `max_uses=1` too restrictive — raised to 2 | Fixed |
| P1 | `letter_engine/sender.py` | ✓ `MIMEText(body)` defaults to us-ascii — specify utf-8 | Fixed |
| P1 | `contact_resolver/cost_tracker.py` | ✓ `_persist()` swallowed exception silently — now prints warning | Fixed |
| P1 | `contact_resolver/cost_tracker.py` | ✓ `cost_log.json` grew unbounded — rotates at 1000 entries | Fixed |
| P1 | `contact_resolver/resolver.py` | ✓ GitHub API rate limit silently ignored — warns when < 10 remaining | Fixed |
| P1 | `contact_resolver/resolver.py` | ✓ `dataowners_override` `last_verified` not refreshed on load — causes infinite stale loop | Fixed |
| P2 | `reply_monitor/classifier.py` | ✓ `alerts@` scored +2 (too aggressive) — reduced to +1 | Fixed |
| P2 | `reply_monitor/classifier.py` | ✓ LLM called multiple times for identical auto-replies — added dedup cache | Fixed |
| P2 | `reply_monitor/state_manager.py` | ✓ `days_remaining(None)` crashed — now None-safe | Fixed |
| P2 | `reply_monitor/link_downloader.py` | ✓ Missing Playwright binaries gave opaque error — now prints install hint | Fixed |
| P2 | `reply_monitor/classifier.py` | ✓ Notification-shell emails (data behind a body link, no keywords in snippet) not tagged `DATA_PROVIDED_LINK` — link-first promotion + `_is_data_url()` guard added | Fixed |
| P2 | `reply_monitor/classifier.py` | ✓ Zendesk-format linked attachments (`Attachment(s): file.zip - URL` in body) not detected as data delivery — `_RE_ZENDESK_ATTACHMENT_A/B` added | Fixed |
| P2 | `reply_monitor/classifier.py` | ✓ Self-service deflection responses buried in body not tagged `WRONG_CHANNEL` — `_RE_BODY_WRONG_CHANNEL` body-level pass added | Fixed |
| P2 | `reply_monitor/classifier.py` | ✓ Multi-file data deliveries only tracked/downloaded first URL — `data_links` list added | Fixed |
| P2 | `reply_monitor/monitor.py` | ✓ Auto-downloader only followed first data URL — now iterates full `data_links` list | Fixed |
| P3 | `reply_monitor/schema_builder.py` | ✓ `max_tokens=2048` too low for large exports — raised to 4096 | Fixed |
| P3 | `reply_monitor/schema_builder.py` | ✓ Context could exceed 60 KB — dynamic per-file truncation added | Fixed |
| P3 | `contact_resolver/privacy_page_scraper.py` | ✓ Email regex matched `privacy@localhost` etc. — requires 2-char TLD | Fixed |
| P3 | `dashboard/app.py` | Zero test coverage | Open |
| — | `run.py` | ✓ No LLM call cap — `--max-llm-calls N` flag added | Fixed |
| — | Scaling | GitHub API rate limit (60/hour) will block 500+ company runs — add `GITHUB_TOKEN` to `.env` | Open |

**LLM cost projections at 500+ companies (cold cache):**
- Resolver (step 5): ~$0.025/company → $12.50 per cold run; drops to ~$1 once cache warms
- Classifier fallback: ~$0.010/reply → $5 per monitor cycle
- Schema builder: ~$0.080/export → run only on demand
