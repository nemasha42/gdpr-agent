"""Unit tests for reply_monitor/state_manager.py."""

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest

from reply_monitor.models import CompanyState, ReplyRecord
from reply_monitor.state_manager import (
    _safe_email,
    compute_status,
    days_remaining,
    deadline_from_sent,
    domain_from_sent_record,
    load_state,
    save_state,
    status_sort_key,
    update_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_state(domain="example.com", company_name="ExampleCo", replies=None,
                sent_days_ago=5, deadline_days_from_now=25):
    sent_at = (date.today() - timedelta(days=sent_days_ago)).isoformat() + "T00:00:00"
    dl = (date.today() + timedelta(days=deadline_days_from_now)).isoformat()
    return CompanyState(
        domain=domain,
        company_name=company_name,
        sar_sent_at=sent_at,
        to_email=f"privacy@{domain}",
        subject="Subject Access Request — Test",
        gmail_thread_id="",
        deadline=dl,
        replies=replies or [],
    )


def _make_reply(tags, msg_id="msg001", snippet="test snippet"):
    return ReplyRecord(
        gmail_message_id=msg_id,
        received_at="2026-03-17T10:00:00Z",
        from_addr="privacy@example.com",
        subject="Re: SAR",
        snippet=snippet,
        tags=tags,
        extracted={"reference_number": "", "confirmation_url": "", "data_link": "", "portal_url": "", "deadline_extension_days": None},
        llm_used=False,
        has_attachment=False,
        attachment_catalog=None,
    )


# ---------------------------------------------------------------------------
# Status derivation tests
# ---------------------------------------------------------------------------

class TestComputeStatus:
    def test_pending_no_replies(self):
        state = _make_state()
        assert compute_status(state) == "PENDING"

    def test_bounced_from_permanent_bounce(self):
        state = _make_state(replies=[_make_reply(["BOUNCE_PERMANENT"])])
        assert compute_status(state) == "BOUNCED"

    def test_acknowledged_auto_ack(self):
        state = _make_state(replies=[_make_reply(["AUTO_ACKNOWLEDGE"])])
        assert compute_status(state) == "ACKNOWLEDGED"

    def test_acknowledged_request_accepted(self):
        state = _make_state(replies=[_make_reply(["REQUEST_ACCEPTED"])])
        assert compute_status(state) == "ACKNOWLEDGED"

    def test_action_required_identity(self):
        state = _make_state(replies=[_make_reply(["AUTO_ACKNOWLEDGE", "IDENTITY_REQUIRED"])])
        assert compute_status(state) == "ACTION_REQUIRED"

    def test_action_required_confirmation(self):
        state = _make_state(replies=[_make_reply(["CONFIRMATION_REQUIRED"])])
        assert compute_status(state) == "ACTION_REQUIRED"

    def test_action_required_wrong_channel(self):
        state = _make_state(replies=[_make_reply(["WRONG_CHANNEL"])])
        assert compute_status(state) == "ACTION_REQUIRED"

    def test_action_required_wrong_channel_portal(self):
        # WRONG_CHANNEL now covers former REDIRECT_TO_PORTAL cases too
        state = _make_state(replies=[_make_reply(["WRONG_CHANNEL"])])
        assert compute_status(state) == "ACTION_REQUIRED"

    def test_extended(self):
        state = _make_state(replies=[_make_reply(["EXTENDED"])])
        assert compute_status(state) == "EXTENDED"

    def test_completed_data_link(self):
        state = _make_state(replies=[_make_reply(["DATA_PROVIDED_LINK"])])
        assert compute_status(state) == "COMPLETED"

    def test_completed_data_attachment(self):
        state = _make_state(replies=[_make_reply(["DATA_PROVIDED_ATTACHMENT"])])
        assert compute_status(state) == "COMPLETED"

    def test_completed_fulfilled_deletion(self):
        state = _make_state(replies=[_make_reply(["FULFILLED_DELETION"])])
        assert compute_status(state) == "COMPLETED"

    def test_denied_request_denied(self):
        state = _make_state(replies=[_make_reply(["REQUEST_DENIED"])])
        assert compute_status(state) == "DENIED"

    def test_denied_no_data(self):
        state = _make_state(replies=[_make_reply(["NO_DATA_HELD"])])
        assert compute_status(state) == "DENIED"

    def test_denied_not_applicable(self):
        state = _make_state(replies=[_make_reply(["NOT_GDPR_APPLICABLE"])])
        assert compute_status(state) == "DENIED"

    def test_overdue_past_deadline_no_terminal(self):
        # deadline in the past, no terminal tags
        state = _make_state(sent_days_ago=35, deadline_days_from_now=-5)
        assert compute_status(state) == "OVERDUE"

    def test_overdue_not_triggered_when_completed(self):
        state = _make_state(
            replies=[_make_reply(["DATA_PROVIDED_LINK"])],
            sent_days_ago=35,
            deadline_days_from_now=-5,
        )
        assert compute_status(state) == "COMPLETED"


# ---------------------------------------------------------------------------
# Status priority tests
# ---------------------------------------------------------------------------

class TestStatusPriority:
    def test_bounced_above_overdue(self):
        # Bounced takes priority over overdue in the spec
        # (BOUNCED is checked before OVERDUE in compute_status)
        state = _make_state(
            replies=[_make_reply(["BOUNCE_PERMANENT"])],
            sent_days_ago=35,
            deadline_days_from_now=-5,
        )
        assert compute_status(state) == "BOUNCED"

    def test_action_above_extended(self):
        state = _make_state(replies=[
            _make_reply(["EXTENDED"]),
            _make_reply(["IDENTITY_REQUIRED"], msg_id="msg002"),
        ])
        assert compute_status(state) == "ACTION_REQUIRED"

    def test_sort_key_ordering(self):
        keys = [status_sort_key(s) for s in ["PENDING", "ACKNOWLEDGED", "COMPLETED", "OVERDUE"]]
        assert keys == sorted(keys)  # ascending by urgency in sort_key


# ---------------------------------------------------------------------------
# days_remaining / deadline_from_sent tests
# ---------------------------------------------------------------------------

class TestDeadline:
    def test_days_remaining_sent_today(self):
        today = date.today().isoformat() + "T00:00:00"
        remaining = days_remaining(today)
        assert remaining == 30

    def test_days_remaining_sent_10_days_ago(self):
        sent = (date.today() - timedelta(days=10)).isoformat() + "T00:00:00"
        assert days_remaining(sent) == 20

    def test_days_remaining_negative_when_overdue(self):
        sent = (date.today() - timedelta(days=35)).isoformat() + "T00:00:00"
        assert days_remaining(sent) == -5

    def test_deadline_from_sent(self):
        sent = "2026-03-16T00:56:57"
        dl = deadline_from_sent(sent)
        assert dl == "2026-04-15"

    def test_deadline_from_sent_with_z(self):
        sent = "2026-03-16T00:56:57Z"
        dl = deadline_from_sent(sent)
        assert dl == "2026-04-15"


# ---------------------------------------------------------------------------
# update_state tests
# ---------------------------------------------------------------------------

class TestUpdateState:
    def test_new_replies_appended(self):
        state = _make_state()
        new = [_make_reply(["AUTO_ACKNOWLEDGE"])]
        updated = update_state(state, new)
        assert len(updated.replies) == 1

    def test_duplicate_reply_not_added(self):
        existing = _make_reply(["AUTO_ACKNOWLEDGE"], msg_id="existing")
        state = _make_state(replies=[existing])
        new = [_make_reply(["IDENTITY_REQUIRED"], msg_id="existing")]  # same ID
        updated = update_state(state, new)
        assert len(updated.replies) == 1  # no duplicate

    def test_last_checked_updated(self):
        state = _make_state()
        assert state.last_checked == ""
        updated = update_state(state, [])
        assert updated.last_checked != ""


# ---------------------------------------------------------------------------
# Load/save state tests
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_save_and_load_roundtrip(self, tmp_path):
        path = tmp_path / "reply_state.json"
        state = _make_state(replies=[_make_reply(["AUTO_ACKNOWLEDGE"])])
        save_state("user@gmail.com", {"example.com": state}, path=path)

        loaded = load_state("user@gmail.com", path=path)
        assert "example.com" in loaded
        assert loaded["example.com"].company_name == "ExampleCo"
        assert len(loaded["example.com"].replies) == 1

    def test_load_empty_when_file_missing(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        result = load_state("user@gmail.com", path=path)
        assert result == {}

    def test_save_preserves_other_accounts(self, tmp_path):
        path = tmp_path / "reply_state.json"
        # Save first account
        state1 = _make_state(company_name="Alpha")
        save_state("alice@gmail.com", {"alpha.com": state1}, path=path)
        # Save second account
        state2 = _make_state(company_name="Beta", domain="beta.com")
        save_state("bob@gmail.com", {"beta.com": state2}, path=path)
        # Both should be present
        loaded1 = load_state("alice@gmail.com", path=path)
        loaded2 = load_state("bob@gmail.com", path=path)
        assert "alpha.com" in loaded1
        assert "beta.com" in loaded2

    def test_load_corrupted_file_returns_empty(self, tmp_path):
        path = tmp_path / "reply_state.json"
        path.write_text("NOT VALID JSON")
        result = load_state("user@gmail.com", path=path)
        assert result == {}


# ---------------------------------------------------------------------------
# domain_from_sent_record tests
# ---------------------------------------------------------------------------

class TestDomainFromSentRecord:
    def test_extracts_from_email_domain(self):
        record = {"to_email": "privacy@glassdoor.com"}
        assert domain_from_sent_record(record) == "glassdoor.com"

    def test_falls_back_to_company_name(self):
        record = {"to_email": "", "company_name": "Intercom"}
        domain = domain_from_sent_record(record)
        assert "intercom" in domain.lower()

    def test_empty_record(self):
        domain = domain_from_sent_record({})
        assert domain  # any non-empty fallback
