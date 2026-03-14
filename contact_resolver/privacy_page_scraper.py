"""Scrape company privacy pages to extract GDPR contact details."""

import re
from datetime import date
from typing import Any

import requests

from contact_resolver.models import CompanyRecord, Contact, Flags, RequestNotes

# Privacy page URL candidates — tried in order, first 200 wins
_PRIVACY_URL_TEMPLATES: tuple[str, ...] = (
    "https://{domain}/privacy-policy",
    "https://{domain}/privacy",
    "https://{domain}/legal/privacy",
    "https://{domain}/gdpr",
)

# Email addresses whose local part indicates a GDPR/privacy contact
_PRIVACY_EMAIL_RE = re.compile(
    r"\b(?:privacy|dpo|gdpr|legal|dataprotection|data-protection|dataprivacy)"
    r"@[\w.-]+\.\w+",
    re.IGNORECASE,
)

# Portal/webform URLs with GDPR-related path segments
_PORTAL_URL_RE = re.compile(
    r"https?://[\w./%~-]+"
    r"(?:privacy-request|dsar|data-request|gdpr-request|subject-access)"
    r"[\w./%~-]*",
    re.IGNORECASE,
)

# Labels that suggest we're near GDPR contact information
_GDPR_LABEL_RE = re.compile(
    r"(?:data\s+protection\s+officer"
    r"|(?<!\w)DPO(?!\w)"
    r"|privacy\s+team"
    r"|(?<!\w)GDPR(?!\w)"
    r"|data\s+controller"
    r"|article\s+27\s+representative)",
    re.IGNORECASE,
)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_TIMEOUT = 10


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scrape_privacy_page(
    domain: str,
    company_name: str,
    *,
    http_get: Any = None,
    verbose: bool = False,
) -> CompanyRecord | None:
    """Try to find GDPR contact details by scraping *domain*'s privacy page.

    Tries up to four well-known privacy page URLs in order and stops at the
    first HTTP 200 response.  Scans the page text for GDPR-related emails and
    portal/webform links.

    Args:
        domain: Registrable domain, e.g. ``"example.com"``.
        company_name: Human-readable name used to populate the returned record.
        http_get: Injectable HTTP GET callable; defaults to ``requests.get``.
        verbose: If ``True``, print a ``[PRIVACY PAGE]`` status line for each
                 URL attempted.

    Returns:
        A :class:`CompanyRecord` with ``source_confidence="medium"`` when
        useful contacts were found, or ``None`` otherwise.
    """
    get = http_get or requests.get

    for template in _PRIVACY_URL_TEMPLATES:
        url = template.format(domain=domain)
        if verbose:
            print(f"[PRIVACY PAGE] {domain} — trying {url}...", end=" ", flush=True)

        try:
            resp = get(url, timeout=_TIMEOUT)
        except Exception:
            if verbose:
                print("connection error")
            continue

        if resp.status_code != 200:
            if verbose:
                print(resp.status_code)
            continue

        text = _strip_html(resp.text)
        emails = _extract_emails(text)
        portal = _extract_portal_url(text)

        if not emails and not portal:
            if verbose:
                print("no contacts found")
            continue

        if verbose:
            if emails:
                print(f"found email: {emails[0]}")
            else:
                print(f"found portal: {portal}")

        return _build_record(company_name, emails, portal)

    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _strip_html(html: str) -> str:
    """Replace HTML tags with spaces to preserve word boundaries."""
    return _HTML_TAG_RE.sub(" ", html)


def _extract_emails(text: str) -> list[str]:
    """Return de-duplicated GDPR/privacy emails found in *text*."""
    seen: set[str] = set()
    results: list[str] = []
    for email in _PRIVACY_EMAIL_RE.findall(text):
        lower = email.lower()
        if lower not in seen:
            seen.add(lower)
            results.append(email)
    return results


def _extract_portal_url(text: str) -> str:
    """Return the first GDPR portal/webform URL found in *text*, or empty string."""
    match = _PORTAL_URL_RE.search(text)
    return match.group() if match else ""


def _classify_emails(emails: list[str]) -> tuple[str, str]:
    """Split *emails* into (dpo_email, privacy_email) by local-part heuristic."""
    dpo_email = ""
    privacy_email = ""
    for email in emails:
        local = re.sub(r"[-_]", "", email.split("@")[0].lower())
        if local in ("dpo", "gdpr", "dataprotection") and not dpo_email:
            dpo_email = email
        elif not privacy_email:
            privacy_email = email
    return dpo_email, privacy_email


def _build_record(
    company_name: str,
    emails: list[str],
    portal: str,
) -> CompanyRecord:
    dpo_email, privacy_email = _classify_emails(emails)
    has_email = bool(emails)
    preferred = "portal" if portal and not has_email else "email"

    return CompanyRecord(
        company_name=company_name,
        source="privacy_scrape",
        source_confidence="medium",
        last_verified=date.today().isoformat(),
        contact=Contact(
            dpo_email=dpo_email,
            privacy_email=privacy_email,
            gdpr_portal_url=portal,
            preferred_method=preferred,  # type: ignore[arg-type]
        ),
        flags=Flags(
            portal_only=(bool(portal) and not has_email),
            email_accepted=has_email,
            auto_send_possible=False,
        ),
        request_notes=RequestNotes(),
    )
