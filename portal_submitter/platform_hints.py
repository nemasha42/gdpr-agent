"""Detect DSAR portal platform from URL patterns and provide OTP sender hints."""

import re
from urllib.parse import urlparse

# Domains where the portal requires account login — not automatable
_LOGIN_REQUIRED_DOMAINS: set[str] = {
    "google.com",
    "apple.com",
    "meta.com",
    "amazon.com",
    "facebook.com",
    "twitter.com",
    "x.com",
}

# URL pattern → platform
_PLATFORM_RULES: list[tuple[str, re.Pattern]] = [
    ("onetrust", re.compile(r"onetrust\.com|privacyportal", re.I)),
    ("trustarc", re.compile(r"trustarc\.com|submit-irm", re.I)),
    ("ketch", re.compile(r"ketch\.com|\.ketch\.", re.I)),
]

# HTML signatures that identify Ketch portals on branded domains
_KETCH_HTML_SIGNATURES = [
    "ketch-tag",
    "ketch.js",
    "window.semaphore",
    "cdn.ketch.com",
]

# OTP sender email patterns per platform
_OTP_SENDERS: dict[str, list[str]] = {
    "onetrust": ["noreply@onetrust.com", "privacyportal"],
    "trustarc": ["privacy@trustarc.com", "noreply@trustarc.com"],
    "salesforce": ["noreply@salesforce.com"],
    "ketch": ["noreply@ketch.com"],
}

# Domains that portal platforms use to send replies (verification emails,
# status updates, results).  The fetcher searches these when a company has
# portal_status set, catching out-of-domain portal replies.
_PORTAL_REPLY_DOMAINS: dict[str, list[str]] = {
    "onetrust": ["onetrust.com"],
    "trustarc": ["trustarc.com"],
    "ketch": ["ketch.com", "m.ketch.com"],
    "salesforce": ["salesforce.com"],
}


def detect_platform(url: str, html: str = "") -> str:
    """Classify a portal URL into a known platform or 'unknown'.

    Returns one of: "onetrust", "trustarc", "salesforce", "ketch", "login_required", "unknown".

    The optional ``html`` parameter accepts page source. When a URL matches no
    known pattern, HTML-based signature matching is attempted (e.g. to detect
    Ketch on branded domains that don't contain "ketch" in the URL).
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

    # Check platform rules against hostname only (to avoid false matches on query strings/paths)
    for platform, pattern in _PLATFORM_RULES:
        if pattern.search(host):
            return platform

    # Salesforce Experience Cloud: /s/ path prefix.
    # Note: This heuristic has potential false positives for non-Salesforce URLs that
    # use /s/ as a path segment (e.g. /support/, /search/). This is a best-effort
    # detection when no other platform indicators are present.
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


def otp_sender_hints(platform: str) -> list[str]:
    """Return email sender patterns to watch for OTP/verification emails."""
    return list(_OTP_SENDERS.get(platform, []))


def portal_reply_domains(platform: str) -> list[str]:
    """Return email sender domains used by the portal platform for replies.

    These domains are searched by the fetcher when a company uses a portal
    platform (Ketch, OneTrust, TrustArc, Salesforce) that sends replies
    from the platform's own domain rather than the company's domain.
    """
    return list(_PORTAL_REPLY_DOMAINS.get(platform, []))


def all_portal_reply_domains() -> list[str]:
    """Return all known portal platform reply domains (deduplicated).

    Used as a fallback when a company has portal_status set but the
    platform cannot be determined from the portal URL alone (e.g.
    Zendesk uses Ketch, but the portal URL is on zendesk.com).
    """
    seen: set[str] = set()
    result: list[str] = []
    for domains in _PORTAL_REPLY_DOMAINS.values():
        for d in domains:
            if d not in seen:
                seen.add(d)
                result.append(d)
    return result
