"""Unit tests for scanner/inbox_reader.py — no real credentials required."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scanner.inbox_reader import fetch_emails

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "sample_emails.json"


def _load_fixtures() -> list[dict[str, str]]:
    return json.loads(_FIXTURES.read_text())


def _api_message(email: dict[str, str]) -> dict:
    """Convert a fixture email record into the shape Gmail API returns."""
    return {
        "id": email["message_id"],
        "payload": {
            "headers": [
                {"name": "From", "value": email["sender"]},
                {"name": "Subject", "value": email["subject"]},
                {"name": "Date", "value": email["date"]},
            ]
        },
    }


def _make_service(
    emails: list[dict[str, str]],
    *,
    page_size: int | None = None,
) -> MagicMock:
    """Build a mock Gmail service that serves the given emails.

    Args:
        emails: Fixture email records to serve.
        page_size: If set, split emails across two pages to simulate
                   pagination (first page has page_size emails).
    """
    api_by_id = {e["message_id"]: _api_message(e) for e in emails}
    ids = list(api_by_id)

    service = MagicMock()

    # --- messages().list() ---
    if page_size is not None and page_size < len(ids):
        first_ids, second_ids = ids[:page_size], ids[page_size:]
        first_resp = {
            "messages": [{"id": i} for i in first_ids],
            "nextPageToken": "page2_token",
        }
        second_resp = {"messages": [{"id": i} for i in second_ids]}
        service.users().messages().list.return_value.execute.side_effect = [
            first_resp,
            second_resp,
        ]
    else:
        service.users().messages().list.return_value.execute.return_value = {
            "messages": [{"id": i} for i in ids]
        }

    # --- messages().get() ---
    def _get_side_effect(**kwargs: str) -> MagicMock:
        msg_id = kwargs["id"]
        mock_req = MagicMock()
        mock_req.execute.return_value = api_by_id.get(
            msg_id, {"id": msg_id, "payload": {"headers": []}}
        )
        return mock_req

    service.users().messages().get.side_effect = _get_side_effect

    return service


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fetch_emails_returns_all_fixtures() -> None:
    """All fixture emails are returned with correct field values."""
    fixtures = _load_fixtures()
    service = _make_service(fixtures)

    result = fetch_emails(service, max_results=len(fixtures))

    assert len(result) == len(fixtures)
    for expected, actual in zip(fixtures, result):
        assert actual["message_id"] == expected["message_id"]
        assert actual["sender"] == expected["sender"]
        assert actual["subject"] == expected["subject"]
        assert actual["date"] == expected["date"]


def test_fetch_emails_respects_max_results() -> None:
    """max_results caps the number of returned emails."""
    fixtures = _load_fixtures()  # 10 emails
    service = _make_service(fixtures)

    result = fetch_emails(service, max_results=3)

    assert len(result) == 3


def test_fetch_emails_pagination() -> None:
    """Emails spanning two API pages are all returned."""
    fixtures = _load_fixtures()  # 10 emails
    service = _make_service(fixtures, page_size=6)  # split 6 + 4

    result = fetch_emails(service, max_results=len(fixtures))

    assert len(result) == len(fixtures)
    returned_ids = {e["message_id"] for e in result}
    expected_ids = {e["message_id"] for e in fixtures}
    assert returned_ids == expected_ids


def test_fetch_emails_empty_inbox() -> None:
    """An empty inbox returns an empty list without error."""
    service = _make_service([])

    result = fetch_emails(service)

    assert result == []


def test_fetch_emails_missing_headers() -> None:
    """Emails with missing headers produce empty-string fields gracefully."""
    service = MagicMock()
    service.users().messages().list.return_value.execute.return_value = {
        "messages": [{"id": "msg_no_headers"}]
    }

    # Message with no headers at all
    def _get_side_effect(**kwargs: str) -> MagicMock:
        mock_req = MagicMock()
        mock_req.execute.return_value = {"id": "msg_no_headers", "payload": {"headers": []}}
        return mock_req

    service.users().messages().get.side_effect = _get_side_effect

    result = fetch_emails(service, max_results=1)

    assert len(result) == 1
    assert result[0]["message_id"] == "msg_no_headers"
    assert result[0]["sender"] == ""
    assert result[0]["subject"] == ""
    assert result[0]["date"] == ""
