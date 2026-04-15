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
_NAVIGATION_HINTS: dict[str, list[str]] = {
    "ketch": [
        r"(?:your\s+)?privacy\s+request",
        r"access\s+(?:your\s+)?data",
    ],
}


def page_has_form(page: Any) -> bool:
    """Check if the current page has visible form fields (not hidden cookie/tracking inputs)."""
    for el in page.locator("input:not([type=hidden]), textarea, select").all():
        try:
            if el.is_visible():
                return True
        except Exception:
            continue
    return False


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
    # Dismiss cookie banners that may overlay the portal
    _dismiss_cookie_banner(page)

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


def _dismiss_cookie_banner(page: Any) -> None:
    """Try to dismiss cookie consent overlays that block navigation clicks."""
    for text in ("Reject all", "Rechazar todas", "Decline", "Deny"):
        try:
            btn = page.locator(f"button:has-text('{text}')").first
            if btn.is_visible(timeout=1000):
                btn.click(force=True)
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


def _click_by_pattern(page: Any, pattern: str) -> bool:
    """Find and click a link, button, or tab matching a regex pattern. Returns True if clicked."""
    compiled = re.compile(pattern, re.I)
    for role in ("tab", "link", "button"):
        locator = page.get_by_role(role, name=compiled)
        if locator.count() > 0:
            try:
                locator.first.click(timeout=5000)
            except Exception:
                # Overlay may intercept — try force click
                try:
                    locator.first.click(force=True)
                except Exception:
                    continue
            return True
    return False


def _click_by_name(page: Any, name: str) -> bool:
    """Find and click an element by its exact accessible name. Returns True if clicked."""
    for role in ("tab", "link", "button"):
        locator = page.get_by_role(role, name=name)
        if locator.count() > 0:
            try:
                locator.first.click(timeout=5000)
            except Exception:
                try:
                    locator.first.click(force=True)
                except Exception:
                    continue
            return True
    return False


def _wait_for_load(page: Any) -> None:
    """Wait for navigation/content to settle."""
    try:
        page.wait_for_load_state("networkidle", timeout=_NAV_TIMEOUT)
    except Exception:
        pass  # timeout is non-fatal


def _llm_suggest_click(client: Any, page: Any) -> str:
    """Ask Claude Haiku which element to click to reach the GDPR form.

    Returns the accessible name of the element, or empty string on failure.
    """
    try:
        snapshot_text = page.locator("body").aria_snapshot()
    except Exception:
        return ""

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
