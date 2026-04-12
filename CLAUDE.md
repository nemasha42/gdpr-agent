# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run all unit tests
.venv/bin/pytest tests/unit/ -q

# Run a single test file
.venv/bin/pytest tests/unit/test_resolver.py -v

# Run a single test by name
.venv/bin/pytest tests/unit/test_resolver.py::TestContactResolver::test_cache_hit -v

# Full pipeline (real Gmail + sending)
python run.py

# Full pipeline — preview only, nothing sent
python run.py --dry-run

# Smoke test Phase 4 letter engine interactively
python test_phase4.py --dry-run

# Full pipeline — portal-method companies only
python run.py --portal-only

# Monitor Gmail for SAR replies (prints summary table)
python monitor.py [--account EMAIL] [--verbose]

# Test portal automation interactively
python test_portal.py --list-portals                    # Show all portal companies
python test_portal.py --domain glassdoor.com --dry-run  # Analyze form only
python test_portal.py --domain glassdoor.com            # Full submission

# Web dashboard (Flask, port 5001)
python dashboard/app.py
```

## Architecture

The pipeline runs in five stages:

```
Gmail inbox → service detection → contact resolution → SAR letter send → reply monitoring + subprocessor discovery
```

**Stage 1 — Scan** (`scanner/`): `inbox_reader.py` fetches email headers only (no body, `gmail.readonly` scope). `service_extractor.py` classifies senders as HIGH/MEDIUM/LOW confidence services and deduplicates by domain. `company_normalizer.py` maps raw domains to display names (strips noise subdomains, handles co.uk/com.au, hardcoded exceptions for t.co → Twitter/X etc.).

**Stage 2 — Resolve** (`contact_resolver/`): `resolver.py` runs a 5-step chain per domain, stopping at first success. `write_subprocessors(domain, record)` persists a `SubprocessorRecord` into `data/companies.json`; if the domain has no existing entry it creates a minimal stub (`source="llm_search"`, `source_confidence="low"`) so subprocessors are stored for all SAR domains regardless of whether contact resolution succeeded.
1. Local cache `data/companies.json` — TTLs: datarequests/overrides=180d, scrape/llm=90d
2. `data/dataowners_overrides.json` — hand-curated records
3. datarequests.org via GitHub API — open-source GDPR DB, matches on domain root + company name words, verifies against company's `runs` array
4. `privacy_page_scraper.py` — tries `/privacy-policy`, `/privacy`, `/legal/privacy`, `/gdpr`; regex-extracts `privacy@`/`dpo@` emails and DSAR portal URLs
5. `llm_searcher.py` — Claude Haiku with `web_search_20250305` tool, `max_uses=2`, ~$0.025/call

All successful lookups are written back to `data/companies.json`. `cost_tracker.py` records every LLM call and prints a summary table at end of run.

**Stage 3 — Compose & Send** (`letter_engine/` + `portal_submitter/`): `composer.py` fills `templates/sar_email.txt` or `templates/sar_postal.txt` based on `CompanyRecord.contact.preferred_method`. For `portal` method, `composer.py` sets `SARLetter.portal_url` from `record.contact.gdpr_portal_url`. `sender.py` dispatches based on method: `email` sends via Gmail API, `portal` delegates to `portal_submitter.submit_portal()` for automated form submission (with graceful fallback to manual instructions on failure), and `postal` prints instructions. `tracker.py` logs sent letters to `user_data/sent_letters.json` and records the Gmail thread ID for reply tracking; portal entries additionally store `portal_status`, `portal_confirmation_ref`, and `portal_screenshot`. A parallel path exists for subprocessor disclosure requests: `compose_subprocessor_request(record) → SARLetter | None` uses `templates/subprocessor_request_email.txt` / `subprocessor_request_postal.txt` (cites CJEU C-154/21 and EDPB Opinion 22/2024, requests AI providers, data brokers, advertising platforms by name) and logs to `user_data/subprocessor_requests.json` via `record_subprocessor_request(letter, domain)`. Returns `None` if the record has no email contact and method is not postal.

**Portal Automation** (`portal_submitter/`): Automates SAR submission via GDPR web portals using Playwright. Seven modules:
- `submitter.py` — entry point `submit_portal(letter, scan_email)`; detects platform, analyzes form, fills & submits, handles CAPTCHA/OTP, captures screenshots, extracts confirmation references. Falls back to manual instructions on failure.
- `models.py` — `PortalResult` (success, needs_manual, confirmation_ref, screenshot_path, error, portal_status) and `CaptchaChallenge` (domain, portal_url, created_at, status, solution, screenshot_path).
- `form_analyzer.py` — LLM-powered (Claude Haiku) form field analysis. Extracts form fields from the accessibility tree, calls LLM to map user data to fields. Results cached in `CompanyRecord.portal_field_mapping` with 90-day TTL. Cost tracked via `cost_tracker`.
- `form_filler.py` — Playwright automation: fills textbox/combobox/checkbox fields, detects CAPTCHA (reCAPTCHA, hCaptcha, Cloudflare), clicks submit. Injects stealth JavaScript to bypass automation detection.
- `captcha_relay.py` — bridges CAPTCHA challenges to the dashboard for manual solving. Saves screenshot + challenge JSON to `user_data/captcha_pending/{domain}.png|.json`. Polls for user solution with 5-minute timeout (2-second intervals).
- `otp_handler.py` — handles email verification steps during portal submission. `wait_for_otp()` polls Gmail for verification emails from platform-specific senders. `extract_otp_from_message()` extracts confirmation URLs (preferred) or 6-digit codes. 2-minute timeout.
- `platform_hints.py` — detects portal platform: `onetrust`, `trustarc`, `salesforce`, `login_required` (Google, Apple, Meta, Amazon, Facebook, Twitter/X), `unknown`. `otp_sender_hints()` returns expected verification email senders per platform.

**Stage 4 — Monitor** (`reply_monitor/`): After letters are sent, `monitor.py` polls Gmail for replies and updates `user_data/reply_state.json`. Key modules:
- `fetcher.py` — fetches new messages in each SAR's Gmail thread. Skips only the first message (the original sent letter); subsequent outgoing messages (user's manual Gmail replies) are returned with `from_self=True`. `monitor.py` converts `from_self` messages into `ReplyRecord` with `tags=["YOUR_REPLY"]` without LLM classification. `YOUR_REPLY` is excluded from status computation in `state_manager.py` and from company reply counts on dashboard cards — it is display-only.
- `classifier.py` — classifies each reply with one or more tags (e.g. `REQUEST_ACCEPTED`, `IDENTITY_REQUIRED`, `DATA_PROVIDED_LINK`, `DATA_PROVIDED_INLINE`, `BOUNCE_PERMANENT`, `NON_GDPR`, ~20 total). Uses regex first, falls back to Claude Haiku (`max_tokens=400`). Extracts all data download URLs into `data_links` list (not just the first). Includes body-level detection for self-service deflections (`_RE_BODY_WRONG_CHANNEL`), Zendesk-format linked attachments (`_RE_ZENDESK_ATTACHMENT_A/B`), and inline personal data responses (`_RE_BODY_INLINE_DATA` — detects structured data provided directly in email body, replaces false `DATA_PROVIDED_ATTACHMENT` from CID inline images). **`extracted` field schema** (all keys always present, empty/null if not found): `reference_number` (ticket/case ref), `confirmation_url` (URL to confirm request), `data_link` (first export URL, backward compat), `data_links` (all export URLs), `portal_url` (self-service portal), `deadline_extension_days` (integer or null), `summary` (plain-English one-sentence description — LLM path only, empty string on regex path).
  **`extracted` field reliability:** `data_link` and `portal_url` can contain false positives — e.g. a privacy policy URL misclassified as a data export link, or multiple URLs concatenated into one field. The template (`company_detail.html`) defends against this by gating link display on reply tags (see "Company detail" section). Do not trust `extracted` URLs without checking the reply's tags.
- `attachment_handler.py` — downloads and catalogs email attachments (zip/json/csv) to `user_data/received/<domain>/`
- `link_downloader.py` — downloads data export links using Playwright (handles Cloudflare-protected pages)
- `schema_builder.py` — LLM-powered analysis of received data exports to produce a structured schema. Two entry points: `build_schema(file_path)` for downloaded files (ZIP/JSON/CSV), `build_schema_from_body(body)` for inline email data (`DATA_PROVIDED_INLINE` replies where personal data is provided directly in the email text). Both return `{categories, services, export_meta}` dicts stored as `attachment_catalog` on the reply
- `url_verifier.py` — classifies URLs extracted from replies as `gdpr_portal`, `help_center`, `login_required`, `dead_link`, `survey`, or `unknown`. Layered strategy: (1) fast path via `platform_hints.detect_platform()` for login-required and known platforms; (2) URL path heuristics for surveys and help centers; (3) HTTP fetch + HTML inspection for form/submit detection. `verify_if_needed()` uses 7-day TTL caching. Results stored on `ReplyRecord.portal_verification`. When `monitor.py` classifies a WRONG_CHANNEL/CONFIRMATION_REQUIRED/DATA_PROVIDED_PORTAL reply with a portal URL, it runs verification and auto-submits via `portal_submitter` if the URL is a real GDPR portal.
- `state_manager.py` — loads/saves per-account state, computes SAR status (`PENDING`, `ACKNOWLEDGED`, `ACTION_REQUIRED`, `USER_REPLIED`, `COMPLETED`, `OVERDUE`, `DENIED`, `BOUNCED`, `EXTENDED`, `ADDRESS_NOT_FOUND`). Status resolution rules: (1) terminal tags (`_TERMINAL_TAGS`) override unresolved action tags — if the company already provided data or fulfilled deletion, stale action items are moot; (2) `USER_REPLIED` fires when all action-tagged replies have `reply_review_status` in `("sent", "dismissed")` OR when a `YOUR_REPLY` exists that postdates the latest action-required reply; (3) `ADDRESS_NOT_FOUND` fires when `address_exhausted=True` on the CompanyState.

**Stage 5 — Subprocessors** (`contact_resolver/subprocessor_fetcher.py`): Discovers third-party data processors (subprocessors) for each SAR company. `fetch_subprocessors(company_name, domain)` returns a `SubprocessorRecord`. Strategy: (1) scrape known paths (`/sub-processors`, `/vendors`, etc.) with `requests` for both bare and `www.` domain; (2) `_extract_page_content()` extracts `<table>` elements first (subprocessor pages nearly always use tables), then falls back to a keyword-anchored text window, then full stripped text — a page must yield ≥500 chars of plain text (`_MIN_PLAIN_TEXT`) to be considered non-empty; (3) Playwright fallback for JS-rendered SPAs; (4) Claude Haiku call — tools (`web_search`) only attached when no scraped content was found (saves output tokens for JSON). The background task (`_fetch_all_subprocessors`) in `dashboard/app.py` only skips a domain if it has `fetch_status="ok"` within the 30-day TTL — `not_found` and `error` records are always retried.

**Dashboard** (`dashboard/app.py`): Flask web UI on port 5001. Routes: `/` (all companies), `/company/<domain>` (reply thread), `/data/<domain>` (data catalog), `/cards` (companies with/without data), `/costs` (LLM cost history), `/transfers` (subprocessor data transfer map), `/pipeline` (scan/resolve/send pipeline), `/pipeline/review` (letter review & approve), `/pipeline/reauth-send` (re-authorize gmail.send OAuth), `/refresh` (runs monitor + re-extracts missing links, saves to reply_state.json). Portal automation routes: `POST /portal/submit/<domain>?account=EMAIL` (starts background portal submission, returns 409 if already running), `GET /portal/status/<domain>` (polls task progress — status, success, needs_manual, portal_status, confirmation_ref), `GET /captcha/<domain>` (displays CAPTCHA screenshot + solution form), `POST /captcha/<domain>` (accepts user CAPTCHA solution, resumes portal submission). Background task endpoints: `POST /transfers/fetch` (starts subprocessor fetch task), `GET /api/transfers/task` (polls task progress), `POST /transfers/request-letter/<domain>` (sends subprocessor disclosure request for one company), `POST /transfers/request-all` (background task — sends to all companies with email contact and no prior request, tracked in `user_data/subprocessor_requests.json`).

**Important:** Always use `_load_all_states(account)` (dashboard/app.py) — not `load_state()` — for any route that displays company counts or cards. `_load_all_states()` merges reply_state.json with sent_letters.json via `promote_latest_attempt()` so recently-sent letters appear immediately without waiting for a monitor run. Using `load_state()` directly undercounts by missing companies sent since the last monitor run.

**`promote_latest_attempt()`** (`state_manager.py`): When multiple SAR letters were sent to the same domain (e.g. first address bounced, user retried with a new address), this function ensures the most recent letter is the "active" attempt. Older attempts — along with their replies — are archived into `CompanyState.past_attempts`. Called by `_load_all_states()` on every dashboard load. **Critical invariant:** only SAR letters should exist in `sent_letters.json`. If SP letters leak in, `promote_latest_attempt()` treats the SP letter as the latest SAR attempt, corrupting thread_id, subject, and losing existing replies (see `record=False` constraint). `compute_status()` also checks `past_attempts` for terminal tags (DATA_PROVIDED, FULFILLED_DELETION) so a company that received data on a previous attempt retains COMPLETED status.

**Company detail** (`company_detail.html`): Two-panel layout with a `stream_panel()` Jinja2 macro rendering SAR and SP streams independently. `company_detail()` builds `sar_thread` and `sp_thread` as separate event lists (oldest first). `sp_all_msg_ids` (all SP reply IDs including `YOUR_REPLY`) is used to dedup SAR replies — if a message appears in the SP stream, it is excluded from SAR. Thread events have types: `sent` (outgoing letter), `reply` (company message), `your_reply` (user's manual Gmail reply or dashboard-sent follow-up). NON_GDPR replies are hidden entirely from the detail view (not dimmed). Links in reply messages are gated on tags: "Download data" requires `DATA_PROVIDED_*` or `FULFILLED_DELETION`; "Privacy portal" requires `WRONG_CHANNEL`, `DATA_PROVIDED_PORTAL`, `CONFIRMATION_REQUIRED`, or `MORE_INFO_REQUIRED`; "Confirm request" requires `CONFIRMATION_REQUIRED`. A "View received data" button links to `/data/<domain>` on messages with data provision tags or attachments. Each stream panel includes a "Compose follow-up" collapsible form at the bottom of the thread for free-form replies (POST to `/company/<domain>/compose-reply` or `/compose-sp-reply`). The compose route sends the email, creates an immediate YOUR_REPLY record, and auto-dismisses any pending action drafts.

**Dashboard cards:** Show a "View correspondence" button (no reply count) — styled `btn-outline-primary` when the company has at least one non-`NON_GDPR`, non-`YOUR_REPLY` reply, pale `btn-outline-secondary` otherwise. A "View data" button appears when `has_data` is true (status=COMPLETED with a DATA_PROVIDED tag).

**Snippet display:** Raw Gmail snippets often contain encoding artifacts (HTML entities, MIME quoted-printable, URL encoding). `_clean_snippet(text)` in `dashboard/app.py` decodes these at display time — raw data in `reply_state.json` is never modified. Applied in `company_detail()` for SAR replies, past-attempt replies, and SP replies. `_is_human_friendly(text)` is the paired test predicate; it is not called in production routes.

**Draft reply guard:** `has_pending_draft` (used to show the "Draft reply ready" badge on cards) requires three conditions: `reply_review_status == "pending"`, a non-empty `suggested_reply`, **and** at least one tag in `_ACTION_DRAFT_TAGS` (imported from `reply_monitor.classifier`). The tag guard prevents stale `"pending"` state on AUTO_ACKNOWLEDGE or other non-action replies from showing a false-positive badge. `company_detail.html` applies the same guard (`r.has_action_draft`) before rendering the draft form. When a YOUR_REPLY is detected by the monitor, all pending action drafts for that company are auto-dismissed (user already replied via Gmail, so dashboard drafts are stale). Both `monitor.py` and the dashboard's inline monitors apply this auto-dismiss logic.

**LLM summary:** When `classifier.py` falls back to Claude Haiku, it also populates `extracted["summary"]` — a ≤15-word plain-English sentence. `company_detail.html` shows this in italic instead of the raw snippet when present. Summary is only set on the LLM path (~10–20% of replies); all other replies show the cleaned snippet.

**Tag display:** `_effective_tags(all_tags)` in app.py applies tier-based supersession for cards:
- Tier 1 (terminal): DATA_PROVIDED_*, REQUEST_DENIED, NO_DATA_HELD, NOT_GDPR_APPLICABLE, FULFILLED_DELETION
- Tier 2 (action): WRONG_CHANNEL, IDENTITY_REQUIRED, CONFIRMATION_REQUIRED, MORE_INFO_REQUIRED, HUMAN_REVIEW
- Tier 3 (progress): REQUEST_ACCEPTED, IN_PROGRESS, EXTENDED
- Tier 4 (informational): AUTO_ACKNOWLEDGE, BOUNCE_*
- Always hidden: OUT_OF_OFFICE, NON_GDPR (unless only tag)

Higher tiers supersede lower — e.g. DATA_PROVIDED hides REQUEST_ACCEPTED; WRONG_CHANNEL hides ACK. `_DISPLAY_NAMES` maps raw constants to user-friendly labels. `HUMAN_REVIEW` is in `_ACTION_TAGS` (state_manager.py) so it triggers ACTION_REQUIRED status — a real reply that needs manual reading.

**Company-Level Status (two-stream derived):** `compute_company_status(sar_status, sp_status, sp_sent)` in `state_manager.py` aggregates both the SAR and Subprocessor (SP) streams into one company-level badge shown as the primary badge on dashboard cards. 9 values, priority order (highest first):

| Priority | Value | Condition |
|----------|-------|-----------|
| 8 | `OVERDUE` | Any stream past GDPR deadline |
| 7 | `ACTION_REQUIRED` | Any stream needs user action |
| 6 | `STALLED` | Any stream is BOUNCED or ADDRESS_NOT_FOUND |
| 5 | `USER_REPLIED` | SAR=USER_REPLIED — user sent follow-up, awaiting company response |
| 4 | `DATA_RECEIVED` | SAR terminal (COMPLETED/DENIED); SP sent but not yet terminal |
| 3 | `FULLY_RESOLVED` | SAR terminal + (SP terminal OR SP not sent) |
| 2 | `IN_PROGRESS` | SAR is ACKNOWLEDGED or EXTENDED |
| 1 | `SP_PENDING` | SAR=PENDING + SP sent + SP=PENDING |
| 0 | `PENDING` | Default — SAR pending, SP not sent |

Invariant: **SP can only escalate; `sp_sent=False` never downgrades.** `DATA_RECEIVED` ranks above `FULLY_RESOLVED` in sort urgency because the SP thread is still open. Dashboard cards show `company_status` as the primary badge with a smaller secondary badge showing the raw SAR status. `_COMPANY_STATUS_PRIORITY` dict drives sort order. `COMPANY_LEVEL_STATUSES` list in `models.py` is the canonical list of 9 values.

**Auth** (`auth/gmail_oauth.py`): Centralised OAuth2 logic. Tokens are stored per-account in `user_data/tokens/{email}_readonly.json` and `{email}_send.json`. Auto-migrates legacy flat `token.json`/`token_send.json` on first run.

**Config** (`config/settings.py`): Pydantic `Settings` model loaded from `.env` at project root. Fields: `GOOGLE_CLIENT_ID/SECRET`, `ANTHROPIC_API_KEY`, `USER_FULL_NAME`, `USER_EMAIL`, `USER_ADDRESS_*`, `GDPR_FRAMEWORK`.

## Key constraints

- `data/companies.json` stores public contact info only — committed to repo, never contains PII
- `user_data/` is gitignored — contains OAuth tokens and the sent letters log
- Gmail OAuth tokens are stored per-account in `user_data/tokens/` as `{email}_readonly.json` and `{email}_send.json` (legacy flat `token.json`/`token_send.json` auto-migrated on first run)
- LLM is last resort only — free lookup paths always attempted first
- `record_llm_call()` must be called **after** JSON extraction so `found=` reflects the actual result
- `write_subprocessors()` creates a stub `CompanyRecord` for domains not yet in `data/companies.json` — never skip-on-missing, or subprocessors will silently not persist for companies resolved only via reply_state.json
- Subprocessor background task: only skip `fetch_status="ok"` records within TTL — always retry `not_found` and `error` (a recent failed fetch is not "cached")
- `send_letter(record=False)` must be used for all subprocessor disclosure request sends. SP letters must never be written to `sent_letters.json` — they are tracked separately in `subprocessor_requests.json`. If SP letters leak into `sent_letters.json`, `promote_latest_attempt()` will corrupt SAR state by treating the SP letter as the latest SAR attempt (wrong thread_id, wrong subject, lost replies).
- Portal form field mappings are cached in `CompanyRecord.portal_field_mapping` with 90-day TTL — `form_analyzer.py` checks `cached_at` before re-analyzing. The LLM call is recorded via `cost_tracker`.
- CAPTCHA challenge files are stored in `user_data/captcha_pending/{domain}.png|.json` — cleaned up by `captcha_relay.py` on solution or timeout (5 minutes).
- Portal screenshots are saved to `user_data/portal_screenshots/` for audit trail.
- Portal submissions use Playwright with stealth scripts — `form_filler.py` injects JavaScript to bypass automation detection. Login-required portals (Google, Apple, Meta, Amazon, Facebook, Twitter/X) are detected by `platform_hints.py` and fall back to manual instructions.
- Gmail send tokens (`*_send.json`) can be revoked by Google independently of readonly tokens. Symptoms: letters show "ready" forever, send task completes with 0 sent, no error shown. Diagnosis: run `auth/gmail_oauth.py` → `check_send_token_valid(email)` or visit `/pipeline/reauth-send`. The dashboard pre-flight check in `pipeline_send()` calls `_send_token_valid()` before launching the background task — any send failure that reaches `_dispatch_email` is a token issue.

## Data models

`contact_resolver/models.py` is the source of truth. `CompanyRecord` is the central type — flows from resolver through composer into `SARLetter`. `preferred_method` on `Contact` drives which template and dispatch path is used (`email` / `portal` / `postal`). Portal-related fields: `Contact.gdpr_portal_url` (portal URL), `Flags.portal_only` (true when portal exists but no email), `CompanyRecord.portal_field_mapping` (optional cached `PortalFieldMapping` with `cached_at`, `platform`, `fields: list[PortalFormField]`, `submit_button` — 90-day TTL). `SARLetter.portal_url` is set by `composer.py` when method is `portal`.

`portal_submitter/models.py` defines `PortalResult` (success, needs_manual, confirmation_ref, screenshot_path, error, portal_status) and `CaptchaChallenge` (domain, portal_url, created_at, status, solution, screenshot_path).

`reply_monitor/models.py` defines `ReplyRecord` — the per-reply record stored in `reply_state.json`. Key fields beyond classification data:
- `suggested_reply: str` — LLM-generated draft follow-up text (empty if not generated)
- `reply_review_status: str` — `""` (unseen) | `"pending"` (draft ready) | `"sent"` (user replied) | `"dismissed"`
- `sent_reply_body: str` — actual text the user sent (may differ from `suggested_reply` if edited before sending)
- `sent_reply_at: str` — ISO 8601 UTC timestamp of when the user sent the follow-up
- `portal_verification: dict | None` — URL verification result: `{url, classification, checked_at, error, page_title}`. Classification values: `gdpr_portal`, `help_center`, `login_required`, `dead_link`, `survey`, `unknown`. Set by `monitor.py` when a reply has a portal URL and tags include WRONG_CHANNEL, CONFIRMATION_REQUIRED, or DATA_PROVIDED_PORTAL.

`sent_reply_body` and `sent_reply_at` are populated by `send_followup` / `send_sp_followup` in `dashboard/app.py` at send time and displayed in the company detail thread as a styled sent-message card.

`reply_monitor/models.py` also defines `CompanyState` — the per-domain tracking record stored in `reply_state.json`. Key fields:
- `gmail_thread_id: str` — the Gmail thread ID of the active SAR letter (used by `fetcher.py` to poll for replies)
- `replies: list[ReplyRecord]` — replies to the active attempt
- `past_attempts: list[dict]` — archived older attempts, each with `to_email`, `gmail_thread_id`, `sar_sent_at`, `deadline`, `replies`. Populated by `promote_latest_attempt()` when a retry is detected.
- `address_exhausted: bool` — all known addresses bounced; triggers `ADDRESS_NOT_FOUND` status
- `deadline: str` — ISO date, 30 days from `sar_sent_at`

## Testing

All tests in `tests/unit/` use dependency injection or `unittest.mock` — no real network, Gmail, or Anthropic calls. `ContactResolver` accepts injectable `http_get`, `llm_search`, and `privacy_scrape` callables. Mock Anthropic responses must set `response.usage.input_tokens` and `response.usage.output_tokens` as integers (not MagicMock auto-attributes) or cost recording will fail. Portal automation tests are in `test_portal_submitter.py` — covers models, platform detection, OTP sender hints, `build_user_data()`, `analyze_form()` with LLM mocking and cache expiration, CAPTCHA detection/relay, `fill_and_submit()` with various field types, OTP extraction, `wait_for_otp()` with mock Gmail, full `submit_portal()` workflow, and portal tracking in `sent_letters.json`.

## Known Issues / Tech Debt

Issues discovered in code review (2026-03-16). 22 issues fixed; open items below.

**Open issues:**

| Priority | File | Issue |
|----------|------|-------|
| P3 | `dashboard/app.py` | Flask routes and template rendering have no test coverage — only pure helper functions (`_clean_snippet`, `_is_human_friendly`) are tested via `test_snippet_clean.py` |
| — | Scaling | GitHub API rate limit (60/hour) will block 500+ company runs — add `GITHUB_TOKEN` to `.env` |

<details><summary>Fixed issues (18 items — click to expand)</summary>

| Priority | File | Issue |
|----------|------|-------|
| P1 | `contact_resolver/llm_searcher.py` | Greedy regex in `_extract_json()` — replaced with `json.JSONDecoder().raw_decode()` |
| P1 | `contact_resolver/llm_searcher.py` | `max_uses=1` too restrictive — raised to 2 |
| P1 | `letter_engine/sender.py` | `MIMEText(body)` defaults to us-ascii — specified utf-8 |
| P1 | `contact_resolver/cost_tracker.py` | `_persist()` swallowed exception silently — now prints warning |
| P1 | `contact_resolver/cost_tracker.py` | `cost_log.json` grew unbounded — rotates at 1000 entries |
| P1 | `contact_resolver/resolver.py` | GitHub API rate limit silently ignored — warns when < 10 remaining |
| P1 | `contact_resolver/resolver.py` | `dataowners_override` `last_verified` not refreshed on load — caused infinite stale loop |
| P2 | `reply_monitor/classifier.py` | `alerts@` scored +2 (too aggressive) — reduced to +1 |
| P2 | `reply_monitor/classifier.py` | LLM called multiple times for identical auto-replies — added dedup cache |
| P2 | `reply_monitor/state_manager.py` | `days_remaining(None)` crashed — now None-safe |
| P2 | `reply_monitor/link_downloader.py` | Missing Playwright binaries gave opaque error — now prints install hint |
| P2 | `reply_monitor/classifier.py` | Notification-shell emails not tagged `DATA_PROVIDED_LINK` — link-first promotion + `_is_data_url()` guard |
| P2 | `reply_monitor/classifier.py` | Zendesk-format linked attachments not detected — `_RE_ZENDESK_ATTACHMENT_A/B` added |
| P2 | `reply_monitor/classifier.py` | Self-service deflection in body not tagged `WRONG_CHANNEL` — `_RE_BODY_WRONG_CHANNEL` added |
| P2 | `reply_monitor/classifier.py` | Multi-file data deliveries only tracked first URL — `data_links` list added |
| P2 | `reply_monitor/monitor.py` | Auto-downloader only followed first data URL — now iterates full `data_links` list |
| P2 | `contact_resolver/llm_searcher.py` | LLM accepted generic `support@`/`info@` — `_GENERIC_LOCAL_PARTS` blocklist (confidence-gated) |
| P2 | `reply_monitor/classifier.py` | Gmail snippets displayed with encoding artifacts — `_clean_snippet()` + `extracted["summary"]` |
| P3 | `reply_monitor/schema_builder.py` | `max_tokens=2048` too low — raised to 4096; context capped with dynamic truncation |
| P3 | `contact_resolver/privacy_page_scraper.py` | Email regex matched `privacy@localhost` — requires 2-char TLD |
| — | `run.py` | No LLM call cap — `--max-llm-calls N` flag added |
| P2 | `reply_monitor/classifier.py` | Premature ticket closure (Zendesk "set to Solved") not detected — closure regex patterns added to WRONG_CHANNEL, post-pass guard suppresses when terminal data tag present |
| P2 | `reply_monitor/classifier.py` | Zendesk ticket/survey/help center URLs extracted as data_link/portal_url — `_RE_JUNK_URL` + `_is_junk_url()` filter added to all extraction passes |
| P2 | `reply_monitor/classifier.py` | `_is_data_url()` false positives on vendor/sub-processor pages — covered by `_RE_JUNK_URL` filter |
| P2 | `reply_monitor/classifier.py` | WRONG_CHANNEL draft tone argued GDPR violations — closure-aware prompt now says "follow portal first" |

</details>

**LLM cost projections at 500+ companies (cold cache):**
- Resolver (step 5): ~$0.025/company → $12.50 per cold run; drops to ~$1 once cache warms
- Subprocessor discovery: ~$0.030–0.050/company → $15–25 per cold fetch; free on re-fetch within 30-day TTL
- Classifier fallback: ~$0.010/reply → $5 per monitor cycle (approximate; LLM path now requests `summary` field adding ~30–50 output tokens, negligible per-call impact)
- Schema builder: ~$0.080/export → run only on demand
- Portal form analyzer: ~$0.020/company → one-time per portal company, cached 90 days in `companies.json`
