"""Unit tests for reply_monitor/classifier.py — regex patterns and LLM fallback."""

import json
from unittest.mock import MagicMock, patch

import pytest

from reply_monitor.classifier import classify


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def msg(from_addr="company@example.com", subject="Re: Subject Access Request", snippet="", has_attachment=False):
    return {"from": from_addr, "subject": subject, "snippet": snippet, "has_attachment": has_attachment}


# ---------------------------------------------------------------------------
# Bounce tests
# ---------------------------------------------------------------------------

class TestBouncePermanent:
    def test_mailer_daemon_from(self):
        result = classify(msg(from_addr="mailer-daemon@googlemail.com", subject="Delivery Status Notification"))
        assert "BOUNCE_PERMANENT" in result.tags

    def test_postmaster_550(self):
        result = classify(msg(from_addr="postmaster@reflexivity.com",
                              snippet="550 5.1.1 email account does not exist"))
        assert "BOUNCE_PERMANENT" in result.tags

    def test_google_group_rejection(self):
        result = classify(msg(snippet="group you tried to contact may not exist or you may not have permission to post"))
        assert "BOUNCE_PERMANENT" in result.tags

    def test_no_such_user(self):
        result = classify(msg(from_addr="mailer-daemon@smtp.example.com",
                              snippet="No such user here"))
        assert "BOUNCE_PERMANENT" in result.tags

    def test_bounce_temporary(self):
        result = classify(msg(from_addr="postmaster@example.com",
                              snippet="421 try again later, service temporarily unavailable"))
        assert "BOUNCE_TEMPORARY" in result.tags
        assert "BOUNCE_PERMANENT" not in result.tags


# ---------------------------------------------------------------------------
# Auto-acknowledge tests
# ---------------------------------------------------------------------------

class TestAutoAcknowledge:
    def test_zendesk_ticket_subject(self):
        result = classify(msg(subject="[GDPR-123456] Your request has been received"))
        assert "AUTO_ACKNOWLEDGE" in result.tags

    def test_google_ticket_format(self):
        result = classify(msg(snippet="Thank you [5-9110000040081] your case has been logged"))
        assert "AUTO_ACKNOWLEDGE" in result.tags

    def test_substack_request_received(self):
        result = classify(msg(subject="[Request received] Subject Access Request"))
        assert "AUTO_ACKNOWLEDGE" in result.tags

    def test_snippet_received_your_request(self):
        result = classify(msg(snippet="We received your request and will respond within 30 days"))
        assert "AUTO_ACKNOWLEDGE" in result.tags


# ---------------------------------------------------------------------------
# Out of office tests
# ---------------------------------------------------------------------------

class TestOutOfOffice:
    def test_subject_out_of_office(self):
        result = classify(msg(subject="Out of Office: Re: Subject Access Request"))
        assert "OUT_OF_OFFICE" in result.tags

    def test_automatic_reply_snippet(self):
        result = classify(msg(snippet="I am away on annual leave and will return on Monday"))
        assert "OUT_OF_OFFICE" in result.tags


# ---------------------------------------------------------------------------
# Confirmation required tests
# ---------------------------------------------------------------------------

class TestConfirmationRequired:
    def test_hrtechprivacy_url(self):
        result = classify(msg(
            snippet="will not begin processing your request until you have confirmed it by clicking the Confirm Request button"
        ))
        assert "CONFIRMATION_REQUIRED" in result.tags

    def test_confirm_subject(self):
        result = classify(msg(subject="Confirm Your Request — privacy portal"))
        assert "CONFIRMATION_REQUIRED" in result.tags

    def test_hrtechprivacy_url_extracted(self):
        url = "https://requests.hrtechprivacy.com/confirm/abc-123-xyz"
        result = classify(msg(snippet=f"Click here to confirm: {url}"))
        assert result.extracted["confirmation_url"] == url


# ---------------------------------------------------------------------------
# Identity required tests
# ---------------------------------------------------------------------------

class TestIdentityRequired:
    def test_proof_of_identity(self):
        result = classify(msg(snippet="please provide proof of identity before we can process your request"))
        assert "IDENTITY_REQUIRED" in result.tags

    def test_government_issued_id(self):
        result = classify(msg(snippet="We need a government-issued photo ID to verify your identity"))
        assert "IDENTITY_REQUIRED" in result.tags

    def test_passport_copy(self):
        result = classify(msg(snippet="Please send a copy of your passport for verification"))
        assert "IDENTITY_REQUIRED" in result.tags


# ---------------------------------------------------------------------------
# More info required tests
# ---------------------------------------------------------------------------

class TestMoreInfoRequired:
    def test_please_clarify(self):
        result = classify(msg(snippet="Please clarify your request — we cannot identify you in our systems"))
        assert "MORE_INFO_REQUIRED" in result.tags

    def test_unable_to_locate(self):
        result = classify(msg(snippet="We are unable to locate any record matching your details"))
        assert "MORE_INFO_REQUIRED" in result.tags


# ---------------------------------------------------------------------------
# Wrong channel tests (covers unmonitored inboxes + portal redirects)
# ---------------------------------------------------------------------------

class TestWrongChannel:
    def test_no_longer_monitored(self):
        result = classify(msg(snippet="This email address is no longer monitored. Please use our support form."))
        assert "WRONG_CHANNEL" in result.tags

    def test_german_nicht_gelesen(self):
        result = classify(msg(snippet="Diese Adresse wird nicht gelesen. Bitte nutzen Sie das Formular."))
        assert "WRONG_CHANNEL" in result.tags

    def test_privacy_portal_mention(self):
        result = classify(msg(snippet="Please submit your request via our privacy portal"))
        assert "WRONG_CHANNEL" in result.tags

    def test_hrtechprivacy_url(self):
        result = classify(msg(snippet="Submit via requests.hrtechprivacy.com/submit"))
        assert "WRONG_CHANNEL" in result.tags

    def test_self_service_deflection(self):
        # Company tells user to manage data via self-service instead of processing the SAR
        result = classify(msg(snippet=(
            "To access, manage, or delete your personal data, "
            "you can do so directly via our self-service portal"
        )))
        assert "WRONG_CHANNEL" in result.tags

    def test_finalroundai_snippet(self):
        # Real Final Round AI reply — truncated snippet that ends mid-sentence
        result = classify(msg(snippet=(
            "Hi Maria, Thank you for reaching out regarding your data access request "
            "under Article 15 of the UK GDPR. To access, manage, or delete your personal "
            "data, you can do so directly via our self-service"
        )))
        assert "WRONG_CHANNEL" in result.tags

    def test_zendesk_ticket_set_to_solved(self):
        """Zendesk 'ticket is set to Solved' without data → WRONG_CHANNEL."""
        result = classify(msg(
            subject="[Employee Help Center] Re: Subject Access Request",
            snippet="Please be sure to REPLY-ALL. Request (649929) has been updated and the ticket is set to Solved.",
        ))
        assert "WRONG_CHANNEL" in result.tags

    def test_request_marked_as_resolved(self):
        result = classify(msg(snippet="Your request has been marked as resolved. If you need further help, contact us."))
        assert "WRONG_CHANNEL" in result.tags

    def test_case_closed(self):
        result = classify(msg(snippet="Your case has been closed. Thank you for contacting support."))
        assert "WRONG_CHANNEL" in result.tags

    def test_ticket_solved_subject(self):
        result = classify(msg(subject="Request #649929 set to Solved"))
        assert "WRONG_CHANNEL" in result.tags

    def test_solved_with_data_link_not_wrong_channel(self):
        """If the message also has a real data link, WRONG_CHANNEL should be suppressed."""
        result = classify(msg(
            snippet="Your request has been resolved. Download your data: https://example.com/export/download?token=abc123",
        ))
        assert "DATA_PROVIDED_LINK" in result.tags
        assert "WRONG_CHANNEL" not in result.tags


# ---------------------------------------------------------------------------
# Request accepted tests
# ---------------------------------------------------------------------------

class TestRequestAccepted:
    def test_confirmed_and_begin_gathering(self):
        result = classify(msg(snippet="We have confirmed your request and will begin gathering your data"))
        assert "REQUEST_ACCEPTED" in result.tags

    def test_start_of_request_subject(self):
        result = classify(msg(subject="Start of Your Request to Access Your Personal Data"))
        assert "REQUEST_ACCEPTED" in result.tags


# ---------------------------------------------------------------------------
# Data provided tests
# ---------------------------------------------------------------------------

class TestDataProvided:
    def test_data_link_glassdoor(self):
        result = classify(msg(
            subject="Download Your Glassdoor Personal Data File",
            snippet="Your data file is now available for download. https://www.glassdoor.com/dyd/download?token=abc123xyz"
        ))
        assert "DATA_PROVIDED_LINK" in result.tags

    def test_data_link_extracted(self):
        url = "https://www.glassdoor.com/dyd/download?token=abc123xyz"
        result = classify(msg(snippet=f"data file is now available for download {url}"))
        assert result.extracted["data_link"] == url

    def test_data_link_full_token_with_colons(self):
        """Real Glassdoor tokens contain colon-separated segments — must capture in full."""
        url = "https://www.glassdoor.com/dyd/download?token=2mUMY8nURO44N2FmJH4TyA:AaZmUVY-abc:SPjrP6rx2Yg0"
        result = classify(msg(snippet=f"data file is now available {url}"))
        assert result.extracted["data_link"] == url

    def test_data_attachment(self):
        result = classify(msg(has_attachment=True, snippet="Please find your data attached"))
        assert "DATA_PROVIDED_ATTACHMENT" in result.tags

    def test_data_portal(self):
        result = classify(msg(snippet="You can access your data via our self-service account management page"))
        assert "DATA_PROVIDED_PORTAL" in result.tags

    def test_no_attachment_no_tag(self):
        result = classify(msg(has_attachment=False))
        assert "DATA_PROVIDED_ATTACHMENT" not in result.tags


# ---------------------------------------------------------------------------
# Request denied / no data / not applicable tests
# ---------------------------------------------------------------------------

class TestDenial:
    def test_request_denied(self):
        result = classify(msg(snippet="We are unable to comply with your request as it is manifestly unfounded"))
        assert "REQUEST_DENIED" in result.tags

    def test_no_data_held(self):
        result = classify(msg(snippet="We do not hold any data or records about you in our systems"))
        assert "NO_DATA_HELD" in result.tags

    def test_not_gdpr_applicable(self):
        result = classify(msg(snippet="GDPR does not apply to our organisation as we are not based in the EU or UK"))
        assert "NOT_GDPR_APPLICABLE" in result.tags

    def test_fulfilled_deletion(self):
        result = classify(msg(snippet="Your account has been removed and your data has been deleted from our systems"))
        assert "FULFILLED_DELETION" in result.tags


# ---------------------------------------------------------------------------
# Extended / in-progress tests
# ---------------------------------------------------------------------------

class TestExtended:
    def test_extended_three_months(self):
        result = classify(msg(snippet="Due to the complexity we require more time — up to three months"))
        assert "EXTENDED" in result.tags

    def test_in_progress(self):
        result = classify(msg(snippet="We are currently processing your request"))
        assert "IN_PROGRESS" in result.tags


# ---------------------------------------------------------------------------
# Multi-tag tests
# ---------------------------------------------------------------------------

class TestMultiTag:
    def test_auto_ack_and_identity(self):
        result = classify(msg(
            snippet="We received your request [TICKET-123456-7890]. "
                    "Please provide proof of identity before we can proceed."
        ))
        assert "AUTO_ACKNOWLEDGE" in result.tags
        assert "IDENTITY_REQUIRED" in result.tags

    def test_bounce_and_ack_not_both(self):
        # A bounce should be just BOUNCE_PERMANENT
        result = classify(msg(
            from_addr="mailer-daemon@example.com",
            subject="Delivery Status Notification",
            snippet="550 5.1.1 email account does not exist"
        ))
        assert "BOUNCE_PERMANENT" in result.tags


# ---------------------------------------------------------------------------
# Reference number extraction tests
# ---------------------------------------------------------------------------

class TestReferenceExtraction:
    def test_google_ticket_ref(self):
        result = classify(msg(snippet="Your case [5-9110000040081] has been received"))
        assert result.extracted["reference_number"] == "[5-9110000040081]"

    def test_zendesk_ref(self):
        result = classify(msg(snippet="Thank you for contacting us [GDPR-88421]"))
        assert result.extracted["reference_number"] == "[GDPR-88421]"

    def test_generic_ref(self):
        result = classify(msg(snippet="Your Ref: ABC-2026-XYZ has been logged"))
        assert "ABC-2026-XYZ" in result.extracted["reference_number"]


# ---------------------------------------------------------------------------
# HUMAN_REVIEW fallback
# ---------------------------------------------------------------------------

class TestHumanReview:
    def test_no_pattern_match(self):
        result = classify(msg(snippet="Hello, thank you for your email."))
        assert "HUMAN_REVIEW" in result.tags

    def test_human_review_no_api_key(self):
        # Without an API key, LLM is not called; HUMAN_REVIEW is returned
        result = classify(msg(snippet="Something completely unclassifiable xyz123"), api_key=None)
        assert "HUMAN_REVIEW" in result.tags
        assert result.llm_used is False


# ---------------------------------------------------------------------------
# NON_GDPR pre-pass tests
# ---------------------------------------------------------------------------

class TestNonGDPR:
    def test_job_alert_from_address_and_subject(self):
        """Strong from-address + job alert subject → NON_GDPR."""
        result = classify(msg(
            from_addr="alerts@glassdoor.com",
            subject="New job alert: Python Developer in London",
            snippet="View new jobs matching your profile.",
        ))
        assert result.tags == ["NON_GDPR"]

    def test_newsletter_with_unsubscribe_snippet(self):
        """Marketing from-address + unsubscribe snippet → NON_GDPR."""
        result = classify(msg(
            from_addr="news@substack.com",
            subject="Your weekly digest is ready",
            snippet="View this email in your browser. Manage your email preferences.",
        ))
        assert result.tags == ["NON_GDPR"]

    def test_community_address_and_community_subject(self):
        """community@ local prefix scores 2 → NON_GDPR even without snippet signal."""
        result = classify(msg(
            from_addr="community@glassdoor.com",
            subject="Join the community — weekly highlights",
            snippet="See what's happening in the Glassdoor community this week.",
        ))
        assert result.tags == ["NON_GDPR"]

    def test_display_name_jobs_plus_zero_width_snippet(self):
        """Real Glassdoor case: noreply@ but display name 'Glassdoor Jobs' + ZWC snippet."""
        result = classify(msg(
            from_addr="Glassdoor Jobs <noreply@glassdoor.com>",
            subject="Options Resourcing, bp is hiring in London, England. Apply Now.",
            snippet="Options Resourcing is hiring \u200c\u200b\u200c\u200b",
        ))
        assert result.tags == ["NON_GDPR"]

    def test_display_name_community_plus_zero_width_snippet(self):
        """Real Glassdoor case: noreply@ but display name 'Glassdoor Community' + ZWC."""
        result = classify(msg(
            from_addr="Glassdoor Community <noreply@glassdoor.com>",
            subject="With the recent jobs report, I'm feeling more nervous about job hunting",
            snippet="Get the latest Tech trending posts \u200c\u200b\u200c\u200b",
        ))
        assert result.tags == ["NON_GDPR"]

    def test_noreply_with_gdpr_data_content_not_filtered(self):
        """noreply@ with GDPR data subject must NOT be tagged NON_GDPR."""
        result = classify(msg(
            from_addr="noreply@glassdoor.com",
            subject="Your Personal Data Access Request Is Complete",
            snippet="Your data file is now available for download.",
        ))
        assert "NON_GDPR" not in result.tags
        assert "DATA_PROVIDED_LINK" in result.tags

    def test_single_unsubscribe_signal_not_enough(self):
        """Snippet with unsubscribe but GDPR from/subject → NOT NON_GDPR."""
        result = classify(msg(
            from_addr="privacy@example.com",
            subject="Re: Subject Access Request",
            snippet="We have received your request. Unsubscribe from notifications.",
        ))
        assert "NON_GDPR" not in result.tags

    def test_non_gdpr_short_circuits_llm(self):
        """NON_GDPR pre-pass returns before LLM is ever considered."""
        with patch("reply_monitor.classifier._llm_classify") as mock_llm:
            result = classify(
                msg(
                    from_addr="jobs@linkedin.com",
                    subject="New job alert: 5 matching jobs",
                    snippet="Unsubscribe from job alerts.",
                ),
                api_key="sk-test",
            )
        mock_llm.assert_not_called()
        assert result.tags == ["NON_GDPR"]
        assert result.llm_used is False


# ---------------------------------------------------------------------------
# Body-based URL extraction tests
# ---------------------------------------------------------------------------

class TestBodyURLExtraction:
    def test_data_link_extracted_from_body_not_snippet(self):
        """Core Glassdoor bug: download URL is in body, snippet is truncated."""
        url = "https://www.glassdoor.com/dyd/download?token=abc123xyz"
        result = classify({
            "from": "noreply@glassdoor.com",
            "subject": "Your Personal Data Access Request Is Complete",
            "snippet": "Your personal data is now ready. Dear traderm1620@gmail.com, On March 16,",
            "body": f"Please click the link below to download your data:\n{url}\nThis link expires in 7 days.",
            "has_attachment": False,
        })
        assert "DATA_PROVIDED_LINK" in result.tags
        assert result.extracted["data_link"] == url

    def test_snippet_url_still_works(self):
        """When URL is in snippet, extraction still works as before."""
        url = "https://www.glassdoor.com/dyd/download?token=xyz789"
        result = classify(msg(
            subject="Download Your Glassdoor Personal Data File",
            snippet=f"Your data file is now available. {url}",
        ))
        assert result.extracted["data_link"] == url

    def test_body_url_not_overwritten_by_snippet(self):
        """If snippet has no URL but body does, body URL is captured."""
        url = "https://www.glassdoor.com/dyd/download?token=bodyonly"
        result = classify({
            "from": "noreply@glassdoor.com",
            "subject": "Download Your Personal Data",
            "snippet": "Your data is ready for download.",
            "body": f"Click here: {url}",
            "has_attachment": False,
        })
        assert result.extracted["data_link"] == url


# ---------------------------------------------------------------------------
# LLM fallback mock tests
# ---------------------------------------------------------------------------

class TestLLMFallback:
    def test_llm_called_when_no_tags(self):
        """LLM should be invoked when regex finds nothing."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(
            text='{"tags":["REQUEST_ACCEPTED"],"reference_number":"REF-001",'
                 '"confirmation_url":null,"data_link":null,"portal_url":null,"deadline_extension_days":null}'
        )]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 50

        # _llm_classify does `import anthropic` inside the function, so patch the internal helper
        with patch("reply_monitor.classifier._llm_classify") as mock_llm:
            mock_llm.return_value = {"tags": ["REQUEST_ACCEPTED"], "reference_number": "REF-001",
                                     "confirmation_url": None, "data_link": None,
                                     "portal_url": None, "deadline_extension_days": None}
            result = classify(
                msg(snippet="A completely unclassifiable response xyz999"),
                api_key="sk-test-key",
            )
        mock_llm.assert_called_once()
        assert "REQUEST_ACCEPTED" in result.tags
        assert result.llm_used is True

    def test_llm_not_called_when_actionable_tags(self):
        """LLM should NOT be called when regex already found actionable tags."""
        with patch("reply_monitor.classifier._llm_classify") as mock_llm:
            result = classify(
                msg(snippet="Please provide proof of identity before we can process"),
                api_key="sk-test",
            )
            mock_llm.assert_not_called()
        assert "IDENTITY_REQUIRED" in result.tags
        assert result.llm_used is False


# ---------------------------------------------------------------------------
# Junk URL filtering tests
# ---------------------------------------------------------------------------

class TestJunkURLFiltering:
    def test_zendesk_ticket_page_not_data_link(self):
        """Zendesk /hc/*/requests/NNN is a ticket page, not a data download."""
        result = classify({
            "from": "privacy@society.zendesk.com",
            "subject": "[Employee Help Center] Re: SAR",
            "snippet": "Visit https://society.zendesk.com/hc/en-us/requests/649929 for details",
            "body": "",
            "has_attachment": False,
        })
        assert result.extracted["data_link"] == ""
        assert result.extracted["data_links"] == []

    def test_zendesk_survey_url_not_data_link(self):
        """Zendesk survey_responses URL should not be extracted as data_link."""
        result = classify({
            "from": "privacy@society.zendesk.com",
            "subject": "Request #649929: How would you rate the support?",
            "snippet": "Please let us know https://society.zendesk.com/hc/en-us/survey_responses/01KM?access_token=abc",
            "body": "",
            "has_attachment": False,
        })
        assert result.extracted["data_link"] == ""
        assert result.extracted["data_links"] == []

    def test_zendesk_ticket_page_not_portal_url(self):
        """Zendesk ticket page should not be extracted as portal_url either."""
        result = classify({
            "from": "privacy@society.zendesk.com",
            "subject": "Re: SAR",
            "snippet": "Your request has been updated. https://society.zendesk.com/hc/en-us/requests/649929",
            "body": "Please submit via our portal at https://society.zendesk.com/hc/en-us/requests/649929",
            "has_attachment": False,
        })
        assert result.extracted["portal_url"] == ""

    def test_help_center_article_not_data_link(self):
        """Help center articles are not data downloads."""
        result = classify({
            "from": "support@example.com",
            "subject": "Re: Data Request",
            "snippet": "See https://help.example.com/hc/en-us/articles/123456 for instructions on downloading your data",
            "body": "",
            "has_attachment": False,
        })
        assert result.extracted["data_link"] == ""

    def test_real_data_link_still_extracted(self):
        """Legitimate data download URLs must still work."""
        url = "https://example.com/export/download?token=abc123"
        result = classify({
            "from": "privacy@example.com",
            "subject": "Your data export is ready",
            "snippet": f"Download here: {url}",
            "body": "",
            "has_attachment": False,
        })
        assert result.extracted["data_link"] == url

    def test_feedback_url_not_data_link(self):
        """Feedback/rating URLs should not be extracted."""
        result = classify({
            "from": "support@example.com",
            "subject": "How was your experience?",
            "snippet": "Rate us at https://example.com/feedback/rate?session=abc123",
            "body": "",
            "has_attachment": False,
        })
        assert result.extracted["data_link"] == ""

    def test_bare_request_path_is_junk(self):
        from reply_monitor.classifier import _is_junk_url
        assert _is_junk_url("https://company.zendesk.com/requests/12345") is True

    def test_support_tickets_is_junk(self):
        from reply_monitor.classifier import _is_junk_url
        assert _is_junk_url("https://support.example.com/support/tickets/789") is True

    def test_help_root_is_junk(self):
        from reply_monitor.classifier import _is_junk_url
        assert _is_junk_url("https://example.com/help/privacy") is True

    def test_real_portal_not_junk(self):
        from reply_monitor.classifier import _is_junk_url
        assert _is_junk_url("https://zendesk.es/") is False

    def test_data_export_not_junk(self):
        from reply_monitor.classifier import _is_junk_url
        assert _is_junk_url("https://example.com/data-export/download?token=abc") is False


# ---------------------------------------------------------------------------
# Draft tone tests
# ---------------------------------------------------------------------------

class TestDraftTone:
    def test_closure_draft_mentions_follow_instructions(self):
        """When reply has closure language, the draft prompt should instruct to follow portal."""
        from reply_monitor.classifier import generate_reply_draft
        from unittest.mock import patch, MagicMock

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="I will follow the portal instructions.")]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 30

        with patch("reply_monitor.classifier.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_response

            with patch("reply_monitor.classifier.cost_tracker"):
                result = generate_reply_draft(
                    "Your ticket is set to Solved. Visit our portal for details.",
                    ["WRONG_CHANNEL"],
                    "Zendesk",
                    api_key="sk-test",
                )

            # Verify the prompt sent to LLM contains the follow-instructions guidance
            call_args = mock_client.messages.create.call_args
            prompt_text = call_args[1]["messages"][0]["content"]
            assert "follow" in prompt_text.lower() or "portal" in prompt_text.lower()
            # When closure language is detected, the prompt must NOT instruct to argue violations
            # (it's OK to say "do NOT argue" — guard against "argue about GDPR violations" as imperative)
            assert "argue" not in prompt_text.lower() or "do not argue" in prompt_text.lower()

    def test_standard_redirect_draft_no_closure_context(self):
        """Standard WRONG_CHANNEL (no closure language) uses normal prompt."""
        from reply_monitor.classifier import generate_reply_draft
        from unittest.mock import patch, MagicMock

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Please clarify the appropriate channel.")]
        mock_response.usage.input_tokens = 100
        mock_response.usage.output_tokens = 30

        with patch("reply_monitor.classifier.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = mock_response

            with patch("reply_monitor.classifier.cost_tracker"):
                result = generate_reply_draft(
                    "This address is no longer monitored. Use our support form.",
                    ["WRONG_CHANNEL"],
                    "Example Corp",
                    api_key="sk-test",
                )

            call_args = mock_client.messages.create.call_args
            prompt_text = call_args[1]["messages"][0]["content"]
            # Should NOT contain the closure-specific guidance about following portal
            assert "closed" not in prompt_text.lower() or "follow their portal" not in prompt_text.lower()
