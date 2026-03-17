"""GDPR contact lookup via web_search + Haiku."""

import json
import re
from datetime import date
from typing import Any

import anthropic

from config.settings import settings
from contact_resolver import cost_tracker
from contact_resolver.models import (
    CompanyRecord,
    Contact,
    Flags,
    PostalAddress,
    RequestNotes,
)

_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1024

# Contact fields that constitute a "usable" contact method
_CONTACT_FIELDS = ("dpo_email", "privacy_email", "gdpr_portal_url")

# System prompt
_SYSTEM_PROMPT = """\
You are a GDPR contact data extractor. Find the company's GDPR/privacy \
contacts and reply with ONLY a valid JSON object — no prose, no markdown fences:
{"company_name":"","legal_entity_name":"","source_confidence":"medium",\
"contact":{"dpo_email":"","privacy_email":"","gdpr_portal_url":"",\
"postal_address":{"line1":"","city":"","postcode":"","country":""},\
"preferred_method":"email"},\
"flags":{"portal_only":false,"email_accepted":true,"auto_send_possible":false},\
"request_notes":{"special_instructions":"","identity_verification_required":false,\
"known_response_time_days":30}}
confidence: high=official contacts clearly stated; \
medium=contacts found indirectly; low=no usable GDPR contact found. \
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
    """Search for GDPR contact details using web_search + Haiku (~$0.025/call).

    Claude autonomously searches and reads pages, handling bot-blocked and
    JS-rendered sites. Returns ``None`` if no usable contacts found.
    """
    anthropic_key = api_key or settings.anthropic_api_key
    if not anthropic_key:
        return None
    if cost_tracker.is_llm_limit_reached():
        return None

    return _extract_with_websearch(company_name, domain, anthropic_key)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_with_websearch(
    company_name: str, domain: str, api_key: str
) -> CompanyRecord | None:
    """Claude autonomously searches — handles bot-blocked / JS-rendered sites."""
    client = anthropic.Anthropic(api_key=api_key)
    user_message = f"GDPR contacts for {company_name} ({domain})"

    try:
        response = client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}],
            messages=[{"role": "user", "content": user_message}],
        )
    except anthropic.APIError:
        return None

    text = _extract_text(response)
    data = _extract_json(text) if text else None
    record = _validate_and_build(data, company_name) if data else None

    cost_tracker.record_llm_call(
        company_name=company_name,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        model=_MODEL,
        found=record is not None,
        source="contact_resolver",
        purpose="GDPR contact address lookup",
    )
    return record


def _extract_text(response: Any) -> str:
    parts: list[str] = []
    for block in response.content:
        if hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts).strip()


def _extract_json(text: str) -> dict | None:
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    idx = text.find("{")
    if idx == -1:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, idx)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    return None


def _validate_and_build(data: dict, company_name: str) -> CompanyRecord | None:
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
