"""Unit tests for reply_monitor/fetcher.py — Gmail lookup and search fallback."""

from unittest.mock import MagicMock, call, patch

import pytest

from reply_monitor.fetcher import (
    _extract_body,
    _find_attachment_parts,
    _get_header,
    _parse_message,
    fetch_replies_for_sar,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_gmail_msg(msg_id="msg001", from_addr="privacy@example.com",
                    subject="Re: SAR", snippet="thank you",
                    date_str="Mon, 17 Mar 2026 10:00:00 +0000",
                    parts=None):
    """Build a fake Gmail full message resource."""
    return {
        "id": msg_id,
        "threadId": "thread001",
        "snippet": snippet,
        "payload": {
            "headers": [
                {"name": "From", "value": from_addr},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": date_str},
            ],
            "parts": parts or [],
        },
    }


def _make_service(thread_messages=None, search_messages=None):
    """Build a mock Gmail service with configurable responses."""
    service = MagicMock()

    # Thread lookup
    if thread_messages is not None:
        service.users().threads().get().execute.return_value = {
            "messages": thread_messages
        }

    # Message search
    if search_messages is not None:
        service.users().messages().list().execute.return_value = {
            "messages": [{"id": m["id"]} for m in search_messages]
        }
        # Each messages.get() call returns the corresponding message
        def get_full_msg(userId, id, format):
            for m in search_messages:
                if m["id"] == id:
                    mock = MagicMock()
                    mock.execute.return_value = m
                    return mock
            return MagicMock()

        service.users().messages().get.side_effect = get_full_msg

    return service


# ---------------------------------------------------------------------------
# Thread-based lookup tests
# ---------------------------------------------------------------------------

class TestFetchByThread:
    def test_thread_returns_reply_messages(self):
        sent_record = {
            "gmail_thread_id": "thread001",
            "to_email": "privacy@example.com",
            "sent_at": "2026-03-16T00:00:00",
        }
        reply_msg = _make_gmail_msg(msg_id="reply001", from_addr="privacy@example.com")
        outgoing = _make_gmail_msg(msg_id="sent001", from_addr="me@gmail.com")

        service = MagicMock()
        service.users().threads().get().execute.return_value = {
            "messages": [outgoing, reply_msg]
        }
        # _fetch_by_search always runs; mock search to return empty so it terminates
        service.users().messages().list().execute.return_value = {"messages": []}

        results = fetch_replies_for_sar(service, sent_record, user_email="me@gmail.com")
        # Outgoing message filtered out; only the reply
        assert len(results) == 1
        assert results[0]["id"] == "reply001"

    def test_thread_deduplicates_existing_replies(self):
        sent_record = {"gmail_thread_id": "thread001", "to_email": "p@example.com", "sent_at": "2026-03-16T00:00:00"}
        existing = {"reply001"}  # already stored

        reply_msg = _make_gmail_msg(msg_id="reply001")
        service = MagicMock()
        service.users().threads().get().execute.return_value = {"messages": [reply_msg]}
        # When thread dedup returns nothing, search fallback runs — must return a
        # real dict (not a MagicMock) so _paginated_search's loop can terminate.
        service.users().messages().list().execute.return_value = {"messages": []}

        results = fetch_replies_for_sar(service, sent_record, existing_reply_ids=existing)
        assert len(results) == 0  # already seen

    def test_thread_returns_empty_on_api_error(self):
        sent_record = {"gmail_thread_id": "thread001", "to_email": "p@example.com", "sent_at": "2026-03-16T00:00:00"}
        service = MagicMock()
        service.users().threads().get().execute.side_effect = Exception("API error")
        service.users().messages().list().execute.return_value = {"messages": []}

        results = fetch_replies_for_sar(service, sent_record)
        assert results == []

    def test_own_email_filtered_out(self):
        sent_record = {"gmail_thread_id": "t1", "to_email": "p@example.com", "sent_at": "2026-03-16T00:00:00"}
        own_msg = _make_gmail_msg(msg_id="own001", from_addr="trader@gmail.com")

        service = MagicMock()
        service.users().threads().get().execute.return_value = {"messages": [own_msg]}
        # Search fallback runs when thread returns empty — must return a real dict
        service.users().messages().list().execute.return_value = {"messages": []}

        results = fetch_replies_for_sar(service, sent_record, user_email="trader@gmail.com")
        assert results == []

    def test_thread_retries_on_transient_error_then_succeeds(self):
        """Transient 429/500 errors are retried up to 3 times."""
        sent_record = {"gmail_thread_id": "thread001", "to_email": "p@example.com", "sent_at": "2026-03-16T00:00:00"}
        reply_msg = _make_gmail_msg(msg_id="reply001", from_addr="privacy@example.com")
        outgoing = _make_gmail_msg(msg_id="sent001", from_addr="me@gmail.com")

        transient_exc = Exception("Rate limit")
        transient_exc.resp = MagicMock()
        transient_exc.resp.status = 429

        service = MagicMock()
        # First call raises 429, second succeeds
        service.users().threads().get().execute.side_effect = [
            transient_exc,
            {"messages": [outgoing, reply_msg]},
        ]
        service.users().messages().list().execute.return_value = {"messages": []}

        with patch("reply_monitor.fetcher.time.sleep"):  # skip actual sleep
            results = fetch_replies_for_sar(service, sent_record, user_email="me@gmail.com")

        assert len(results) == 1
        assert results[0]["id"] == "reply001"
        # Verify it was called twice (retry)
        assert service.users().threads().get().execute.call_count == 2

    def test_thread_permanent_error_no_retry(self):
        """Non-retryable errors (e.g. 404) fail immediately without retry."""
        sent_record = {"gmail_thread_id": "thread001", "to_email": "p@example.com", "sent_at": "2026-03-16T00:00:00"}

        perm_exc = Exception("Not found")
        perm_exc.resp = MagicMock()
        perm_exc.resp.status = 404

        service = MagicMock()
        service.users().threads().get().execute.side_effect = perm_exc
        service.users().messages().list().execute.return_value = {"messages": []}

        results = fetch_replies_for_sar(service, sent_record)
        assert results == []
        # Should NOT retry — only 1 call
        assert service.users().threads().get().execute.call_count == 1


# ---------------------------------------------------------------------------
# Search fallback tests
# ---------------------------------------------------------------------------

class TestSearchFallback:
    def test_search_used_when_no_thread_id(self):
        sent_record = {
            "gmail_thread_id": "",
            "to_email": "privacy@glassdoor.com",
            "sent_at": "2026-03-16T00:00:00",
        }
        reply_msg = _make_gmail_msg(msg_id="search001", from_addr="privacy@glassdoor.com")

        service = MagicMock()
        # Both queries (exact address + domain) return the same message
        service.users().messages().list().execute.return_value = {
            "messages": [{"id": "search001"}]
        }
        service.users().messages().get().execute.return_value = reply_msg

        results = fetch_replies_for_sar(service, sent_record, user_email="me@gmail.com")
        # Should use search
        service.users().messages().list.assert_called()
        # Deduplicated: same msg returned by both queries → only 1 result
        assert len(results) == 1

    def test_search_skips_empty_to_email(self):
        sent_record = {"gmail_thread_id": "", "to_email": "", "sent_at": "2026-03-16T00:00:00"}
        service = MagicMock()
        results = fetch_replies_for_sar(service, sent_record)
        assert results == []
        service.users().messages().list.assert_not_called()

    def test_search_deduplicates_by_id(self):
        sent_record = {"gmail_thread_id": "", "to_email": "p@example.com", "sent_at": "2026-03-16T00:00:00"}
        existing = {"already001"}
        reply_msg = _make_gmail_msg(msg_id="already001")

        service = MagicMock()
        service.users().messages().list().execute.return_value = {
            "messages": [{"id": "already001"}]
        }
        service.users().messages().get().execute.return_value = reply_msg

        results = fetch_replies_for_sar(service, sent_record, existing_reply_ids=existing)
        assert results == []


# ---------------------------------------------------------------------------
# _get_header tests
# ---------------------------------------------------------------------------

class TestGetHeader:
    def test_returns_header_value(self):
        msg = _make_gmail_msg(from_addr="test@example.com")
        assert _get_header(msg, "From") == "test@example.com"

    def test_case_insensitive(self):
        msg = _make_gmail_msg(subject="Hello World")
        assert _get_header(msg, "subject") == "Hello World"

    def test_missing_header_returns_empty(self):
        msg = _make_gmail_msg()
        assert _get_header(msg, "X-Custom-Header") == ""


# ---------------------------------------------------------------------------
# _find_attachment_parts tests
# ---------------------------------------------------------------------------

class TestFindAttachmentParts:
    def test_finds_attachment_in_parts(self):
        payload = {
            "parts": [
                {
                    "filename": "data.zip",
                    "mimeType": "application/zip",
                    "body": {"attachmentId": "attach001", "size": 1024},
                    "parts": [],
                }
            ]
        }
        parts = _find_attachment_parts(payload)
        assert len(parts) == 1
        assert parts[0]["filename"] == "data.zip"
        assert parts[0]["attachmentId"] == "attach001"

    def test_nested_attachment(self):
        payload = {
            "parts": [
                {
                    "filename": "",
                    "body": {},
                    "parts": [
                        {
                            "filename": "report.csv",
                            "mimeType": "text/csv",
                            "body": {"attachmentId": "attach002", "size": 512},
                            "parts": [],
                        }
                    ],
                }
            ]
        }
        parts = _find_attachment_parts(payload)
        assert len(parts) == 1
        assert parts[0]["filename"] == "report.csv"

    def test_no_attachments_returns_empty(self):
        payload = {"parts": [{"filename": "", "body": {}, "parts": []}]}
        parts = _find_attachment_parts(payload)
        assert parts == []


# ---------------------------------------------------------------------------
# _parse_message tests
# ---------------------------------------------------------------------------

class TestParseMessage:
    def test_parses_basic_message(self):
        msg = _make_gmail_msg(
            msg_id="m001",
            from_addr="privacy@example.com",
            subject="Re: SAR",
            snippet="Thank you for your request",
        )
        result = _parse_message(msg)
        assert result is not None
        assert result["id"] == "m001"
        assert result["from"] == "privacy@example.com"
        assert result["subject"] == "Re: SAR"
        assert result["snippet"] == "Thank you for your request"
        assert result["has_attachment"] is False

    def test_detects_attachment(self):
        msg = _make_gmail_msg(
            parts=[{
                "filename": "data.zip",
                "mimeType": "application/zip",
                "body": {"attachmentId": "att001", "size": 2048},
                "parts": [],
            }]
        )
        result = _parse_message(msg)
        assert result["has_attachment"] is True
        assert len(result["parts"]) == 1

    def test_body_field_present_in_parsed_message(self):
        """_parse_message must include a 'body' key (may be empty string)."""
        msg = _make_gmail_msg()
        result = _parse_message(msg)
        assert "body" in result
        assert isinstance(result["body"], str)


# ---------------------------------------------------------------------------
# _extract_body tests
# ---------------------------------------------------------------------------

class TestExtractBody:
    def _encoded(self, text: str) -> str:
        import base64
        return base64.urlsafe_b64encode(text.encode()).decode()

    def test_plain_text_part(self):
        url = "https://glassdoor.com/dyd/download?token=abc123"
        payload = {
            "mimeType": "text/plain",
            "body": {"data": self._encoded(f"Your data is ready at {url}")},
            "parts": [],
        }
        result = _extract_body(payload)
        assert url in result

    def test_multipart_alternative_prefers_plain(self):
        plain = "Plain text body with URL https://example.com/download?token=xyz"
        html_content = "<html><body>HTML version only</body></html>"
        payload = {
            "mimeType": "multipart/alternative",
            "body": {},
            "parts": [
                {
                    "mimeType": "text/html",
                    "body": {"data": self._encoded(html_content)},
                    "parts": [],
                },
                {
                    "mimeType": "text/plain",
                    "body": {"data": self._encoded(plain)},
                    "parts": [],
                },
            ],
        }
        result = _extract_body(payload)
        assert "Plain text body" in result
        assert "HTML version only" not in result

    def test_html_fallback_strips_tags(self):
        html_content = "<p>Click <a href='https://example.com'>here</a> to download</p>"
        payload = {
            "mimeType": "text/html",
            "body": {"data": self._encoded(html_content)},
            "parts": [],
        }
        result = _extract_body(payload)
        assert "here" in result
        assert "<p>" not in result
        assert "<a" not in result

    def test_html_fallback_decodes_entities(self):
        html_content = "<p>AT&amp;T &lt;data&gt;</p>"
        payload = {
            "mimeType": "text/html",
            "body": {"data": self._encoded(html_content)},
            "parts": [],
        }
        result = _extract_body(payload)
        assert "AT&T" in result
        assert "&amp;" not in result

    def test_empty_payload_returns_empty_string(self):
        assert _extract_body({}) == ""

    def test_multipart_mixed_concatenates_parts(self):
        part1 = "First part content."
        part2 = "Second part content."
        payload = {
            "mimeType": "multipart/mixed",
            "body": {},
            "parts": [
                {"mimeType": "text/plain", "body": {"data": self._encoded(part1)}, "parts": []},
                {"mimeType": "text/plain", "body": {"data": self._encoded(part2)}, "parts": []},
            ],
        }
        result = _extract_body(payload)
        assert "First part" in result
        assert "Second part" in result
