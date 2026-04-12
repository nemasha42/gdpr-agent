# Ketch Portal Support + Multi-Step Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable automated GDPR portal submission for Ketch-powered portals (starting with Zendesk) by adding platform detection, multi-step navigation, and stale data cleanup.

**Architecture:** Hybrid navigator — platform-specific hint patterns (fast, free) with LLM-guided fallback (Claude Haiku, ~$0.01/step) when hints fail. New `portal_navigator.py` module handles all multi-step logic. Existing `submitter.py` calls navigator when no form found on landing page.

**Tech Stack:** Python 3.14, Playwright (page navigation), Anthropic Claude Haiku (LLM fallback), pytest + unittest.mock.

---

## File Structure

| File | Responsibility |
|------|---------------|
| `portal_submitter/platform_hints.py` | Platform detection (URL + HTML signatures), OTP sender hints |
| `portal_submitter/portal_navigator.py` | **New.** Multi-step portal navigation: hint-based + LLM fallback |
| `portal_submitter/submitter.py` | Orchestrator — calls navigator when landing page has no form, junk URL guard |
| `reply_monitor/url_verifier.py` | URL classification — Ketch HTML signature → `gdpr_portal` |
| `reply_monitor/classifier.py` | Junk URL filter expansion |
| `monitor.py` | Reprocess with URL re-extraction |
| `data/dataowners_overrides.json` | Zendesk override with correct portal URL |
| `tests/unit/test_portal_submitter.py` | Ketch detection tests, junk URL guard |
| `tests/unit/test_portal_navigator.py` | **New.** Hint nav, LLM fallback, max steps, `page_has_form()` |
| `tests/unit/test_url_verifier.py` | Ketch HTML signature tests |
| `tests/unit/test_reply_classifier.py` | Expanded junk URL pattern tests |

---

### Task 1: Ketch Platform Detection

**Files:**
- Modify: `portal_submitter/platform_hints.py`
- Modify: `tests/unit/test_portal_submitter.py`

- [ ] **Step 1: Write failing tests for Ketch URL detection**

Add to `tests/unit/test_portal_submitter.py` inside `TestPlatformDetection`:

```python
def test_ketch_by_domain(self):
    assert detect_platform("https://privacy.ketch.com/portal/abc") == "ketch"

def test_ketch_subdomain(self):
    assert detect_platform("https://zendesk.ketch.com/") == "ketch"

def test_ketch_branded_domain_with_html(self):
    html = '<script src="https://cdn.ketch.com/ketch-tag.js"></script>'
    assert detect_platform("https://zendesk.es/", html=html) == "ketch"

def test_ketch_html_window_semaphore(self):
    html = '<script>window.semaphore = window.semaphore || [];</script>'
    assert detect_platform("https://company.com/privacy", html=html) == "ketch"

def test_non_ketch_html_returns_unknown(self):
    html = '<html><body>Regular page</body></html>'
    assert detect_platform("https://example.com/privacy", html=html) == "unknown"

def test_ketch_otp_hints(self):
    hints = otp_sender_hints("ketch")
    assert "noreply@ketch.com" in hints
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py::TestPlatformDetection::test_ketch_by_domain -v`
Expected: FAIL — `detect_platform()` does not accept `html` kwarg yet and "ketch" not in rules.

- [ ] **Step 3: Implement Ketch detection**

Update `portal_submitter/platform_hints.py`:

1. Add Ketch to `_PLATFORM_RULES`:

```python
_PLATFORM_RULES: list[tuple[str, re.Pattern]] = [
    ("onetrust", re.compile(r"onetrust\.com|privacyportal", re.I)),
    ("trustarc", re.compile(r"trustarc\.com|submit-irm", re.I)),
    ("ketch", re.compile(r"ketch\.com|\.ketch\.", re.I)),
]
```

2. Add Ketch HTML signatures:

```python
_KETCH_HTML_SIGNATURES = [
    "ketch-tag",
    "ketch.js",
    "window.semaphore",
    "cdn.ketch.com",
]
```

3. Add Ketch OTP senders:

```python
"ketch": ["noreply@ketch.com"],
```

4. Update `detect_platform` signature and add HTML fallback:

```python
def detect_platform(url: str, html: str = "") -> str:
    """Classify a portal URL into a known platform or 'unknown'.

    Returns one of: "onetrust", "trustarc", "ketch", "salesforce", "login_required", "unknown".
    """
    if not url:
        return "unknown"

    # Check login-required domains first
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return "unknown"

    for domain in _LOGIN_REQUIRED_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return "login_required"

    # Check platform rules against hostname only
    for platform, pattern in _PLATFORM_RULES:
        if pattern.search(host):
            return platform

    # Salesforce Experience Cloud: /s/ path prefix
    try:
        path = urlparse(url).path or ""
    except Exception:
        path = ""
    if re.match(r"^/s/", path):
        return "salesforce"

    # HTML-based detection for branded domains (e.g. zendesk.es for Ketch)
    if html:
        lower_html = html.lower()
        for sig in _KETCH_HTML_SIGNATURES:
            if sig.lower() in lower_html:
                return "ketch"

    return "unknown"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py::TestPlatformDetection -v`
Expected: All PASS (existing + new Ketch tests).

- [ ] **Step 5: Commit**

```bash
git add portal_submitter/platform_hints.py tests/unit/test_portal_submitter.py
git commit -m "feat: add Ketch platform detection with HTML signature fallback"
```

---

### Task 2: Zendesk Data Override

**Files:**
- Modify: `data/dataowners_overrides.json`

- [ ] **Step 1: Add Zendesk entry to overrides**

Add before the closing `}` of `data/dataowners_overrides.json`:

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
        "postal_address": {"line1": "989 Market Street", "city": "San Francisco", "postcode": "CA 94103", "country": "United States"},
        "preferred_method": "portal"
    },
    "flags": {"portal_only": false, "email_accepted": true, "auto_send_possible": false},
    "request_notes": {"special_instructions": "Ketch-powered DSAR portal at zendesk.es. Multi-step navigation required.", "identity_verification_required": true, "known_response_time_days": 30}
}
```

- [ ] **Step 2: Validate JSON is well-formed**

Run: `.venv/bin/python -c "import json; json.load(open('data/dataowners_overrides.json'))"`
Expected: No error output.

- [ ] **Step 3: Commit**

```bash
git add data/dataowners_overrides.json
git commit -m "data: add Zendesk override with correct Ketch portal URL"
```

---

### Task 3: URL Verifier Ketch Awareness

**Files:**
- Modify: `reply_monitor/url_verifier.py`
- Modify: `tests/unit/test_url_verifier.py`

- [ ] **Step 1: Write failing tests for Ketch HTML detection**

Add to `tests/unit/test_url_verifier.py`:

```python
class TestVerifyKetchPortal:
    def test_ketch_html_signature_classified_as_portal(self):
        """Page with Ketch JS signature is a GDPR portal even without visible form."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://zendesk.es/"
        mock_resp.text = (
            '<html><head><script src="https://cdn.ketch.com/ketch-tag.js"></script></head>'
            '<body><h1>Privacy Center</h1></body></html>'
        )
        mock_resp.headers = {"content-type": "text/html"}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify("https://zendesk.es/")
        assert result["classification"] == CLASSIFICATION.GDPR_PORTAL

    def test_ketch_window_semaphore_signature(self):
        """window.semaphore is a Ketch indicator."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://privacy.company.com/"
        mock_resp.text = (
            '<html><body><script>window.semaphore = window.semaphore || [];</script>'
            '<div>Privacy Center</div></body></html>'
        )
        mock_resp.headers = {"content-type": "text/html"}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify("https://privacy.company.com/")
        assert result["classification"] == CLASSIFICATION.GDPR_PORTAL

    def test_non_ketch_page_without_form_stays_unknown(self):
        """A page without Ketch signatures and no form remains unknown."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com/privacy"
        mock_resp.text = '<html><body><p>Our privacy policy</p></body></html>'
        mock_resp.headers = {"content-type": "text/html"}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify("https://example.com/privacy")
        assert result["classification"] == CLASSIFICATION.UNKNOWN
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_url_verifier.py::TestVerifyKetchPortal -v`
Expected: FAIL ��� `test_ketch_html_signature_classified_as_portal` returns UNKNOWN (no Ketch detection in verifier).

- [ ] **Step 3: Implement Ketch detection in url_verifier.py**

In `reply_monitor/url_verifier.py`, update the import and add Ketch check after the `detect_platform` fast path:

1. Update the import:

```python
from portal_submitter.platform_hints import detect_platform
```

No change needed — `detect_platform` now accepts `html` kwarg (from Task 1).

2. Add `_KETCH_HTML_SIGNATURES` list (import from platform_hints or duplicate for decoupling — import is cleaner):

After the existing `if platform in ("onetrust", "trustarc"):` fast path (line 87), add a Ketch fast path:

```python
if platform == "ketch":
    return _result(url, CLASSIFICATION.GDPR_PORTAL, now)
```

3. In the HTML inspection section (after `html = resp.text`, around line 115), add Ketch HTML detection before the form check. After the help center redirect check (line 125) and before the form element check (line 128):

```python
# Check for Ketch platform via HTML signatures (form is behind navigation steps)
html_platform = detect_platform(url, html=html)
if html_platform == "ketch":
    return _result(url, CLASSIFICATION.GDPR_PORTAL, now, page_title=title)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_url_verifier.py -v`
Expected: All PASS (existing 13 + 3 new Ketch tests).

- [ ] **Step 5: Commit**

```bash
git add reply_monitor/url_verifier.py tests/unit/test_url_verifier.py
git commit -m "feat(url_verifier): detect Ketch portals via HTML signatures"
```

---

### Task 4: Portal Navigator Module

**Files:**
- Create: `portal_submitter/portal_navigator.py`
- Create: `tests/unit/test_portal_navigator.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_portal_navigator.py`:

```python
"""Unit tests for portal_submitter/portal_navigator.py — multi-step portal navigation."""

import re
from unittest.mock import MagicMock, patch, call

import pytest

from portal_submitter.portal_navigator import navigate_to_form, page_has_form


class TestPageHasForm:
    def test_page_with_inputs(self):
        page = MagicMock()
        page.locator.return_value.count.return_value = 3
        assert page_has_form(page) is True
        page.locator.assert_called_once_with("input:not([type=hidden]), textarea, select")

    def test_page_without_inputs(self):
        page = MagicMock()
        page.locator.return_value.count.return_value = 0
        assert page_has_form(page) is False


class TestHintNavigation:
    def test_ketch_hints_find_form(self):
        """Ketch hints: click 'privacy request' then 'access data', form found after second click."""
        page = MagicMock()

        # First call to page_has_form (before first click): no form
        # Second call (after first click): no form
        # Third call (after second click): form found
        locator_counts = iter([0, 0, 3])
        page.locator.return_value.count.side_effect = lambda: next(locator_counts)

        # get_by_role returns a locator with click()
        link_locator = MagicMock()
        page.get_by_role.return_value = link_locator
        link_locator.count.return_value = 1

        result = navigate_to_form(page, "ketch")
        assert result is True
        # Should have clicked twice (two hints)
        assert link_locator.first.click.call_count == 2

    def test_unknown_platform_no_hints_no_llm(self):
        """Unknown platform with no LLM key — returns False immediately."""
        page = MagicMock()
        page.locator.return_value.count.return_value = 0

        result = navigate_to_form(page, "unknown")
        assert result is False

    def test_hints_exhausted_falls_to_llm(self):
        """When hints don't find form, falls back to LLM navigator."""
        page = MagicMock()
        page.locator.return_value.count.return_value = 0

        link_locator = MagicMock()
        page.get_by_role.return_value = link_locator
        link_locator.count.return_value = 1

        # LLM returns an element name, but form still not found → returns False
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Submit Request")]
        mock_response.usage.input_tokens = 500
        mock_response.usage.output_tokens = 10
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        page.accessibility = MagicMock()
        page.accessibility.snapshot.return_value = {"role": "WebArea", "children": []}

        with patch("portal_submitter.portal_navigator._get_anthropic_client", return_value=mock_client):
            result = navigate_to_form(page, "ketch", api_key="test-key")

        assert result is False
        # LLM was called (hints failed, fell through)
        assert mock_client.messages.create.called


class TestLLMNavigation:
    def test_llm_finds_form_in_one_step(self):
        """LLM suggests a button, clicking it reveals form fields."""
        page = MagicMock()

        # page_has_form: False initially, True after LLM-guided click
        locator_counts = iter([0, 2])
        page.locator.return_value.count.side_effect = lambda: next(locator_counts)

        page.accessibility = MagicMock()
        page.accessibility.snapshot.return_value = {
            "role": "WebArea",
            "children": [{"role": "link", "name": "Access your data"}],
        }

        link_locator = MagicMock()
        link_locator.count.return_value = 1
        page.get_by_role.return_value = link_locator

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Access your data")]
        mock_response.usage.input_tokens = 500
        mock_response.usage.output_tokens = 10
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("portal_submitter.portal_navigator._get_anthropic_client", return_value=mock_client):
            result = navigate_to_form(page, "unknown", api_key="test-key")

        assert result is True

    def test_llm_max_steps_exceeded(self):
        """LLM navigator gives up after 3 steps without finding form."""
        page = MagicMock()
        page.locator.return_value.count.return_value = 0  # never finds form

        page.accessibility = MagicMock()
        page.accessibility.snapshot.return_value = {
            "role": "WebArea",
            "children": [{"role": "button", "name": "Next"}],
        }

        link_locator = MagicMock()
        link_locator.count.return_value = 1
        page.get_by_role.return_value = link_locator

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Next")]
        mock_response.usage.input_tokens = 500
        mock_response.usage.output_tokens = 10
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        with patch("portal_submitter.portal_navigator._get_anthropic_client", return_value=mock_client):
            result = navigate_to_form(page, "unknown", api_key="test-key")

        assert result is False
        # Should have tried exactly 3 LLM steps
        assert mock_client.messages.create.call_count == 3


class TestNoApiKey:
    def test_no_api_key_skips_llm(self):
        """Without API key, LLM fallback is skipped."""
        page = MagicMock()
        page.locator.return_value.count.return_value = 0

        link_locator = MagicMock()
        page.get_by_role.return_value = link_locator
        link_locator.count.return_value = 1

        result = navigate_to_form(page, "ketch")  # no api_key
        assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_portal_navigator.py -v`
Expected: FAIL — `ImportError: cannot import name 'navigate_to_form' from 'portal_submitter.portal_navigator'`

- [ ] **Step 3: Implement portal_navigator.py**

Create `portal_submitter/portal_navigator.py`:

```python
"""Multi-step portal navigation — navigate through wizard-like portals to reach the form page.

Hybrid strategy:
1. Platform-specific hint patterns (fast, free)
2. LLM-guided fallback via Claude Haiku (~$0.01/step, max 3 steps)
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

_MAX_LLM_STEPS = 3
_NAV_TIMEOUT = 10_000  # ms

# Platform → ordered list of regex patterns for clickable element names.
# Each pattern is tried in order; after clicking, check if form appeared.
_NAVIGATION_HINTS: dict[str, list[str]] = {
    "ketch": [
        r"(?:your\s+)?privacy\s+request",
        r"access\s+(?:your\s+)?data",
    ],
}


def page_has_form(page: Any) -> bool:
    """Check if the current page has visible form fields."""
    return page.locator("input:not([type=hidden]), textarea, select").count() > 0


def navigate_to_form(
    page: Any,
    platform: str,
    *,
    api_key: str | None = None,
) -> bool:
    """Navigate through a multi-step portal to reach the form page.

    Args:
        page: Playwright page object (already navigated to portal landing).
        platform: Detected platform string (e.g. "ketch", "unknown").
        api_key: Anthropic API key for LLM fallback. If None, LLM fallback is skipped.

    Returns:
        True if a page with form fields was reached, False otherwise.
    """
    # Layer 1: Platform-specific hints
    hints = _NAVIGATION_HINTS.get(platform, [])
    for pattern in hints:
        if page_has_form(page):
            return True
        if not _click_by_pattern(page, pattern):
            continue
        _wait_for_load(page)

    if page_has_form(page):
        return True

    # Layer 2: LLM-guided navigation
    if not api_key:
        return False

    client = _get_anthropic_client(api_key)
    if not client:
        return False

    for _step in range(_MAX_LLM_STEPS):
        if page_has_form(page):
            return True

        element_name = _llm_suggest_click(client, page)
        if not element_name:
            return False

        if not _click_by_name(page, element_name):
            return False
        _wait_for_load(page)

    return page_has_form(page)


def _click_by_pattern(page: Any, pattern: str) -> bool:
    """Find and click a link or button matching a regex pattern. Returns True if clicked."""
    compiled = re.compile(pattern, re.I)
    for role in ("link", "button"):
        locator = page.get_by_role(role, name=compiled)
        if locator.count() > 0:
            locator.first.click()
            return True
    return False


def _click_by_name(page: Any, name: str) -> bool:
    """Find and click an element by its exact accessible name. Returns True if clicked."""
    for role in ("link", "button"):
        locator = page.get_by_role(role, name=name)
        if locator.count() > 0:
            locator.first.click()
            return True
    return False


def _wait_for_load(page: Any) -> None:
    """Wait for navigation/content to settle."""
    try:
        page.wait_for_load_state("networkidle", timeout=_NAV_TIMEOUT)
    except Exception:
        pass  # timeout is non-fatal — page may already be idle


def _llm_suggest_click(client: Any, page: Any) -> str:
    """Ask Claude Haiku which element to click to reach the GDPR form.

    Returns the accessible name of the element, or empty string on failure.
    """
    try:
        snapshot = page.accessibility.snapshot()
    except Exception:
        return ""

    snapshot_text = json.dumps(snapshot, indent=2, default=str)
    # Cap snapshot size to avoid excessive tokens
    if len(snapshot_text) > 8000:
        snapshot_text = snapshot_text[:8000] + "\n... (truncated)"

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": (
                    "This is a privacy/GDPR portal page. I need to reach the data access "
                    "request form. Here is the page's accessibility tree:\n\n"
                    f"{snapshot_text}\n\n"
                    "Which ONE button or link should I click next to get to the GDPR data "
                    "access request form? Return ONLY the exact accessible name of the "
                    "element, nothing else. If no relevant element exists, return NONE."
                ),
            }],
        )
        element_name = response.content[0].text.strip()

        # Track cost
        try:
            from contact_resolver.cost_tracker import record_llm_call
            record_llm_call(
                company_name="portal_navigator",
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                model="claude-haiku-4-5-20251001",
                found=element_name.upper() != "NONE",
                source="portal_navigator",
                purpose="portal_navigation",
            )
        except Exception:
            pass

        if element_name.upper() == "NONE":
            return ""
        return element_name

    except Exception:
        return ""


def _get_anthropic_client(api_key: str | None = None) -> Any:
    """Create an Anthropic client. Returns None if unavailable."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=key)
    except ImportError:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_portal_navigator.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add portal_submitter/portal_navigator.py tests/unit/test_portal_navigator.py
git commit -m "feat: add multi-step portal navigator with hint + LLM hybrid"
```

---

### Task 5: Integrate Navigator into Submitter

**Files:**
- Modify: `portal_submitter/submitter.py`
- Modify: `tests/unit/test_portal_submitter.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_portal_submitter.py`:

```python
class TestJunkUrlGuard:
    def test_junk_url_returns_failed(self):
        """Portal URL that matches junk filter fails immediately without browser."""
        letter = MagicMock()
        letter.portal_url = "https://society.zendesk.com/hc/en-us/requests/649929"
        letter.company_name = "Zendesk"
        result = submit_portal(letter, "test@example.com")
        assert result.portal_status == "failed"
        assert "junk" in result.error.lower()
        assert result.needs_manual is True


class TestNavigatorIntegration:
    def test_no_form_triggers_navigator(self):
        """When landing page has no form, submitter calls navigate_to_form."""
        letter = MagicMock()
        letter.portal_url = "https://zendesk.es/"
        letter.company_name = "Zendesk"

        mock_page = MagicMock()
        # page_has_form returns False on initial check
        form_counts = iter([0, 0])
        mock_page.locator.return_value.count.side_effect = lambda: next(form_counts)

        mock_pw = MagicMock()
        mock_browser = MagicMock()
        mock_pw.__enter__ = MagicMock(return_value=mock_pw)
        mock_pw.__exit__ = MagicMock(return_value=False)
        mock_pw.chromium.launch.return_value = mock_browser
        mock_context = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        with patch("portal_submitter.submitter.detect_platform", return_value="ketch"):
            with patch("portal_submitter.submitter.navigate_to_form", return_value=False) as mock_nav:
                result = submit_portal(
                    letter, "test@example.com",
                    browser_launcher=lambda: mock_pw,
                )

        mock_nav.assert_called_once()
        assert result.needs_manual is True
        assert "form_not_found" in result.error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py::TestJunkUrlGuard -v`
Expected: FAIL — no junk URL guard in submitter yet.

- [ ] **Step 3: Implement changes in submitter.py**

Update `portal_submitter/submitter.py`:

1. Add imports at top (after existing imports):

```python
from portal_submitter.portal_navigator import navigate_to_form, page_has_form
from reply_monitor.classifier import _is_junk_url
```

2. Add junk URL guard in `submit_portal()`, after the `if not letter.portal_url:` check (line 44) and before the platform check:

```python
    if _is_junk_url(letter.portal_url):
        return PortalResult(
            error="junk_portal_url",
            needs_manual=True,
            portal_status="failed",
        )
```

3. Update the `detect_platform` call to pass page HTML when available. In `_browser_submit()`, after `page.goto()` (line 131), add navigator integration:

Replace the block from `page.goto(...)` through the `if not mapping.fields:` error return (lines 131-142) with:

```python
            # Navigate to portal
            page.goto(letter.portal_url, wait_until="networkidle", timeout=30_000)

            # Check if landing page has form fields; if not, navigate multi-step
            if not page_has_form(page):
                api_key = os.environ.get("ANTHROPIC_API_KEY")
                if not navigate_to_form(page, platform, api_key=api_key):
                    screenshot_path = _take_screenshot(page, letter.company_name)
                    browser.close()
                    return PortalResult(
                        error="form_not_found_after_navigation",
                        needs_manual=True,
                        screenshot_path=screenshot_path,
                        portal_status="failed",
                    )

            # Analyze form
            mapping = analyze_form(page, llm_call=llm_call, cached_mapping=cached_mapping)
            if not mapping.fields:
                screenshot_path = _take_screenshot(page, letter.company_name)
                browser.close()
                return PortalResult(
                    error="no_form_fields_detected",
                    screenshot_path=screenshot_path,
                    portal_status="failed",
                )
```

4. Add `import os` to the imports at the top if not already present.

5. Update `detect_platform` call to pass HTML for branded domain detection. In `submit_portal()`, after navigation, get page HTML:

In `_browser_submit()`, update the platform detection to be re-checked after page load. After `page.goto()`, detect platform with HTML:

```python
            # Re-detect platform with HTML (for branded domains like zendesk.es → ketch)
            try:
                html_content = page.content()
                detected = detect_platform(letter.portal_url, html=html_content)
                if detected != "unknown":
                    platform = detected
            except Exception:
                pass
```

This goes between `page.goto()` and the `if not page_has_form(page):` check.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py -v`
Expected: All PASS.

- [ ] **Step 5: Run full test suite**

Run: `.venv/bin/pytest tests/unit/ -q`
Expected: All pass, no regressions.

- [ ] **Step 6: Commit**

```bash
git add portal_submitter/submitter.py tests/unit/test_portal_submitter.py
git commit -m "feat(submitter): integrate navigator + junk URL guard"
```

---

### Task 6: Expand Junk URL Filter

**Files:**
- Modify: `reply_monitor/classifier.py`
- Modify: `tests/unit/test_reply_classifier.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_reply_classifier.py` inside `TestJunkURLFiltering`:

```python
def test_bare_request_path_is_junk(self):
    from reply_monitor.classifier import _is_junk_url
    assert _is_junk_url("https://company.zendesk.com/requests/12345") is True

def test_support_tickets_is_junk(self):
    from reply_monitor.classifier import _is_junk_url
    assert _is_junk_url("https://support.example.com/support/tickets/789") is True

def test_help_root_is_junk(self):
    from reply_monitor.classifier import _is_junk_url
    assert _is_junk_url("https://example.com/help/privacy") is True

def test_real_portal_not_junk(self):
    from reply_monitor.classifier import _is_junk_url
    assert _is_junk_url("https://zendesk.es/") is False

def test_data_export_not_junk(self):
    from reply_monitor.classifier import _is_junk_url
    assert _is_junk_url("https://example.com/data-export/download?token=abc") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_reply_classifier.py::TestJunkURLFiltering::test_bare_request_path_is_junk -v`
Expected: FAIL — current `_RE_JUNK_URL` doesn't match `/requests/12345`.

- [ ] **Step 3: Expand `_RE_JUNK_URL`**

In `reply_monitor/classifier.py`, update `_RE_JUNK_URL` (around line 409):

```python
_RE_JUNK_URL = re.compile(
    r"/hc/[a-z-]+/requests/\d+"          # Zendesk help center ticket pages
    r"|/hc/[a-z-]+/survey_responses/"     # Zendesk/CSAT surveys
    r"|/hc/[a-z-]+/articles/"             # Help center knowledge base articles
    r"|/satisfaction/"                      # CSAT survey endpoints
    r"|/feedback/"                          # Feedback forms
    r"|/survey[_-]?responses?/"            # Generic survey response URLs
    r"|/requests/\d+"                      # Bare support ticket paths
    r"|/support/tickets/"                  # Generic support ticket paths
    r"|/help/",                            # Help center root pages
    re.I,
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_reply_classifier.py::TestJunkURLFiltering -v`
Expected: All PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add reply_monitor/classifier.py tests/unit/test_reply_classifier.py
git commit -m "fix(classifier): expand junk URL filter for tickets, support, help pages"
```

---

### Task 7: Reprocess with URL Re-extraction

**Files:**
- Modify: `monitor.py`

- [ ] **Step 1: Update `_reprocess_existing` to re-extract URLs**

In `monitor.py`, modify the `_reprocess_existing` function (around line 686). After the `reply.tags = new_result.tags` line (line 722), add URL re-extraction:

```python
                if not dry_run:
                    reply.tags = new_result.tags
                    # Re-extract URLs with current junk URL filter
                    new_extracted = classify(msg, api_key=None).extracted
                    for url_key in ("data_link", "data_links", "portal_url"):
                        reply.extracted[url_key] = new_extracted.get(url_key, reply.extracted.get(url_key))
                changed += 1
```

Wait — the classify call is already being made (line 713). We can reuse `new_result.extracted` instead of calling classify again. Update to:

Replace lines 721-722:
```python
                if not dry_run:
                    reply.tags = new_result.tags
```

With:
```python
                if not dry_run:
                    reply.tags = new_result.tags
                    # Re-extract URL fields with current junk URL filter
                    for url_key in ("data_link", "data_links", "portal_url"):
                        if url_key in new_result.extracted:
                            reply.extracted[url_key] = new_result.extracted[url_key]
```

Also update the filter scope: currently `_reprocess_existing` only re-classifies replies tagged `{HUMAN_REVIEW, AUTO_ACKNOWLEDGE}`. For URL cleanup, we also need to reprocess `WRONG_CHANNEL` replies. Update the filter set:

```python
_REPROCESS_TAGS = {"HUMAN_REVIEW", "AUTO_ACKNOWLEDGE", "WRONG_CHANNEL"}
```

- [ ] **Step 2: Run full test suite to verify no regressions**

Run: `.venv/bin/pytest tests/unit/ -q`
Expected: All pass.

- [ ] **Step 3: Commit**

```bash
git add monitor.py
git commit -m "feat(monitor): re-extract URLs during reprocess to clean stale data"
```

---

### Task 8: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update CLAUDE.md with new module docs**

Add to the Portal Automation section in CLAUDE.md:

1. Add `portal_navigator.py` to the module list:
```
- `portal_navigator.py` — multi-step portal navigation. `navigate_to_form(page, platform, api_key=)` uses platform-specific hint patterns (free, fast) then LLM-guided fallback (Claude Haiku, max 3 steps). Called by `submitter.py` when landing page has no form fields. Hint patterns in `_NAVIGATION_HINTS` dict, extensible per platform.
```

2. Update `platform_hints.py` description to mention Ketch:
```
- `platform_hints.py` — detects portal platform: `onetrust`, `trustarc`, `ketch`, `salesforce`, `login_required` (Google, Apple, Meta, Amazon, Facebook, Twitter/X), `unknown`. `detect_platform(url, html="")` checks URL patterns first, then HTML signatures for branded domains (e.g. zendesk.es → ketch via `_KETCH_HTML_SIGNATURES`). `otp_sender_hints()` returns expected verification email senders per platform.
```

3. Add to the Known Issues / Fixed issues table:
```
| P1 | `portal_submitter/submitter.py` | No multi-step navigation — Ketch portals (zendesk.es) failed with `no_form_fields_detected`. Added `portal_navigator.py` with hybrid hint + LLM navigation |
| P1 | `portal_submitter/platform_hints.py` | Ketch platform not detected — added URL rules + HTML signature fallback |
| P2 | `reply_monitor/classifier.py` | Junk URL filter missed bare `/requests/`, `/support/tickets/`, `/help/` paths — expanded `_RE_JUNK_URL` |
| P2 | `monitor.py` | `--reprocess` didn't re-extract URLs — stale `portal_url`/`data_link` persisted. Now re-extracts URL fields during reprocess |
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with Ketch portal support and navigator module"
```

---

## Self-Review

**Spec coverage:**
- Section 1a (Ketch detection): Task 1 ✓
- Section 1b (OTP hints): Task 1 ✓
- Section 1c (Zendesk override): Task 2 ✓
- Section 1d (URL verifier Ketch): Task 3 ✓
- Section 2a-2d (Navigator module): Task 4 ✓
- Section 2c (Submitter integration): Task 5 ✓
- Section 3a (Junk URL expansion): Task 6 ✓
- Section 3b (Reprocess re-extraction): Task 7 ✓
- Section 3c (Junk URL guard): Task 5 ✓
- Section 4 (Dashboard): No changes needed (confirmed in spec)

**Placeholder scan:** No TBDs, TODOs, or vague instructions. All steps have code.

**Type consistency:**
- `detect_platform(url, html="")` signature matches across Task 1 (definition), Task 3 (url_verifier call), Task 5 (submitter call)
- `navigate_to_form(page, platform, *, api_key=None)` matches Task 4 (definition) and Task 5 (caller)
- `page_has_form(page)` matches Task 4 (definition) and Task 5 (caller)
- `_is_junk_url(url)` exists in classifier.py, imported in Task 5
