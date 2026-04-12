# Ketch Portal Support + Multi-Step Navigation + Zendesk Fix

**Date:** 2026-04-13
**Scope:** Add Ketch platform detection, multi-step portal navigation (hybrid: hints + LLM fallback), fix Zendesk portal data, harden junk URL filtering.

## Context

The Zendesk SAR reply directed the user to a Ketch-powered privacy portal at `zendesk.es`. The system failed at every layer:

1. **Wrong URL extracted:** The email contained `society.zendesk.com/hc/en-us/requests/649929` (a help center ticket page requiring login). The classifier stored this as `portal_url` and `data_link` — both false positives. The junk URL filter (`_RE_JUNK_URL`) was added in Phase A but only applies to future classifications; existing persisted data still has the bad URLs.

2. **No company data:** `data/companies.json` has no entry for zendesk.com, and `dataowners_overrides.json` doesn't include it. Without `gdpr_portal_url` on the `CompanyRecord`, portal submission has nothing to submit to.

3. **Ketch not detected:** `platform_hints.py` recognizes OneTrust, TrustArc, Salesforce, and login-required platforms. Ketch is missing entirely — a Ketch portal is classified as `unknown`.

4. **No multi-step navigation:** `submitter.py` calls `page.goto(url)` then immediately runs `analyze_form()`. Ketch portals require 2-3 clicks (Overview → "Your Privacy Request" → "Access your data") before the form appears. The submitter finds no form fields on the landing page and fails with `no_form_fields_detected`.

**Design principle (carried from Phase A):** Follow company instructions first. If they say "use our portal," use the portal. Only escalate if the portal path fails.

---

## Section 1: Ketch Platform Detection + Zendesk Data Fix

### 1a. Platform Detection

Add Ketch to `_PLATFORM_RULES` in `portal_submitter/platform_hints.py`:

```python
("ketch", re.compile(r"ketch\.com|\.ketch\.", re.I))
```

Ketch portals often use branded domains (e.g., `zendesk.es` instead of `zendesk.ketch.com`). URL-based detection won't catch these. Add an HTML-based detection fallback in `detect_platform()`: after URL rules fail to match a known platform, if an HTML string is provided, check for Ketch signatures:

```python
_KETCH_HTML_SIGNATURES = [
    "ketch-tag",
    "ketch.js",
    "window.semaphore",
    "cdn.ketch.com",
]
```

If any signature is found in the page HTML, return `"ketch"`.

**Function signature change:** `detect_platform(url: str, html: str = "") -> str`. The `html` parameter is optional for backward compatibility. Callers that have page content (submitter, url_verifier) pass it; callers with only a URL (classifier) don't.

### 1b. OTP Sender Hints

Add to `_OTP_SENDERS` in `platform_hints.py`:

```python
"ketch": ["noreply@ketch.com"],
```

### 1c. Zendesk Override

Add `zendesk.com` to `data/dataowners_overrides.json`:

```json
"zendesk.com": {
    "company_name": "Zendesk",
    "legal_entity_name": "Zendesk, Inc.",
    "source": "dataowners_override",
    "source_confidence": "high",
    "last_verified": "2026-04-13",
    "contact": {
        "dpo_email": "euprivacy@zendesk.com",
        "privacy_email": "euprivacy@zendesk.com",
        "gdpr_portal_url": "https://zendesk.es/",
        "postal_address": {
            "line1": "989 Market Street",
            "city": "San Francisco",
            "postcode": "CA 94103",
            "country": "United States"
        },
        "preferred_method": "portal"
    },
    "flags": {
        "portal_only": false,
        "email_accepted": true,
        "auto_send_possible": false
    }
}
```

### 1d. URL Verifier Ketch Awareness

In `reply_monitor/url_verifier.py`, update the HTML inspection step: after fetching a page, check for Ketch HTML signatures (same list as 1a). If Ketch signatures are found, classify as `gdpr_portal` even if no form is visible on the landing page — the form is behind navigation steps.

**Files changed:** `portal_submitter/platform_hints.py`, `data/dataowners_overrides.json`, `reply_monitor/url_verifier.py`

---

## Section 2: Multi-Step Portal Navigator

### 2a. New Module

**New file:** `portal_submitter/portal_navigator.py`

Single responsibility: given a Playwright page with no visible form, navigate through multi-step portal flows to reach the form page.

```python
def navigate_to_form(page: Any, platform: str) -> bool:
    """Navigate through multi-step portal to reach the form page.

    Returns True if a page with form fields was reached, False otherwise.
    """
```

### 2b. Hybrid Strategy

**Layer 1 — Platform-specific hints (free, fast):**

```python
_NAVIGATION_HINTS: dict[str, list[str]] = {
    "ketch": [
        r"(?:your\s+)?privacy\s+request",
        r"access\s+(?:your\s+)?data",
    ],
}
```

For each hint pattern (in order):
1. Find a clickable element matching the pattern via `page.get_by_role("link", name=re.compile(pattern, re.I))` then fall back to `page.get_by_role("button", name=...)`.
2. Click it.
3. Wait for load (`page.wait_for_load_state("networkidle", timeout=10_000)`).
4. Check if the page now has form fields: query `page.locator("input:not([type=hidden]), textarea, select")` — if count > 0, stop and return `True`.

If all hints are exhausted and no form found, fall through to Layer 2.

**Layer 2 — LLM-guided navigation (fallback, ~$0.01/step):**

When hints fail or platform is `unknown` and no form is visible:

1. Extract the page's accessibility tree snapshot (`page.accessibility.snapshot()`).
2. Call Claude Haiku with prompt: "This is a privacy/GDPR portal page. I need to reach the data access request form. Here is the page's accessibility tree. Which ONE button or link should I click next to get to the form? Return only the exact accessible name of the element, nothing else."
3. Find and click the element by accessible name.
4. Wait for load, check for form fields.
5. Repeat up to **3 LLM steps max**. If form still not found, return `False`.

Cost tracked via `cost_tracker.record_llm_call()`.

### 2c. Integration in submitter.py

In `_browser_submit()`, after `page.goto()`:

```python
# Check if page already has form fields
has_form = page.locator("input:not([type=hidden]), textarea, select").count() > 0

if not has_form:
    from portal_submitter.portal_navigator import navigate_to_form
    if not navigate_to_form(page, platform):
        screenshot_path = _take_screenshot(page, domain)
        return PortalResult(
            error="form_not_found_after_navigation",
            needs_manual=True,
            screenshot_path=screenshot_path,
            portal_status="failed",
        )
```

Then proceed to `analyze_form()` as before.

### 2d. Form Field Detection Helper

Extract the "does this page have form fields?" check into a shared helper in `portal_navigator.py`:

```python
def page_has_form(page: Any) -> bool:
    return page.locator("input:not([type=hidden]), textarea, select").count() > 0
```

Used by both `navigate_to_form()` (after each click) and `submitter.py` (initial check).

**Files changed:** `portal_submitter/portal_navigator.py` (new), `portal_submitter/submitter.py`

---

## Section 3: Stale Data Cleanup + Junk URL Hardening

### 3a. Expand `_RE_JUNK_URL`

In `reply_monitor/classifier.py`, add patterns to the existing `_RE_JUNK_URL`:

```python
_RE_JUNK_URL = re.compile(
    r"/hc/[a-z-]+/requests/\d+"          # Zendesk help center ticket pages
    r"|/survey_responses/"                 # Zendesk/satisfaction surveys
    r"|/satisfaction/"                      # CSAT surveys
    r"|/hc/[a-z-]+/articles/"             # Help center articles
    r"|rate.{0,20}(support|service)"       # Rating pages
    r"|/feedback/"                          # Feedback forms
    r"|/requests/\d+"                      # Bare ticket paths
    r"|/support/tickets/"                  # Generic support tickets
    r"|/help/"                             # Help center roots
    , re.I,
)
```

### 3b. Reprocess with Re-extraction

`monitor.py --reprocess` currently re-runs classification (tags) but not URL extraction. Extend it: when `--reprocess` is active, also re-run `_extract()` on each reply to apply the current `_RE_JUNK_URL` filter. This cleans stale `portal_url` and `data_link` values from old replies.

Implementation: in `_reprocess_existing()`, after updating tags, call the extraction function on the reply snippet/body and overwrite `reply.extracted` fields that are URL-based (`data_link`, `data_links`, `portal_url`). Non-URL fields (`reference_number`, `summary`, `deadline_extension_days`) are preserved.

### 3c. Defensive Guard in submitter.py

Before launching Playwright, validate that `letter.portal_url` is not a junk URL:

```python
from reply_monitor.classifier import _is_junk_url

if _is_junk_url(letter.portal_url):
    return PortalResult(error="junk_portal_url", needs_manual=True, portal_status="failed")
```

This prevents wasting Playwright resources on help center pages, surveys, etc.

**Files changed:** `reply_monitor/classifier.py`, `monitor.py`, `portal_submitter/submitter.py`

---

## Section 4: Dashboard Integration

No new routes or templates needed. Existing infrastructure handles all states:

- **Navigation failure:** `PortalResult.error = "form_not_found_after_navigation"` displayed in existing error display on company detail page.
- **Navigation success + submission:** Identical to current single-page flow — same `portal_status`, `confirmation_ref`, screenshot capture, polling via `GET /portal/status/<domain>`.
- **Verification badges:** Already implemented (Phase B from earlier session). Ketch portals classified as `gdpr_portal` by url_verifier (with 1d update) show green "Verified portal" badge.

**Files changed:** None (existing templates and routes already handle these states).

---

## Files Changed Summary

| File | Changes |
|------|---------|
| `portal_submitter/platform_hints.py` | 1a: Ketch detection rule + HTML signature fallback. 1b: Ketch OTP sender hints. Signature change: `detect_platform(url, html="")` |
| `portal_submitter/portal_navigator.py` | **New.** 2a-2d: `navigate_to_form(page, platform)` with hint + LLM hybrid. `page_has_form()` helper. |
| `portal_submitter/submitter.py` | 2c: Call navigator when no form on landing page. 3c: Junk URL guard before Playwright. |
| `reply_monitor/url_verifier.py` | 1d: Ketch HTML signature detection → classify as `gdpr_portal`. |
| `reply_monitor/classifier.py` | 3a: Expand `_RE_JUNK_URL` with ticket/support/help patterns. |
| `monitor.py` | 3b: Re-extract URLs during `--reprocess`. |
| `data/dataowners_overrides.json` | 1c: Add zendesk.com entry with `gdpr_portal_url: https://zendesk.es/`. |
| `tests/unit/test_portal_navigator.py` | **New.** Tests for hint-based nav, LLM fallback, max step limit, `page_has_form()`. |
| `tests/unit/test_portal_submitter.py` | Update: Ketch detection tests, junk URL guard test. |
| `tests/unit/test_url_verifier.py` | Update: Ketch HTML signature → `gdpr_portal` classification. |
| `tests/unit/test_reply_classifier.py` | Update: Expanded junk URL patterns. |

## Out of Scope

- Changing `form_analyzer.py` or `form_filler.py` internals — navigator gets us to the form page, existing modules handle the rest
- Generic portal URL discovery (scraping unknown company sites for Ketch portals) — manual overrides handle known cases
- Retry logic for navigation failures — surface on dashboard, user decides
- Other multi-step platforms (OneTrust wizards, etc.) — hints dict is extensible, add patterns when encountered
