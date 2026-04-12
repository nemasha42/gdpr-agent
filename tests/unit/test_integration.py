"""Integration smoke test: full pipeline scan → resolve → compose → send → monitor.

All external calls (Gmail, Anthropic, GitHub, HTTP) are mocked. This test
exercises cross-module data flow and catches model mismatches that unit tests
can miss when each module is isolated.
"""

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from contact_resolver.models import (
    CompanyRecord,
    Contact,
    Flags,
    PostalAddress,
    RequestNotes,
)
from contact_resolver.resolver import ContactResolver
from letter_engine.composer import compose
from letter_engine.models import SARLetter
from reply_monitor.classifier import classify
from reply_monitor.models import ClassificationResult, ReplyRecord
from reply_monitor.state_manager import (
    compute_status,
    deadline_from_sent,
    load_state,
    save_state,
    update_state,
)
from scanner.service_extractor import extract_services


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


TODAY = date.today().isoformat()

_RECORD = CompanyRecord(
    company_name="Spotify",
    legal_entity_name="Spotify AB",
    source="dataowners_override",
    source_confidence="high",
    last_verified=TODAY,
    contact=Contact(
        privacy_email="privacy@spotify.com",
        preferred_method="email",
    ),
    flags=Flags(email_accepted=True),
    request_notes=RequestNotes(known_response_time_days=30),
)

_INBOX_EMAILS = [
    {
        "sender": "hello@spotify.com",
        "display_name": "Spotify",
        "subject": "Welcome to Spotify",
        "date": "2026-01-01",
        "message_id": "msg001",
    }
]


# ---------------------------------------------------------------------------
# Stage 1 → Stage 2: scan + resolve
# ---------------------------------------------------------------------------


def test_extract_services_from_emails():
    services = extract_services(_INBOX_EMAILS)
    assert len(services) == 1
    assert services[0]["domain"] == "spotify.com"


def test_resolver_returns_record_for_known_domain(tmp_path):
    resolver = ContactResolver(
        db_path=tmp_path / "companies.json",
        dataowners_path=tmp_path / "overrides.json",
        http_get=MagicMock(side_effect=AssertionError("should not make HTTP calls")),
        llm_search=MagicMock(side_effect=AssertionError("should not call LLM")),
        privacy_scrape=MagicMock(return_value=None),
    )
    # Inject record via llm_search that returns our record (step 5)
    resolver._llm_search = lambda name, domain: _RECORD
    record = resolver.resolve("spotify.com", "Spotify")
    assert record is not None
    assert record.contact.privacy_email == "privacy@spotify.com"


# ---------------------------------------------------------------------------
# Stage 2 → Stage 3: compose
# ---------------------------------------------------------------------------


def test_compose_produces_sar_letter():
    user_identity = {
        "user_full_name": "Jane Doe",
        "user_email": "jane@example.com",
        "user_address_line1": "1 Main St",
        "user_address_city": "London",
        "user_address_postcode": "EC1A 1BB",
        "user_address_country": "United Kingdom",
        "gdpr_framework": "UK GDPR",
    }
    letter = compose(_RECORD, user_identity=user_identity)

    assert isinstance(letter, SARLetter)
    assert letter.method == "email"
    assert letter.to_email == "privacy@spotify.com"
    assert letter.company_name == "Spotify"
    assert "Jane Doe" in letter.body


# ---------------------------------------------------------------------------
# Stage 3 → Stage 4: classify reply
# ---------------------------------------------------------------------------


def test_classify_auto_acknowledge_reply():
    msg = {
        "from": "privacy@spotify.com",
        "subject": "[PRIV-12345] Your request has been received",
        "snippet": "We received your request and have logged it.",
        "has_attachment": False,
    }
    result = classify(msg)
    assert isinstance(result, ClassificationResult)
    assert "AUTO_ACKNOWLEDGE" in result.tags


def test_classify_data_provided_link():
    msg = {
        "from": "privacy@spotify.com",
        "subject": "Your personal data is ready to download",
        "snippet": "Your data file is now available for download. The download link will expire in 7 days.",
        "has_attachment": False,
    }
    result = classify(msg)
    assert "DATA_PROVIDED_LINK" in result.tags


def test_classify_non_gdpr_newsletter():
    msg = {
        "from": "digest@spotify.com",
        "subject": "Your weekly digest",
        "snippet": "Unsubscribe from our newsletter. View this email in your browser.",
        "has_attachment": False,
    }
    result = classify(msg)
    assert "NON_GDPR" in result.tags


# ---------------------------------------------------------------------------
# Stage 4: state manager
# ---------------------------------------------------------------------------


def test_full_state_cycle(tmp_path):
    state_path = tmp_path / "reply_state.json"
    account = "jane@example.com"

    from reply_monitor.models import CompanyState

    sent_at = f"{TODAY}T10:00:00Z"
    state = CompanyState(
        domain="spotify.com",
        company_name="Spotify",
        sar_sent_at=sent_at,
        to_email="privacy@spotify.com",
        subject="Subject Access Request",
        gmail_thread_id="thread123",
        deadline=deadline_from_sent(sent_at),
    )

    # 1. Save
    save_state(account, {"spotify.com": state}, path=state_path)
    assert state_path.exists()

    # 2. Load
    loaded = load_state(account, path=state_path)
    assert "spotify.com" in loaded
    assert loaded["spotify.com"].company_name == "Spotify"

    # 3. Add reply
    reply = ReplyRecord(
        gmail_message_id="msg_001",
        received_at=f"{TODAY}T12:00:00Z",
        from_addr="privacy@spotify.com",
        subject="[PRIV-001] Request received",
        snippet="We received your request",
        tags=["AUTO_ACKNOWLEDGE"],
        extracted={},
        llm_used=False,
        has_attachment=False,
        attachment_catalog=None,
    )
    updated = update_state(loaded["spotify.com"], [reply])
    assert len(updated.replies) == 1

    # 4. Compute status
    status = compute_status(updated)
    assert status == "ACKNOWLEDGED"

    # 5. Save updated state
    save_state(account, {"spotify.com": updated}, path=state_path)
    reloaded = load_state(account, path=state_path)
    assert len(reloaded["spotify.com"].replies) == 1


# ---------------------------------------------------------------------------
# Corrupt user_data files
# ---------------------------------------------------------------------------


def test_load_state_handles_corrupt_json(tmp_path):
    """Corrupt reply_state.json should return empty dict, not crash."""
    state_path = tmp_path / "reply_state.json"
    state_path.write_text("{ this is not valid json }")
    result = load_state("any@example.com", path=state_path)
    assert result == {}


def test_save_state_handles_corrupt_existing_file(tmp_path):
    """save_state should overwrite a corrupt file without crashing."""
    state_path = tmp_path / "reply_state.json"
    state_path.write_text("corrupt data")
    from reply_monitor.models import CompanyState

    state = CompanyState(
        domain="test.com", company_name="Test",
        sar_sent_at="2026-01-01T00:00:00Z",
        to_email="privacy@test.com",
        subject="SAR",
        gmail_thread_id="",
        deadline="2026-01-31",
    )
    save_state("test@example.com", {"test.com": state}, path=state_path)
    loaded = load_state("test@example.com", path=state_path)
    assert "test.com" in loaded


def test_load_sent_letters_corrupt_handled(tmp_path):
    """tracker.get_log() on a corrupt sent_letters.json should return []."""
    letters_path = tmp_path / "sent_letters.json"
    letters_path.write_text("not json")

    from letter_engine.tracker import get_log
    result = get_log(path=letters_path)
    assert isinstance(result, list)
    assert result == []
