"""Compose a SAR letter from a CompanyRecord and user settings."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from contact_resolver.models import CompanyRecord
from letter_engine.models import SARLetter

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _resolve_identity(user_identity: dict | None) -> dict:
    """Return a user_identity dict, falling back to settings singleton."""
    if user_identity is not None:
        return user_identity
    from config.settings import settings
    return {
        "user_full_name": settings.user_full_name,
        "user_email": settings.user_email,
        "user_address_line1": settings.user_address_line1,
        "user_address_city": settings.user_address_city,
        "user_address_postcode": settings.user_address_postcode,
        "user_address_country": settings.user_address_country,
        "gdpr_framework": settings.gdpr_framework,
    }


def compose(record: CompanyRecord, *, user_identity: dict | None = None) -> SARLetter:
    """Fill the appropriate template and return a ready-to-send SARLetter."""
    identity = _resolve_identity(user_identity)
    method = record.contact.preferred_method
    template_name = "sar_postal.txt" if method == "postal" else "sar_email.txt"
    template = (_TEMPLATES_DIR / template_name).read_text()

    vars: dict[str, str] = {
        "user_full_name": identity["user_full_name"],
        "user_email": identity["user_email"],
        "user_address_line1": identity["user_address_line1"],
        "user_address_city": identity["user_address_city"],
        "user_address_postcode": identity["user_address_postcode"],
        "user_address_country": identity["user_address_country"],
        "gdpr_framework": identity["gdpr_framework"],
        "company_name": record.company_name,
        "company_address": _format_company_address(record),
        "date": date.today().strftime("%d %B %Y"),
    }
    body = template.format(**vars)

    to_email = record.contact.privacy_email or record.contact.dpo_email
    subject = f"Subject Access Request — {identity['user_full_name']}"

    return SARLetter(
        company_name=record.company_name,
        method=method,
        to_email=to_email,
        subject=subject,
        body=body,
        portal_url=record.contact.gdpr_portal_url,
        postal_address=_format_company_address(record),
    )


def compose_subprocessor_request(
    record: CompanyRecord, *, user_identity: dict | None = None
) -> SARLetter | None:
    """Compose a subprocessor disclosure request letter.

    Returns None if the record has no usable email contact and method is not postal.
    """
    identity = _resolve_identity(user_identity)
    to_email = record.contact.privacy_email or record.contact.dpo_email
    method = record.contact.preferred_method

    if method != "postal" and not to_email:
        return None

    template_name = (
        "subprocessor_request_postal.txt" if method == "postal"
        else "subprocessor_request_email.txt"
    )
    template = (_TEMPLATES_DIR / template_name).read_text()

    vars: dict[str, str] = {
        "user_full_name": identity["user_full_name"],
        "user_email": identity["user_email"],
        "user_address_line1": identity["user_address_line1"],
        "user_address_city": identity["user_address_city"],
        "user_address_postcode": identity["user_address_postcode"],
        "user_address_country": identity["user_address_country"],
        "gdpr_framework": identity["gdpr_framework"],
        "company_name": record.company_name,
        "company_address": _format_company_address(record),
        "date": date.today().strftime("%d %B %Y"),
        "deadline": (date.today() + timedelta(days=30)).strftime("%d %B %Y"),
    }
    body = template.format(**vars)

    return SARLetter(
        company_name=record.company_name,
        method=method,
        to_email=to_email,
        subject=f"Subprocessor Disclosure Request — {identity['user_full_name']}",
        body=body,
        portal_url=record.contact.gdpr_portal_url,
        postal_address=_format_company_address(record),
    )


def _format_company_address(record: CompanyRecord) -> str:
    addr = record.contact.postal_address
    parts = [p for p in [addr.line1, addr.city, addr.postcode, addr.country] if p]
    return "\n".join(parts) if parts else "(address not available)"
