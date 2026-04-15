"""Extract form structure via accessibility tree and map fields using LLM."""

import json
import re
from datetime import date, timedelta
from typing import Any, Callable

from config.settings import settings
from contact_resolver.models import PortalFieldMapping, PortalFormField
from letter_engine.models import SARLetter

_CACHE_TTL_DAYS = 90

_INTERACTIVE_ROLES = {"textbox", "combobox", "checkbox", "radio", "spinbutton", "searchbox"}
_BUTTON_ROLES = {"button", "link"}

_FIELD_MAPPING_PROMPT = """You are mapping a web form's fields to user data for a GDPR Subject Access Request.

Here are the form's interactive elements (from the accessibility tree):
{elements_json}

Map these user data fields to the form elements:
- first_name: "{first_name}"
- last_name: "{last_name}"
- email: "{email}"
- country: "{country}"
- request_type: "Access my personal data"
- description: (SAR letter body — long text, use if there is a textarea)
- relationship: "Customer"

Return JSON only, no markdown:
{{"fields": [{{"name": "<element name>", "value_key": "<user data key>", "role": "<element role>"}}], "submit_button": "<name of submit button>"}}

Rules:
- Only map fields that have a clear match. Skip fields you cannot confidently map.
- For dropdowns (combobox), use the closest matching option text as the value.
- The submit button is typically labeled "Submit", "Send", "Submit Request", or similar.
- If request_type is a dropdown, choose the option closest to "Access my personal data" or "Subject Access Request".
"""


def build_user_data(letter: SARLetter) -> dict[str, str]:
    """Assemble user data dict from settings + letter for form filling."""
    name_parts = settings.user_full_name.split(" ", 1)
    return {
        "first_name": name_parts[0],
        "last_name": name_parts[1] if len(name_parts) > 1 else "",
        "email": settings.user_email,
        "country": settings.user_address_country,
        "request_type": "Access my personal data",
        "description": letter.body,
        "relationship": "Customer",
    }


def analyze_form(
    page: Any,
    *,
    llm_call: Callable[[str], str] | None = None,
    cached_mapping: PortalFieldMapping | None = None,
) -> PortalFieldMapping:
    """Extract form fields from the page and return a field mapping.

    Args:
        page: Playwright page object (or mock with .accessibility.snapshot()).
        llm_call: Callable that takes a prompt string and returns LLM response text.
                  Injectable for testing. If None, uses default Anthropic call.
        cached_mapping: Previously cached mapping. If fresh (within TTL), returned as-is.

    Returns:
        PortalFieldMapping with fields mapped to user data keys.
    """
    if cached_mapping and _is_cache_fresh(cached_mapping.cached_at):
        return cached_mapping

    try:
        snapshot_text = page.locator("body").aria_snapshot()
        elements = _extract_elements_from_aria_snapshot(snapshot_text)
    except Exception:
        elements = []

    if not elements:
        return PortalFieldMapping(cached_at=date.today().isoformat())

    name_parts = settings.user_full_name.split(" ", 1)
    prompt = _FIELD_MAPPING_PROMPT.format(
        elements_json=json.dumps(elements, indent=2),
        first_name=name_parts[0],
        last_name=name_parts[1] if len(name_parts) > 1 else "",
        email=settings.user_email,
        country=settings.user_address_country,
    )

    if llm_call is None:
        llm_call = _default_llm_call
    raw = llm_call(prompt)

    return _parse_mapping_response(raw)


def _is_cache_fresh(cached_at: str) -> bool:
    if not cached_at:
        return False
    try:
        cached_date = date.fromisoformat(cached_at)
        return (date.today() - cached_date).days < _CACHE_TTL_DAYS
    except ValueError:
        return False


def _extract_interactive_elements(node: dict, results: list | None = None) -> list[dict]:
    """Extract from old JSON accessibility tree (legacy, kept for tests)."""
    if results is None:
        results = []

    role = node.get("role", "")
    name = node.get("name", "")

    if role in _INTERACTIVE_ROLES and name:
        results.append({"role": role, "name": name})
    elif role in _BUTTON_ROLES and name:
        results.append({"role": "button", "name": name})

    for child in node.get("children", []):
        _extract_interactive_elements(child, results)

    return results


# Regex to parse lines like: - textbox "First Name"  or  - button "Submit"
_RE_ARIA_ELEMENT = re.compile(
    r'^\s*-\s+(textbox|combobox|checkbox|radio|spinbutton|searchbox|button|link)\s+"([^"]+)"',
)


def _extract_elements_from_aria_snapshot(snapshot_text: str) -> list[dict]:
    """Extract interactive elements from Playwright aria_snapshot() text format."""
    results = []
    for line in snapshot_text.splitlines():
        m = _RE_ARIA_ELEMENT.match(line)
        if m:
            role, name = m.group(1), m.group(2)
            if role in ("link",):
                role = "button"  # normalize for LLM prompt
            results.append({"role": role, "name": name})
    return results


def _parse_mapping_response(raw: str) -> PortalFieldMapping:
    idx = raw.find("{")
    if idx == -1:
        return PortalFieldMapping(cached_at=date.today().isoformat())
    try:
        data, _ = json.JSONDecoder().raw_decode(raw, idx)
    except json.JSONDecodeError:
        return PortalFieldMapping(cached_at=date.today().isoformat())

    fields = []
    for f in data.get("fields", []):
        fields.append(PortalFormField(
            name=f.get("name", ""),
            value_key=f.get("value_key", ""),
            role=f.get("role", "textbox"),
        ))

    return PortalFieldMapping(
        cached_at=date.today().isoformat(),
        fields=fields,
        submit_button=data.get("submit_button", ""),
    )


def _default_llm_call(prompt: str, *, domain: str = "") -> str:
    import anthropic
    from contact_resolver.cost_tracker import record_llm_call

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text

    record_llm_call(
        domain,
        response.usage.input_tokens,
        response.usage.output_tokens,
        "claude-haiku-4-5-20251001",
        found=bool(text.strip()),
        source="portal_submitter",
        purpose="portal_field_mapping",
    )

    return text
