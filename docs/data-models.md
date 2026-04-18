# Data Models — Detailed Documentation

> Back to @ARCHITECTURE.md for the system overview and module map.

---

## `data/companies.json` (committed to repo)

The public GDPR contact cache. Safe to commit because it contains only publicly stated contact information — no PII. Keyed by registrable domain (e.g. `"spotify.com"`), each value is a serialised `CompanyRecord`.

**Key fields:**
- `company_name` — display name (e.g. "Spotify")
- `legal_entity_name` — GDPR controller legal name (e.g. "Spotify AB")
- `source` — which resolver step populated this: `"datarequests"`, `"llm_search"`, `"user_manual"`, `"dataowners_override"`, or `"privacy_scrape"`
- `source_confidence` — `"high"`, `"medium"`, or `"low"` (low records are never returned)
- `last_verified` — ISO date string; staleness is computed against this
- `contact.dpo_email` / `contact.privacy_email` — GDPR contact emails (prefer DPO if both present)
- `contact.gdpr_portal_url` — web portal URL if the company prefers portal over email
- `contact.privacy_policy_url` — URL of the privacy policy page (populated by privacy page scraper in Step 4; shown on company detail page)
- `contact.preferred_method` — `"email"`, `"portal"`, or `"postal"`
- `flags.portal_only` — if true, email is not accepted; letter engine skips email dispatch
- `request_notes.special_instructions` — free text shown to user before composing letter
- `request_notes.identity_verification_required` — flag; shown in dashboard action hints
- `portal_field_mapping` — optional cached `PortalFieldMapping` with `cached_at`, `platform`, `fields: list[PortalFormField]`, `submit_button` — 90-day TTL. Used by `form_analyzer.py` to avoid re-analyzing portal forms.

**What breaks if malformed:** `CompanyRecord.model_validate_json()` is called on load; any schema violation causes the entire DB to be treated as empty (`CompaniesDB()` is returned) and all cached contacts are lost, forcing a fresh resolution run. The error is now logged via `[resolver] _load_db failed: {exc}`.

---

## `data/dataowners_overrides.json` (committed to repo)

Hand-curated high-confidence records for major services. Same schema as individual `companies.json` entries. The resolver reads this in Step 2 and always sets `last_verified = date.today()` on the returned record before saving it to the cache — this prevents an infinite stale loop that would occur if a static date in this file were older than the 180-day TTL.

**What breaks if malformed:** `json.loads()` failure is caught; the file is treated as empty and Step 2 is skipped silently.

---

## `user_data/sent_letters.json` (gitignored)

Append-only log of sent SAR letters. Created by `tracker.record_sent()`. Read by both `monitor.py` and the dashboard to enumerate which companies have been contacted and what thread IDs to poll.

**Key fields per entry:**
- `sent_at` — ISO datetime (e.g. `"2026-02-01T14:23:00"`)
- `company_name` — display name used to label the state entry
- `method` — `"email"`, `"portal"`, or `"postal"`
- `to_email` — recipient address; used by fetcher as a search query fallback
- `subject` — subject line used; stored for reference
- `gmail_message_id` / `gmail_thread_id` — IDs returned by Gmail API after send; `gmail_thread_id` is the primary key for reply monitoring. Both are empty strings for portal/postal letters.

**What breaks if malformed:** `json.loads()` failure is caught by `get_log()`, which returns `[]`. This means a corrupt file causes all monitoring and dashboard state to appear empty — the user's SAR history effectively vanishes until the file is repaired manually.

---

## `user_data/reply_state.json` (gitignored)

Per-account, per-domain reply state. Written by `state_manager.save_state()` after every monitor run.

**Top-level structure:** `{ "<safe_email_key>": { "<domain>": <CompanyState> } }` where the safe email key replaces `@` with `_at_` and `.` with `_` (e.g. `"jane_at_example_com"`).

### CompanyState fields

- `domain` / `company_name` — identity
- `sar_sent_at` — ISO datetime of the sent letter (copied from `sent_letters.json`)
- `to_email` / `subject` / `gmail_thread_id` — mirrored from sent record
- `deadline` — ISO date, 30 days from `sar_sent_at`; computed at state creation
- `replies` — list of `ReplyRecord` objects in receipt order
- `last_checked` — ISO datetime of the last monitor poll
- `past_attempts` — archived older attempts, each with `to_email`, `gmail_thread_id`, `sar_sent_at`, `deadline`, `replies`. Populated by `promote_latest_attempt()` when a retry is detected.
- `address_exhausted: bool` — all known addresses bounced; triggers `ADDRESS_NOT_FOUND` status
- `portal_submission: dict | None` — portal submission tracking: `{status, submitted_at, portal_url, confirmation_ref, error}`. Status: `"submitted"` (auto or manual), `"manual"` (needs manual — e.g. reCAPTCHA blocked), `"failed"`. Persisted by `save_portal_submission()`.
- `portal_status: str` — `""` | `"submitted"` | `"awaiting_verification"` | `"awaiting_captcha"` | `"manual"` | `"failed"`. Drives `PORTAL_SUBMITTED`/`PORTAL_VERIFICATION` SAR statuses. Updated by `set_portal_status()` and `verify_portal()`.
- `portal_verified_at: str` — ISO datetime when portal verification was confirmed. Set by `verify_portal()`, which also resets `deadline` to 30 days from this date.
- `portal_confirmation_ref: str` — reference/ticket number returned by the portal.
- `portal_screenshot: str` — path to confirmation screenshot.
- `status_log: list[dict]` — status transition audit log, each entry `{from, to, at, reason}`. Appended by `log_status_transition()`.

### ReplyRecord fields

- `gmail_message_id` — dedup key; ensures the same message is never processed twice
- `received_at` — ISO datetime
- `from_addr`, `subject`, `snippet` — raw Gmail fields
- `tags` — list of classification tags
- `extracted` — dict with `reference_number`, `confirmation_url`, `data_link` (first URL, for backward compat), `data_links` (all URLs — multi-file deliveries like Substack send multiple ZIPs), `portal_url`, `deadline_extension_days`, `wrong_channel_instructions` (short phrase from the reply body describing what the company says to do, empty if not WRONG_CHANNEL), `login_required` (bool — true if the company says user must log in)
- `llm_used` — boolean; shown as an indicator in the dashboard
- `has_attachment` / `attachment_catalog` — attachment metadata if downloaded
- `suggested_reply: str` — LLM-generated draft follow-up text (empty if not generated)
- `reply_review_status: str` — `""` (unseen) | `"pending"` (draft ready) | `"sent"` (user replied) | `"dismissed"` | `"portal_submitted"` (user submitted via portal manually)
- `sent_reply_body: str` — actual text the user sent (may differ from `suggested_reply` if edited before sending)
- `sent_reply_at: str` — ISO 8601 UTC timestamp of when the user sent the follow-up
- `portal_verification: dict | None` — URL verification result: `{url, classification, checked_at, error, page_title}`. Classification values: `gdpr_portal`, `help_center`, `login_required`, `dead_link`, `survey`, `unknown`. Set by `monitor.py` when a reply has a portal URL and tags include WRONG_CHANNEL, CONFIRMATION_REQUIRED, or DATA_PROVIDED_PORTAL.

**What breaks if malformed:** `json.JSONDecodeError` is caught in both `load_state()` and `save_state()` — a corrupt file causes the account's state to reset to empty, losing all reply history for that account. The monitor will re-fetch and re-classify all messages on the next run (duplicate detection by `gmail_message_id` prevents duplicate entries, but the LLM fallback may be called again for previously-classified messages).

---

## `user_data/cost_log.json` (gitignored)

Persistent log of every LLM API call made. Appended to by `cost_tracker._persist()` on every LLM call during production runs (skipped during pytest via `PYTEST_CURRENT_TEST` env check). Rotates at 1,000 entries — oldest entries are dropped.

**Key fields per entry:**
- `timestamp` — ISO datetime
- `source` — `"contact_resolver"` or `"reply_classifier"` or `"schema_builder"`
- `company_name` — which company triggered the call
- `model` — model ID (always `"claude-haiku-4-5-20251001"` currently)
- `input_tokens` / `output_tokens` / `cost_usd` — for accounting
- `found` — boolean; whether the LLM actually returned usable data

**What breaks if malformed:** `load_persistent_log()` catches all exceptions and returns `[]`. The cost dashboard and cumulative totals will show zero history but the pipeline continues normally.

---

## `user_data/subprocessor_requests.json` (gitignored)

Log of sent subprocessor disclosure request letters. Created by `record_subprocessor_request(letter, domain)`. Same structure as `sent_letters.json` entries but tracked separately. **Must never be mixed with `sent_letters.json`** — see SP letter invariant in @docs/pipeline-stages.md.

---

## Portal Automation Models (`portal_submitter/models.py`)

Not persisted as standalone files — these are runtime types:

- **`PortalResult`** — returned by `submit_portal()`: `success: bool`, `needs_manual: bool`, `confirmation_ref: str`, `screenshot_path: str`, `error: str`, `portal_status: str`.
- **`CaptchaChallenge`** — bridges portal submission to dashboard CAPTCHA UI: `domain`, `portal_url`, `created_at`, `status`, `solution`, `screenshot_path`. Files stored in `user_data/captcha_pending/{domain}.png|.json`.
