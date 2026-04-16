"""Unit tests for reply_monitor/url_verifier.py — URL classification."""

from unittest.mock import patch, MagicMock


from reply_monitor.url_verifier import verify, CLASSIFICATION


class TestVerifyDeadLink:
    def test_timeout_returns_dead_link(self):
        import requests as req_lib

        with patch(
            "reply_monitor.url_verifier.requests.get", side_effect=req_lib.Timeout
        ):
            result = verify("https://dead.example.com/privacy")
        assert result["classification"] == CLASSIFICATION.DEAD_LINK
        assert "timeout" in (result["error"] or "").lower()

    def test_404_returns_dead_link(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"
        mock_resp.headers = {}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify("https://example.com/gone")
        assert result["classification"] == CLASSIFICATION.DEAD_LINK

    def test_500_returns_dead_link(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_resp.headers = {}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify("https://example.com/broken")
        assert result["classification"] == CLASSIFICATION.DEAD_LINK


class TestVerifySurvey:
    def test_survey_url_pattern(self):
        """Survey URL path pattern detected before HTTP fetch."""
        result = verify(
            "https://society.zendesk.com/hc/en-us/survey_responses/01KM?access_token=abc"
        )
        assert result["classification"] == CLASSIFICATION.SURVEY

    def test_satisfaction_content(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com/feedback"
        mock_resp.text = "<html><title>Feedback</title><body>Please rate the support you received</body></html>"
        mock_resp.headers = {"content-type": "text/html"}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify("https://example.com/rate")
        assert result["classification"] == CLASSIFICATION.SURVEY


class TestVerifyHelpCenter:
    def test_zendesk_ticket_page(self):
        """Zendesk ticket page path detected before HTTP fetch."""
        result = verify("https://society.zendesk.com/hc/en-us/requests/649929")
        assert result["classification"] == CLASSIFICATION.HELP_CENTER

    def test_help_center_article(self):
        """Help center article path detected before HTTP fetch."""
        result = verify("https://help.example.com/hc/en-us/articles/123456")
        assert result["classification"] == CLASSIFICATION.HELP_CENTER


class TestVerifyLoginRequired:
    def test_login_required_platform(self):
        """URLs on login-required domains detected without HTTP fetch."""
        result = verify("https://myaccount.google.com/privacy")
        assert result["classification"] == CLASSIFICATION.LOGIN_REQUIRED

    def test_apple_login_required(self):
        result = verify("https://privacy.apple.com/account")
        assert result["classification"] == CLASSIFICATION.LOGIN_REQUIRED


class TestVerifyGDPRPortal:
    def test_onetrust_portal(self):
        """OneTrust detected via platform_hints fast path."""
        result = verify("https://privacyportal.onetrust.com/webform/abc")
        assert result["classification"] == CLASSIFICATION.GDPR_PORTAL

    def test_generic_form_with_submit(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com/privacy-request"
        mock_resp.text = (
            "<html><title>Data Request Form</title>"
            "<body><form action='/submit'><input type='email' name='email'>"
            "<select name='request_type'><option>Access my data</option></select>"
            "<button>Submit</button></form></body></html>"
        )
        mock_resp.headers = {"content-type": "text/html"}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify("https://example.com/privacy-request")
        assert result["classification"] == CLASSIFICATION.GDPR_PORTAL


class TestVerifyKetchPortal:
    def test_ketch_html_signature_classified_as_portal(self):
        """Page with Ketch JS signature is a GDPR portal even without visible form."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://zendesk.es/"
        mock_resp.text = (
            '<html><head><script src="https://cdn.ketch.com/ketch-tag.js"></script></head>'
            "<body><h1>Privacy Center</h1></body></html>"
        )
        mock_resp.headers = {"content-type": "text/html"}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify("https://zendesk.es/")
        assert result["classification"] == CLASSIFICATION.GDPR_PORTAL

    def test_ketch_window_semaphore_signature(self):
        """window.semaphore is a Ketch indicator."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://privacy.company.com/"
        mock_resp.text = (
            "<html><body><script>window.semaphore = window.semaphore || [];</script>"
            "<div>Privacy Center</div></body></html>"
        )
        mock_resp.headers = {"content-type": "text/html"}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify("https://privacy.company.com/")
        assert result["classification"] == CLASSIFICATION.GDPR_PORTAL

    def test_non_ketch_page_without_form_stays_unknown(self):
        """A page without Ketch signatures and no form remains unknown."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com/privacy"
        mock_resp.text = "<html><body><p>Our privacy policy</p></body></html>"
        mock_resp.headers = {"content-type": "text/html"}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify("https://example.com/privacy")
        assert result["classification"] == CLASSIFICATION.UNKNOWN


class TestVerifyIfNeeded:
    def test_already_verified_within_ttl(self):
        """If existing verification is fresh, return it without re-checking."""
        from reply_monitor.url_verifier import verify_if_needed
        from datetime import datetime, timezone

        existing = {
            "url": "https://example.com/portal",
            "classification": "gdpr_portal",
            "checked_at": "2026-04-13T10:00:00Z",
            "error": None,
            "page_title": "Privacy Portal",
        }
        now = datetime(2026, 4, 13, 10, 5, 0, tzinfo=timezone.utc)
        result = verify_if_needed(
            "https://example.com/portal", existing=existing, now=now
        )
        assert result is existing  # same object, no re-fetch

    def test_stale_verification_re_checks(self):
        """If existing verification is older than TTL (7 days), re-verify."""
        from reply_monitor.url_verifier import verify_if_needed
        from datetime import datetime, timezone

        existing = {
            "url": "https://example.com/portal",
            "classification": "unknown",
            "checked_at": "2026-04-01T10:00:00Z",
            "error": None,
            "page_title": "",
        }
        now = datetime(2026, 4, 13, 10, 0, 0, tzinfo=timezone.utc)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.url = "https://example.com/portal"
        mock_resp.text = "<html><title>Portal</title><body><form><input name='email'><button>Submit</button></form></body></html>"
        mock_resp.headers = {"content-type": "text/html"}
        with patch("reply_monitor.url_verifier.requests.get", return_value=mock_resp):
            result = verify_if_needed(
                "https://example.com/portal", existing=existing, now=now
            )
        assert result is not existing  # fresh result
