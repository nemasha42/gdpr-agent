# Reply Monitor — Detailed Documentation

> Back to @ARCHITECTURE.md for the system overview and module map.

---

## Overview

**What it does:** Polls Gmail for replies to sent SARs, classifies each reply into one or more structured tags, downloads any attached or linked data packages, and maintains a per-domain status derived from the accumulated reply history.

**How it works:** `monitor.py` is the CLI entry point. It loads `sent_letters.json` to get the list of sent SARs (including their Gmail thread IDs), then for each SAR calls `fetcher.fetch_replies_for_sar()`.

---

## Fetcher (`reply_monitor/fetcher.py`)

`fetcher.py` uses the Gmail thread ID when available — it fetches all messages in the thread and filters out the user's own outgoing message by comparing the `From` header to the authenticated user's email. When no thread ID is available (portal/postal letters), it falls back to two Gmail search queries: one for exact sender address match and one for domain match. Messages are deduplicated against already-seen message IDs. Skips only the first message (the original sent letter); subsequent outgoing messages (user's manual Gmail replies) are returned with `from_self=True`. `monitor.py` converts `from_self` messages into `ReplyRecord` with `tags=["YOUR_REPLY"]` without LLM classification. `YOUR_REPLY` is excluded from status computation in `state_manager.py` and from company reply counts on dashboard cards — it is display-only.

---

## Classifier (`reply_monitor/classifier.py`)

Each new message is passed to `classifier.classify()`, which applies a three-pass strategy:

### Pass 0 — NON_GDPR pre-pass

Scores the message on five independent signals: strong marketing local parts (`news`, `digest`, `jobs`, `marketing`, `career`, `noreply-jobs`, `community`, `newsletters`) score +2; `alerts@` scores +1 (reduced from +2 after review, since GDPR-compliant services legitimately send breach alerts from `alerts@`); display names containing marketing keywords score +1; subjects matching newsletter/job-alert patterns score +1; snippets containing unsubscribe language score +1; zero-width Unicode characters (newsletter email-client spacers) score +1. A threshold of 2 or more signals triggers early return of `["NON_GDPR"]` — these messages are invisible to all status computations.

### Pass 1 — Regex

18 compiled patterns match against the `from`, `subject`, and `snippet` fields to produce tags. Multiple tags can fire on a single message. If `BOUNCE_PERMANENT` and `BOUNCE_TEMPORARY` both fire, only `BOUNCE_TEMPORARY` is kept (the 4xx transient signal overrides the 5xx permanent one). If the message has an attachment and no `DATA_PROVIDED_LINK` tag fired, `DATA_PROVIDED_ATTACHMENT` is added.

### Body-level pass

After Pass 1, the full decoded message body is scanned for additional patterns that are commonly absent from snippets. `_RE_BODY_WRONG_CHANNEL` detects **self-service deflection** responses — where the company tells the user to manage their own data via an account portal rather than delivering it directly. `_RE_BODY_WRONG_CHANNEL` is intentionally conservative (requires specific deflection phrases such as "available to you through our tools" or "sign in to your account to manage your data") to avoid false positives on bodies that merely contain account links. `_RE_BODY_INLINE_DATA` detects structured personal data provided directly in the email body, replacing false `DATA_PROVIDED_ATTACHMENT` tags that fire on CID inline images. `_RE_ZENDESK_ATTACHMENT_A/B` patterns detect Zendesk-format linked attachments (`filename.zip\nURL` and `Attachment(s): filename.zip - URL`). Closure detection patterns (Zendesk "set to Solved", premature ticket closure) are included with a post-pass guard that suppresses WRONG_CHANNEL when a terminal data tag is already present.

### URL extraction

`_extract()` searches the full body for download links using a four-pass strategy: (A) Zendesk/support-platform expanded format — `filename.zip\nURL` on separate lines, which correctly handles multi-file deliveries without URL concatenation; (B) generic download URL patterns (token URLs, path-based `/download/` or `/export/` segments, token query params); (C) Zendesk compact inline format — `Attachment(s): filename.zip - URL`; (D) any URL within 400 chars of data/export/download keywords. All matching URLs are collected into `data_links` (list) in addition to `data_link` (first URL, kept for backward compatibility). Unicode smart quotes and HTML artefacts are stripped from all extracted URLs.

### Link-first promotion

Handles **notification-shell emails** — where the body is a brief "your export is ready" message containing a download URL, but the subject and snippet contain no recognisable data-delivery keywords. After extraction, if `data_link` is populated, `_is_data_url()` validates that the URL points to a data file (requires a downloadable extension, a `/download/`-style path, or a token query param) before tagging `DATA_PROVIDED_LINK`. This guard prevents generic account or privacy-policy URLs from triggering false positives.

### Junk URL filter

`_RE_JUNK_URL` + `_is_junk_url()` filters Zendesk ticket URLs, survey URLs, help center paths (`/requests/`, `/support/tickets/`, `/help/`), and vendor/sub-processor pages from all extraction passes. Prevents false positives on `data_link` and `portal_url`.

### `extracted` field schema

All keys always present, empty/null if not found: `reference_number` (ticket/case ref), `confirmation_url` (URL to confirm request), `data_link` (first export URL, backward compat), `data_links` (all export URLs), `portal_url` (self-service portal), `deadline_extension_days` (integer or null), `summary` (plain-English ≤15-word sentence — LLM path only, empty string on regex path).

### `extracted` field reliability

`data_link` and `portal_url` can contain false positives — e.g. a privacy policy URL misclassified as a data export link, or multiple URLs concatenated. Templates gate link display on reply tags (see @docs/dashboard-routes.md). Do not trust `extracted` URLs without checking the reply's tags.

### Pass 2 — LLM fallback

Only triggers if the regex produced no tags, or produced only `AUTO_ACKNOWLEDGE`. The LLM prompt now includes the first 500 chars of the body (not just the snippet) and explicit guidance to tag `DATA_PROVIDED_LINK` when a download URL is present. Results are cached in a module-level `_llm_cache` dict keyed by `(from_addr, subject)` to prevent re-classifying identical auto-replies from the same company. The cache is in-memory only and resets between runs.

---

## Attachment Handler (`reply_monitor/attachment_handler.py`)

After classification, `attachment_handler.py` downloads any Gmail attachment parts and catalogs their contents. For ZIP files it recursively lists files and guesses data categories from filenames. For JSON and CSV files it extracts top-level keys and column headers respectively.

---

## State Manager (`reply_monitor/state_manager.py`)

`state_manager.py` loads and saves `user_data/reply_state.json`, which is partitioned by account email. It maintains a `CompanyState` for each domain with all accumulated `ReplyRecord` objects. The derived `status` is computed on demand by `compute_status(state)` — never stored in JSON. It returns one of 7 unified statuses used by both SAR and SP streams. `compute_done_reason(state)` returns a sub-label for DONE status.

### The 7 statuses

| Priority | Status | Color | Meaning |
|----------|--------|-------|---------|
| 7 | `OVERDUE` | danger (red) | Past 30-day GDPR deadline, no terminal resolution |
| 6 | `ACTION_NEEDED` | warning (yellow) | Company asked for something (identity verification, confirmation, portal redirect) — user must act |
| 5 | `STALLED` | danger (red) | Delivery failed — email bounced or all addresses exhausted |
| 4 | `REPLIED` | primary (blue) | User sent a follow-up reply, awaiting company response |
| 3 | `IN_PROGRESS` | info (teal) | Company acknowledged the request or extended the deadline |
| 2 | `WAITING` | primary (blue) | Request sent, no substantive response yet (default state) |
| 1 | `DONE` | success/secondary | Terminal — request resolved one way or another |

Priority drives dashboard sort order via `_STATUS_PRIORITY` dict. `REQUEST_STATUSES` list in `models.py` is the canonical list of 7 values.

### Status resolution logic (`compute_status`)

`compute_status(state)` evaluates checks in a fixed order. The first check that matches wins — later checks are skipped. This is the complete evaluation sequence:

**Step 0 — Gather tags.** Collect all tags from `state.replies`, skipping `NON_GDPR` and `YOUR_REPLY` replies (these are invisible to status computation). Also scan `past_attempts` for data-terminal tags (`DATA_PROVIDED_*`, `FULFILLED_DELETION`) so a company that provided data on a previous address attempt retains DONE status even after a retry.

**Step 1 — Address exhausted → `STALLED`.** If `state.address_exhausted` is True (all known addresses bounced and no alternatives found), return immediately. This is checked first because it is a hard dead end — no further monitoring is possible.

**Step 2 — Bounce recency → `STALLED`.** If `BOUNCE_PERMANENT` is in the gathered tags, compare timestamps: find the most recent bounce and the most recent non-bounce reply. If the bounce is the latest event (no subsequent reply superseded it), return `STALLED`. If a real reply arrived after the bounce, discard the bounce tag and fall through to later checks. This prevents a single early bounce from permanently blocking status when the company eventually replied from a different address.

**Step 3 — Overdue → `OVERDUE`.** Parse `state.deadline` (ISO date, 30 days from `sar_sent_at`). If today's date exceeds the deadline AND no terminal tags are present, return `OVERDUE`. Terminal tags take precedence — a company that provided data on day 35 is `DONE`, not `OVERDUE`.

**Step 4 — Terminal tags → `DONE`.** If any tag in `_TERMINAL_TAGS` is present, return `DONE`. Terminal tags: `DATA_PROVIDED_LINK`, `DATA_PROVIDED_ATTACHMENT`, `DATA_PROVIDED_PORTAL`, `DATA_PROVIDED_INLINE`, `FULFILLED_DELETION`, `REQUEST_DENIED`, `NO_DATA_HELD`, `NOT_GDPR_APPLICABLE`. This check comes before action tags — if the company already fulfilled the request, stale action items (identity verification, confirmation) are moot.

**Step 5 — Action tags → `ACTION_NEEDED` or `REPLIED`.** Collect all replies that have at least one tag in `_ACTION_TAGS` (`CONFIRMATION_REQUIRED`, `IDENTITY_REQUIRED`, `MORE_INFO_REQUIRED`, `WRONG_CHANNEL`, `HUMAN_REVIEW`, `PORTAL_VERIFICATION`). If any exist:
- Check if ALL action replies are resolved: `reply_review_status` is `"sent"` or `"dismissed"`.
- If not all resolved, check if a `YOUR_REPLY` exists with a timestamp later than the latest action reply (user replied via Gmail directly, bypassing the dashboard draft flow).
- If all resolved → `REPLIED` (user acted, awaiting company response).
- If unresolved actions remain → `ACTION_NEEDED`.

**Step 6 — Acknowledgement / extension → `IN_PROGRESS`.** If `EXTENDED` or any tag in `_ACK_TAGS` (`AUTO_ACKNOWLEDGE`, `REQUEST_ACCEPTED`, `IN_PROGRESS`) is present, the company has acknowledged the request and is working on it.

**Step 7 — Default → `WAITING`.** No tags matched any of the above checks. The request was sent but nothing has happened yet. Portal statuses (`portal_status == "awaiting_verification"`, `"submitted"`, `"awaiting_captcha"`) also fall here — portal submission is informational, not a separate status tier.

### DONE sub-labels (`compute_done_reason`)

When `compute_status()` returns `DONE`, `compute_done_reason(state)` provides a human-readable sub-label checked in this priority order:

| Sub-label | Triggers on tags | Badge color |
|-----------|-----------------|-------------|
| "Data received" | `DATA_PROVIDED_LINK`, `DATA_PROVIDED_ATTACHMENT`, `DATA_PROVIDED_INLINE`, `DATA_PROVIDED_PORTAL` | success (green) |
| "Deletion confirmed" | `FULFILLED_DELETION` | success (green) |
| "Denied" | `REQUEST_DENIED` | secondary (grey) |
| "No data held" | `NO_DATA_HELD` | secondary (grey) |
| "Not applicable" | `NOT_GDPR_APPLICABLE` | secondary (grey) |

Data provision is checked first — if a company both denied and later provided data, the sub-label is "Data received".

### Tag sets

Three internal tag sets drive the status logic:

- **`_TERMINAL_TAGS`** (8 tags): `DATA_PROVIDED_LINK`, `DATA_PROVIDED_ATTACHMENT`, `DATA_PROVIDED_PORTAL`, `DATA_PROVIDED_INLINE`, `FULFILLED_DELETION`, `REQUEST_DENIED`, `NO_DATA_HELD`, `NOT_GDPR_APPLICABLE`
- **`_ACTION_TAGS`** (6 tags): `CONFIRMATION_REQUIRED`, `IDENTITY_REQUIRED`, `MORE_INFO_REQUIRED`, `WRONG_CHANNEL`, `HUMAN_REVIEW`, `PORTAL_VERIFICATION`
- **`_ACK_TAGS`** (3 tags): `AUTO_ACKNOWLEDGE`, `REQUEST_ACCEPTED`, `IN_PROGRESS`

Tags not in any of these sets (`BOUNCE_TEMPORARY`, `OUT_OF_OFFICE`, `EXTENDED`) are handled by specific checks or ignored. `NON_GDPR` and `YOUR_REPLY` are filtered out before evaluation begins.

### Day count display rules

- **Active** (WAITING, IN_PROGRESS, ACTION_NEEDED, REPLIED): show "Xd left" countdown from 30-day deadline
- **Hidden** (DONE, STALLED): show "—" with dimmed progress bar
- **Negative** (OVERDUE): show "X days overdue" in red
- **Restart**: after portal verification (`verify_portal()`), deadline resets to 30 days from `portal_verified_at`

### Portal helpers

`set_portal_status(state, portal_status, *, confirmation_ref, screenshot)` updates `CompanyState.portal_status` and logs the transition. `verify_portal(state)` marks portal verification as passed, resets `portal_status` to `"submitted"`, and restarts the 30-day deadline from the verification date. `log_status_transition(state, old, new, reason)` appends to `state.status_log`. `save_portal_submission()` persists portal submission state to reply_state.json — **never** to sent_letters.json.

### `promote_latest_attempt()`

When multiple SAR letters were sent to the same domain (e.g. first address bounced, user retried with a new address), this function ensures the most recent letter is the "active" attempt. Older attempts — along with their replies — are archived into `CompanyState.past_attempts`. Called by `_load_all_states()` on every dashboard load. Portal field preservation: carries forward `portal_status`, `portal_confirmation_ref`, `portal_screenshot`, `portal_verified_at`, `status_log` from the existing `CompanyState` when available — these may have been updated via `verify_portal()` or `set_portal_status()` since the sent record was created. Falls back to the sent record's portal fields only when no existing state exists. `compute_status()` also checks `past_attempts` for terminal tags (DATA_PROVIDED, FULFILLED_DELETION) so a company that received data on a previous attempt retains DONE status.

---

## Link Downloader (`reply_monitor/link_downloader.py`)

For replies tagged `DATA_PROVIDED_LINK`, the monitor iterates the full `data_links` list and attempts to download each linked data package using `link_downloader.py`. Playwright (headless Chromium) is tried first because many data download pages are Cloudflare-protected; if Playwright is not installed, `requests` is used as a fallback.

---

## Schema Builder (`reply_monitor/schema_builder.py`)

After download, `schema_builder.py` sends file samples to Claude Haiku for LLM-powered schema analysis, producing a structured description of what categories of personal data the export contains. `schema_builder` has two entry points: `build_schema(file_path)` for downloaded files (ZIP/JSON/CSV) and `build_schema_from_body(body)` for inline email data (`DATA_PROVIDED_INLINE` replies). Both return `{categories, services, export_meta}` dicts stored as `attachment_catalog` on the reply.

---

## URL Verifier (`reply_monitor/url_verifier.py`)

`url_verifier.py` classifies URLs extracted from replies as `gdpr_portal`, `help_center`, `login_required`, `dead_link`, `survey`, or `unknown`. Layered strategy: (1) fast path via `platform_hints.detect_platform()` for login-required and known platforms; (2) URL path heuristics for surveys and help centers; (3) HTTP fetch + HTML inspection for form/submit detection. `verify_if_needed()` uses 7-day TTL caching. Results stored on `ReplyRecord.portal_verification`. When `monitor.py` classifies a WRONG_CHANNEL/CONFIRMATION_REQUIRED/DATA_PROVIDED_PORTAL reply with a portal URL, it runs verification and auto-submits via `portal_submitter` if the URL is a real GDPR portal.

---

## Key Assumptions

The Gmail thread ID accurately identifies the reply thread. Replies arrive in the same thread as the original SAR email (true for most companies, not true for all). The 30-day deadline is computed from `sar_sent_at` — a company that processes the request in 29 days and 23 hours will not appear as `OVERDUE`.

## Known Limitations

Portal and postal SARs cannot be monitored because there is no thread ID. The `_llm_cache` for the classifier is in-memory only — identical auto-replies processed in separate `monitor.py` runs will each trigger an LLM call. If `sar_sent_at` is `None` or empty (which can happen for portal/postal letters), `days_remaining()` returns 30 and `deadline_from_sent()` returns today + 30 days — a safe default but not meaningful.

---

## LLM Call Sites in Reply Monitor

### Call site 2: `reply_monitor/classifier.py` — `_llm_classify()`

**Why LLM is used here:** The regex pass covers the common well-structured responses (bounces, acknowledgements, data links, denials) confidently. But many replies are conversational — a human wrote "Your request has been noted and we are processing it" with no ticket number format — and these are not matched by any regex. Without an LLM fallback, all such messages would receive `["HUMAN_REVIEW"]`, requiring the user to read every unusual reply manually.

**Prompt strategy:** Classification. The prompt provides the full list of valid tags, asks for a JSON response with `tags` and extracted fields (`reference_number`, `confirmation_url`, `data_link`, `portal_url`, `deadline_extension_days`). `max_tokens=300` is deliberately tight because the expected output is a small JSON object. The model receives `from`, `subject`, `snippet`, and the first 500 characters of the decoded body — the body excerpt is included because some replies (notification-shell emails, Zendesk-style responses) have no useful content in the snippet but contain a download URL or portal redirect in the body. Explicit guidance instructs the model to tag `DATA_PROVIDED_LINK` when a download URL is present, regardless of snippet content.

**Fallback:** If the API call or JSON parsing fails, `_llm_classify()` returns `None` — the classifier then assigns `["HUMAN_REVIEW"]`. This is the correct degradation: the message is flagged for manual review rather than silently classified.

**Deduplication:** Results are cached in a module-level `_llm_cache: dict[tuple[str, str], dict | None]` keyed by `(from_addr, subject)`. Identical auto-replies (e.g. the same acknowledgement format sent by the same company in response to multiple SARs) only trigger one LLM call per monitor session. The cache resets between runs.

**Cost:** ~$0.010 per unique classification call. At 500 companies each receiving one reply, with ~30% going to LLM (regex handles 70%), cost is ~$1.50 per monitor cycle.

---

### Call site 3: `reply_monitor/schema_builder.py` — `_call_llm()`

**Why LLM is used here:** GDPR data exports arrive as ZIP files containing dozens of JSON and CSV files with company-specific naming conventions. Inferring what personal data these files represent from filename patterns alone is unreliable — `activity.json` could mean search history, purchase history, or something else entirely. The LLM can read sample content and produce human-readable category descriptions (e.g. "Job Applications", "Search History", "Profile Data") aligned with the dataowners.org card format.

**Prompt strategy:** Open reasoning within a structured output constraint. Up to 25 files are sampled (first `min(2000, 60000 // num_files)` bytes each, to keep total context under ~60 KB). The prompt asks for a JSON object with `categories` (name, description, fields with examples), `services` (products the company offers), and `export_meta` (format, delivery method, timeline). `max_tokens=4096` is set high because the output schema can be large for complex exports.

**Fallback:** Any exception (API error, JSON parse failure) returns `{}` — the catalog is saved without schema metadata, and the dashboard shows the file list without category descriptions.

**Cost:** ~$0.080 per export (roughly 5,000 input tokens for file samples + 1,000 output tokens). This call only happens when a data package is actually downloaded, which is optional and user-triggered (either via the dashboard's scan button or automatic download after `DATA_PROVIDED_LINK` classification). At 500 companies with 500 data packages, cost would be ~$40 — but in practice only a subset of companies will provide data within a given run.
