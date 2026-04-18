# Customer-Naming Audit

> **Phase 1 deliverable.** No changes until approved.
> Scope: `tests/`, `reply_monitor/`, `portal_submitter/`, `dashboard/`, `scanner/`.

---

## Category A — Test names referencing a customer

Each entry: current name, file:line, the **pattern** being tested, and the proposed rename.

### `tests/unit/test_reply_classifier.py`

| # | Current name | Line | Pattern tested | Proposed rename |
|---|---|---|---|---|
| A1 | `test_google_group_rejection` | 52 | Group-permission bounce (no permission to post) | `test_group_permission_bounce` |
| A2 | `test_google_ticket_format` | 87 | Numeric bracketed case ID triggers auto-acknowledge | `test_numeric_case_id_auto_acknowledged` |
| A3 | `test_substack_request_received` | 93 | `[Request received]` bracket subject triggers ACK | `test_bracketed_request_received_subject` |
| A4 | `test_hrtechprivacy_url` (TestConfirmationRequired) | 127 | Snippet with "confirmed it by clicking" triggers CONFIRMATION_REQUIRED | `test_confirm_before_processing_snippet` |
| A5 | `test_hrtechprivacy_url_extracted` | 139 | Confirmation portal URL extracted from snippet | `test_confirmation_url_extracted` |
| A6 | `test_hrtechprivacy_url` (TestWrongChannel) | 221 | Privacy request portal domain in snippet triggers WRONG_CHANNEL | `test_third_party_privacy_portal_url` |
| A7 | `test_finalroundai_snippet` | 237 | Self-service portal deflection (truncated snippet) | `test_self_service_portal_mention_is_wrong_channel` |
| A8 | `test_zendesk_ticket_set_to_solved` | 249 | Ticket solved/closed without data provision | `test_ticket_solved_without_data_is_wrong_channel` |
| A9 | `test_data_link_glassdoor` | 330 | Proprietary download-token URL triggers DATA_PROVIDED_LINK | `test_data_link_proprietary_token_url` |
| A10 | `test_data_link_full_token_with_colons` | 344 | Token URL with colon-separated segments captured in full | `test_data_link_token_with_colon_segments` |
| A11 | `test_google_ticket_ref` | 459 | Numeric case ID extracted as reference_number | `test_numeric_case_id_ref_extracted` |

### `tests/unit/test_company_normalizer.py`

| # | Current name | Line | Pattern tested | Proposed rename |
|---|---|---|---|---|
| A12 | `test_co_uk_tld_deliveroo` | 32 | Second `.co.uk` domain produces correct capitalization | `test_co_uk_tld_capitalizes_name` |
| A13 | `test_known_exception_facebookmail` | 91 | Alternate mail domain resolves to branded name | `test_known_exception_alternate_mail_domain` |
| A14 | `test_known_exception_glassdoor` | 95 | Single-word domain preserves casing from exception table | `test_known_exception_preserves_casing` |
| A15 | `test_known_exception_substack` | 99 | Single-word `.com` exception | `test_known_exception_single_word_domain` |
| A16 | `test_known_exception_github` | 103 | Mixed-case brand exception (GitHub) | `test_known_exception_mixed_case` |
| A17 | `test_known_exception_linkedin` | 107 | CamelCase brand exception (LinkedIn) | `test_known_exception_camelcase` |
| A18 | `test_normalize_youtube_returns_google` | 147 | Alias domain resolves to parent group name | `test_alias_resolves_to_parent_group` |
| A19 | `test_normalize_instagram_returns_facebook` | 155 | Second alias resolves to parent group name | `test_second_alias_resolves_to_parent_group` |

### `tests/unit/test_portal_submitter.py`

| # | Current name | Line | Pattern tested | Proposed rename |
|---|---|---|---|---|
| A20 | `test_login_required_google` | 77 | Account-portal URL classified as login_required | `test_login_required_account_portal` |
| A21 | `test_login_required_apple` | 83 | Privacy-portal URL classified as login_required | `test_login_required_privacy_portal` |
| A22 | `test_login_required_meta` | 86 | DYI-portal URL classified as login_required | `test_login_required_dyi_portal` |

### `tests/unit/test_url_verifier.py`

| # | Current name | Line | Pattern tested | Proposed rename |
|---|---|---|---|---|
| A23 | `test_apple_login_required` | 76 | Privacy URL classified as login_required | `test_login_required_privacy_url` |

### `tests/unit/test_auth_routes.py`

| # | Current name | Line | Pattern tested | Proposed rename |
|---|---|---|---|---|
| A24 | `test_login_google_redirects_to_oauth` | 98 | Login route starts OAuth flow and redirects | `test_login_redirects_to_oauth_provider` |

### `tests/unit/test_preprocessor.py`

| # | Current name | Line | Pattern tested | Proposed rename |
|---|---|---|---|---|
| A25 | `test_twitter_js_wrapper_unwrapped` | 103 | `window.YTD.*.part0 = [...]` JS wrapper parsed as JSON array | `test_js_variable_assignment_wrapper_unwrapped` |

### NON_GDPR tests (fixture-data only renames)

These test names are **generic already** (`test_job_alert_from_address_and_subject`, etc.) but their docstrings reference customers — see Category D below.

---

## Category B — Hardcoded branching on a specific customer domain

These are **not** in `if domain ==` branching (confirmed: zero matches in production code). They are customer-specific URL/domain patterns baked into regex rules that should be generic.

### `reply_monitor/classifier.py`

| # | Location | Current pattern | Problem | Proposed fix |
|---|---|---|---|---|
| B1 | Line 157 (CONFIRMATION_REQUIRED snippet regex) | `hrtechprivacy\.com/confirm` | Customer-specific domain in regex. The generic patterns on lines 154-156 (`will not begin processing...until confirmed`, `confirm...request...button`) already cover the behavioural signal. This alternative is redundant for the snippet match — the URL itself is captured by `_RE_CONFIRM_URL` (B3). | **Remove this alternative** from the snippet regex. URL extraction via `_RE_CONFIRM_URL` is the correct place to handle specific URL shapes. |
| B2 | Line 206 (WRONG_CHANNEL snippet regex) | `requests\.hrtechprivacy\.com` | Customer-specific domain in regex. The generic alternatives (`please submit via`, `privacy portal`, `dsar portal`, `submit your request at`) already cover the behavioural signal. A URL containing `/submit` or `privacy` in its path would match the generic patterns. | **Remove this alternative.** If the surrounding text says "submit via [URL]", the generic `please submit via` or `submit your request at` patterns fire. |
| B3 | Line 428-429 (`_RE_CONFIRM_URL`) | `https://requests\.hrtechprivacy\.com/confirm/[\w/-]+` | Named generically but the pattern is a single customer's URL shape. No other confirmation URLs would match this. | **Generalize** to match any domain with a `/confirm/` path segment: `https?://\S+/confirm/[\w/-]+` (with junk-URL exclusion if needed). |
| B4 | Line 299 (DATA_PROVIDED snippet regex) | `glassdoor\.com/dyd/download\?token=` | Customer domain in snippet regex. The same `/dyd/download?token=` path pattern is already captured generically by `_RE_DOWNLOAD_URL` at line 432 (`\S+/dyd/download\?token=`). The snippet-level check is redundant for the domain-specific part. | **Replace** with the generic `\S+/dyd/download\?token=` or simply remove — the URL extraction pass will catch it and promote via link-first logic. |

**Impact:** All 4 patterns have generic counterparts already in place. Removing the customer-specific alternatives should not change classification outcomes for any message. Tests A4-A6 and A9 already use generic snippet text (except A5/A6 which use the URL string — those will need fixture URL updates after B3 is generalized).

---

## Category C — Variables, classes, files, or functions named after a customer

| # | File | Line | Current name | Proposed rename |
|---|---|---|---|---|
| C1 | `tests/unit/test_resolver.py` | 41 | `_DATAOWNERS_SPOTIFY` | `_DATAOWNERS_OVERRIDE_ENTRY` |

This is the only variable named after a customer. The fixture data inside it (Spotify AB, privacy@spotify.com, etc.) is Category E and stays.

---

## Category D — Docstring or comment references using a customer as justification

### `reply_monitor/classifier.py`

| # | Line | Current text | Proposed replacement |
|---|---|---|---|
| D1 | 432 | `# Glassdoor` (inline comment on download URL pattern) | `# proprietary download-token URL` |
| D2 | 552 | `(Glassdoor Jobs, Community…)` in docstring | `(Jobs, Community…)` |
| D3 | 819 | `# Pass B: generic download URL patterns (Glassdoor, path-based, token params).` | `# Pass B: generic download URL patterns (proprietary token, path-based, token params).` |

### `tests/unit/test_reply_classifier.py`

| # | Line | Current text | Proposed replacement |
|---|---|---|---|
| D4 | 238 | `# Real Final Round AI reply — truncated snippet that ends mid-sentence` | `# Truncated snippet ending mid-sentence (realistic self-service deflection)` |
| D5 | 250 | `"""Zendesk 'ticket is set to Solved' without data → WRONG_CHANNEL."""` | `"""Ticket closed without data provision → WRONG_CHANNEL."""` |
| D6 | 345 | `"""Real Glassdoor tokens contain colon-separated segments — must capture in full."""` | `"""Colon-separated token segments must be captured in full."""` |
| D7 | 531 | `"""Real Glassdoor case: noreply@ but display name 'Glassdoor Jobs' + ZWC snippet."""` | `"""noreply@ with marketing display name + zero-width chars → NON_GDPR."""` |
| D8 | 542 | `"""Real Glassdoor case: noreply@ but display name 'Glassdoor Community' + ZWC."""` | `"""noreply@ with community display name + zero-width chars → NON_GDPR."""` |

### `dashboard/services/monitor_runner.py`

| # | Line | Current text | Proposed replacement |
|---|---|---|---|
| D9 | 108-109 | `# ... (e.g. Zendesk uses Ketch on a branded domain).` | `# ... (e.g. a company uses Ketch on a branded domain).` |

---

## Category E — Fixture strings containing customer names (no action)

These are email addresses, display names, URLs, and company names used as **test input data**. They provide realistic fidelity and do not appear in test names, variable names, or assertions. No renaming needed.

### `tests/unit/test_reply_classifier.py`

- Line 501: `from_addr="alerts@glassdoor.com"` (NON_GDPR fixture)
- Line 512: `from_addr="news@substack.com"` (NON_GDPR fixture)
- Line 523: `from_addr="community@glassdoor.com"` (NON_GDPR fixture)
- Lines 534-536: `from_addr="Glassdoor Jobs <noreply@glassdoor.com>"` (NON_GDPR fixture)
- Lines 545-547: `from_addr="Glassdoor Community <noreply@glassdoor.com>"` (NON_GDPR fixture)
- Line 557: `from_addr="noreply@glassdoor.com"` (NON_GDPR-negative fixture)
- Line 333: `subject="Download Your Glassdoor Personal Data File"` (DATA_PROVIDED fixture)
- Lines 340, 346: `https://www.glassdoor.com/dyd/download?token=...` (DATA_PROVIDED fixture)
- Line 696: `from_addr="privacy@society.zendesk.com"` (junk URL fixture)
- Lines 698, 712, 727: `society.zendesk.com` in fixture URLs (junk URL fixture)
- Line 140: `https://requests.hrtechprivacy.com/confirm/abc-123-xyz` (confirmation URL fixture — **will need URL update** if B3 generalizes the pattern)

### `tests/unit/test_company_normalizer.py`

- Fixture assertions like `normalize_domain("glassdoor.com") == "Glassdoor"` — these test the `_KNOWN_EXCEPTIONS` data table which stays. The customer name in the assertion is the expected output of the function.

### `tests/unit/test_resolver.py`

- Lines 30-38: `_DR_ENTRY` with Spotify datarequests.org data (generic variable name, customer data inside)
- Lines 42-60: `_DATAOWNERS_SPOTIFY` fixture content (Spotify AB, privacy@spotify.com, etc.) — variable **name** is C1, content stays

### `tests/unit/test_portal_submit_route.py`

- Lines 23-27: `"zendesk.com"` as mock company domain throughout (generic test names, customer in fixture data only)
- Lines 508-521 in `test_portal_submitter.py`: "Zendesk" used as mock company name in full-workflow fixtures

### `tests/unit/test_reply_fetcher.py`

- Line 432: `"to_email": "privacy@zendesk.com"` (fixture)
- Line 437: `from_addr="support@zendesk.com"` (fixture)

---

## Legitimate platform references (KEEP)

These reference **platform infrastructure** that the code legitimately detects. They are not customer names.

### `reply_monitor/classifier.py`

- Lines 420-425: `_RE_REF_ZENDESK` — Zendesk ticket ID format extraction (platform pattern)
- Lines 447-461: `_RE_ZENDESK_ATTACHMENT_A`, `_RE_ZENDESK_ATTACHMENT_B` — Zendesk attachment URL formats (platform pattern)
- Lines 607-618: `_RE_JUNK_URL` with Zendesk path patterns (`/requests/`, `/survey_responses/`, `/hc/`) — platform URL filtering

### `portal_submitter/platform_hints.py`

- Entire file: Platform detection for Zendesk, Ketch, OneTrust, TrustArc, Salesforce. All legitimate.

### `tests/unit/test_reply_classifier.py`

- Line 83: `test_zendesk_ticket_subject` — tests Zendesk ticket ID **format** detection (platform pattern)
- Line 463: `test_zendesk_ref` — tests Zendesk reference number **format** extraction (platform pattern)
- Lines 692-731: `TestJunkURLFiltering` class — tests Zendesk URL **structure** filtering (platform pattern). Test names (`test_zendesk_ticket_page_not_data_link`, `test_zendesk_survey_url_not_data_link`, `test_zendesk_ticket_page_not_portal_url`) describe platform-specific URL structures. **Keep.**

### `scanner/company_normalizer.py`

- Lines 49-64: `_KNOWN_EXCEPTIONS` — domain-to-display-name data table (configuration, not branching)
- Lines 67-80: `_COMPANY_GROUPS` — canonical domain alias grouping (configuration, not branching)
- These are **data tables**, not `if/elif` customer branching. They stay.

---

## Summary counts

| Category | Count | Action |
|---|---|---|
| A — Test name renames | 25 | Rename to describe pattern |
| B — Hardcoded customer patterns | 4 | Generalize or remove |
| C — Customer-named variables | 1 | Rename |
| D — Customer-referencing comments | 9 | Rewrite to describe pattern |
| E — Fixture data | ~25 instances | No action (1 URL update if B3 changes) |
| Legitimate platform refs | ~15 instances | No action |

**Total items requiring changes: 39** (25 renames + 4 regex fixes + 1 variable rename + 9 comment rewrites)
