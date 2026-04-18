# Portal Automation — Detailed Documentation

> Back to @ARCHITECTURE.md for the system overview and module map.

---

## Overview

Automates SAR submission via GDPR web portals using Playwright with stealth scripts. Located in `portal_submitter/`. Seven modules:

---

## Modules

### `submitter.py`

Entry point: `submit_portal(letter, scan_email)`. Detects platform, analyzes form, fills & submits, handles CAPTCHA/OTP, captures screenshots to `user_data/portal_screenshots/`, extracts confirmation references. Falls back to manual instructions on failure.

### `models.py`

`PortalResult` (success, needs_manual, confirmation_ref, screenshot_path, error, portal_status) and `CaptchaChallenge` (domain, portal_url, created_at, status, solution, screenshot_path).

### `form_analyzer.py`

LLM-powered (Claude Haiku) form field analysis. Extracts form fields via `page.locator("body").aria_snapshot()` (Playwright text-format API, replaces deprecated `page.accessibility.snapshot()`), parses with `_extract_elements_from_aria_snapshot()` regex, calls LLM to map user data to fields. Results cached in `CompanyRecord.portal_field_mapping` with 90-day TTL (`cached_at` checked before re-analyzing). Cost tracked via `cost_tracker`.

### `form_filler.py`

Playwright automation: fills textbox/combobox/checkbox fields, clicks submit. `detect_captcha_type(page)` returns `"interactive"`, `"invisible_v3"`, or `"none"` — invisible reCAPTCHA v3 (`.grecaptcha-badge`) is distinguished from interactive CAPTCHAs. Injects stealth JavaScript to bypass automation detection.

### `captcha_relay.py`

Bridges CAPTCHA challenges to the dashboard for manual solving. Saves screenshot + challenge JSON to `user_data/captcha_pending/{domain}.png|.json`. Polls for user solution with 5-minute timeout (2-second intervals). Files cleaned on solution or timeout.

### `otp_handler.py`

Handles email verification steps during portal submission. `wait_for_otp()` polls Gmail for verification emails from platform-specific senders. `extract_otp_from_message()` extracts confirmation URLs (preferred) or 6-digit codes. 2-minute timeout.

### `portal_navigator.py`

Multi-step portal navigation. `navigate_to_form(page, platform, api_key=)` dismisses cookie banners first, then uses platform-specific hint patterns (free, fast) then LLM-guided fallback (Claude Haiku, max 3 steps via `aria_snapshot()`). Called by `submitter.py` when landing page has no form fields. Hint patterns in `_NAVIGATION_HINTS` dict, extensible per platform. `page_has_form(page)` checks for *visible* input/textarea/select (iterates `.all()` + `.is_visible()` to exclude hidden cookie/tracking inputs). Click helpers include `"tab"` role and force-click fallback for overlay-intercepted elements.

### `platform_hints.py`

Detects portal platform: `onetrust`, `trustarc`, `ketch`, `salesforce`, `login_required` (Google, Apple, Meta, Amazon, Facebook, Twitter/X), `unknown`. `detect_platform(url, html="")` checks URL patterns first, then HTML signatures for branded domains (e.g. zendesk.es → ketch via `_KETCH_HTML_SIGNATURES`). `otp_sender_hints()` returns expected verification email senders per platform. `portal_reply_domains(platform)` returns email sender domains used by a specific portal platform for replies (e.g. `["onetrust.com"]` for OneTrust). `all_portal_reply_domains()` returns all known portal platform reply domains (deduplicated) — used as a fallback when a company has `portal_status` set but the platform cannot be determined from the portal URL alone (e.g. Zendesk uses Ketch on a branded domain that doesn't contain "ketch" in the URL). `_PORTAL_REPLY_DOMAINS` dict maps platform names to their reply domains: `onetrust` → `onetrust.com`, `trustarc` → `trustarc.com`, `ketch` → `ketch.com`, `m.ketch.com`, `salesforce` → `salesforce.com`.

---

## Portal-Specific Constraints

- Ketch portals (Zendesk) use reCAPTCHA v3 invisible — headless Playwright always fails the score check. `submitter.py` detects this (`"bot-like behavior"` text after submit) and returns `needs_manual=True`. The headless browser session is destroyed after the attempt, so the form cannot be "pre-filled" for the user.
- Login-required portals (Google, Apple, Meta, Amazon, Facebook, Twitter/X) are detected by `platform_hints.py` and fall back to manual instructions.
- `Playwright ≥1.58`: `page.accessibility.snapshot()` is removed. Use `page.locator("body").aria_snapshot()` which returns a text-format accessibility tree (not JSON).

---

## LLM Call Sites in Portal Automation

### Call site 4: `portal_submitter/form_analyzer.py` — `analyze_form()`

**Why LLM is used here:** GDPR portal forms vary wildly in field naming, layout, and required information. A rules-based approach cannot reliably map user data (name, email, address) to arbitrary form fields across hundreds of portal implementations. The LLM reads the accessibility tree (`aria_snapshot()`) and produces a mapping.

**Prompt strategy:** Structured extraction. The LLM receives the parsed form elements from `_extract_elements_from_aria_snapshot()` regex output and user data fields, and returns a JSON mapping of which user data goes into which form field.

**Fallback:** If the API call fails, the portal submission falls back to manual instructions (`needs_manual=True`).

**Cost:** ~$0.020 per company. One-time per portal company, cached in `CompanyRecord.portal_field_mapping` for 90 days. Cost tracked via `cost_tracker`.

---

### Call site 5: `portal_submitter/portal_navigator.py` — `navigate_to_form()`

**Why LLM is used here:** Many GDPR portals require navigating through cookie consent banners, landing pages, and multi-step flows before reaching the actual request form. Platform-specific hint patterns (`_NAVIGATION_HINTS`) handle common cases for free, but unknown portals need the LLM to read the page and decide what to click.

**Prompt strategy:** Iterative navigation. The LLM receives the `aria_snapshot()` output and decides which element to click. Max 3 steps to prevent runaway navigation. Only triggered when `page_has_form(page)` returns false after hint-based navigation.

**Fallback:** If the LLM cannot find a form after 3 steps, returns failure and falls back to manual instructions.

**Cost:** ~$0.010–0.030 per navigation attempt (1–3 LLM calls). Only triggered for portals where hint patterns fail.
