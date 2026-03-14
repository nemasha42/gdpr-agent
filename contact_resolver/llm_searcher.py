"""GDPR contact lookup via Claude Haiku — structured extraction from privacy page text."""

import json
import re
from datetime import date
from typing import Any

import anthropic
import requests as http

from config.settings import settings
from contact_resolver import cost_tracker
from contact_resolver import privacy_page_scraper
from contact_resolver.models import (
    CompanyRecord,
    Contact,
    Flags,
    PostalAddress,
    RequestNotes,
)

_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1024
_TEXT_LIMIT = 1500  # max chars of privacy page text passed to Claude
_TIMEOUT = 10

# Contact fields that constitute a "usable" contact method
_CONTACT_FIELDS = ("dpo_email", "privacy_email", "gdpr_portal_url")

# Fallback URLs tried directly if privacy_page_scraper finds no text
_FALLBACK_URLS: tuple[str, ...] = (
    "https://{domain}/privacy-policy",
    "https://{domain}/privacy",
    "https://{domain}/gdpr",
)

# System prompt with JSON schema — compact, under 250 tokens
_SYSTEM_PROMPT = """\
Extract GDPR contact details from privacy policy text. \
Reply with ONLY a valid JSON object — no prose, no markdown fences:
{"company_name":"","legal_entity_name":"","source_confidence":"medium",\
"contact":{"dpo_email":"","privacy_email":"","gdpr_portal_url":"",\
"postal_address":{"line1":"","city":"","postcode":"","country":""},\
"preferred_method":"email"},\
"flags":{"portal_only":false,"email_accepted":true,"auto_send_possible":false},\
"request_notes":{"special_instructions":"","identity_verification_required":false,\
"known_response_time_days":30}}
confidence: high=official contacts clearly stated; \
medium=contacts mentioned indirectly; low=no usable GDPR contact found. \
preferred_method: email, portal, or postal."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search_company(
    company_name: str,
    domain: str,
    *,
    api_key: str | None = None,
) -> CompanyRecord | None:
    """Search for GDPR contact details by extracting text from the company's
    privacy page and passing it to Claude Haiku for structured extraction.

    Returns ``None`` immediately (without calling Claude) if no privacy page
    text can be fetched — saving the API cost entirely.

    Args:
        company_name: Human-readable company name (e.g. ``"Spotify"``).
        domain: Registrable domain (e.g. ``"spotify.com"``).
        api_key: Anthropic API key; falls back to ``settings.anthropic_api_key``.
    """
    key = api_key or settings.anthropic_api_key
    if not key:
        return None

    page_text = _fetch_privacy_text(domain)
    if not page_text:
        return None

    client = anthropic.Anthropic(api_key=key)
    user_message = _build_prompt(company_name, page_text)

    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError:
        return None

    cost_tracker.record_llm_call(
        company_name=company_name,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        model=_MODEL,
    )

    text = _extract_text(response)
    if not text:
        return None

    data = _extract_json(text)
    if not data:
        return None

    return _validate_and_build(data, company_name)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fetch_privacy_text(domain: str, *, http_get: Any = None) -> str:
    """Return stripped privacy page text truncated to ``_TEXT_LIMIT`` chars.

    Step 1 — use the scraper's URL templates (4 candidates, reuses _strip_html).
    Step 2 — if still empty, try three explicit fallback URLs directly.
    Returns empty string if all attempts fail.
    """
    # Step 1: scraper's URL templates
    text = privacy_page_scraper.fetch_privacy_text(domain, http_get=http_get)
    if text:
        return text[:_TEXT_LIMIT]

    # Step 2: explicit fallback URLs
    get = http_get or http.get
    for template in _FALLBACK_URLS:
        url = template.format(domain=domain)
        try:
            resp = get(url, timeout=_TIMEOUT)
        except Exception:
            continue
        if resp.status_code == 200 and resp.text.strip():
            return privacy_page_scraper._strip_html(resp.text)[:_TEXT_LIMIT]

    return ""


def _build_prompt(company_name: str, privacy_text: str) -> str:
    return (
        f"Extract GDPR contact details from this privacy policy text "
        f"and return JSON matching the schema.\n\n"
        f"Company: {company_name}\n\n"
        f"Privacy policy text:\n{privacy_text}"
    )


def _extract_text(response: Any) -> str:
    """Concatenate all text blocks from a Claude API response."""
    parts: list[str] = []
    for block in response.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts).strip()


def _extract_json(text: str) -> dict | None:
    """Find and parse the first JSON object in *text*.

    Strips markdown code fences (```json … ```) before searching.
    """
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group())
    except json.JSONDecodeError:
        return None


def _validate_and_build(data: dict, company_name: str) -> CompanyRecord | None:
    """Validate parsed LLM JSON and return a :class:`CompanyRecord`, or ``None``.

    Downgrades ``source_confidence`` to ``"low"`` (and returns ``None``) when
    no usable contact method is present.
    """
    confidence: str = data.get("source_confidence", "low")

    contact_data: dict = data.get("contact", {})
    has_contact = any(contact_data.get(f, "").strip() for f in _CONTACT_FIELDS)

    if not has_contact:
        confidence = "low"

    if confidence == "low":
        return None

    preferred: str = contact_data.get("preferred_method", "email")
    if preferred not in ("email", "portal", "postal"):
        preferred = "email"

    postal_raw: dict = contact_data.get("postal_address", {})
    flags_raw: dict = data.get("flags", {})
    notes_raw: dict = data.get("request_notes", {})

    try:
        return CompanyRecord(
            company_name=data.get("company_name") or company_name,
            legal_entity_name=data.get("legal_entity_name", ""),
            source="llm_search",
            source_confidence=confidence,  # type: ignore[arg-type]
            last_verified=date.today().isoformat(),
            contact=Contact(
                dpo_email=contact_data.get("dpo_email", ""),
                privacy_email=contact_data.get("privacy_email", ""),
                gdpr_portal_url=contact_data.get("gdpr_portal_url", ""),
                postal_address=PostalAddress(
                    line1=postal_raw.get("line1", ""),
                    city=postal_raw.get("city", ""),
                    postcode=postal_raw.get("postcode", ""),
                    country=postal_raw.get("country", ""),
                ),
                preferred_method=preferred,  # type: ignore[arg-type]
            ),
            flags=Flags(
                portal_only=flags_raw.get("portal_only", False),
                email_accepted=flags_raw.get("email_accepted", True),
                auto_send_possible=flags_raw.get("auto_send_possible", False),
            ),
            request_notes=RequestNotes(
                special_instructions=notes_raw.get("special_instructions", ""),
                identity_verification_required=notes_raw.get(
                    "identity_verification_required", False
                ),
                known_response_time_days=notes_raw.get("known_response_time_days", 30),
            ),
        )
    except Exception:
        return None
