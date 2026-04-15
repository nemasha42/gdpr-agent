"""Main orchestrator for portal-based SAR submission."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any, Callable

from config.settings import settings
from contact_resolver.models import PortalFieldMapping
from letter_engine.models import SARLetter
from portal_submitter.captcha_relay import poll_solution, request_solve
from portal_submitter.form_analyzer import analyze_form, build_user_data
from portal_submitter.form_filler import STEALTH_SCRIPT, detect_captcha_type, fill_and_submit
from portal_submitter.models import PortalResult
from portal_submitter.platform_hints import detect_platform, otp_sender_hints
from portal_submitter.portal_navigator import navigate_to_form, page_has_form
from reply_monitor.classifier import _is_junk_url

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

    if _is_junk_url(letter.portal_url):
        return PortalResult(
            error="junk_portal_url",
            needs_manual=True,
            portal_status="failed",
        )

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
    if browser_launcher:
        launcher = browser_launcher
    else:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return PortalResult(
                error="playwright not installed — run: pip install playwright && python -m playwright install chromium",
                portal_status="failed",
            )
        launcher = sync_playwright

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

            # Re-detect platform with HTML (for branded domains like zendesk.es → ketch)
            try:
                html_content = page.content()
                detected = detect_platform(letter.portal_url, html=html_content)
                if detected != "unknown":
                    platform = detected
            except Exception:
                pass

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

            # Fill form (without submitting yet)
            fill_result = fill_and_submit(page, mapping, user_data, click_submit=False)

            # Detect CAPTCHA type
            captcha_type = detect_captcha_type(page)

            if captcha_type == "interactive":
                # Interactive CAPTCHA — relay to user for solving
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

            if captcha_type == "invisible_v3":
                # Invisible reCAPTCHA v3 — try submitting (may pass with stealth),
                # but report pre-filled + needs manual if it fails
                submit_result = fill_and_submit(page, mapping, user_data, click_submit=True)
                page.wait_for_timeout(3000)

                # Check if submission was blocked by reCAPTCHA
                page_text = page.inner_text("body")
                if "bot" in page_text.lower() and "recaptcha" in page_text.lower():
                    screenshot_path = _take_screenshot(page, letter.company_name)
                    browser.close()
                    return PortalResult(
                        needs_manual=True,
                        error="recaptcha_v3_blocked",
                        screenshot_path=screenshot_path,
                        portal_status="manual",
                    )
            else:
                # No CAPTCHA or interactive CAPTCHA already solved — submit
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
