"""Unit tests for reply_monitor/fetcher.py — Gmail lookup and search fallback."""

from unittest.mock import MagicMock


from reply_monitor.fetcher import (
    _extract_body,
    _fetch_gdpr_from_domain,
    _find_attachment_parts,
    _get_header,
    _is_gdpr_relevant,
    _parse_message,
    fetch_replies_for_sar,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gmail_msg(
    msg_id="msg001",
    from_addr="privacy@example.com",
    subject="Re: SAR",
    snippet="thank you",
    date_str="Mon, 17 Mar 2026 10:00:00 +0000",
    parts=None,
):
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
        sent_record = {
            "gmail_thread_id": "thread001",
            "to_email": "p@example.com",
            "sent_at": "2026-03-16T00:00:00",
        }
        existing = {"reply001"}  # already stored

        reply_msg = _make_gmail_msg(msg_id="reply001")
        service = MagicMock()
        service.users().threads().get().execute.return_value = {"messages": [reply_msg]}
        # When thread dedup returns nothing, search fallback runs — must return a
        # real dict (not a MagicMock) so _paginated_search's loop can terminate.
        service.users().messages().list().execute.return_value = {"messages": []}

        results = fetch_replies_for_sar(
            service, sent_record, existing_reply_ids=existing
        )
        assert len(results) == 0  # already seen

    def test_thread_returns_empty_on_api_error(self):
        sent_record = {
            "gmail_thread_id": "thread001",
            "to_email": "p@example.com",
            "sent_at": "2026-03-16T00:00:00",
        }
        service = MagicMock()
        service.users().threads().get().execute.side_effect = OSError("API error")
        service.users().messages().list().execute.return_value = {"messages": []}

        results = fetch_replies_for_sar(service, sent_record)
        assert results == []

    def test_own_email_filtered_out(self):
        sent_record = {
            "gmail_thread_id": "t1",
            "to_email": "p@example.com",
            "sent_at": "2026-03-16T00:00:00",
        }
        own_msg = _make_gmail_msg(msg_id="own001", from_addr="trader@gmail.com")

        service = MagicMock()
        service.users().threads().get().execute.return_value = {"messages": [own_msg]}
        # Search fallback runs when thread returns empty — must return a real dict
        service.users().messages().list().execute.return_value = {"messages": []}

        results = fetch_replies_for_sar(
            service, sent_record, user_email="trader@gmail.com"
        )
        assert results == []


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
        reply_msg = _make_gmail_msg(
            msg_id="search001", from_addr="privacy@glassdoor.com"
        )

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
        sent_record = {
            "gmail_thread_id": "",
            "to_email": "",
            "sent_at": "2026-03-16T00:00:00",
        }
        service = MagicMock()
        results = fetch_replies_for_sar(service, sent_record)
        assert results == []
        service.users().messages().list.assert_not_called()

    def test_search_deduplicates_by_id(self):
        sent_record = {
            "gmail_thread_id": "",
            "to_email": "p@example.com",
            "sent_at": "2026-03-16T00:00:00",
        }
        existing = {"already001"}
        reply_msg = _make_gmail_msg(msg_id="already001")

        service = MagicMock()
        service.users().messages().list().execute.return_value = {
            "messages": [{"id": "already001"}]
        }
        service.users().messages().get().execute.return_value = reply_msg

        results = fetch_replies_for_sar(
            service, sent_record, existing_reply_ids=existing
        )
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
            parts=[
                {
                    "filename": "data.zip",
                    "mimeType": "application/zip",
                    "body": {"attachmentId": "att001", "size": 2048},
                    "parts": [],
                }
            ]
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
                {
                    "mimeType": "text/plain",
                    "body": {"data": self._encoded(part1)},
                    "parts": [],
                },
                {
                    "mimeType": "text/plain",
                    "body": {"data": self._encoded(part2)},
                    "parts": [],
                },
            ],
        }
        result = _extract_body(payload)
        assert "First part" in result
        assert "Second part" in result


# ---------------------------------------------------------------------------
# GDPR keyword search tests
# ---------------------------------------------------------------------------


class TestFetchGdprFromDomain:
    def test_finds_out_of_thread_gdpr_reply(self):
        """GDPR keyword search catches replies from ticket systems that create
        new threads instead of replying in the original SAR thread."""
        sent_record = {
            "to_email": "privacy@zendesk.com",
            "sent_at": "2026-03-16T00:00:00",
        }
        gdpr_msg = _make_gmail_msg(
            msg_id="gdpr001",
            from_addr="support@zendesk.com",
            subject="Your data subject request [#12345]",
            snippet="We have received your GDPR request",
        )

        service = MagicMock()
        service.users().messages().list().execute.return_value = {
            "messages": [{"id": "gdpr001"}]
        }
        service.users().messages().get().execute.return_value = gdpr_msg

        results = _fetch_gdpr_from_domain(
            service, sent_record, user_email="me@gmail.com", existing_ids=set()
        )
        assert len(results) == 1
        assert results[0]["id"] == "gdpr001"

    def test_deduplicates_with_thread_results(self):
        """Messages already seen via thread lookup are skipped."""
        sent_record = {
            "to_email": "privacy@example.com",
            "sent_at": "2026-03-16T00:00:00",
        }

        service = MagicMock()
        service.users().messages().list().execute.return_value = {
            "messages": [{"id": "already_seen"}]
        }

        results = _fetch_gdpr_from_domain(
            service,
            sent_record,
            user_email="me@gmail.com",
            existing_ids={"already_seen"},
        )
        assert results == []

    def test_skips_empty_to_email(self):
        """No search when to_email is empty (portal/postal SAR)."""
        sent_record = {"to_email": "", "sent_at": "2026-03-16T00:00:00"}
        service = MagicMock()

        results = _fetch_gdpr_from_domain(
            service, sent_record, user_email="me@gmail.com", existing_ids=set()
        )
        assert results == []
        service.users().messages().list.assert_not_called()

    def test_filters_own_email(self):
        """Messages from the user's own address are excluded."""
        sent_record = {
            "to_email": "privacy@example.com",
            "sent_at": "2026-03-16T00:00:00",
        }
        own_msg = _make_gmail_msg(
            msg_id="own001",
            from_addr="me@gmail.com",
            subject="Re: GDPR request",
            snippet="Following up on my data request",
        )

        service = MagicMock()
        service.users().messages().list().execute.return_value = {
            "messages": [{"id": "own001"}]
        }
        service.users().messages().get().execute.return_value = own_msg

        results = _fetch_gdpr_from_domain(
            service, sent_record, user_email="me@gmail.com", existing_ids=set()
        )
        assert results == []

    def test_rejects_non_gdpr_content(self):
        """Messages from the domain that aren't GDPR-relevant are filtered out
        by _is_gdpr_relevant()."""
        sent_record = {
            "to_email": "privacy@example.com",
            "sent_at": "2026-03-16T00:00:00",
        }
        marketing_msg = _make_gmail_msg(
            msg_id="mkt001",
            from_addr="news@example.com",
            subject="Weekly Newsletter",
            snippet="Check out our latest deals and promotions!",
        )

        service = MagicMock()
        service.users().messages().list().execute.return_value = {
            "messages": [{"id": "mkt001"}]
        }
        service.users().messages().get().execute.return_value = marketing_msg

        results = _fetch_gdpr_from_domain(
            service, sent_record, user_email="me@gmail.com", existing_ids=set()
        )
        assert results == []

    def test_api_error_on_message_fetch_continues(self):
        """An API error fetching one message doesn't abort the whole search."""
        sent_record = {
            "to_email": "privacy@example.com",
            "sent_at": "2026-03-16T00:00:00",
        }
        gdpr_msg = _make_gmail_msg(
            msg_id="good001",
            from_addr="dpo@example.com",
            subject="Your SAR request",
            snippet="We received your data subject access request",
        )

        service = MagicMock()
        service.users().messages().list().execute.return_value = {
            "messages": [{"id": "bad001"}, {"id": "good001"}]
        }

        def get_msg(userId, id, format):
            mock = MagicMock()
            if id == "bad001":
                mock.execute.side_effect = OSError("timeout")
            else:
                mock.execute.return_value = gdpr_msg
            return mock

        service.users().messages().get.side_effect = get_msg

        results = _fetch_gdpr_from_domain(
            service, sent_record, user_email="me@gmail.com", existing_ids=set()
        )
        assert len(results) == 1
        assert results[0]["id"] == "good001"


# ---------------------------------------------------------------------------
# _is_gdpr_relevant tests
# ---------------------------------------------------------------------------


class TestIsGdprRelevant:
    def test_matches_gdpr_keyword(self):
        msg = {"subject": "Re: GDPR request", "snippet": "Thank you", "body": ""}
        assert _is_gdpr_relevant(msg) is True

    def test_matches_subject_access(self):
        msg = {"subject": "Subject Access Request #123", "snippet": "", "body": ""}
        assert _is_gdpr_relevant(msg) is True

    def test_matches_data_subject_in_body(self):
        msg = {
            "subject": "Request update",
            "snippet": "",
            "body": "Your data subject request has been processed",
        }
        assert _is_gdpr_relevant(msg) is True

    def test_matches_right_to_erasure(self):
        msg = {"subject": "", "snippet": "right to erasure confirmed", "body": ""}
        assert _is_gdpr_relevant(msg) is True

    def test_matches_subprocessor(self):
        msg = {"subject": "Sub-processor list", "snippet": "", "body": ""}
        assert _is_gdpr_relevant(msg) is True

    def test_matches_data_portability(self):
        msg = {"subject": "", "snippet": "", "body": "data portability export ready"}
        assert _is_gdpr_relevant(msg) is True

    def test_matches_article_references(self):
        msg = {"subject": "Article 15 request", "snippet": "", "body": ""}
        assert _is_gdpr_relevant(msg) is True

    def test_rejects_non_gdpr_content(self):
        msg = {
            "subject": "Weekly newsletter",
            "snippet": "Check out our latest deals",
            "body": "Shop now for great savings on electronics",
        }
        assert _is_gdpr_relevant(msg) is False

    def test_case_insensitive(self):
        msg = {"subject": "YOUR DATA PROTECTION request", "snippet": "", "body": ""}
        assert _is_gdpr_relevant(msg) is True
