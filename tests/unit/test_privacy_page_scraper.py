"""Unit tests for contact_resolver/privacy_page_scraper.py."""

from unittest.mock import MagicMock

import pytest

from contact_resolver.privacy_page_scraper import (
    _classify_emails,
    _extract_emails,
    _extract_portal_url,
    _strip_html,
    scrape_privacy_page,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_http(status_code: int = 200, text: str = "") -> MagicMock:
    """Build a mock http_get that always returns the same response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    mock = MagicMock(return_value=resp)
    return mock


def _make_http_sequence(*pairs: tuple[int, str]) -> MagicMock:
    """Build a mock http_get with a sequence of (status_code, text) responses."""
    responses = []
    for status, text in pairs:
        resp = MagicMock()
        resp.status_code = status
        resp.text = text
        responses.append(resp)
    return MagicMock(side_effect=responses)


# ---------------------------------------------------------------------------
# _strip_html
# ---------------------------------------------------------------------------


def test_strip_html_removes_tags() -> None:
    assert _strip_html("<p>Hello</p>") == " Hello "


def test_strip_html_preserves_text() -> None:
    result = _strip_html("<a href='x'>privacy@example.com</a>")
    assert "privacy@example.com" in result


def test_strip_html_empty_string() -> None:
    assert _strip_html("") == ""


# ---------------------------------------------------------------------------
# _extract_emails
# ---------------------------------------------------------------------------


def test_extract_emails_finds_privacy_email() -> None:
    text = "Contact us at privacy@example.com for data requests."
    assert _extract_emails(text) == ["privacy@example.com"]


def test_extract_emails_finds_dpo_email() -> None:
    text = "Our DPO can be reached at dpo@example.com"
    assert _extract_emails(text) == ["dpo@example.com"]


def test_extract_emails_finds_gdpr_email() -> None:
    assert _extract_emails("gdpr@example.com") == ["gdpr@example.com"]


def test_extract_emails_finds_legal_email() -> None:
    assert _extract_emails("legal@example.com") == ["legal@example.com"]


def test_extract_emails_finds_dataprotection_email() -> None:
    assert _extract_emails("dataprotection@example.com") == [
        "dataprotection@example.com"
    ]


def test_extract_emails_finds_data_protection_hyphen() -> None:
    assert _extract_emails("data-protection@example.com") == [
        "data-protection@example.com"
    ]


def test_extract_emails_finds_dataprivacy_email() -> None:
    assert _extract_emails("dataprivacy@example.com") == ["dataprivacy@example.com"]


def test_extract_emails_ignores_non_privacy_addresses() -> None:
    text = "support@example.com or sales@example.com"
    assert _extract_emails(text) == []


def test_extract_emails_deduplicates() -> None:
    text = "privacy@example.com and privacy@example.com again"
    assert len(_extract_emails(text)) == 1


def test_extract_emails_multiple_distinct() -> None:
    text = "dpo@example.com and privacy@example.com"
    emails = _extract_emails(text)
    assert len(emails) == 2
    assert "dpo@example.com" in emails
    assert "privacy@example.com" in emails


# ---------------------------------------------------------------------------
# _extract_portal_url
# ---------------------------------------------------------------------------


def test_extract_portal_url_dsar() -> None:
    text = "Submit a request at https://example.com/dsar"
    assert _extract_portal_url(text) == "https://example.com/dsar"


def test_extract_portal_url_privacy_request() -> None:
    text = "https://example.com/privacy-request/new"
    assert _extract_portal_url(text) == "https://example.com/privacy-request/new"


def test_extract_portal_url_data_request() -> None:
    assert "https://example.com/data-request" in _extract_portal_url(
        "see https://example.com/data-request"
    )


def test_extract_portal_url_gdpr_request() -> None:
    assert _extract_portal_url("https://example.com/gdpr-request") != ""


def test_extract_portal_url_subject_access() -> None:
    assert _extract_portal_url("https://example.com/subject-access") != ""


def test_extract_portal_url_no_match_returns_empty() -> None:
    assert _extract_portal_url("No special links here.") == ""


# ---------------------------------------------------------------------------
# _classify_emails
# ---------------------------------------------------------------------------


def test_classify_emails_dpo_goes_to_dpo_field() -> None:
    dpo, privacy = _classify_emails(["dpo@example.com"])
    assert dpo == "dpo@example.com"
    assert privacy == ""


def test_classify_emails_gdpr_goes_to_dpo_field() -> None:
    dpo, _ = _classify_emails(["gdpr@example.com"])
    assert dpo == "gdpr@example.com"


def test_classify_emails_dataprotection_goes_to_dpo_field() -> None:
    dpo, _ = _classify_emails(["dataprotection@example.com"])
    assert dpo == "dataprotection@example.com"


def test_classify_emails_privacy_goes_to_privacy_field() -> None:
    dpo, privacy = _classify_emails(["privacy@example.com"])
    assert dpo == ""
    assert privacy == "privacy@example.com"


def test_classify_emails_split_when_both_present() -> None:
    dpo, privacy = _classify_emails(["dpo@example.com", "privacy@example.com"])
    assert dpo == "dpo@example.com"
    assert privacy == "privacy@example.com"


def test_classify_emails_empty_list() -> None:
    assert _classify_emails([]) == ("", "")


# ---------------------------------------------------------------------------
# scrape_privacy_page — successful scrapes
# ---------------------------------------------------------------------------

_PRIVACY_HTML = "<html><body>Contact privacy@acme.com for data requests.</body></html>"
_PORTAL_HTML = "<html><body>Submit at https://acme.com/dsar</body></html>"
_DPO_HTML = "<html><body>DPO: dpo@acme.com</body></html>"


def test_scrape_returns_record_with_privacy_email() -> None:
    mock_http = _make_http(200, _PRIVACY_HTML)
    result = scrape_privacy_page("acme.com", "Acme", http_get=mock_http)

    assert result is not None
    assert result.source == "privacy_scrape"
    assert result.source_confidence == "medium"
    assert result.contact.privacy_email == "privacy@acme.com"


def test_scrape_returns_record_with_portal_url() -> None:
    mock_http = _make_http(200, _PORTAL_HTML)
    result = scrape_privacy_page("acme.com", "Acme", http_get=mock_http)

    assert result is not None
    assert result.contact.gdpr_portal_url == "https://acme.com/dsar"
    assert result.flags.portal_only is True
    assert result.flags.email_accepted is False
    assert result.contact.preferred_method == "portal"


def test_scrape_dpo_email_classified_correctly() -> None:
    mock_http = _make_http(200, _DPO_HTML)
    result = scrape_privacy_page("acme.com", "Acme", http_get=mock_http)

    assert result is not None
    assert result.contact.dpo_email == "dpo@acme.com"
    assert result.contact.privacy_email == ""


def test_scrape_sets_company_name() -> None:
    mock_http = _make_http(200, _PRIVACY_HTML)
    result = scrape_privacy_page("acme.com", "Acme Corp", http_get=mock_http)

    assert result is not None
    assert result.company_name == "Acme Corp"


def test_scrape_email_preferred_over_portal() -> None:
    html = "<body>privacy@acme.com and https://acme.com/dsar</body>"
    mock_http = _make_http(200, html)
    result = scrape_privacy_page("acme.com", "Acme", http_get=mock_http)

    assert result is not None
    assert result.contact.preferred_method == "email"
    assert result.flags.portal_only is False
    assert result.flags.email_accepted is True


# ---------------------------------------------------------------------------
# scrape_privacy_page — fallthrough / failure cases
# ---------------------------------------------------------------------------


def test_scrape_tries_next_url_on_404() -> None:
    """First URL returns 404; second returns 200 with email."""
    mock_http = _make_http_sequence(
        (404, ""),
        (200, _PRIVACY_HTML),
    )
    result = scrape_privacy_page("acme.com", "Acme", http_get=mock_http)

    assert result is not None
    assert result.contact.privacy_email == "privacy@acme.com"
    assert mock_http.call_count == 2


def test_scrape_returns_none_when_no_contacts_on_page() -> None:
    html = "<html><body>We value your privacy. Contact us at support@acme.com.</body></html>"
    mock_http = _make_http(200, html)
    result = scrape_privacy_page("acme.com", "Acme", http_get=mock_http)

    assert result is None


def test_scrape_returns_none_when_all_urls_fail() -> None:
    mock_http = _make_http_sequence(
        (404, ""),
        (404, ""),
        (404, ""),
        (404, ""),
    )
    result = scrape_privacy_page("acme.com", "Acme", http_get=mock_http)

    assert result is None


def test_scrape_handles_network_error_on_first_url() -> None:
    """Connection error on first URL; second URL succeeds."""
    success_resp = MagicMock()
    success_resp.status_code = 200
    success_resp.text = _PRIVACY_HTML

    mock_http = MagicMock(side_effect=[Exception("timeout"), success_resp])
    result = scrape_privacy_page("acme.com", "Acme", http_get=mock_http)

    assert result is not None
    assert result.contact.privacy_email == "privacy@acme.com"


def test_scrape_returns_none_when_all_urls_raise_exceptions() -> None:
    mock_http = MagicMock(side_effect=Exception("Connection refused"))
    result = scrape_privacy_page("acme.com", "Acme", http_get=mock_http)

    assert result is None


# ---------------------------------------------------------------------------
# scrape_privacy_page — verbose output
# ---------------------------------------------------------------------------


def test_scrape_verbose_prints_url_and_found_email(
    capsys: pytest.CaptureFixture,
) -> None:
    mock_http = _make_http(200, _PRIVACY_HTML)
    scrape_privacy_page("acme.com", "Acme", http_get=mock_http, verbose=True)

    out = capsys.readouterr().out
    assert "[PRIVACY PAGE]" in out
    assert "acme.com" in out
    assert "trying" in out


def test_scrape_verbose_prints_found_email(
    capsys: pytest.CaptureFixture,
) -> None:
    mock_http = _make_http(200, _PRIVACY_HTML)
    scrape_privacy_page("acme.com", "Acme", http_get=mock_http, verbose=True)

    out = capsys.readouterr().out
    assert "found email" in out


def test_scrape_verbose_prints_found_portal(
    capsys: pytest.CaptureFixture,
) -> None:
    mock_http = _make_http(200, _PORTAL_HTML)
    scrape_privacy_page("acme.com", "Acme", http_get=mock_http, verbose=True)

    out = capsys.readouterr().out
    assert "found portal" in out


def test_scrape_verbose_prints_status_code_on_failure(
    capsys: pytest.CaptureFixture,
) -> None:
    mock_http = _make_http_sequence((404, ""), (404, ""), (404, ""), (404, ""))
    scrape_privacy_page("acme.com", "Acme", http_get=mock_http, verbose=True)

    out = capsys.readouterr().out
    assert "404" in out


def test_scrape_verbose_silent_when_false() -> None:
    """verbose=False (default) must not print anything."""
    import io
    import sys

    mock_http = _make_http(200, _PRIVACY_HTML)
    captured = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured
    try:
        scrape_privacy_page("acme.com", "Acme", http_get=mock_http, verbose=False)
    finally:
        sys.stdout = old_stdout

    assert captured.getvalue() == ""
