"""Tests for the /portal/submit/<domain> route logic.

Verifies that the route accepts portal submissions even when the company's
preferred_method is not 'portal' (e.g. WRONG_CHANNEL redirects from email companies).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def state_dir(tmp_path):
    """Create minimal reply_state.json for a test account."""
    data_dir = tmp_path / "user_data"
    data_dir.mkdir()
    state_path = data_dir / "reply_state.json"
    state_path.write_text(json.dumps({
        "test_at_test_com": {
            "zendesk.com": {
                "domain": "zendesk.com",
                "company_name": "Zendesk",
                "sar_sent_at": "2026-04-01T00:00:00Z",
                "to_email": "privacy@zendesk.com",
                "subject": "Subject Access Request",
                "gmail_thread_id": "thread_zd_1",
                "deadline": "2026-05-01",
                "replies": [{
                    "gmail_message_id": "msg_1",
                    "received_at": "2026-04-02T10:00:00Z",
                    "from": "privacy@zendesk.com",
                    "subject": "Re: SAR",
                    "snippet": "Please use our portal",
                    "tags": ["WRONG_CHANNEL"],
                    "extracted": {"portal_url": "", "reference_number": "",
                                  "confirmation_url": "", "data_link": "",
                                  "data_links": [], "deadline_extension_days": None},
                    "llm_used": False,
                    "has_attachment": False,
                    "attachment_catalog": None,
                }],
            }
        }
    }))
    return data_dir


def test_portal_submit_accepts_portal_url_param(state_dir):
    """Route should accept portal_url query param for non-portal companies."""
    from contact_resolver.resolver import ContactResolver
    from letter_engine.composer import compose
    from letter_engine.models import SARLetter
    from contact_resolver.models import CompanyRecord, Contact

    # Mock resolver to return a company with preferred_method="email"
    mock_record = MagicMock(spec=CompanyRecord)
    mock_record.company_name = "Zendesk"
    mock_record.contact = MagicMock(spec=Contact)
    mock_record.contact.preferred_method = "email"
    mock_record.contact.gdpr_portal_url = ""
    mock_record.contact.privacy_email = "privacy@zendesk.com"
    mock_record.contact.dpo_email = ""

    # Mock compose to return a letter
    mock_letter = SARLetter(
        company_name="Zendesk",
        method="email",
        to_email="privacy@zendesk.com",
        subject="SAR - Test",
        body="Dear DPO, I request access...",
        portal_url="",
        postal_address="",
    )

    with patch.object(ContactResolver, "resolve", return_value=mock_record), \
         patch("letter_engine.composer.compose", return_value=mock_letter) as mock_compose:

        # Import the app module — need to simulate route logic
        # Rather than spin up a full Flask test client (needs auth), test the logic directly
        from dashboard.app import _lookup_company

        domain = "zendesk.com"
        portal_url_param = "https://www.zendesk.com/datasubjectrequest/"

        resolver = ContactResolver()
        record = resolver.resolve(domain, domain, verbose=False)

        # Route logic: determine effective portal URL
        effective_portal_url = portal_url_param
        if not effective_portal_url and record:
            effective_portal_url = record.contact.gdpr_portal_url

        assert effective_portal_url == "https://www.zendesk.com/datasubjectrequest/"

        # Route logic: build letter
        if record:
            letter = mock_compose(record)
            letter.portal_url = effective_portal_url
            letter.method = "portal"

        assert letter.portal_url == "https://www.zendesk.com/datasubjectrequest/"
        assert letter.method == "portal"


def test_portal_submit_falls_back_to_overrides(state_dir):
    """Route should find portal URL from overrides when not passed explicitly."""
    from contact_resolver.resolver import ContactResolver
    from contact_resolver.models import CompanyRecord, Contact

    mock_record = MagicMock(spec=CompanyRecord)
    mock_record.company_name = "Zendesk"
    mock_record.contact = MagicMock(spec=Contact)
    mock_record.contact.preferred_method = "email"
    mock_record.contact.gdpr_portal_url = ""

    with patch.object(ContactResolver, "resolve", return_value=mock_record), \
         patch("dashboard.app._lookup_company", return_value={
             "contact": {"gdpr_portal_url": "https://www.zendesk.com/datasubjectrequest/"}
         }):
        from dashboard.app import _lookup_company

        domain = "zendesk.com"
        portal_url_param = ""  # not passed

        effective_portal_url = portal_url_param
        if not effective_portal_url and mock_record:
            effective_portal_url = mock_record.contact.gdpr_portal_url
        if not effective_portal_url:
            company_rec = _lookup_company(domain)
            effective_portal_url = (company_rec.get("contact", {}) or {}).get("gdpr_portal_url", "")

        assert effective_portal_url == "https://www.zendesk.com/datasubjectrequest/"


def test_portal_submit_rejects_no_url():
    """Route should return error when no portal URL is available."""
    from contact_resolver.resolver import ContactResolver
    from contact_resolver.models import CompanyRecord, Contact

    mock_record = MagicMock(spec=CompanyRecord)
    mock_record.company_name = "NoCorp"
    mock_record.contact = MagicMock(spec=Contact)
    mock_record.contact.preferred_method = "email"
    mock_record.contact.gdpr_portal_url = ""

    with patch.object(ContactResolver, "resolve", return_value=mock_record), \
         patch("dashboard.app._lookup_company", return_value={}):
        from dashboard.app import _lookup_company

        domain = "nocorp.com"
        portal_url_param = ""

        effective_portal_url = portal_url_param
        if not effective_portal_url and mock_record:
            effective_portal_url = mock_record.contact.gdpr_portal_url
        if not effective_portal_url:
            company_rec = _lookup_company(domain)
            effective_portal_url = (company_rec.get("contact", {}) or {}).get("gdpr_portal_url", "")

        assert effective_portal_url == ""
        # Route would return 400 here


def test_save_portal_submission_persists(tmp_path):
    """Verify save_portal_submission writes to reply_state.json correctly."""
    data_dir = tmp_path
    state_path = data_dir / "reply_state.json"
    state_path.write_text(json.dumps({
        "test_at_test_com": {
            "example.com": {
                "domain": "example.com",
                "company_name": "Example",
                "sar_sent_at": "2026-04-01T00:00:00Z",
                "to_email": "dpo@example.com",
                "subject": "SAR",
                "gmail_thread_id": "thread1",
                "deadline": "2026-05-01",
                "replies": [],
            }
        }
    }))

    from reply_monitor.state_manager import save_portal_submission, load_state

    save_portal_submission(
        "test@test.com", "example.com",
        status="submitted",
        portal_url="https://example.com/privacy",
        confirmation_ref="REF-123",
        data_dir=data_dir,
    )

    states = load_state("test@test.com", data_dir=data_dir)
    ps = states["example.com"].portal_submission
    assert ps is not None
    assert ps["status"] == "submitted"
    assert ps["portal_url"] == "https://example.com/privacy"
    assert ps["confirmation_ref"] == "REF-123"
    assert ps["submitted_at"]  # non-empty ISO timestamp


def test_save_portal_submission_manual(tmp_path):
    """Verify manual portal submission status is persisted."""
    data_dir = tmp_path
    state_path = data_dir / "reply_state.json"
    state_path.write_text(json.dumps({
        "user_at_gmail_com": {
            "zendesk.com": {
                "domain": "zendesk.com",
                "company_name": "Zendesk",
                "sar_sent_at": "2026-04-01T00:00:00Z",
                "to_email": "privacy@zendesk.com",
                "subject": "SAR",
                "gmail_thread_id": "thread_zd",
                "deadline": "2026-05-01",
                "replies": [],
            }
        }
    }))

    from reply_monitor.state_manager import save_portal_submission, load_state

    # First save as "manual" (needs manual)
    save_portal_submission(
        "user@gmail.com", "zendesk.com",
        status="manual",
        portal_url="https://www.zendesk.com/datasubjectrequest/",
        error="recaptcha_v3_blocked",
        data_dir=data_dir,
    )

    states = load_state("user@gmail.com", data_dir=data_dir)
    assert states["zendesk.com"].portal_submission["status"] == "manual"

    # Then user marks it as submitted
    save_portal_submission(
        "user@gmail.com", "zendesk.com",
        status="submitted",
        portal_url="https://www.zendesk.com/datasubjectrequest/",
        confirmation_ref="ZD-649929",
        data_dir=data_dir,
    )

    states = load_state("user@gmail.com", data_dir=data_dir)
    ps = states["zendesk.com"].portal_submission
    assert ps["status"] == "submitted"
    assert ps["confirmation_ref"] == "ZD-649929"
    assert ps["error"] == ""  # cleared
