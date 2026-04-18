"""Unit tests for portal_submitter package."""

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch


from portal_submitter.models import PortalResult, CaptchaChallenge
from portal_submitter.platform_hints import detect_platform, otp_sender_hints
from portal_submitter.form_analyzer import analyze_form, build_user_data
from portal_submitter.form_filler import fill_and_submit, detect_captcha
from portal_submitter.captcha_relay import request_solve, poll_solution, _challenge_path
from portal_submitter.otp_handler import extract_otp_from_message, wait_for_otp
from portal_submitter.submitter import submit_portal
from contact_resolver.models import PortalFieldMapping, PortalFormField


class TestPortalResult:
    def test_success_result(self):
        r = PortalResult(success=True, confirmation_ref="TICKET-123")
        assert r.success is True
        assert r.needs_manual is False
        assert r.confirmation_ref == "TICKET-123"

    def test_manual_result(self):
        r = PortalResult(success=False, needs_manual=True, error="login_required")
        assert r.success is False
        assert r.needs_manual is True

    def test_default_values(self):
        r = PortalResult()
        assert r.success is False
        assert r.needs_manual is False
        assert r.confirmation_ref == ""
        assert r.screenshot_path == ""
        assert r.error == ""
        assert r.portal_status == ""


class TestCaptchaChallenge:
    def test_creation(self):
        c = CaptchaChallenge(
            domain="example.com", portal_url="https://example.com/privacy"
        )
        assert c.status == "pending"
        assert c.solution == ""
        assert c.domain == "example.com"


class TestPlatformDetection:
    def test_onetrust_by_domain(self):
        assert (
            detect_platform("https://privacyportal.onetrust.com/webform/abc-123")
            == "onetrust"
        )

    def test_onetrust_by_privacyportal(self):
        assert (
            detect_platform("https://company.my.onetrust.com/webform/xyz") == "onetrust"
        )

    def test_trustarc(self):
        assert (
            detect_platform("https://submit-irm.trustarc.com/services/validation/abc")
            == "trustarc"
        )

    def test_trustarc_by_keyword(self):
        assert detect_platform("https://privacy.trustarc.com/form/abc") == "trustarc"

    def test_salesforce(self):
        assert (
            detect_platform("https://help.glassdoor.com/s/privacyrequest")
            == "salesforce"
        )

    def test_login_required_account_portal(self):
        assert (
            detect_platform("https://myaccount.google.com/data-and-privacy")
            == "login_required"
        )

    def test_unknown(self):
        assert detect_platform("https://example.com/privacy-request") == "unknown"

    def test_empty_url(self):
        assert detect_platform("") == "unknown"

    def test_ketch_by_domain(self):
        assert detect_platform("https://privacy.ketch.com/portal/abc") == "ketch"

    def test_ketch_subdomain(self):
        assert detect_platform("https://zendesk.ketch.com/") == "ketch"

    def test_ketch_branded_domain_with_html(self):
        html = '<script src="https://cdn.ketch.com/ketch-tag.js"></script>'
        assert detect_platform("https://zendesk.es/", html=html) == "ketch"

    def test_ketch_html_window_semaphore(self):
        html = "<script>window.semaphore = window.semaphore || [];</script>"
        assert detect_platform("https://company.com/privacy", html=html) == "ketch"

    def test_non_ketch_html_returns_unknown(self):
        html = "<html><body>Regular page</body></html>"
        assert detect_platform("https://example.com/privacy", html=html) == "unknown"

    def test_ketch_otp_hints(self):
        hints = otp_sender_hints("ketch")
        assert "noreply@ketch.com" in hints


class TestOTPSenderHints:
    def test_onetrust_hints(self):
        hints = otp_sender_hints("onetrust")
        assert "noreply@onetrust.com" in hints

    def test_trustarc_hints(self):
        hints = otp_sender_hints("trustarc")
        assert any("trustarc" in h for h in hints)

    def test_unknown_returns_empty(self):
        assert otp_sender_hints("unknown") == []


class TestBuildUserData:
    @patch("portal_submitter.form_analyzer.settings")
    def test_name_splitting(self, mock_settings):
        mock_settings.user_full_name = "Jane Doe"
        mock_settings.user_email = "jane@example.com"
        mock_settings.user_address_country = "United Kingdom"

        letter = MagicMock()
        letter.body = "SAR body text"

        data = build_user_data(letter)
        assert data["first_name"] == "Jane"
        assert data["last_name"] == "Doe"
        assert data["email"] == "jane@example.com"
        assert data["country"] == "United Kingdom"
        assert data["request_type"] == "Access my personal data"
        assert data["description"] == "SAR body text"

    @patch("portal_submitter.form_analyzer.settings")
    def test_single_name(self, mock_settings):
        mock_settings.user_full_name = "Cher"
        mock_settings.user_email = "cher@example.com"
        mock_settings.user_address_country = "US"

        letter = MagicMock()
        letter.body = ""

        data = build_user_data(letter)
        assert data["first_name"] == "Cher"
        assert data["last_name"] == ""


class TestAnalyzeForm:
    def test_parses_llm_response(self):
        fake_aria_snapshot = (
            '- textbox "First Name"\n'
            '- textbox "Email Address"\n'
            '- combobox "Country"\n'
            '- button "Submit"'
        )
        fake_page = MagicMock()
        fake_page.locator.return_value.aria_snapshot.return_value = fake_aria_snapshot

        llm_response = json.dumps(
            {
                "fields": [
                    {
                        "name": "First Name",
                        "value_key": "first_name",
                        "role": "textbox",
                    },
                    {"name": "Email Address", "value_key": "email", "role": "textbox"},
                    {"name": "Country", "value_key": "country", "role": "combobox"},
                ],
                "submit_button": "Submit",
            }
        )

        def mock_llm_call(prompt: str) -> str:
            return llm_response

        mapping = analyze_form(fake_page, llm_call=mock_llm_call)
        assert len(mapping.fields) == 3
        assert mapping.fields[0].name == "First Name"
        assert mapping.fields[0].value_key == "first_name"
        assert mapping.submit_button == "Submit"

    def test_uses_cached_mapping(self):
        cached = PortalFieldMapping(
            cached_at=date.today().isoformat(),
            platform="onetrust",
            fields=[PortalFormField(name="Email", value_key="email", role="textbox")],
            submit_button="Submit",
        )

        call_count = 0

        def mock_llm_call(prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            return "{}"

        fake_page = MagicMock()
        mapping = analyze_form(fake_page, llm_call=mock_llm_call, cached_mapping=cached)
        assert call_count == 0
        assert mapping.fields[0].name == "Email"

    def test_expired_cache_triggers_llm(self):
        old_date = (date.today() - timedelta(days=91)).isoformat()
        cached = PortalFieldMapping(
            cached_at=old_date,
            platform="onetrust",
            fields=[PortalFormField(name="Email", value_key="email", role="textbox")],
            submit_button="Submit",
        )

        fake_aria_snapshot = '- textbox "Email"\n- button "Submit"'
        fake_page = MagicMock()
        fake_page.locator.return_value.aria_snapshot.return_value = fake_aria_snapshot

        llm_response = json.dumps(
            {
                "fields": [{"name": "Email", "value_key": "email", "role": "textbox"}],
                "submit_button": "Submit",
            }
        )

        call_count = 0

        def mock_llm_call(prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            return llm_response

        _mapping = analyze_form(
            fake_page, llm_call=mock_llm_call, cached_mapping=cached
        )
        assert call_count == 1


class TestDetectCaptcha:
    def test_detects_recaptcha_iframe(self):
        page = MagicMock()
        page.query_selector.side_effect = lambda sel: (
            MagicMock() if "recaptcha" in sel else None
        )
        assert detect_captcha(page) is True

    def test_detects_hcaptcha(self):
        page = MagicMock()
        page.query_selector.side_effect = lambda sel: (
            MagicMock() if "hcaptcha" in sel else None
        )
        assert detect_captcha(page) is True

    def test_detects_sitekey(self):
        page = MagicMock()
        page.query_selector.side_effect = lambda sel: (
            MagicMock() if "data-sitekey" in sel else None
        )
        assert detect_captcha(page) is True

    def test_no_captcha(self):
        page = MagicMock()
        page.query_selector.return_value = None
        assert detect_captcha(page) is False


class TestFillAndSubmit:
    def test_fills_textbox_fields(self):
        page = MagicMock()
        page.query_selector.return_value = None  # no CAPTCHA

        mapping = PortalFieldMapping(
            cached_at=date.today().isoformat(),
            fields=[
                PortalFormField(
                    name="First Name", value_key="first_name", role="textbox"
                ),
                PortalFormField(name="Email", value_key="email", role="textbox"),
            ],
            submit_button="Submit",
        )
        user_data = {"first_name": "Jane", "email": "jane@test.com"}

        fill_and_submit(page, mapping, user_data, click_submit=False)

        # Verify get_by_role was called for each field
        assert page.get_by_role.call_count >= 2

    def test_returns_captcha_detected(self):
        """When CAPTCHA is detected, fill_and_submit returns before clicking submit."""
        page = MagicMock()
        # Simulate CAPTCHA present
        page.query_selector.side_effect = lambda sel: (
            MagicMock() if "recaptcha" in sel else None
        )

        mapping = PortalFieldMapping(
            cached_at=date.today().isoformat(),
            fields=[PortalFormField(name="Email", value_key="email", role="textbox")],
            submit_button="Submit",
        )
        user_data = {"email": "jane@test.com"}

        result = fill_and_submit(page, mapping, user_data)
        assert result["captcha_detected"] is True


class TestCaptchaRelay:
    def test_request_solve_creates_files(self, tmp_path):
        screenshot_bytes = b"\x89PNG fake image data"
        challenge = request_solve(
            domain="example.com",
            portal_url="https://example.com/privacy",
            screenshot_bytes=screenshot_bytes,
            base_dir=tmp_path,
        )
        assert challenge.status == "pending"
        assert (tmp_path / "example.com.png").exists()
        assert (tmp_path / "example.com.json").exists()

    def test_poll_solution_returns_answer(self, tmp_path):
        challenge_data = {
            "domain": "example.com",
            "portal_url": "https://example.com/privacy",
            "status": "solved",
            "solution": "abc123",
        }
        (tmp_path / "example.com.json").write_text(json.dumps(challenge_data))

        solution = poll_solution(
            "example.com", base_dir=tmp_path, timeout=1, poll_interval=0.1
        )
        assert solution == "abc123"

    def test_poll_solution_timeout(self, tmp_path):
        challenge_data = {"domain": "example.com", "status": "pending", "solution": ""}
        (tmp_path / "example.com.json").write_text(json.dumps(challenge_data))

        solution = poll_solution(
            "example.com", base_dir=tmp_path, timeout=0.3, poll_interval=0.1
        )
        assert solution is None

    def test_challenge_path(self, tmp_path):
        p = _challenge_path("example.com", tmp_path)
        assert p == tmp_path / "example.com.json"


class TestExtractOTP:
    def test_extracts_confirmation_url(self):
        body = "Please confirm your request: https://requests.hrtechprivacy.com/confirm/abc-123"
        result = extract_otp_from_message(body)
        assert result["type"] == "url"
        assert "hrtechprivacy.com/confirm" in result["value"]

    def test_extracts_verify_url(self):
        body = "Click here to verify: https://privacy.example.com/verify?token=xyz"
        result = extract_otp_from_message(body)
        assert result["type"] == "url"
        assert "verify" in result["value"]

    def test_extracts_six_digit_code(self):
        body = "Your verification code is 847291. Enter this code to continue."
        result = extract_otp_from_message(body)
        assert result["type"] == "code"
        assert result["value"] == "847291"

    def test_no_otp_found(self):
        body = "Thank you for contacting us. We will review your request."
        result = extract_otp_from_message(body)
        assert result is None

    def test_url_preferred_over_code(self):
        body = "Your code is 123456. Or click https://example.com/confirm/token-abc"
        result = extract_otp_from_message(body)
        assert result["type"] == "url"


class TestWaitForOTP:
    def test_finds_otp_in_inbox(self):
        fake_messages = [
            {
                "from": "noreply@onetrust.com",
                "body": "Verify your request: https://privacyportal.onetrust.com/confirm/abc",
                "date": "2026-04-12T12:00:00Z",
            }
        ]

        def mock_fetch_recent(scan_email, sender_hints, since_minutes):
            return fake_messages

        result = wait_for_otp(
            scan_email="user@gmail.com",
            sender_hints=["noreply@onetrust.com"],
            fetch_recent=mock_fetch_recent,
            timeout=1,
            poll_interval=0.1,
        )
        assert result is not None
        assert result["type"] == "url"
        assert "confirm" in result["value"]

    def test_no_matching_email(self):
        def mock_fetch_recent(scan_email, sender_hints, since_minutes):
            return []

        result = wait_for_otp(
            scan_email="user@gmail.com",
            sender_hints=["noreply@onetrust.com"],
            fetch_recent=mock_fetch_recent,
            timeout=0.3,
            poll_interval=0.1,
        )
        assert result is None


class TestSubmitPortal:
    def test_login_required_returns_needs_manual(self):
        letter = MagicMock()
        letter.portal_url = "https://myaccount.google.com/data-and-privacy"
        letter.body = "SAR body"
        letter.company_name = "Google"

        result = submit_portal(
            letter, scan_email="user@gmail.com", browser_launcher=MagicMock()
        )
        assert result.needs_manual is True
        assert result.success is False
        assert result.error == "login_required"

    @patch("portal_submitter.submitter.page_has_form", return_value=True)
    @patch("portal_submitter.submitter.settings")
    def test_successful_submission(self, mock_settings, mock_page_has_form):
        mock_settings.user_full_name = "Jane Doe"
        mock_settings.user_email = "jane@test.com"
        mock_settings.user_address_country = "United Kingdom"

        letter = MagicMock()
        letter.portal_url = "https://example.com/privacy-request"
        letter.body = "SAR body"
        letter.company_name = "Example Corp"

        # Mock browser
        mock_page = MagicMock()
        mock_page.locator.return_value.aria_snapshot.return_value = (
            '- textbox "Email"\n- button "Submit"'
        )
        mock_page.query_selector.return_value = None  # no CAPTCHA
        mock_page.query_selector_all.return_value = []  # no CAPTCHA iframes
        mock_page.screenshot.return_value = b"fake screenshot"

        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser.new_context.return_value = mock_context

        def mock_launcher():
            return MagicMock(
                __enter__=MagicMock(
                    return_value=MagicMock(
                        chromium=MagicMock(launch=MagicMock(return_value=mock_browser))
                    )
                ),
                __exit__=MagicMock(return_value=False),
            )

        # Mock LLM
        llm_response = json.dumps(
            {
                "fields": [{"name": "Email", "value_key": "email", "role": "textbox"}],
                "submit_button": "Submit",
            }
        )

        result = submit_portal(
            letter,
            scan_email="user@gmail.com",
            browser_launcher=mock_launcher,
            llm_call=lambda prompt: llm_response,
        )
        assert result.success is True
        assert result.portal_status == "submitted"

    def test_empty_portal_url(self):
        letter = MagicMock()
        letter.portal_url = ""
        letter.company_name = "No Portal"

        result = submit_portal(letter, scan_email="user@gmail.com")
        assert result.success is False
        assert result.error == "no_portal_url"


class TestJunkUrlGuard:
    def test_junk_url_returns_failed(self):
        """Portal URL that matches junk filter fails immediately without browser."""
        letter = MagicMock()
        letter.portal_url = "https://society.zendesk.com/hc/en-us/requests/649929"
        letter.company_name = "Zendesk"
        result = submit_portal(letter, "test@example.com")
        assert result.portal_status == "failed"
        assert "junk" in result.error.lower()
        assert result.needs_manual is True


class TestNavigatorIntegration:
    def test_no_form_triggers_navigator(self):
        """When landing page has no form, submitter calls navigate_to_form."""
        letter = MagicMock()
        letter.portal_url = "https://zendesk.es/"
        letter.company_name = "Zendesk"

        mock_page = MagicMock()
        mock_page.locator.return_value.count.return_value = 0
        mock_page.content.return_value = "<html></html>"

        mock_pw = MagicMock()
        mock_browser = MagicMock()
        mock_pw.__enter__ = MagicMock(return_value=mock_pw)
        mock_pw.__exit__ = MagicMock(return_value=False)
        mock_pw.chromium.launch.return_value = mock_browser
        mock_context = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        with patch("portal_submitter.submitter.detect_platform", return_value="ketch"):
            with patch(
                "portal_submitter.submitter.navigate_to_form", return_value=False
            ) as mock_nav:
                with patch(
                    "portal_submitter.submitter.page_has_form", return_value=False
                ):
                    result = submit_portal(
                        letter,
                        "test@example.com",
                        browser_launcher=lambda: mock_pw,
                    )

        mock_nav.assert_called_once()
        assert result.needs_manual is True
        assert "form_not_found" in result.error


class TestPortalTracking:
    def test_record_sent_includes_portal_fields(self, tmp_path):
        from letter_engine import tracker
        from letter_engine.models import SARLetter as RealSARLetter

        path = tmp_path / "sent.json"
        letter = RealSARLetter(
            company_name="Glassdoor",
            method="portal",
            to_email="",
            subject="",
            body="SAR body",
            portal_url="https://help.glassdoor.com/s/privacyrequest",
            postal_address="",
        )
        tracker.record_sent(
            letter,
            path=path,
            portal_status="submitted",
            portal_confirmation_ref="TICKET-123",
        )

        log = tracker.get_log(path=path)
        assert len(log) == 1
        assert log[0]["portal_url"] == "https://help.glassdoor.com/s/privacyrequest"
        assert log[0]["portal_status"] == "submitted"
        assert log[0]["portal_confirmation_ref"] == "TICKET-123"
        assert log[0]["method"] == "portal"
