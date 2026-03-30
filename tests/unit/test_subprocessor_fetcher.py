"""Unit tests for contact_resolver/subprocessor_fetcher.py."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from contact_resolver import cost_tracker
from contact_resolver.subprocessor_fetcher import (
    _MIN_PLAIN_TEXT,
    _build_record,
    _extract_json,
    _fetch_page_playwright,
    fetch_subprocessors,
    is_stale,
)
from contact_resolver.models import SubprocessorRecord


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_response(text: str, input_tokens: int = 500, output_tokens: int = 200) -> MagicMock:
    block = MagicMock()
    block.text = text
    response = MagicMock()
    response.content = [block]
    response.usage.input_tokens = input_tokens
    response.usage.output_tokens = output_tokens
    return response


_VALID_PAYLOAD = {
    "subprocessors": [
        {
            "domain": "stripe.com",
            "company_name": "Stripe",
            "hq_country": "United States",
            "hq_country_code": "US",
            "purposes": ["payment processing"],
            "data_categories": ["payment data"],
            "transfer_basis": "SCCs",
            "source_url": "https://example.com/sub-processors",
            "source": "scrape_subprocessor_page",
        }
    ],
    "source_url": "https://example.com/sub-processors",
}


# ---------------------------------------------------------------------------
# _extract_json
# ---------------------------------------------------------------------------

def test_extract_json_plain():
    import json
    text = json.dumps(_VALID_PAYLOAD)
    result = _extract_json(text)
    assert result is not None
    assert "subprocessors" in result


def test_extract_json_fenced():
    import json
    text = f"```json\n{json.dumps(_VALID_PAYLOAD)}\n```"
    result = _extract_json(text)
    assert result is not None


def test_extract_json_no_json():
    assert _extract_json("no json here") is None


# ---------------------------------------------------------------------------
# _build_record
# ---------------------------------------------------------------------------

def test_build_record_valid():
    record = _build_record(_VALID_PAYLOAD, "example.com", "https://example.com/sub-processors")
    assert record.fetch_status == "ok"
    assert len(record.subprocessors) == 1
    sp = record.subprocessors[0]
    assert sp.domain == "stripe.com"
    assert sp.transfer_basis == "SCCs"


def test_build_record_self_referential_excluded():
    data = {
        "subprocessors": [{
            "domain": "example.com",
            "company_name": "Example",
        }],
        "source_url": "",
    }
    record = _build_record(data, "example.com", "")
    assert record.fetch_status == "not_found"
    assert len(record.subprocessors) == 0


def test_build_record_empty_list():
    record = _build_record({"subprocessors": [], "source_url": ""}, "example.com", "")
    assert record.fetch_status == "not_found"


def test_build_record_none():
    record = _build_record(None, "example.com", "")
    assert record.fetch_status == "not_found"


# ---------------------------------------------------------------------------
# is_stale
# ---------------------------------------------------------------------------

def test_is_stale_fresh():
    now = datetime.now(timezone.utc).isoformat()
    record = SubprocessorRecord(fetched_at=now, fetch_status="ok")
    assert not is_stale(record, ttl_days=30)


def test_is_stale_old():
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    record = SubprocessorRecord(fetched_at=old, fetch_status="ok")
    assert is_stale(record, ttl_days=30)


def test_is_stale_boundary():
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    record = SubprocessorRecord(fetched_at=old, fetch_status="ok")
    # Exactly 30 days — not stale (> 30, not >= 30)
    assert not is_stale(record, ttl_days=30)


def test_is_stale_empty_fetched_at():
    record = SubprocessorRecord(fetched_at="", fetch_status="ok")
    assert is_stale(record, ttl_days=30)


# ---------------------------------------------------------------------------
# fetch_subprocessors (mocked Anthropic)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_cost_tracker():
    cost_tracker.reset()
    yield
    cost_tracker.reset()


def test_fetch_subprocessors_success():
    import json
    mock_response = _make_response(json.dumps(_VALID_PAYLOAD))

    with patch("contact_resolver.subprocessor_fetcher.requests.get") as mock_get, \
         patch("contact_resolver.subprocessor_fetcher.anthropic.Anthropic") as MockClient:
        mock_get.return_value.status_code = 404
        instance = MockClient.return_value
        instance.messages.create.return_value = mock_response

        record = fetch_subprocessors("Example", "example.com", api_key="test-key")

    assert record.fetch_status == "ok"
    assert len(record.subprocessors) == 1
    assert record.subprocessors[0].domain == "stripe.com"


def test_fetch_subprocessors_llm_limit():
    cost_tracker.set_llm_limit(0)
    record = fetch_subprocessors("Example", "example.com", api_key="test-key")
    assert record.fetch_status == "pending"
    assert "limit" in record.error_message.lower()


def test_fetch_subprocessors_no_api_key():
    with patch("contact_resolver.subprocessor_fetcher.settings") as mock_settings:
        mock_settings.anthropic_api_key = ""
        record = fetch_subprocessors("Example", "example.com", api_key=None)
    assert record.fetch_status == "error"


def test_fetch_subprocessors_api_error():
    import anthropic
    with patch("contact_resolver.subprocessor_fetcher.requests.get") as mock_get, \
         patch("contact_resolver.subprocessor_fetcher.anthropic.Anthropic") as MockClient:
        mock_get.return_value.status_code = 404
        instance = MockClient.return_value
        instance.messages.create.side_effect = anthropic.APIError(
            message="rate limit", request=MagicMock(), body=None
        )

        record = fetch_subprocessors("Example", "example.com", api_key="test-key")

    assert record.fetch_status == "error"


# ---------------------------------------------------------------------------
# JS-shell detection and Playwright fallback
# ---------------------------------------------------------------------------

_JS_SHELL_HTML = (
    "<!DOCTYPE html><html><head>"
    "<script src='/_next/static/main.js'></script>"
    "<meta name='viewport' content='width=device-width'>"
    "</head><body><div id='root'></div></body></html>"
)
_REAL_PAGE_TEXT = "A" * (_MIN_PLAIN_TEXT + 100)  # enough plain text to pass the threshold


def test_js_shell_not_passed_to_llm():
    """When requests returns a JS-rendered shell, page_text must NOT be sent to the LLM."""
    import json

    mock_response = _make_response(json.dumps(_VALID_PAYLOAD))
    captured_messages = []

    def _capture_create(**kwargs):
        captured_messages.append(kwargs.get("messages", []))
        return mock_response

    with patch("contact_resolver.subprocessor_fetcher.requests.get") as mock_get, \
         patch("contact_resolver.subprocessor_fetcher._fetch_page_playwright", return_value=""), \
         patch("contact_resolver.subprocessor_fetcher.anthropic.Anthropic") as MockClient:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _JS_SHELL_HTML  # JS shell — plain text after strip < _MIN_PLAIN_TEXT
        mock_get.return_value = mock_resp
        MockClient.return_value.messages.create.side_effect = _capture_create

        fetch_subprocessors("Example", "example.com", api_key="test-key")

    assert captured_messages, "LLM was never called"
    user_content = captured_messages[0][0]["content"]
    # The poisoned HTML shell must NOT appear in the LLM message
    assert "Subprocessor page content" not in user_content
    assert "<script" not in user_content


def test_real_page_text_passed_to_llm():
    """When scrape returns meaningful plain text, it IS prepended to the LLM message."""
    import json

    mock_response = _make_response(json.dumps(_VALID_PAYLOAD))
    captured_messages = []

    def _capture_create(**kwargs):
        captured_messages.append(kwargs.get("messages", []))
        return mock_response

    with patch("contact_resolver.subprocessor_fetcher.requests.get") as mock_get, \
         patch("contact_resolver.subprocessor_fetcher.anthropic.Anthropic") as MockClient:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # Real page: strip_html of plain text returns the text unchanged (no tags)
        mock_resp.text = _REAL_PAGE_TEXT
        mock_get.return_value = mock_resp
        MockClient.return_value.messages.create.side_effect = _capture_create

        fetch_subprocessors("Example", "example.com", api_key="test-key")

    assert captured_messages
    user_content = captured_messages[0][0]["content"]
    assert "Subprocessor page content" in user_content


def test_playwright_fallback_called_when_requests_returns_shell():
    """When all requests scrapes are JS shells, Playwright fallback is attempted."""
    import json

    mock_response = _make_response(json.dumps(_VALID_PAYLOAD))
    playwright_calls = []

    def _fake_playwright(url: str) -> str:
        playwright_calls.append(url)
        return ""  # also returns nothing (Playwright not installed in test env)

    with patch("contact_resolver.subprocessor_fetcher.requests.get") as mock_get, \
         patch("contact_resolver.subprocessor_fetcher._fetch_page_playwright", side_effect=_fake_playwright), \
         patch("contact_resolver.subprocessor_fetcher.anthropic.Anthropic") as MockClient:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _JS_SHELL_HTML
        mock_get.return_value = mock_resp
        MockClient.return_value.messages.create.return_value = mock_response

        fetch_subprocessors("Example", "example.com", api_key="test-key")

    assert len(playwright_calls) > 0, "Playwright fallback was never called"


def test_playwright_fallback_not_called_when_requests_succeeds():
    """When requests finds a real page, Playwright fallback is NOT attempted."""
    import json

    mock_response = _make_response(json.dumps(_VALID_PAYLOAD))
    playwright_calls = []

    def _fake_playwright(url: str) -> str:
        playwright_calls.append(url)
        return ""

    with patch("contact_resolver.subprocessor_fetcher.requests.get") as mock_get, \
         patch("contact_resolver.subprocessor_fetcher._fetch_page_playwright", side_effect=_fake_playwright), \
         patch("contact_resolver.subprocessor_fetcher.anthropic.Anthropic") as MockClient:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = _REAL_PAGE_TEXT
        mock_get.return_value = mock_resp
        MockClient.return_value.messages.create.return_value = mock_response

        fetch_subprocessors("Example", "example.com", api_key="test-key")

    assert playwright_calls == [], "Playwright was called unnecessarily"


def test_playwright_returns_no_import():
    """_fetch_page_playwright returns '' when Playwright is not installed."""
    with patch.dict("sys.modules", {"playwright": None, "playwright.sync_api": None}):
        # Should not raise — returns empty string gracefully
        result = _fetch_page_playwright("https://example.com/sub-processors")
    assert result == ""


def test_llm_message_uses_search_queries_not_url_fetch():
    """LLM user message must contain search queries, NOT 'Try these URLs first'."""
    import json

    mock_response = _make_response(json.dumps(_VALID_PAYLOAD))
    captured_messages = []

    def _capture_create(**kwargs):
        captured_messages.append(kwargs.get("messages", []))
        return mock_response

    with patch("contact_resolver.subprocessor_fetcher.requests.get") as mock_get, \
         patch("contact_resolver.subprocessor_fetcher._fetch_page_playwright", return_value=""), \
         patch("contact_resolver.subprocessor_fetcher.anthropic.Anthropic") as MockClient:
        mock_get.return_value.status_code = 404
        MockClient.return_value.messages.create.side_effect = _capture_create

        fetch_subprocessors("Figma", "figma.com", api_key="test-key")

    assert captured_messages
    user_content = captured_messages[0][0]["content"]
    # New prompt pattern
    assert "Search for" in user_content
    # Old bad pattern must be gone
    assert "Try these URLs first" not in user_content
