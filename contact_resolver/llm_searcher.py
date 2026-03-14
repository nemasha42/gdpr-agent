"""GDPR contact lookup via Anthropic Claude with built-in web search."""

import json
import re
from datetime import date
from typing import Any

import anthropic

from config.settings import settings
from contact_resolver.models import (
    CompanyRecord,
    Contact,
    Flags,
    PostalAddress,
    RequestNotes,
)

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 2048

# Contact fields that constitute a "usable" contact method
_CONTACT_FIELDS = ("dpo_email", "privacy_email", "gdpr_portal_url")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search_company(
    company_name: str,
    domain: str,
    *,
    api_key: str | None = None,
) -> CompanyRecord | None:
    """Search for GDPR contact details using Claude with web search.

    Args:
        company_name: Human-readable company name (e.g. ``"Spotify"``).
        domain: Registrable domain (e.g. ``"spotify.com"``).
        api_key: Anthropic API key; falls back to ``settings.anthropic_api_key``.

    Returns:
        A :class:`CompanyRecord` on success, or ``None`` when:

        - the API call fails
        - the response cannot be parsed as valid JSON
        - ``source_confidence`` is ``"low"``
        - no usable contact method (email / portal / postal) was found
    """
    key = api_key or settings.anthropic_api_key
    if not key:
        return None

    client = anthropic.Anthropic(api_key=key)
    prompt = _build_prompt(company_name, domain)

    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError:
        return None

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


def _build_prompt(company_name: str, domain: str) -> str:
    return f"""\
Search the web for GDPR / data protection contact details for {company_name} \
(website: {domain}).

Find:
1. DPO (Data Protection Officer) email address
2. General privacy / GDPR contact email
3. Any dedicated GDPR / Subject Access Request web portal URL
4. Legal data controller name and registered postal address
5. Preferred channel for submitting Subject Access Requests

Return ONLY a valid JSON object with this exact structure — no prose, no markdown:
{{
  "company_name": "{company_name}",
  "legal_entity_name": "",
  "source_confidence": "high",
  "contact": {{
    "dpo_email": "",
    "privacy_email": "",
    "gdpr_portal_url": "",
    "postal_address": {{"line1": "", "city": "", "postcode": "", "country": ""}},
    "preferred_method": "email"
  }},
  "flags": {{
    "portal_only": false,
    "email_accepted": true,
    "auto_send_possible": false
  }},
  "request_notes": {{
    "special_instructions": "",
    "identity_verification_required": false,
    "known_response_time_days": 30
  }}
}}

Rules:
- source_confidence = "high"   → found official privacy/GDPR page with clear contact details
- source_confidence = "medium" → found contact details from indirect sources
- source_confidence = "low"    → could NOT find reliable GDPR contact information
- preferred_method must be one of: "email", "portal", "postal"
- Return ONLY the JSON object, nothing else."""


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
