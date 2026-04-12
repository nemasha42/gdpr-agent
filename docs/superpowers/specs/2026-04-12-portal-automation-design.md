# Portal Automation Design Spec

**Date:** 2026-04-12
**Branch:** feature/portal-automation
**Status:** Draft

## Problem

Companies with `preferred_method="portal"` currently require manual SAR submission — the user gets a URL and copies the letter body by hand. At scale (500+ companies, ~10-20% portal-only), this is a significant bottleneck.

## Approach

**Playwright + LLM Field Mapper.** Navigate to the portal with Playwright, extract the accessibility tree, send to Claude Haiku to map form fields to user data, fill deterministically, handle CAPTCHA via dashboard relay, handle email OTP via Gmail API. Cache field mappings per domain for zero-cost repeat submissions.

No new dependencies — Playwright and Anthropic API are already in the project.

## Decisions Log

| Decision | Choice | Rationale |
|----------|--------|-----------|
| CAPTCHA handling | Dashboard relay — screenshot shown in dashboard, user solves, solution relayed back | Fully legitimate, no external service |
| Login-required portals | Manual with smart assist — open in browser, pre-copy text, "Mark as submitted" | Google/Apple/Meta actively block automation |
| Phone field | Skip unless required | Only ~15% of portals have it, always optional |
| Architecture | Playwright + LLM hybrid (Approach A) | Zero new deps, $0.01-0.03/portal, 95%+ reliability, cacheable |

## Research Summary

### Portal Platform Landscape

| Platform | Market share | Automation difficulty |
|----------|-------------|---------------------|
| OneTrust | ~30-35% | Medium — React SPA, label-based selectors, CAPTCHA varies |
| TrustArc | ~15-20% | Medium — multi-step wizard, email OTP instead of CAPTCHA |
| Securiti | ~10% | Medium-hard — conditional fields |
| Custom (Google, Apple, Meta) | ~20-25% | Not automatable — login walls, bot detection |
| Email-only (no portal) | ~15-20% | Already handled by existing pipeline |

~40-50% of portals have no CAPTCHA. No existing open-source tool automates DSAR portal submissions.

### Common Form Fields

Nearly all portals require: first name, last name, email, country, request type. All pre-fillable from existing `config/settings.py`.

## Module Structure

```
portal_submitter/
    __init__.py
    submitter.py        # Orchestrator: submit_portal(letter, scan_email) -> PortalResult
    form_analyzer.py    # Extract AXTree, LLM field mapping, caching
    form_filler.py      # Playwright: fill fields, detect CAPTCHA, submit
    otp_handler.py      # Monitor Gmail for verification emails, extract code/link
    captcha_relay.py    # Save CAPTCHA screenshot, poll for user solution
    models.py           # PortalResult, FieldMapping, CaptchaChallenge
    platform_hints.py   # URL-pattern -> platform detection
```

Separate from `letter_engine/` because portal submission is browser automation, not letter composition. Takes a composed `SARLetter` as input.

## Data Flow

```
composer.py produces SARLetter(method="portal")
    |
sender.py:send_letter() -- delegates to portal_submitter
    |
submitter.submit_portal(letter, scan_email)
    |
    1. platform_hints.detect_platform(portal_url)
       -> "onetrust" | "trustarc" | "salesforce" | "login_required" | "unknown"
       -> login_required: return PortalResult(needs_manual=True) immediately
    |
    2. form_analyzer.analyze_form(page)
       -> check companies.json cache first (90-day TTL)
       -> cache miss: extract AXTree, Claude Haiku call, cache result
       -> return FieldMapping
    |
    3. form_filler.fill_and_submit(page, mapping, user_data)
       -> fill each field via Playwright using AXTree names
       -> if CAPTCHA detected: goto step 4
       -> click submit button
    |
    4. captcha_relay.request_solve(screenshot)
       -> save screenshot to user_data/captcha_pending/<domain>.png
       -> poll for solution (5-min timeout)
       -> inject answer, continue submission
    |
    5. otp_handler.wait_for_otp(scan_email, sender_hints, timeout=120)
       -> poll Gmail API for verification email from known senders
       -> extract confirmation URL or 6-digit code
       -> navigate to URL or type code into portal
    |
    Returns PortalResult(success, confirmation_ref, screenshot_path, needs_manual)
```

## Integration Points (existing file changes)

### sender.py

`send_letter()` when `method == "portal"`: call `submit_portal()` instead of returning `True, "", ""`. On failure or `needs_manual`, fall back to current "print URL" behavior.

### contact_resolver/models.py

New model:

```python
class PortalFormField(BaseModel):
    name: str          # AXTree element name, e.g. "First Name"
    value_key: str     # key into user_data dict, e.g. "first_name"
    role: str          # AXTree role: "textbox", "combobox", "checkbox"

class PortalFieldMapping(BaseModel):
    cached_at: str = ""
    platform: str = ""  # "onetrust", "trustarc", "salesforce", "unknown"
    fields: list[PortalFormField] = Field(default_factory=list)
    submit_button: str = ""
```

Added as optional field on `CompanyRecord`:

```python
portal_field_mapping: PortalFieldMapping | None = None
```

### letter_engine/tracker.py

Portal submissions write to `sent_letters.json` (they are SARs). New fields in the record:

```json
{
  "portal_url": "https://help.glassdoor.com/s/privacyrequest",
  "portal_status": "submitted",
  "portal_confirmation_ref": "TICKET-123456-1",
  "portal_screenshot": "user_data/portal_screenshots/glassdoor.com_2026-04-12.png"
}
```

`portal_status` values: `"submitted"` | `"awaiting_verification"` | `"manual"` | `"failed"`

### dashboard/app.py

New routes:

| Route | Method | Purpose |
|-------|--------|---------|
| `/portal/submit/<domain>` | POST | Start portal submission (background task) |
| `/portal/status/<domain>` | GET | Poll submission progress (JSON) |
| `/captcha/<domain>` | GET | Show pending CAPTCHA screenshot |
| `/captcha/<domain>` | POST | Submit CAPTCHA solution |

Card changes:
- Automatable portals: "Submit via portal" button -> background task -> spinner -> result badge
- Login-required portals: "Open portal" button (new tab) + clipboard copy + "Mark as submitted" checkbox

### run.py

- Portal companies attempted alongside email sends
- `--dry-run`: previews portal submissions without submitting
- `--portal-only`: only process portal-method companies

## Field Mapping and Caching

### LLM Call

`form_analyzer.analyze_form(page)`:

1. `page.accessibility.snapshot()` -> AXTree
2. Filter to interactive elements: textbox, combobox, checkbox, button
3. Prompt Claude Haiku with AXTree elements + user data keys
4. Parse JSON response -> `FieldMapping`
5. Cache in `companies.json` under `portal_field_mapping`

### User Data Assembly

Built from `config/settings.py`:

```python
{
    "first_name": settings.user_full_name.split(" ", 1)[0],
    "last_name": settings.user_full_name.split(" ", 1)[1] if " " in settings.user_full_name else "",
    "email": settings.user_email,
    "country": settings.user_address_country,
    "request_type": "Access my personal data",
    "description": letter.body,
    "relationship": "Customer",
}
```

### Cache Schema

Stored in `companies.json` per domain:

```json
{
  "portal_field_mapping": {
    "cached_at": "2026-04-12",
    "platform": "onetrust",
    "fields": [
      {"name": "First Name", "value_key": "first_name", "role": "textbox"},
      {"name": "Email", "value_key": "email", "role": "textbox"},
      {"name": "Country", "value_key": "country", "role": "combobox"}
    ],
    "submit_button": "Submit"
  }
}
```

- `value_key` references user data dict keys, not actual values — reusable across users
- TTL: 90 days (consistent with existing scrape/llm cache TTLs)
- On fill failure: clear cache, re-analyze form

## Platform Detection

`platform_hints.detect_platform(url)` matches URL patterns:

| Pattern | Platform |
|---------|----------|
| `onetrust.com` or `privacyportal` in URL | `onetrust` |
| `trustarc.com` or `submit-irm` in URL | `trustarc` |
| `/s/` path + Salesforce indicators | `salesforce` |
| Domain in `_LOGIN_REQUIRED_DOMAINS` set | `login_required` |
| Everything else | `unknown` |

`_LOGIN_REQUIRED_DOMAINS`: `google.com`, `apple.com`, `meta.com`, `amazon.com`, `facebook.com`, `twitter.com`, `x.com`. Extensible via `companies.json` flag `flags.portal_login_required: bool`.

Platform hints inform OTP sender patterns and provide structural expectations, but the LLM field mapper handles all platforms uniformly.

## CAPTCHA Relay

### Detection

After filling fields, `form_filler.py` checks for:
- `iframe[src*="recaptcha"]` (reCAPTCHA v2)
- `iframe[src*="hcaptcha"]` (hCaptcha)
- `.g-recaptcha`, `#captcha`, `[data-sitekey]` selectors

reCAPTCHA v3 (invisible): no detection needed — Playwright with stealth script may pass. If submission fails post-submit, fall back to `needs_manual`.

### Relay Flow

1. Screenshot CAPTCHA region -> `user_data/captcha_pending/<domain>.png`
2. Write `CaptchaChallenge` JSON: `{domain, portal_url, created_at, status: "pending"}`
3. Playwright browser stays open (background thread)
4. Dashboard `GET /captcha/<domain>` shows screenshot + input
5. User solves, `POST /captcha/<domain>` writes `status: "solved"` + answer
6. `captcha_relay.poll_solution()` picks up answer (polls every 2s)
7. Inject answer into page, continue submission
8. Timeout after 5 minutes -> `PortalResult(needs_manual=True)`

Pending files auto-deleted after solve or 24-hour expiry.

## OTP Handler

### Flow

After form submission, `otp_handler.wait_for_otp(scan_email, sender_hints, timeout=120)`:

1. `sender_hints` from `platform_hints.py`:
   - OneTrust: `["noreply@onetrust.com"]`
   - TrustArc: `["privacy@trustarc.com"]`
   - Glassdoor/HRTech: `["requests.hrtechprivacy.com"]`
2. Poll Gmail API (readonly scope, existing auth) for new messages from those senders
3. Extract confirmation URL (`https://...confirm...`, `https://...verify...`) or 6-digit OTP code
4. Confirmation URL: navigate in Playwright -> done
5. OTP code: type into portal verification field -> done
6. Nothing found in 120s: mark `awaiting_verification`, show "check email" in dashboard

Reuses existing `auth/gmail_oauth.py` infrastructure. No new OAuth permissions.

## Error Handling

### Tiered Fallback

```
Attempt automated submission
    | fails (timeout, element not found, unexpected page)
Re-analyze form (clear cache, fresh LLM call)
    | fails again
Mark needs_manual + show in dashboard with:
    - Screenshot of failure point
    - Pre-copied SAR body text
    - Direct portal URL link
    - Error description
```

### Failure Mode Table

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Portal URL returns 404/500 | HTTP status before form analysis | `portal_status: "failed"`, suggest email if available |
| Form structure changed | Cached names don't match AXTree | Clear cache, re-analyze. Fails twice -> manual |
| CAPTCHA timeout | `poll_solution()` 5-min timeout | Close browser, `awaiting_captcha`, retryable from dashboard |
| OTP never arrives | `wait_for_otp()` 2-min timeout | `awaiting_verification`, "check email" in dashboard |
| Playwright crash | Exception handler | Log, mark failed, close browser context |
| Submit succeeds, no confirmation | Page content check post-submit | Screenshot anyway, mark `submitted` with warning |

No silent failures. Every attempt produces a `PortalResult` written to `sent_letters.json` and visible in dashboard.

## Security

- **No stored credentials.** Login-required portals go to manual flow. No passwords saved.
- **CAPTCHA screenshots** in `user_data/captcha_pending/` — gitignored, auto-deleted after solve or 24h.
- **Portal screenshots** in `user_data/portal_screenshots/` — gitignored. May contain user name/email (already in settings, not new exposure).
- **Field mappings** in `companies.json` — contain field names and value_key references, never actual user data. Safe to commit.
- **Playwright browser** — fresh context per submission, no persistent cookies, closed after each attempt.
- **OTP handler** — existing Gmail readonly scope, no new OAuth permissions.

## Cost

- Form analysis LLM call: ~$0.01-0.03 per new portal (Claude Haiku)
- Cached portals: $0
- At 500 companies, ~50-100 portal companies, cold run: ~$1-3
- Tracked via existing `cost_tracker.record_llm_call()`

## Testing

### Unit Tests (`tests/unit/test_portal_submitter.py`)

| Test | Verifies |
|------|----------|
| `test_platform_detection` | URL patterns -> correct platform |
| `test_field_mapping_parse` | LLM response -> FieldMapping |
| `test_field_mapping_cache_hit` | Fresh cache skips LLM |
| `test_field_mapping_cache_expired` | 90+ day cache triggers re-analysis |
| `test_user_data_assembly` | Name splitting, country, defaults |
| `test_captcha_detection` | Mock page with reCAPTCHA -> detected |
| `test_captcha_relay_roundtrip` | Pending -> solved -> poll returns answer |
| `test_captcha_timeout` | No solution -> returns None |
| `test_otp_extraction_link` | Gmail with confirm URL -> extracted |
| `test_otp_extraction_code` | Gmail with 6-digit code -> extracted |
| `test_submit_login_required` | Google domain -> immediate needs_manual |
| `test_submit_success` | Mock Playwright + mock LLM -> success |
| `test_submit_fallback` | Two failures -> needs_manual with screenshot |
| `test_tracker_portal_fields` | Portal record has portal_status, portal_url |

Testing approach: dependency injection (injectable `browser_launcher`, `llm_call`). Mock Playwright via fake page objects returning predefined AXTree snapshots. Same pattern as existing tests.

### Manual Test Script (`test_portal.py`)

```bash
python test_portal.py --domain glassdoor.com --dry-run   # analyze form only
python test_portal.py --domain glassdoor.com              # full submission
python test_portal.py --list-portals                      # show portal companies
```

## File Inventory

### New files

| File | Purpose |
|------|---------|
| `portal_submitter/__init__.py` | Package init |
| `portal_submitter/submitter.py` | Main orchestrator |
| `portal_submitter/form_analyzer.py` | AXTree extraction + LLM mapping |
| `portal_submitter/form_filler.py` | Playwright form filling + CAPTCHA detection |
| `portal_submitter/otp_handler.py` | Gmail OTP/confirmation link extraction |
| `portal_submitter/captcha_relay.py` | Screenshot save, poll for solution |
| `portal_submitter/models.py` | PortalResult, FieldMapping, CaptchaChallenge |
| `portal_submitter/platform_hints.py` | URL pattern -> platform detection |
| `tests/unit/test_portal_submitter.py` | Unit tests |
| `test_portal.py` | Manual test script |
| `dashboard/templates/captcha.html` | CAPTCHA solving page |

### Modified files

| File | Change |
|------|--------|
| `contact_resolver/models.py` | Add `PortalFieldMapping` model, add field to `CompanyRecord` |
| `letter_engine/sender.py` | Portal path delegates to `submit_portal()` |
| `letter_engine/tracker.py` | Portal-specific fields in `record_sent()` |
| `dashboard/app.py` | New routes: `/portal/*`, `/captcha/*` |
| `dashboard/templates/index.html` | Portal submit/open buttons on cards |
| `dashboard/templates/company_detail.html` | Portal submission status, screenshots |
| `run.py` | Portal submission in pipeline, `--portal-only` flag |
