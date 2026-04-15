"""Playwright-based form filling and CAPTCHA detection."""

from typing import Any

from contact_resolver.models import PortalFieldMapping

# Selectors that indicate CAPTCHA presence (interactive or invisible)
_CAPTCHA_SELECTORS = [
    'iframe[src*="recaptcha"]',
    'iframe[src*="hcaptcha"]',
    '.g-recaptcha',
    '#captcha',
    '[data-sitekey]',
    'iframe[src*="challenges.cloudflare.com"]',
    '.grecaptcha-badge',  # invisible reCAPTCHA v3 badge
]

# Stealth script to reduce automation detection (same as link_downloader.py)
STEALTH_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    window.chrome = { runtime: {} };
    Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en'] });
"""


def detect_captcha(page: Any) -> bool:
    """Check if the page contains a CAPTCHA element (interactive or invisible)."""
    for selector in _CAPTCHA_SELECTORS:
        try:
            if page.query_selector(selector):
                return True
        except Exception:
            continue
    return False


def detect_captcha_type(page: Any) -> str:
    """Detect the type of CAPTCHA: 'interactive', 'invisible_v3', or 'none'.

    Invisible reCAPTCHA v3 has a badge but no visible iframe/checkbox.
    Interactive CAPTCHAs have a visible iframe or checkbox.
    """
    has_badge = bool(page.query_selector(".grecaptcha-badge"))
    has_visible_iframe = False
    for iframe in page.query_selector_all('iframe[src*="recaptcha"], iframe[src*="hcaptcha"]'):
        try:
            if iframe.is_visible():
                has_visible_iframe = True
                break
        except Exception:
            continue
    has_interactive = bool(page.query_selector(".g-recaptcha, [data-sitekey]"))

    if has_visible_iframe or has_interactive:
        return "interactive"
    if has_badge:
        return "invisible_v3"
    for selector in _CAPTCHA_SELECTORS:
        try:
            if page.query_selector(selector):
                return "interactive"
        except Exception:
            continue
    return "none"


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
        except Exception as exc:
            print(f"[form_filler] Could not fill '{field.name}': {exc}", flush=True)
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
            except Exception as exc:
                print(f"[form_filler] Submit fallback failed: {exc}", flush=True)

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
