# Portal Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automate DSAR portal submissions using Playwright + LLM field mapping, with CAPTCHA dashboard relay and Gmail OTP handling.

**Architecture:** New `portal_submitter/` package takes a composed `SARLetter(method="portal")` and drives Playwright to fill and submit the portal form. Claude Haiku maps AXTree form fields to user data. Field mappings are cached per domain in `companies.json`. CAPTCHA screenshots are relayed to the dashboard for human solving. Email OTP/confirmation links are extracted via the existing Gmail API.

**Tech Stack:** Playwright (existing), Anthropic API (existing), Flask dashboard (existing), Gmail API (existing)

**Spec:** `docs/superpowers/specs/2026-04-12-portal-automation-design.md`

---

## File Structure

### New files

| File | Responsibility |
|------|---------------|
| `portal_submitter/__init__.py` | Package init — exports `submit_portal` |
| `portal_submitter/models.py` | `PortalResult`, `CaptchaChallenge` data classes |
| `portal_submitter/platform_hints.py` | URL pattern → platform detection + OTP sender hints |
| `portal_submitter/form_analyzer.py` | Extract AXTree, call Claude Haiku, return `PortalFieldMapping` |
| `portal_submitter/form_filler.py` | Playwright form filling + CAPTCHA detection |
| `portal_submitter/captcha_relay.py` | Save CAPTCHA screenshot, poll for user solution |
| `portal_submitter/otp_handler.py` | Poll Gmail for verification emails, extract code/link |
| `portal_submitter/submitter.py` | Main orchestrator wiring all components together |
| `tests/unit/test_portal_submitter.py` | All unit tests for the portal_submitter package |
| `dashboard/templates/captcha.html` | CAPTCHA solving page template |
| `test_portal.py` | Manual test script for real portal testing |

### Modified files

| File | Change |
|------|--------|
| `contact_resolver/models.py` | Add `PortalFormField`, `PortalFieldMapping` models; add `portal_field_mapping` to `CompanyRecord` |
| `letter_engine/sender.py` | Portal path in `send_letter()` delegates to `submit_portal()` |
| `letter_engine/tracker.py` | Add portal-specific fields to `record_sent()` |
| `dashboard/app.py` | New routes: `/portal/submit`, `/portal/status`, `/captcha` |
| `dashboard/templates/dashboard.html` | Portal submit/open buttons on company cards |
| `dashboard/templates/company_detail.html` | Portal submission status display, screenshots |
| `run.py` | Add `--portal-only` flag, attempt portal submissions in pipeline |

---

## Task 1: Models and platform detection

**Files:**
- Create: `portal_submitter/__init__.py`
- Create: `portal_submitter/models.py`
- Create: `portal_submitter/platform_hints.py`
- Modify: `contact_resolver/models.py`
- Create: `tests/unit/test_portal_submitter.py`

- [ ] **Step 1: Write tests for models and platform detection**

Create `tests/unit/test_portal_submitter.py`:

```python
"""Unit tests for portal_submitter package."""

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from portal_submitter.models import PortalResult, CaptchaChallenge
from portal_submitter.platform_hints import detect_platform, otp_sender_hints


class TestPortalResult:
    def test_success_result(self):
        r = PortalResult(success=True, confirmation_ref="TICKET-123")
        assert r.success is True
        assert r.needs_manual is False
        assert r.confirmation_ref == "TICKET-123"

    def test_manual_result(self):
        r = PortalResult(success=False, needs_manual=True, error="login_required")
        assert r.success is False
        assert r.needs_manual is True

    def test_default_values(self):
        r = PortalResult()
        assert r.success is False
        assert r.needs_manual is False
        assert r.confirmation_ref == ""
        assert r.screenshot_path == ""
        assert r.error == ""


class TestCaptchaChallenge:
    def test_creation(self):
        c = CaptchaChallenge(domain="example.com", portal_url="https://example.com/privacy")
        assert c.status == "pending"
        assert c.solution == ""
        assert c.domain == "example.com"


class TestPlatformDetection:
    def test_onetrust_by_domain(self):
        assert detect_platform("https://privacyportal.onetrust.com/webform/abc-123") == "onetrust"

    def test_onetrust_by_privacyportal(self):
        assert detect_platform("https://company.my.onetrust.com/webform/xyz") == "onetrust"

    def test_trustarc(self):
        assert detect_platform("https://submit-irm.trustarc.com/services/validation/abc") == "trustarc"

    def test_trustarc_by_keyword(self):
        assert detect_platform("https://privacy.trustarc.com/form/abc") == "trustarc"

    def test_salesforce(self):
        assert detect_platform("https://help.glassdoor.com/s/privacyrequest") == "salesforce"

    def test_login_required_google(self):
        assert detect_platform("https://myaccount.google.com/data-and-privacy") == "login_required"

    def test_login_required_apple(self):
        assert detect_platform("https://privacy.apple.com") == "login_required"

    def test_login_required_meta(self):
        assert detect_platform("https://www.facebook.com/dyi") == "login_required"

    def test_unknown(self):
        assert detect_platform("https://example.com/privacy-request") == "unknown"

    def test_empty_url(self):
        assert detect_platform("") == "unknown"


class TestOTPSenderHints:
    def test_onetrust_hints(self):
        hints = otp_sender_hints("onetrust")
        assert "noreply@onetrust.com" in hints

    def test_trustarc_hints(self):
        hints = otp_sender_hints("trustarc")
        assert any("trustarc" in h for h in hints)

    def test_unknown_returns_empty(self):
        assert otp_sender_hints("unknown") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py -v`
Expected: `ModuleNotFoundError: No module named 'portal_submitter'`

- [ ] **Step 3: Create models**

Create `portal_submitter/__init__.py`:

```python
"""Portal submission automation for DSAR portals."""
```

Create `portal_submitter/models.py`:

```python
"""Data models for portal submission results and state."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PortalResult:
    """Result of a portal submission attempt."""
    success: bool = False
    needs_manual: bool = False
    confirmation_ref: str = ""
    screenshot_path: str = ""
    error: str = ""
    portal_status: str = ""  # "submitted", "awaiting_verification", "manual", "failed"


@dataclass
class CaptchaChallenge:
    """State for a CAPTCHA relay between Playwright and the dashboard."""
    domain: str = ""
    portal_url: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    status: str = "pending"  # "pending", "solved", "expired"
    solution: str = ""
    screenshot_path: str = ""
```

- [ ] **Step 4: Create platform_hints**

Create `portal_submitter/platform_hints.py`:

```python
"""Detect DSAR portal platform from URL patterns and provide OTP sender hints."""

import re
from urllib.parse import urlparse

# Domains where the portal requires account login — not automatable
_LOGIN_REQUIRED_DOMAINS: set[str] = {
    "google.com", "apple.com", "meta.com", "amazon.com",
    "facebook.com", "twitter.com", "x.com",
}

# URL pattern → platform
_PLATFORM_RULES: list[tuple[str, re.Pattern]] = [
    ("onetrust", re.compile(r"onetrust\.com|privacyportal", re.I)),
    ("trustarc", re.compile(r"trustarc\.com|submit-irm", re.I)),
]

# OTP sender email patterns per platform
_OTP_SENDERS: dict[str, list[str]] = {
    "onetrust": ["noreply@onetrust.com", "privacyportal"],
    "trustarc": ["privacy@trustarc.com", "noreply@trustarc.com"],
    "salesforce": ["noreply@salesforce.com"],
}


def detect_platform(url: str) -> str:
    """Classify a portal URL into a known platform or 'unknown'.

    Returns one of: "onetrust", "trustarc", "salesforce", "login_required", "unknown".
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

    # Check URL pattern rules
    for platform, pattern in _PLATFORM_RULES:
        if pattern.search(url):
            return platform

    # Salesforce Experience Cloud: /s/ path prefix
    try:
        path = urlparse(url).path or ""
    except Exception:
        path = ""
    if re.match(r"^/s/", path):
        return "salesforce"

    return "unknown"


def otp_sender_hints(platform: str) -> list[str]:
    """Return email sender patterns to watch for OTP/verification emails."""
    return list(_OTP_SENDERS.get(platform, []))
```

- [ ] **Step 5: Add PortalFieldMapping to contact_resolver/models.py**

Add after the `SubprocessorRecord` class (before `CompanyRecord`):

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

Add to `CompanyRecord`:

```python
portal_field_mapping: PortalFieldMapping | None = None
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py -v`
Expected: All 14 tests PASS

- [ ] **Step 7: Run existing tests to verify no regression**

Run: `.venv/bin/pytest tests/unit/ -q`
Expected: All existing tests still pass

- [ ] **Step 8: Commit**

```bash
git add portal_submitter/ tests/unit/test_portal_submitter.py contact_resolver/models.py
git commit -m "feat(portal): add models, platform detection, and PortalFieldMapping"
```

---

## Task 2: Form analyzer (AXTree extraction + LLM field mapping)

**Files:**
- Create: `portal_submitter/form_analyzer.py`
- Test: `tests/unit/test_portal_submitter.py` (add tests)

- [ ] **Step 1: Write tests for form analyzer**

Append to `tests/unit/test_portal_submitter.py`:

```python
from portal_submitter.form_analyzer import analyze_form, build_user_data
from contact_resolver.models import PortalFieldMapping, PortalFormField


class TestBuildUserData:
    @patch("portal_submitter.form_analyzer.settings")
    def test_name_splitting(self, mock_settings):
        mock_settings.user_full_name = "Jane Doe"
        mock_settings.user_email = "jane@example.com"
        mock_settings.user_address_country = "United Kingdom"

        letter = MagicMock()
        letter.body = "SAR body text"

        data = build_user_data(letter)
        assert data["first_name"] == "Jane"
        assert data["last_name"] == "Doe"
        assert data["email"] == "jane@example.com"
        assert data["country"] == "United Kingdom"
        assert data["request_type"] == "Access my personal data"
        assert data["description"] == "SAR body text"

    @patch("portal_submitter.form_analyzer.settings")
    def test_single_name(self, mock_settings):
        mock_settings.user_full_name = "Cher"
        mock_settings.user_email = "cher@example.com"
        mock_settings.user_address_country = "US"

        letter = MagicMock()
        letter.body = ""

        data = build_user_data(letter)
        assert data["first_name"] == "Cher"
        assert data["last_name"] == ""


class TestAnalyzeForm:
    def test_parses_llm_response(self):
        """analyze_form returns PortalFieldMapping from LLM JSON response."""
        fake_axtree = {
            "role": "WebArea",
            "children": [
                {"role": "textbox", "name": "First Name"},
                {"role": "textbox", "name": "Email Address"},
                {"role": "combobox", "name": "Country"},
                {"role": "button", "name": "Submit"},
            ],
        }
        fake_page = MagicMock()
        fake_page.accessibility.snapshot.return_value = fake_axtree

        llm_response = json.dumps({
            "fields": [
                {"name": "First Name", "value_key": "first_name", "role": "textbox"},
                {"name": "Email Address", "value_key": "email", "role": "textbox"},
                {"name": "Country", "value_key": "country", "role": "combobox"},
            ],
            "submit_button": "Submit",
        })

        def mock_llm_call(prompt: str) -> str:
            return llm_response

        mapping = analyze_form(fake_page, llm_call=mock_llm_call)
        assert len(mapping.fields) == 3
        assert mapping.fields[0].name == "First Name"
        assert mapping.fields[0].value_key == "first_name"
        assert mapping.submit_button == "Submit"

    def test_uses_cached_mapping(self):
        """When a fresh cached mapping is provided, no LLM call is made."""
        cached = PortalFieldMapping(
            cached_at=date.today().isoformat(),
            platform="onetrust",
            fields=[PortalFormField(name="Email", value_key="email", role="textbox")],
            submit_button="Submit",
        )

        call_count = 0
        def mock_llm_call(prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            return "{}"

        fake_page = MagicMock()
        mapping = analyze_form(fake_page, llm_call=mock_llm_call, cached_mapping=cached)
        assert call_count == 0
        assert mapping.fields[0].name == "Email"

    def test_expired_cache_triggers_llm(self):
        """Mapping older than 90 days triggers a fresh LLM call."""
        old_date = (date.today() - timedelta(days=91)).isoformat()
        cached = PortalFieldMapping(
            cached_at=old_date,
            platform="onetrust",
            fields=[PortalFormField(name="Email", value_key="email", role="textbox")],
            submit_button="Submit",
        )

        fake_axtree = {
            "role": "WebArea",
            "children": [
                {"role": "textbox", "name": "Email"},
                {"role": "button", "name": "Submit"},
            ],
        }
        fake_page = MagicMock()
        fake_page.accessibility.snapshot.return_value = fake_axtree

        llm_response = json.dumps({
            "fields": [{"name": "Email", "value_key": "email", "role": "textbox"}],
            "submit_button": "Submit",
        })

        call_count = 0
        def mock_llm_call(prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            return llm_response

        mapping = analyze_form(fake_page, llm_call=mock_llm_call, cached_mapping=cached)
        assert call_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py::TestBuildUserData -v`
Expected: `ImportError: cannot import name 'analyze_form' from 'portal_submitter.form_analyzer'`

- [ ] **Step 3: Implement form_analyzer.py**

Create `portal_submitter/form_analyzer.py`:

```python
"""Extract form structure via accessibility tree and map fields using LLM."""

import json
from datetime import date, timedelta
from typing import Any, Callable

from config.settings import settings
from contact_resolver.models import PortalFieldMapping, PortalFormField
from letter_engine.models import SARLetter

_CACHE_TTL_DAYS = 90

_INTERACTIVE_ROLES = {"textbox", "combobox", "checkbox", "radio", "spinbutton", "searchbox"}
_BUTTON_ROLES = {"button", "link"}

_FIELD_MAPPING_PROMPT = """You are mapping a web form's fields to user data for a GDPR Subject Access Request.

Here are the form's interactive elements (from the accessibility tree):
{elements_json}

Map these user data fields to the form elements:
- first_name: "{first_name}"
- last_name: "{last_name}"
- email: "{email}"
- country: "{country}"
- request_type: "Access my personal data"
- description: (SAR letter body — long text, use if there is a textarea)
- relationship: "Customer"

Return JSON only, no markdown:
{{"fields": [{{"name": "<element name>", "value_key": "<user data key>", "role": "<element role>"}}], "submit_button": "<name of submit button>"}}

Rules:
- Only map fields that have a clear match. Skip fields you cannot confidently map.
- For dropdowns (combobox), use the closest matching option text as the value.
- The submit button is typically labeled "Submit", "Send", "Submit Request", or similar.
- If request_type is a dropdown, choose the option closest to "Access my personal data" or "Subject Access Request".
"""


def build_user_data(letter: SARLetter) -> dict[str, str]:
    """Assemble user data dict from settings + letter for form filling."""
    name_parts = settings.user_full_name.split(" ", 1)
    return {
        "first_name": name_parts[0],
        "last_name": name_parts[1] if len(name_parts) > 1 else "",
        "email": settings.user_email,
        "country": settings.user_address_country,
        "request_type": "Access my personal data",
        "description": letter.body,
        "relationship": "Customer",
    }


def analyze_form(
    page: Any,
    *,
    llm_call: Callable[[str], str] | None = None,
    cached_mapping: PortalFieldMapping | None = None,
) -> PortalFieldMapping:
    """Extract form fields from the page and return a field mapping.

    Args:
        page: Playwright page object (or mock with .accessibility.snapshot()).
        llm_call: Callable that takes a prompt string and returns LLM response text.
                  Injectable for testing. If None, uses default Anthropic call.
        cached_mapping: Previously cached mapping. If fresh (within TTL), returned as-is.

    Returns:
        PortalFieldMapping with fields mapped to user data keys.
    """
    # Check cache
    if cached_mapping and _is_cache_fresh(cached_mapping.cached_at):
        return cached_mapping

    # Extract accessibility tree
    axtree = page.accessibility.snapshot()
    elements = _extract_interactive_elements(axtree)

    if not elements:
        return PortalFieldMapping(cached_at=date.today().isoformat())

    # Build prompt
    user_data = {
        "first_name": settings.user_full_name.split(" ", 1)[0],
        "last_name": settings.user_full_name.split(" ", 1)[1] if " " in settings.user_full_name else "",
        "email": settings.user_email,
        "country": settings.user_address_country,
    }

    prompt = _FIELD_MAPPING_PROMPT.format(
        elements_json=json.dumps(elements, indent=2),
        **user_data,
    )

    # Call LLM
    if llm_call is None:
        llm_call = _default_llm_call
    raw = llm_call(prompt)

    # Parse response
    return _parse_mapping_response(raw)


def _is_cache_fresh(cached_at: str) -> bool:
    """Return True if cached_at is within the TTL."""
    if not cached_at:
        return False
    try:
        cached_date = date.fromisoformat(cached_at)
        return (date.today() - cached_date).days < _CACHE_TTL_DAYS
    except ValueError:
        return False


def _extract_interactive_elements(node: dict, results: list | None = None) -> list[dict]:
    """Walk the AXTree and collect interactive elements."""
    if results is None:
        results = []

    role = node.get("role", "")
    name = node.get("name", "")

    if role in _INTERACTIVE_ROLES and name:
        results.append({"role": role, "name": name})
    elif role in _BUTTON_ROLES and name:
        results.append({"role": "button", "name": name})

    for child in node.get("children", []):
        _extract_interactive_elements(child, results)

    return results


def _parse_mapping_response(raw: str) -> PortalFieldMapping:
    """Parse the LLM JSON response into a PortalFieldMapping."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON from markdown code blocks
        import re
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            data = json.loads(match.group())
        else:
            return PortalFieldMapping(cached_at=date.today().isoformat())

    fields = []
    for f in data.get("fields", []):
        fields.append(PortalFormField(
            name=f.get("name", ""),
            value_key=f.get("value_key", ""),
            role=f.get("role", "textbox"),
        ))

    return PortalFieldMapping(
        cached_at=date.today().isoformat(),
        fields=fields,
        submit_button=data.get("submit_button", ""),
    )


def _default_llm_call(prompt: str) -> str:
    """Call Claude Haiku for field mapping. Used when no injectable is provided."""
    import anthropic
    from contact_resolver.cost_tracker import record_llm_call

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text

    record_llm_call(
        purpose="portal_field_mapping",
        model="claude-haiku-4-5-20251001",
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        found=bool(text.strip()),
    )

    return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py -v`
Expected: All tests PASS (TestPortalResult, TestCaptchaChallenge, TestPlatformDetection, TestOTPSenderHints, TestBuildUserData, TestAnalyzeForm)

- [ ] **Step 5: Commit**

```bash
git add portal_submitter/form_analyzer.py tests/unit/test_portal_submitter.py
git commit -m "feat(portal): add form analyzer with AXTree extraction and LLM mapping"
```

---

## Task 3: Form filler (Playwright interaction + CAPTCHA detection)

**Files:**
- Create: `portal_submitter/form_filler.py`
- Test: `tests/unit/test_portal_submitter.py` (add tests)

- [ ] **Step 1: Write tests for form filler**

Append to `tests/unit/test_portal_submitter.py`:

```python
from portal_submitter.form_filler import fill_and_submit, detect_captcha


class TestDetectCaptcha:
    def test_detects_recaptcha_iframe(self):
        page = MagicMock()
        page.query_selector.side_effect = lambda sel: (
            MagicMock() if "recaptcha" in sel else None
        )
        assert detect_captcha(page) is True

    def test_detects_hcaptcha(self):
        page = MagicMock()
        page.query_selector.side_effect = lambda sel: (
            MagicMock() if "hcaptcha" in sel else None
        )
        assert detect_captcha(page) is True

    def test_detects_sitekey(self):
        page = MagicMock()
        page.query_selector.side_effect = lambda sel: (
            MagicMock() if "data-sitekey" in sel else None
        )
        assert detect_captcha(page) is True

    def test_no_captcha(self):
        page = MagicMock()
        page.query_selector.return_value = None
        assert detect_captcha(page) is False


class TestFillAndSubmit:
    def test_fills_textbox_fields(self):
        page = MagicMock()
        page.query_selector.return_value = None  # no CAPTCHA

        mapping = PortalFieldMapping(
            cached_at=date.today().isoformat(),
            fields=[
                PortalFormField(name="First Name", value_key="first_name", role="textbox"),
                PortalFormField(name="Email", value_key="email", role="textbox"),
            ],
            submit_button="Submit",
        )
        user_data = {"first_name": "Jane", "email": "jane@test.com"}

        fill_and_submit(page, mapping, user_data, click_submit=False)

        # Verify get_by_role was called for each field
        assert page.get_by_role.call_count >= 2

    def test_returns_captcha_detected(self):
        """When CAPTCHA is detected, fill_and_submit returns before clicking submit."""
        page = MagicMock()
        # Simulate CAPTCHA present
        page.query_selector.side_effect = lambda sel: (
            MagicMock() if "recaptcha" in sel else None
        )

        mapping = PortalFieldMapping(
            cached_at=date.today().isoformat(),
            fields=[PortalFormField(name="Email", value_key="email", role="textbox")],
            submit_button="Submit",
        )
        user_data = {"email": "jane@test.com"}

        result = fill_and_submit(page, mapping, user_data)
        assert result["captcha_detected"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py::TestDetectCaptcha -v`
Expected: `ImportError: cannot import name 'fill_and_submit' from 'portal_submitter.form_filler'`

- [ ] **Step 3: Implement form_filler.py**

Create `portal_submitter/form_filler.py`:

```python
"""Playwright-based form filling and CAPTCHA detection."""

from typing import Any

from contact_resolver.models import PortalFieldMapping

# Selectors that indicate CAPTCHA presence
_CAPTCHA_SELECTORS = [
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    '.g-recaptcha',
    '#captcha',
    '[data-sitekey]',
    'iframe[src*="challenges.cloudflare.com"]',
]

# Stealth script to reduce automation detection (same as link_downloader.py)
STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    window.chrome = { runtime: {} };
    Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en'] });
"""


def detect_captcha(page: Any) -> bool:
    """Check if the page contains a CAPTCHA element."""
    for selector in _CAPTCHA_SELECTORS:
        try:
            if page.query_selector(selector):
                return True
        except Exception:
            continue
    return False


def fill_and_submit(
    page: Any,
    mapping: PortalFieldMapping,
    user_data: dict[str, str],
    *,
    click_submit: bool = True,
) -> dict:
    """Fill form fields and optionally click submit.

    Args:
        page: Playwright page object.
        mapping: Field mapping from form_analyzer.
        user_data: Dict of user data keyed by value_key.
        click_submit: If False, fill fields but don't submit (for dry-run/testing).

    Returns:
        Dict with keys: filled_count (int), captcha_detected (bool), submitted (bool).
    """
    filled = 0

    for field in mapping.fields:
        value = user_data.get(field.value_key, "")
        if not value:
            continue

        try:
            if field.role == "textbox":
                _fill_textbox(page, field.name, value)
                filled += 1
            elif field.role == "combobox":
                _select_combobox(page, field.name, value)
                filled += 1
            elif field.role == "checkbox":
                _check_checkbox(page, field.name)
                filled += 1
        except Exception:
            # Field not found or not interactable — skip, don't crash
            continue

    # Check for CAPTCHA before submitting
    captcha = detect_captcha(page)
    if captcha or not click_submit:
        return {"filled_count": filled, "captcha_detected": captcha, "submitted": False}

    # Click submit
    submitted = False
    if mapping.submit_button:
        try:
            btn = page.get_by_role("button", name=mapping.submit_button)
            btn.click()
            page.wait_for_load_state("networkidle", timeout=15_000)
            submitted = True
        except Exception:
            # Try a broader selector
            try:
                btn = page.locator(f'button:has-text("{mapping.submit_button}")')
                btn.first.click()
                page.wait_for_load_state("networkidle", timeout=15_000)
                submitted = True
            except Exception:
                pass

    return {"filled_count": filled, "captcha_detected": False, "submitted": submitted}


def _fill_textbox(page: Any, name: str, value: str) -> None:
    """Fill a textbox identified by its accessible name."""
    el = page.get_by_role("textbox", name=name)
    el.clear()
    el.fill(value)


def _select_combobox(page: Any, name: str, value: str) -> None:
    """Select an option in a combobox/dropdown by its accessible name."""
    el = page.get_by_role("combobox", name=name)
    try:
        el.select_option(label=value)
    except Exception:
        # Some dropdowns need click + type to filter
        el.click()
        el.fill(value)
        page.keyboard.press("Enter")


def _check_checkbox(page: Any, name: str) -> None:
    """Check a checkbox identified by its accessible name."""
    el = page.get_by_role("checkbox", name=name)
    if not el.is_checked():
        el.check()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py::TestDetectCaptcha tests/unit/test_portal_submitter.py::TestFillAndSubmit -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add portal_submitter/form_filler.py tests/unit/test_portal_submitter.py
git commit -m "feat(portal): add Playwright form filler with CAPTCHA detection"
```

---

## Task 4: CAPTCHA relay

**Files:**
- Create: `portal_submitter/captcha_relay.py`
- Test: `tests/unit/test_portal_submitter.py` (add tests)

- [ ] **Step 1: Write tests for CAPTCHA relay**

Append to `tests/unit/test_portal_submitter.py`:

```python
from portal_submitter.captcha_relay import request_solve, poll_solution, _challenge_path


class TestCaptchaRelay:
    def test_request_solve_creates_files(self, tmp_path):
        screenshot_bytes = b"\x89PNG fake image data"
        challenge = request_solve(
            domain="example.com",
            portal_url="https://example.com/privacy",
            screenshot_bytes=screenshot_bytes,
            base_dir=tmp_path,
        )
        assert challenge.status == "pending"
        assert (tmp_path / "example.com.png").exists()
        assert (tmp_path / "example.com.json").exists()

    def test_poll_solution_returns_answer(self, tmp_path):
        # Write a solved challenge file
        challenge_data = {
            "domain": "example.com",
            "portal_url": "https://example.com/privacy",
            "status": "solved",
            "solution": "abc123",
        }
        (tmp_path / "example.com.json").write_text(json.dumps(challenge_data))

        solution = poll_solution("example.com", base_dir=tmp_path, timeout=1, poll_interval=0.1)
        assert solution == "abc123"

    def test_poll_solution_timeout(self, tmp_path):
        # Write a pending (unsolved) challenge
        challenge_data = {"domain": "example.com", "status": "pending", "solution": ""}
        (tmp_path / "example.com.json").write_text(json.dumps(challenge_data))

        solution = poll_solution("example.com", base_dir=tmp_path, timeout=0.3, poll_interval=0.1)
        assert solution is None

    def test_challenge_path(self, tmp_path):
        p = _challenge_path("example.com", tmp_path)
        assert p == tmp_path / "example.com.json"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py::TestCaptchaRelay -v`
Expected: `ImportError`

- [ ] **Step 3: Implement captcha_relay.py**

Create `portal_submitter/captcha_relay.py`:

```python
"""CAPTCHA relay: save screenshot for dashboard, poll for user solution."""

import json
import time
from pathlib import Path

from portal_submitter.models import CaptchaChallenge

_DEFAULT_BASE_DIR = Path(__file__).parent.parent / "user_data" / "captcha_pending"
_DEFAULT_TIMEOUT = 300  # 5 minutes
_DEFAULT_POLL_INTERVAL = 2  # seconds


def request_solve(
    domain: str,
    portal_url: str,
    screenshot_bytes: bytes,
    *,
    base_dir: Path = _DEFAULT_BASE_DIR,
) -> CaptchaChallenge:
    """Save a CAPTCHA screenshot and challenge file for the dashboard to display.

    Args:
        domain: Company domain (used as filename).
        portal_url: The portal URL being submitted.
        screenshot_bytes: PNG screenshot of the CAPTCHA region.
        base_dir: Directory to write files to (injectable for testing).

    Returns:
        CaptchaChallenge with status="pending".
    """
    base_dir.mkdir(parents=True, exist_ok=True)

    # Write screenshot
    screenshot_path = base_dir / f"{domain}.png"
    screenshot_path.write_bytes(screenshot_bytes)

    # Write challenge JSON
    challenge = CaptchaChallenge(
        domain=domain,
        portal_url=portal_url,
        screenshot_path=str(screenshot_path),
    )
    challenge_path = _challenge_path(domain, base_dir)
    challenge_path.write_text(json.dumps({
        "domain": challenge.domain,
        "portal_url": challenge.portal_url,
        "created_at": challenge.created_at,
        "status": challenge.status,
        "solution": challenge.solution,
    }, indent=2))

    return challenge


def poll_solution(
    domain: str,
    *,
    base_dir: Path = _DEFAULT_BASE_DIR,
    timeout: float = _DEFAULT_TIMEOUT,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
) -> str | None:
    """Poll for a CAPTCHA solution written by the dashboard.

    Returns the solution string, or None on timeout.
    """
    path = _challenge_path(domain, base_dir)
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            data = json.loads(path.read_text())
            if data.get("status") == "solved" and data.get("solution"):
                # Clean up
                _cleanup(domain, base_dir)
                return data["solution"]
        except (json.JSONDecodeError, FileNotFoundError):
            pass
        time.sleep(poll_interval)

    # Timeout — clean up
    _cleanup(domain, base_dir)
    return None


def _challenge_path(domain: str, base_dir: Path) -> Path:
    return base_dir / f"{domain}.json"


def _cleanup(domain: str, base_dir: Path) -> None:
    """Remove pending CAPTCHA files."""
    for suffix in (".json", ".png"):
        path = base_dir / f"{domain}{suffix}"
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py::TestCaptchaRelay -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add portal_submitter/captcha_relay.py tests/unit/test_portal_submitter.py
git commit -m "feat(portal): add CAPTCHA relay for dashboard-based solving"
```

---

## Task 5: OTP handler

**Files:**
- Create: `portal_submitter/otp_handler.py`
- Test: `tests/unit/test_portal_submitter.py` (add tests)

- [ ] **Step 1: Write tests for OTP handler**

Append to `tests/unit/test_portal_submitter.py`:

```python
from portal_submitter.otp_handler import extract_otp_from_message, wait_for_otp


class TestExtractOTP:
    def test_extracts_confirmation_url(self):
        body = "Please confirm your request: https://requests.hrtechprivacy.com/confirm/abc-123"
        result = extract_otp_from_message(body)
        assert result["type"] == "url"
        assert "hrtechprivacy.com/confirm" in result["value"]

    def test_extracts_verify_url(self):
        body = "Click here to verify: https://privacy.example.com/verify?token=xyz"
        result = extract_otp_from_message(body)
        assert result["type"] == "url"
        assert "verify" in result["value"]

    def test_extracts_six_digit_code(self):
        body = "Your verification code is 847291. Enter this code to continue."
        result = extract_otp_from_message(body)
        assert result["type"] == "code"
        assert result["value"] == "847291"

    def test_no_otp_found(self):
        body = "Thank you for contacting us. We will review your request."
        result = extract_otp_from_message(body)
        assert result is None

    def test_url_preferred_over_code(self):
        body = "Your code is 123456. Or click https://example.com/confirm/token-abc"
        result = extract_otp_from_message(body)
        assert result["type"] == "url"


class TestWaitForOTP:
    def test_finds_otp_in_inbox(self):
        """wait_for_otp finds a verification email and extracts the OTP."""
        fake_messages = [
            {
                "from": "noreply@onetrust.com",
                "body": "Verify your request: https://privacyportal.onetrust.com/confirm/abc",
                "date": "2026-04-12T12:00:00Z",
            }
        ]

        def mock_fetch_recent(scan_email, sender_hints, since_minutes):
            return fake_messages

        result = wait_for_otp(
            scan_email="user@gmail.com",
            sender_hints=["noreply@onetrust.com"],
            fetch_recent=mock_fetch_recent,
            timeout=1,
            poll_interval=0.1,
        )
        assert result is not None
        assert result["type"] == "url"
        assert "confirm" in result["value"]

    def test_no_matching_email(self):
        def mock_fetch_recent(scan_email, sender_hints, since_minutes):
            return []

        result = wait_for_otp(
            scan_email="user@gmail.com",
            sender_hints=["noreply@onetrust.com"],
            fetch_recent=mock_fetch_recent,
            timeout=0.3,
            poll_interval=0.1,
        )
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py::TestExtractOTP -v`
Expected: `ImportError`

- [ ] **Step 3: Implement otp_handler.py**

Create `portal_submitter/otp_handler.py`:

```python
"""Monitor Gmail for OTP/verification emails after portal form submission."""

import re
import time
from typing import Any, Callable

# Patterns for confirmation/verification URLs
_CONFIRM_URL_RE = re.compile(
    r"https?://[\w./-]+"
    r"(?:confirm|verify|validate|activate)"
    r"[\w./?&=%-]*",
    re.IGNORECASE,
)

# 6-digit OTP code
_OTP_CODE_RE = re.compile(r"\b(\d{6})\b")

_DEFAULT_TIMEOUT = 120  # 2 minutes
_DEFAULT_POLL_INTERVAL = 10  # seconds


def extract_otp_from_message(body: str) -> dict | None:
    """Extract a confirmation URL or OTP code from an email body.

    Returns:
        {"type": "url", "value": "https://..."} or
        {"type": "code", "value": "123456"} or
        None if nothing found.
    """
    # URLs take priority over codes
    url_match = _CONFIRM_URL_RE.search(body)
    if url_match:
        return {"type": "url", "value": url_match.group()}

    code_match = _OTP_CODE_RE.search(body)
    if code_match:
        return {"type": "code", "value": code_match.group(1)}

    return None


def wait_for_otp(
    scan_email: str,
    sender_hints: list[str],
    *,
    fetch_recent: Callable | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
) -> dict | None:
    """Poll Gmail for a verification email and extract OTP/confirmation link.

    Args:
        scan_email: Gmail account to poll.
        sender_hints: Email addresses or domain fragments to match against sender.
        fetch_recent: Injectable callable(scan_email, sender_hints, since_minutes) -> list[dict].
                      Each dict has "from", "body", "date". If None, uses Gmail API.
        timeout: Max seconds to wait.
        poll_interval: Seconds between polls.

    Returns:
        {"type": "url"|"code", "value": str} or None on timeout.
    """
    if fetch_recent is None:
        fetch_recent = _gmail_fetch_recent

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        messages = fetch_recent(scan_email, sender_hints, since_minutes=5)
        for msg in messages:
            result = extract_otp_from_message(msg.get("body", ""))
            if result:
                return result
        time.sleep(poll_interval)

    return None


def _gmail_fetch_recent(
    scan_email: str,
    sender_hints: list[str],
    since_minutes: int = 5,
) -> list[dict]:
    """Fetch recent Gmail messages matching sender hints. Uses existing OAuth."""
    try:
        from auth.gmail_oauth import get_gmail_service
        import base64
        from datetime import datetime, timedelta, timezone

        service, _ = get_gmail_service(email_hint=scan_email)

        # Build query: from any of the sender hints, within the time window
        from_clauses = " OR ".join(f"from:{s}" for s in sender_hints)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        query = f"({from_clauses}) after:{int(cutoff.timestamp())}"

        resp = service.users().messages().list(userId="me", q=query, maxResults=5).execute()
        messages = resp.get("messages", [])

        results = []
        for msg_ref in messages:
            msg = service.users().messages().get(
                userId="me", id=msg_ref["id"], format="full"
            ).execute()

            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
            body = _extract_body(msg.get("payload", {}))

            results.append({
                "from": headers.get("from", ""),
                "body": body,
                "date": headers.get("date", ""),
            })

        return results
    except Exception:
        return []


def _extract_body(payload: dict) -> str:
    """Extract plain text body from Gmail message payload."""
    import base64

    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    for part in payload.get("parts", []):
        text = _extract_body(part)
        if text:
            return text

    return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py::TestExtractOTP tests/unit/test_portal_submitter.py::TestWaitForOTP -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add portal_submitter/otp_handler.py tests/unit/test_portal_submitter.py
git commit -m "feat(portal): add OTP handler for email verification after portal submit"
```

---

## Task 6: Main orchestrator (submitter.py)

**Files:**
- Create: `portal_submitter/submitter.py`
- Update: `portal_submitter/__init__.py`
- Test: `tests/unit/test_portal_submitter.py` (add tests)

- [ ] **Step 1: Write tests for submitter**

Append to `tests/unit/test_portal_submitter.py`:

```python
from portal_submitter.submitter import submit_portal


class TestSubmitPortal:
    def test_login_required_returns_needs_manual(self):
        letter = MagicMock()
        letter.portal_url = "https://myaccount.google.com/data-and-privacy"
        letter.body = "SAR body"
        letter.company_name = "Google"

        result = submit_portal(letter, scan_email="user@gmail.com", browser_launcher=MagicMock())
        assert result.needs_manual is True
        assert result.success is False
        assert result.error == "login_required"

    @patch("portal_submitter.submitter.settings")
    def test_successful_submission(self, mock_settings):
        mock_settings.user_full_name = "Jane Doe"
        mock_settings.user_email = "jane@test.com"
        mock_settings.user_address_country = "United Kingdom"

        letter = MagicMock()
        letter.portal_url = "https://example.com/privacy-request"
        letter.body = "SAR body"
        letter.company_name = "Example Corp"

        # Mock browser
        mock_page = MagicMock()
        mock_page.accessibility.snapshot.return_value = {
            "role": "WebArea",
            "children": [
                {"role": "textbox", "name": "Email"},
                {"role": "button", "name": "Submit"},
            ],
        }
        mock_page.query_selector.return_value = None  # no CAPTCHA
        mock_page.screenshot.return_value = b"fake screenshot"

        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser.new_context.return_value = mock_context

        def mock_launcher():
            return MagicMock(
                __enter__=MagicMock(return_value=MagicMock(
                    chromium=MagicMock(launch=MagicMock(return_value=mock_browser))
                )),
                __exit__=MagicMock(return_value=False),
            )

        # Mock LLM
        llm_response = json.dumps({
            "fields": [{"name": "Email", "value_key": "email", "role": "textbox"}],
            "submit_button": "Submit",
        })

        result = submit_portal(
            letter,
            scan_email="user@gmail.com",
            browser_launcher=mock_launcher,
            llm_call=lambda prompt: llm_response,
        )
        assert result.success is True
        assert result.portal_status == "submitted"

    def test_empty_portal_url(self):
        letter = MagicMock()
        letter.portal_url = ""
        letter.company_name = "No Portal"

        result = submit_portal(letter, scan_email="user@gmail.com")
        assert result.success is False
        assert result.error == "no_portal_url"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py::TestSubmitPortal -v`
Expected: `ImportError`

- [ ] **Step 3: Implement submitter.py**

Create `portal_submitter/submitter.py`:

```python
"""Main orchestrator for portal-based SAR submission."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Callable

from config.settings import settings
from contact_resolver.models import PortalFieldMapping
from letter_engine.models import SARLetter
from portal_submitter.captcha_relay import poll_solution, request_solve
from portal_submitter.form_analyzer import analyze_form, build_user_data
from portal_submitter.form_filler import STEALTH_SCRIPT, fill_and_submit
from portal_submitter.models import PortalResult
from portal_submitter.platform_hints import detect_platform, otp_sender_hints

_SCREENSHOT_DIR = Path(__file__).parent.parent / "user_data" / "portal_screenshots"


def submit_portal(
    letter: SARLetter,
    scan_email: str,
    *,
    browser_launcher: Any = None,
    llm_call: Callable[[str], str] | None = None,
    cached_mapping: PortalFieldMapping | None = None,
    dry_run: bool = False,
) -> PortalResult:
    """Submit a SAR via a web portal using Playwright.

    Args:
        letter: Composed SARLetter with method="portal" and portal_url set.
        scan_email: Gmail account for OTP monitoring.
        browser_launcher: Injectable Playwright launcher (for testing).
        llm_call: Injectable LLM callable (for testing).
        cached_mapping: Previously cached field mapping from companies.json.
        dry_run: If True, analyze form but don't submit.

    Returns:
        PortalResult with success/failure details.
    """
    if not letter.portal_url:
        return PortalResult(error="no_portal_url", portal_status="failed")

    # Check platform
    platform = detect_platform(letter.portal_url)
    if platform == "login_required":
        return PortalResult(
            needs_manual=True,
            error="login_required",
            portal_status="manual",
        )

    # Build user data
    user_data = build_user_data(letter)

    if dry_run:
        return _dry_run(letter, platform, llm_call, cached_mapping)

    # Launch browser and submit
    try:
        return _browser_submit(
            letter=letter,
            platform=platform,
            user_data=user_data,
            scan_email=scan_email,
            browser_launcher=browser_launcher,
            llm_call=llm_call,
            cached_mapping=cached_mapping,
        )
    except Exception as exc:
        return PortalResult(error=f"browser_error: {exc}", portal_status="failed")


def _dry_run(
    letter: SARLetter,
    platform: str,
    llm_call: Callable | None,
    cached_mapping: PortalFieldMapping | None,
) -> PortalResult:
    """Analyze form without submitting. For preview/testing."""
    print(f"[DRY RUN] Portal: {letter.portal_url}")
    print(f"[DRY RUN] Platform: {platform}")
    if cached_mapping and cached_mapping.fields:
        print(f"[DRY RUN] Cached mapping: {len(cached_mapping.fields)} fields")
    else:
        print("[DRY RUN] No cached mapping — would call LLM for field analysis")
    return PortalResult(success=True, portal_status="dry_run")


def _browser_submit(
    letter: SARLetter,
    platform: str,
    user_data: dict[str, str],
    scan_email: str,
    browser_launcher: Any,
    llm_call: Callable | None,
    cached_mapping: PortalFieldMapping | None,
) -> PortalResult:
    """Drive Playwright to fill and submit the portal form."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return PortalResult(
            error="playwright not installed — run: pip install playwright && python -m playwright install chromium",
            portal_status="failed",
        )

    launcher = browser_launcher or sync_playwright

    with launcher() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
        )
        context.add_init_script(STEALTH_SCRIPT)
        page = context.new_page()

        try:
            # Navigate to portal
            page.goto(letter.portal_url, wait_until="networkidle", timeout=30_000)

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

            # Fill form
            fill_result = fill_and_submit(page, mapping, user_data, click_submit=False)

            # Handle CAPTCHA
            if fill_result["captcha_detected"]:
                captcha_screenshot = page.screenshot()
                domain = _domain_from_url(letter.portal_url)
                challenge = request_solve(domain, letter.portal_url, captcha_screenshot)
                solution = poll_solution(domain)

                if solution is None:
                    screenshot_path = _take_screenshot(page, letter.company_name)
                    browser.close()
                    return PortalResult(
                        needs_manual=True,
                        error="captcha_timeout",
                        screenshot_path=screenshot_path,
                        portal_status="awaiting_captcha",
                    )
                # TODO: inject CAPTCHA solution — depends on CAPTCHA type
                # For reCAPTCHA v2, this requires specific token injection

            # Submit the form
            submit_result = fill_and_submit(page, mapping, user_data, click_submit=True)

            # Take confirmation screenshot
            page.wait_for_timeout(2000)
            screenshot_path = _take_screenshot(page, letter.company_name)

            # Extract confirmation reference from page
            confirmation_ref = _extract_confirmation(page)

            # Check for OTP requirement
            sender_hints = otp_sender_hints(platform)
            if sender_hints:
                from portal_submitter.otp_handler import wait_for_otp
                otp_result = wait_for_otp(scan_email, sender_hints)
                if otp_result and otp_result["type"] == "url":
                    page.goto(otp_result["value"], wait_until="networkidle", timeout=15_000)
                elif otp_result and otp_result["type"] == "code":
                    # Try to find a code input field
                    try:
                        code_input = page.get_by_role("textbox", name="code").or_(
                            page.get_by_role("textbox", name="verification")
                        )
                        code_input.fill(otp_result["value"])
                        page.get_by_role("button", name="Submit").or_(
                            page.get_by_role("button", name="Verify")
                        ).click()
                    except Exception:
                        pass
                elif otp_result is None and sender_hints:
                    browser.close()
                    return PortalResult(
                        success=True,
                        confirmation_ref=confirmation_ref,
                        screenshot_path=screenshot_path,
                        portal_status="awaiting_verification",
                    )

            browser.close()
            return PortalResult(
                success=True,
                confirmation_ref=confirmation_ref,
                screenshot_path=screenshot_path,
                portal_status="submitted",
            )

        except Exception as exc:
            screenshot_path = _take_screenshot(page, letter.company_name)
            browser.close()
            return PortalResult(
                error=f"submission_error: {exc}",
                screenshot_path=screenshot_path,
                portal_status="failed",
            )


def _take_screenshot(page: Any, company_name: str) -> str:
    """Save a screenshot and return the path."""
    _SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = company_name.replace(" ", "_").replace("/", "_")[:50]
    path = _SCREENSHOT_DIR / f"{safe_name}_{date.today().isoformat()}.png"
    try:
        page.screenshot(path=str(path))
        return str(path)
    except Exception:
        return ""


def _extract_confirmation(page: Any) -> str:
    """Try to extract a confirmation/reference number from the page text."""
    import re
    try:
        text = page.text_content("body") or ""
        # Common patterns: TICKET-123456, REQ-abc123, #12345, Case: 12345
        patterns = [
            re.compile(r"(?:TICKET|REQ|CASE|REF)[- ]?[\w-]{4,}", re.I),
            re.compile(r"(?:reference|confirmation|ticket|case)\s*(?:number|#|:)\s*([\w-]{4,})", re.I),
        ]
        for pat in patterns:
            m = pat.search(text)
            if m:
                return m.group(0)
    except Exception:
        pass
    return ""


def _domain_from_url(url: str) -> str:
    from urllib.parse import urlparse
    try:
        return urlparse(url).hostname or "unknown"
    except Exception:
        return "unknown"
```

- [ ] **Step 4: Update __init__.py to export submit_portal**

Update `portal_submitter/__init__.py`:

```python
"""Portal submission automation for DSAR portals."""

from portal_submitter.submitter import submit_portal

__all__ = ["submit_portal"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py -v`
Expected: All tests PASS

- [ ] **Step 6: Run all tests for regression**

Run: `.venv/bin/pytest tests/unit/ -q`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add portal_submitter/submitter.py portal_submitter/__init__.py tests/unit/test_portal_submitter.py
git commit -m "feat(portal): add main orchestrator wiring form analysis, fill, CAPTCHA, and OTP"
```

---

## Task 7: Integration — sender.py and tracker.py

**Files:**
- Modify: `letter_engine/sender.py:58-89`
- Modify: `letter_engine/tracker.py:13-26`
- Test: `tests/unit/test_portal_submitter.py` (add integration test)

- [ ] **Step 1: Write tests for portal tracking**

Append to `tests/unit/test_portal_submitter.py`:

```python
from letter_engine import tracker
from letter_engine.models import SARLetter


class TestPortalTracking:
    def test_record_sent_includes_portal_fields(self, tmp_path):
        path = tmp_path / "sent.json"
        letter = SARLetter(
            company_name="Glassdoor",
            method="portal",
            to_email="",
            subject="",
            body="SAR body",
            portal_url="https://help.glassdoor.com/s/privacyrequest",
            postal_address="",
        )
        tracker.record_sent(letter, path=path, portal_status="submitted", portal_confirmation_ref="TICKET-123")

        log = tracker.get_log(path=path)
        assert len(log) == 1
        assert log[0]["portal_url"] == "https://help.glassdoor.com/s/privacyrequest"
        assert log[0]["portal_status"] == "submitted"
        assert log[0]["portal_confirmation_ref"] == "TICKET-123"
        assert log[0]["method"] == "portal"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py::TestPortalTracking -v`
Expected: FAIL — `record_sent()` doesn't accept `portal_status` parameter

- [ ] **Step 3: Update tracker.py to accept portal fields**

In `letter_engine/tracker.py`, modify `record_sent()`:

```python
def record_sent(
    letter: SARLetter,
    *,
    path: Path = _TRACKER_PATH,
    portal_status: str = "",
    portal_confirmation_ref: str = "",
    portal_screenshot: str = "",
) -> None:
    """Append a sent letter entry to the tracker file."""
    log = get_log(path=path)
    entry = {
        "sent_at": datetime.now().isoformat(timespec="seconds"),
        "company_name": letter.company_name,
        "method": letter.method,
        "to_email": letter.to_email,
        "subject": letter.subject,
        "gmail_message_id": letter.gmail_message_id,
        "gmail_thread_id": letter.gmail_thread_id,
    }
    # Portal-specific fields (only set for method="portal")
    if letter.method == "portal":
        entry["portal_url"] = letter.portal_url
        entry["portal_status"] = portal_status
        entry["portal_confirmation_ref"] = portal_confirmation_ref
        if portal_screenshot:
            entry["portal_screenshot"] = portal_screenshot
    log.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log, indent=2))
```

- [ ] **Step 4: Update sender.py portal path**

In `letter_engine/sender.py`, modify `send_letter()` to delegate to portal_submitter when `method == "portal"`:

```python
def send_letter(
    letter: SARLetter,
    scan_email: str,
    *,
    record: bool = True,
) -> tuple[bool, str, str]:
    """Send *letter* without an interactive Y/N prompt."""
    if letter.method == "email":
        msg_id, thread_id = _dispatch_email(letter, scan_email)
        letter.gmail_message_id = msg_id
        letter.gmail_thread_id = thread_id
        if msg_id and record:
            tracker.record_sent(letter)
        return bool(msg_id), msg_id, thread_id

    if letter.method == "portal":
        return _dispatch_portal(letter, scan_email, record=record)

    # postal — record as sent; user handles submission manually
    if record:
        tracker.record_sent(letter)
    return True, "", ""


def _dispatch_portal(
    letter: SARLetter,
    scan_email: str,
    *,
    record: bool = True,
) -> tuple[bool, str, str]:
    """Attempt automated portal submission, fall back to manual instructions."""
    try:
        from portal_submitter import submit_portal
        result = submit_portal(letter, scan_email)

        if result.needs_manual:
            print(f"\n[PORTAL] {letter.company_name}: manual submission required ({result.error})")
            print(f"  URL: {letter.portal_url}")
            print(f"  Copy the letter body above to paste into the portal form.")
            if record:
                tracker.record_sent(letter, portal_status="manual")
            return True, "", ""

        if result.success:
            print(f"\n[PORTAL] {letter.company_name}: submitted successfully")
            if result.confirmation_ref:
                print(f"  Confirmation: {result.confirmation_ref}")
            if record:
                tracker.record_sent(
                    letter,
                    portal_status=result.portal_status,
                    portal_confirmation_ref=result.confirmation_ref,
                    portal_screenshot=result.screenshot_path,
                )
            return True, "", ""

        # Submission failed — fall back to manual
        print(f"\n[PORTAL] {letter.company_name}: automation failed ({result.error})")
        print(f"  Please submit manually at: {letter.portal_url}")
        if record:
            tracker.record_sent(letter, portal_status="failed")
        return True, "", ""

    except ImportError:
        # portal_submitter not available — fall back to manual
        print(f"\nPlease submit your SAR manually at:\n  {letter.portal_url}")
        print("\nCopy the letter body above to paste into the portal form.")
        if record:
            tracker.record_sent(letter, portal_status="manual")
        return True, "", ""
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_portal_submitter.py::TestPortalTracking -v`
Expected: PASS

- [ ] **Step 6: Run existing letter_engine tests for regression**

Run: `.venv/bin/pytest tests/unit/test_letter_engine.py -v`
Expected: All pass (existing tests use `method="email"`, unaffected by portal changes)

- [ ] **Step 7: Run all tests**

Run: `.venv/bin/pytest tests/unit/ -q`
Expected: All pass

- [ ] **Step 8: Commit**

```bash
git add letter_engine/sender.py letter_engine/tracker.py tests/unit/test_portal_submitter.py
git commit -m "feat(portal): integrate portal submission into sender.py and tracker.py"
```

---

## Task 8: Dashboard routes — portal submission and CAPTCHA

**Files:**
- Modify: `dashboard/app.py`
- Create: `dashboard/templates/captcha.html`

- [ ] **Step 1: Add portal submission route to dashboard/app.py**

Add after the existing imports at the top of `dashboard/app.py`:

```python
import threading
```

Add the following routes (place them after the existing `/transfers/*` routes):

```python
# ---------------------------------------------------------------------------
# Portal submission routes
# ---------------------------------------------------------------------------

_portal_tasks: dict[str, dict] = {}  # domain -> {"status": ..., "result": ...}


@app.route("/portal/submit/<domain>", methods=["POST"])
def portal_submit(domain: str):
    """Start a portal submission as a background task."""
    account = request.args.get("account", "")

    if domain in _portal_tasks and _portal_tasks[domain].get("status") == "running":
        return jsonify({"error": "submission already in progress"}), 409

    # Find the letter for this domain
    from contact_resolver.resolver import ContactResolver
    from letter_engine.composer import compose

    resolver = ContactResolver()
    record = resolver.resolve(domain, domain, verbose=False)
    if not record or record.contact.preferred_method != "portal":
        return jsonify({"error": "not a portal company"}), 400

    letter = compose(record)

    _portal_tasks[domain] = {"status": "running", "result": None}

    def _run():
        try:
            from portal_submitter import submit_portal
            result = submit_portal(letter, scan_email=account)
            _portal_tasks[domain] = {"status": "done", "result": result}

            # Record to tracker
            if result.success or result.needs_manual:
                from letter_engine import tracker
                tracker.record_sent(
                    letter,
                    portal_status=result.portal_status,
                    portal_confirmation_ref=result.confirmation_ref,
                    portal_screenshot=result.screenshot_path,
                )
        except Exception as exc:
            _portal_tasks[domain] = {"status": "error", "result": str(exc)}

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/portal/status/<domain>")
def portal_status(domain: str):
    """Poll portal submission progress."""
    task = _portal_tasks.get(domain)
    if not task:
        return jsonify({"status": "not_found"})

    if task["status"] == "running":
        return jsonify({"status": "running"})

    result = task["result"]
    if isinstance(result, str):
        return jsonify({"status": "error", "error": result})

    return jsonify({
        "status": "done",
        "success": result.success,
        "needs_manual": result.needs_manual,
        "portal_status": result.portal_status,
        "confirmation_ref": result.confirmation_ref,
        "error": result.error,
    })


@app.route("/captcha/<domain>")
def captcha_show(domain: str):
    """Show a pending CAPTCHA for the user to solve."""
    captcha_dir = Path(__file__).parent.parent / "user_data" / "captcha_pending"
    screenshot = captcha_dir / f"{domain}.png"
    challenge_file = captcha_dir / f"{domain}.json"

    if not screenshot.exists() or not challenge_file.exists():
        flash("No pending CAPTCHA for this domain.", "warning")
        return redirect(url_for("index"))

    import base64
    img_b64 = base64.b64encode(screenshot.read_bytes()).decode()
    challenge = json.loads(challenge_file.read_text())

    return render_template(
        "captcha.html",
        domain=domain,
        captcha_image=img_b64,
        portal_url=challenge.get("portal_url", ""),
    )


@app.route("/captcha/<domain>", methods=["POST"])
def captcha_solve(domain: str):
    """Submit a CAPTCHA solution."""
    solution = request.form.get("solution", "").strip()
    if not solution:
        flash("Please enter the CAPTCHA solution.", "warning")
        return redirect(url_for("captcha_show", domain=domain))

    captcha_dir = Path(__file__).parent.parent / "user_data" / "captcha_pending"
    challenge_file = captcha_dir / f"{domain}.json"

    if challenge_file.exists():
        data = json.loads(challenge_file.read_text())
        data["status"] = "solved"
        data["solution"] = solution
        challenge_file.write_text(json.dumps(data, indent=2))
        flash("CAPTCHA solution submitted. Portal submission continuing...", "success")
    else:
        flash("CAPTCHA challenge not found or already expired.", "warning")

    return redirect(url_for("index"))
```

- [ ] **Step 2: Create captcha.html template**

Create `dashboard/templates/captcha.html`:

```html
{% extends "base.html" %}
{% block title %}CAPTCHA — {{ domain }}{% endblock %}
{% block content %}
<div class="container mt-4">
  <h3>CAPTCHA Required — {{ domain }}</h3>
  <p class="text-muted">
    The portal at <a href="{{ portal_url }}" target="_blank">{{ portal_url }}</a>
    requires CAPTCHA verification. Please solve it below.
  </p>

  <div class="card mb-3">
    <div class="card-body text-center">
      <img src="data:image/png;base64,{{ captcha_image }}"
           alt="CAPTCHA challenge" class="img-fluid border rounded mb-3"
           style="max-width: 500px;">
    </div>
  </div>

  <form method="POST" action="/captcha/{{ domain }}">
    <div class="mb-3">
      <label for="solution" class="form-label">CAPTCHA Solution</label>
      <input type="text" class="form-control" id="solution" name="solution"
             placeholder="Type what you see" autofocus required style="max-width: 400px;">
    </div>
    <button type="submit" class="btn btn-primary">Submit Solution</button>
    <a href="/" class="btn btn-outline-secondary ms-2">Cancel</a>
  </form>
</div>
{% endblock %}
```

- [ ] **Step 3: Run all tests for regression**

Run: `.venv/bin/pytest tests/unit/ -q`
Expected: All pass

- [ ] **Step 4: Commit**

```bash
git add dashboard/app.py dashboard/templates/captcha.html
git commit -m "feat(portal): add dashboard routes for portal submission and CAPTCHA relay"
```

---

## Task 9: run.py — portal-only flag

**Files:**
- Modify: `run.py:109-131`

- [ ] **Step 1: Add --portal-only flag to run.py**

Add to `_parse_args()`:

```python
    parser.add_argument(
        "--portal-only", action="store_true",
        help="Only process portal-method companies",
    )
```

In the Step 5 loop (`for letter in letters:`), filter by method when `--portal-only` is set:

```python
    # ── Step 5: Preview and send ─────────────────────────────────────────────
    if args.portal_only:
        letters = [l for l in letters if l.method == "portal"]
        if not letters:
            print("No portal companies found.")
            cost_tracker.print_cost_summary()
            return
        print(f"Portal-only mode: {len(letters)} letter(s).\n")

    sent = skipped = 0
    for letter in letters:
        result = preview_and_send(letter, dry_run=args.dry_run, scan_email=email)
        if result:
            sent += 1
        else:
            skipped += 1
```

- [ ] **Step 2: Run existing run.py tests for regression**

Run: `.venv/bin/pytest tests/unit/test_run.py -v`
Expected: All pass

- [ ] **Step 3: Commit**

```bash
git add run.py
git commit -m "feat(portal): add --portal-only flag to run.py"
```

---

## Task 10: Manual test script

**Files:**
- Create: `test_portal.py`

- [ ] **Step 1: Create test_portal.py**

```python
"""Manual test script for portal submission.

Usage:
    python test_portal.py --list-portals              # show portal companies
    python test_portal.py --domain glassdoor.com --dry-run   # analyze form only
    python test_portal.py --domain glassdoor.com              # full submission
"""

import argparse
import json
import sys
from pathlib import Path

from contact_resolver.resolver import ContactResolver
from letter_engine.composer import compose
from portal_submitter.platform_hints import detect_platform


def main() -> None:
    args = _parse_args()

    if args.list_portals:
        _list_portals()
        return

    if not args.domain:
        print("Provide --domain or --list-portals")
        sys.exit(1)

    resolver = ContactResolver()
    record = resolver.resolve(args.domain, args.domain, verbose=True)
    if not record:
        print(f"Could not resolve {args.domain}")
        sys.exit(1)

    print(f"\nCompany: {record.company_name}")
    print(f"Method: {record.contact.preferred_method}")
    print(f"Portal URL: {record.contact.gdpr_portal_url}")
    print(f"Platform: {detect_platform(record.contact.gdpr_portal_url)}")

    if record.contact.preferred_method != "portal":
        print(f"\nNote: {args.domain} uses method={record.contact.preferred_method}, not portal.")
        if not args.force:
            print("Use --force to test anyway.")
            return

    letter = compose(record)

    if args.dry_run:
        from portal_submitter import submit_portal
        result = submit_portal(letter, scan_email=args.gmail or "", dry_run=True)
        print(f"\nDry run result: {result}")
        return

    from portal_submitter import submit_portal
    print(f"\nSubmitting to {record.contact.gdpr_portal_url}...")
    result = submit_portal(letter, scan_email=args.gmail or "")
    print(f"\nResult:")
    print(f"  Success: {result.success}")
    print(f"  Status: {result.portal_status}")
    print(f"  Confirmation: {result.confirmation_ref}")
    print(f"  Screenshot: {result.screenshot_path}")
    if result.error:
        print(f"  Error: {result.error}")
    if result.needs_manual:
        print(f"  Manual submission required at: {letter.portal_url}")


def _list_portals() -> None:
    db_path = Path(__file__).parent / "data" / "companies.json"
    if not db_path.exists():
        print("data/companies.json not found.")
        return
    db = json.loads(db_path.read_text())
    companies = db.get("companies", {})

    portal_companies = [
        (domain, rec)
        for domain, rec in companies.items()
        if rec.get("contact", {}).get("preferred_method") == "portal"
        or rec.get("contact", {}).get("gdpr_portal_url")
    ]

    if not portal_companies:
        print("No portal companies found.")
        return

    print(f"Portal companies ({len(portal_companies)}):\n")
    for domain, rec in sorted(portal_companies):
        contact = rec.get("contact", {})
        method = contact.get("preferred_method", "?")
        url = contact.get("gdpr_portal_url", "")
        platform = detect_platform(url) if url else "?"
        print(f"  {domain:<30} method={method:<8} platform={platform:<15} {url}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test portal submission")
    parser.add_argument("--domain", help="Domain to test")
    parser.add_argument("--dry-run", action="store_true", help="Analyze form only")
    parser.add_argument("--list-portals", action="store_true", help="List portal companies")
    parser.add_argument("--gmail", help="Gmail account for OTP monitoring")
    parser.add_argument("--force", action="store_true", help="Force portal test even if method != portal")
    return parser.parse_args()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it runs**

Run: `.venv/bin/python test_portal.py --list-portals`
Expected: Lists portal companies from `data/companies.json` (at least Google and Glassdoor)

- [ ] **Step 3: Commit**

```bash
git add test_portal.py
git commit -m "feat(portal): add manual test script for portal submission"
```

---

## Task 11: Final verification

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/pytest tests/unit/ -v`
Expected: All tests pass, including all new portal_submitter tests

- [ ] **Step 2: Verify test_portal.py list-portals works**

Run: `.venv/bin/python test_portal.py --list-portals`
Expected: Shows portal companies with platform detection

- [ ] **Step 3: Verify test_portal.py dry-run works**

Run: `.venv/bin/python test_portal.py --domain glassdoor.com --dry-run`
Expected: Shows portal analysis without submitting

- [ ] **Step 4: Verify dashboard starts**

Run: `.venv/bin/python dashboard/app.py &` then check `http://localhost:5001`
Expected: Dashboard loads, portal companies show submit/open buttons

- [ ] **Step 5: Commit any final fixes**

If any fixes were needed, commit them now.
