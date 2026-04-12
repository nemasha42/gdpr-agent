"""Verify and classify URLs extracted from GDPR reply emails.

Classifies URLs as: gdpr_portal, help_center, login_required, dead_link,
survey, or unknown. Uses lightweight HTTP fetch + HTML inspection.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

import requests

from portal_submitter.platform_hints import detect_platform

_VERIFY_TTL = timedelta(days=7)
_TIMEOUT = 10  # seconds


class CLASSIFICATION:
    GDPR_PORTAL = "gdpr_portal"
    HELP_CENTER = "help_center"
    LOGIN_REQUIRED = "login_required"
    DEAD_LINK = "dead_link"
    SURVEY = "survey"
    UNKNOWN = "unknown"


# URL path patterns for known non-portal pages
_RE_HELP_CENTER = re.compile(
    r"/hc/[a-z-]+/(requests|articles)/\d+"
    r"|/help/(article|doc)s?/"
    r"|/support/solutions/",
    re.I,
)

_RE_SURVEY = re.compile(
    r"/survey[_-]?responses?/"
    r"|/satisfaction/"
    r"|/feedback/",
    re.I,
)

# HTML content signals
_RE_SURVEY_CONTENT = re.compile(
    r"rate.{0,30}(support|service|experience)"
    r"|how (did we do|was your experience)"
    r"|satisfaction survey"
    r"|please.{0,20}(rate|review).{0,20}(support|service)",
    re.I,
)

_RE_FORM_ELEMENT = re.compile(
    r"<form[\s>]"
    r"|<input[\s>]"
    r"|<textarea[\s>]"
    r"|<select[\s>]",
    re.I,
)

_RE_SUBMIT_BUTTON = re.compile(
    r"<button[^>]*>.*?(submit|send|request).*?</button>"
    r"|type=['\"]submit['\"]",
    re.I | re.S,
)

_RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def verify(url: str) -> dict:
    """Classify a URL by fetching and inspecting its content.

    Returns dict with keys: url, classification, checked_at, error, page_title.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    if not url:
        return _result(url, CLASSIFICATION.DEAD_LINK, now, error="empty URL")

    # Fast path: login-required domains (no HTTP needed)
    platform = detect_platform(url)
    if platform == "login_required":
        return _result(url, CLASSIFICATION.LOGIN_REQUIRED, now)

    # Fast path: known platform portals (OneTrust, TrustArc, Ketch)
    if platform in ("onetrust", "trustarc"):
        return _result(url, CLASSIFICATION.GDPR_PORTAL, now)
    if platform == "ketch":
        return _result(url, CLASSIFICATION.GDPR_PORTAL, now)

    # URL path heuristics (before HTTP fetch)
    path = urlparse(url).path or ""
    if _RE_SURVEY.search(path):
        return _result(url, CLASSIFICATION.SURVEY, now)
    if _RE_HELP_CENTER.search(path):
        return _result(url, CLASSIFICATION.HELP_CENTER, now)

    # HTTP fetch
    try:
        resp = requests.get(url, timeout=_TIMEOUT, allow_redirects=True,
                            headers={"User-Agent": "Mozilla/5.0 (compatible; GDPR-Agent/1.0)"})
    except requests.Timeout:
        return _result(url, CLASSIFICATION.DEAD_LINK, now, error="timeout")
    except requests.ConnectionError as e:
        return _result(url, CLASSIFICATION.DEAD_LINK, now, error=f"connection error: {e}")
    except requests.RequestException as e:
        return _result(url, CLASSIFICATION.DEAD_LINK, now, error=str(e))

    if resp.status_code >= 400:
        return _result(url, CLASSIFICATION.DEAD_LINK, now, error=f"HTTP {resp.status_code}")

    content_type = resp.headers.get("content-type", "")
    if "text/html" not in content_type:
        return _result(url, CLASSIFICATION.UNKNOWN, now)

    html = resp.text
    title = _extract_title(html)

    # Check for survey content
    if _RE_SURVEY_CONTENT.search(html):
        return _result(url, CLASSIFICATION.SURVEY, now, page_title=title)

    # Check for help center (after redirect — may have landed on a different path)
    final_path = urlparse(resp.url).path or ""
    if _RE_HELP_CENTER.search(final_path):
        return _result(url, CLASSIFICATION.HELP_CENTER, now, page_title=title)

    # Check for Ketch platform via HTML signatures (form is behind navigation steps)
    html_platform = detect_platform(url, html=html)
    if html_platform == "ketch":
        return _result(url, CLASSIFICATION.GDPR_PORTAL, now, page_title=title)

    # Check for GDPR portal: needs form elements + submit button
    has_form = bool(_RE_FORM_ELEMENT.search(html))
    has_submit = bool(_RE_SUBMIT_BUTTON.search(html))

    if has_form and has_submit:
        return _result(url, CLASSIFICATION.GDPR_PORTAL, now, page_title=title)

    return _result(url, CLASSIFICATION.UNKNOWN, now, page_title=title)


def verify_if_needed(
    url: str,
    *,
    existing: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Return existing verification if fresh, otherwise re-verify."""
    if existing and existing.get("checked_at"):
        if now is None:
            now = datetime.now(timezone.utc)
        try:
            checked = datetime.fromisoformat(existing["checked_at"].replace("Z", "+00:00"))
            if now - checked < _VERIFY_TTL:
                return existing
        except (ValueError, TypeError):
            pass
    return verify(url)


def _result(
    url: str,
    classification: str,
    checked_at: str,
    *,
    error: str | None = None,
    page_title: str = "",
) -> dict:
    return {
        "url": url,
        "classification": classification,
        "checked_at": checked_at,
        "error": error,
        "page_title": page_title,
    }


def _extract_title(html: str) -> str:
    m = _RE_TITLE.search(html)
    if m:
        return m.group(1).strip()[:200]
    return ""
