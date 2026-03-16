"""Unit tests for contact_resolver/llm_searcher.py."""

import json
from datetime import date
from unittest.mock import MagicMock, patch

import anthropic
import pytest

from contact_resolver import cost_tracker
from contact_resolver.llm_searcher import (
    _extract_json,
    _extract_text,
    _validate_and_build,
    search_company,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_VALID_PAYLOAD: dict = {
    "company_name": "Acme Corp",
    "legal_entity_name": "Acme Corporation Ltd",
    "source_confidence": "high",
    "contact": {
        "dpo_email": "dpo@acme.com",
        "privacy_email": "privacy@acme.com",
        "gdpr_portal_url": "",
        "postal_address": {
            "line1": "1 Acme Street",
            "city": "London",
            "postcode": "EC1A 1BB",
            "country": "United Kingdom",
        },
        "preferred_method": "email",
    },
    "flags": {
        "portal_only": False,
        "email_accepted": True,
        "auto_send_possible": False,
    },
    "request_notes": {
        "special_instructions": "",
        "identity_verification_required": False,
        "known_response_time_days": 30,
    },
}


def _make_text_response(text: str, input_tokens: int = 500, output_tokens: int = 200) -> MagicMock:
    """Build a mock Anthropic API response whose content has one text block."""
    block = MagicMock()
    block.text = text
    block.spec = ["text"]  # hasattr(block, "text") → True
    response = MagicMock()
    response.content = [block]
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    return response


# ---------------------------------------------------------------------------
# _extract_text
# ---------------------------------------------------------------------------


def test_extract_text_single_block() -> None:
    response = _make_text_response("hello world")
    assert _extract_text(response) == "hello world"


def test_extract_text_multiple_blocks() -> None:
    b1, b2 = MagicMock(), MagicMock()
    b1.text = "part one"
    b2.text = "part two"
    resp = MagicMock()
    resp.content = [b1, b2]
    assert _extract_text(resp) == "part one\npart two"


def test_extract_text_no_text_blocks() -> None:
    block = MagicMock(spec=[])  # no 'text' attribute
    resp = MagicMock()
    resp.content = [block]
    assert _extract_text(resp) == ""


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------


def test_extract_json_plain_json() -> None:
    text = json.dumps({"foo": "bar"})
    assert _extract_json(text) == {"foo": "bar"}


def test_extract_json_strips_markdown_fences() -> None:
    text = "```json\n" + json.dumps({"foo": "bar"}) + "\n```"
    assert _extract_json(text) == {"foo": "bar"}


def test_extract_json_json_embedded_in_prose() -> None:
    text = 'Here is the data: {"key": 42} — done.'
    assert _extract_json(text) == {"key": 42}


def test_extract_json_no_json_returns_none() -> None:
    assert _extract_json("No JSON here at all.") is None


def test_extract_json_invalid_json_returns_none() -> None:
    assert _extract_json("{bad json: [}") is None


# ---------------------------------------------------------------------------
# _validate_and_build
# ---------------------------------------------------------------------------


def test_validate_and_build_success() -> None:
    record = _validate_and_build(_VALID_PAYLOAD, "Acme Corp")
    assert record is not None
    assert record.source == "llm_search"
    assert record.source_confidence == "high"
    assert record.contact.dpo_email == "dpo@acme.com"
    assert record.contact.preferred_method == "email"


def test_validate_and_build_low_confidence_returns_none() -> None:
    data = {**_VALID_PAYLOAD, "source_confidence": "low"}
    assert _validate_and_build(data, "Acme") is None


def test_validate_and_build_no_contact_method_downgrades_to_low() -> None:
    data = dict(_VALID_PAYLOAD)
    data["contact"] = {
        "dpo_email": "",
        "privacy_email": "",
        "gdpr_portal_url": "",
        "postal_address": {},
        "preferred_method": "email",
    }
    assert _validate_and_build(data, "Acme") is None


def test_validate_and_build_invalid_preferred_method_defaults_to_email() -> None:
    data = dict(_VALID_PAYLOAD)
    data["contact"] = {**_VALID_PAYLOAD["contact"], "preferred_method": "fax"}
    record = _validate_and_build(data, "Acme")
    assert record is not None
    assert record.contact.preferred_method == "email"


def test_validate_and_build_uses_fallback_company_name() -> None:
    data = {**_VALID_PAYLOAD, "company_name": ""}
    record = _validate_and_build(data, "Fallback Name")
    assert record is not None
    assert record.company_name == "Fallback Name"


def test_validate_and_build_last_verified_is_today() -> None:
    record = _validate_and_build(_VALID_PAYLOAD, "Acme")
    assert record is not None
    assert record.last_verified == date.today().isoformat()


def test_validate_and_build_portal_only_accepted() -> None:
    data = dict(_VALID_PAYLOAD)
    data["contact"] = {
        "dpo_email": "",
        "privacy_email": "",
        "gdpr_portal_url": "https://privacy.acme.com/sar",
        "postal_address": {},
        "preferred_method": "portal",
    }
    record = _validate_and_build(data, "Acme")
    assert record is not None
    assert record.contact.preferred_method == "portal"


# ---------------------------------------------------------------------------
# search_company (integration of all helpers)
# ---------------------------------------------------------------------------

def test_search_company_success() -> None:
    api_response = _make_text_response(json.dumps(_VALID_PAYLOAD), input_tokens=643, output_tokens=250)
    cost_tracker.reset()

    with patch("contact_resolver.llm_searcher.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = api_response
        record = search_company("Acme Corp", "acme.com", api_key="sk-ant")

    assert record is not None
    assert record.company_name == "Acme Corp"
    assert record.source == "llm_search"

    log = cost_tracker.get_log()
    assert len(log) == 1
    assert log[0].found is True
    assert log[0].input_tokens == 643
    assert log[0].output_tokens == 250


def test_search_company_low_confidence_returns_none() -> None:
    payload = {**_VALID_PAYLOAD, "source_confidence": "low"}
    api_response = _make_text_response(json.dumps(payload))
    cost_tracker.reset()

    with patch("contact_resolver.llm_searcher.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = api_response
        result = search_company("Acme Corp", "acme.com", api_key="sk-ant")

    assert result is None
    log = cost_tracker.get_log()
    assert len(log) == 1
    assert log[0].found is False


def test_search_company_api_error_returns_none() -> None:
    with patch("contact_resolver.llm_searcher.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.side_effect = anthropic.APIError(
            message="rate limit", request=MagicMock(), body={}
        )
        result = search_company("Acme Corp", "acme.com", api_key="sk-ant")

    assert result is None


def test_search_company_unparseable_response_returns_none() -> None:
    api_response = _make_text_response("Sorry, I could not find any information.")

    with patch("contact_resolver.llm_searcher.anthropic.Anthropic") as MockClient:
        MockClient.return_value.messages.create.return_value = api_response
        result = search_company("Acme Corp", "acme.com", api_key="sk-ant")

    assert result is None


def test_search_company_missing_api_key_returns_none() -> None:
    with patch("contact_resolver.llm_searcher.settings") as mock_settings:
        mock_settings.anthropic_api_key = ""
        result = search_company("Acme Corp", "acme.com", api_key="")

    assert result is None
