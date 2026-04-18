"""Unit tests for reply_monitor/state_manager.py."""

from datetime import date, timedelta


from reply_monitor.models import CompanyState, ReplyRecord
from reply_monitor.state_manager import (
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


def _make_state(
    domain="example.com",
    company_name="ExampleCo",
    replies=None,
    sent_days_ago=5,
    deadline_days_from_now=25,
):
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


def _make_reply(
    tags,
    msg_id="msg001",
    snippet="test snippet",
    received_at="2026-03-17T10:00:00Z",
    suggested_reply="",
    reply_review_status="",
    sent_reply_body="",
    sent_reply_at="",
):
    return ReplyRecord(
        gmail_message_id=msg_id,
        received_at=received_at,
        from_addr="privacy@example.com",
        subject="Re: SAR",
        snippet=snippet,
        tags=tags,
        extracted={
            "reference_number": "",
            "confirmation_url": "",
            "data_link": "",
            "portal_url": "",
            "deadline_extension_days": None,
        },
        llm_used=False,
        has_attachment=False,
        attachment_catalog=None,
        suggested_reply=suggested_reply,
        reply_review_status=reply_review_status,
        sent_reply_body=sent_reply_body,
        sent_reply_at=sent_reply_at,
    )


# ---------------------------------------------------------------------------
# Status derivation tests
# ---------------------------------------------------------------------------


class TestComputeStatus:
    def test_waiting_no_replies(self):
        state = _make_state()
        assert compute_status(state) == "WAITING"

    def test_stalled_from_permanent_bounce(self):
        state = _make_state(replies=[_make_reply(["BOUNCE_PERMANENT"])])
        assert compute_status(state) == "STALLED"

    def test_in_progress_auto_ack(self):
        state = _make_state(replies=[_make_reply(["AUTO_ACKNOWLEDGE"])])
        assert compute_status(state) == "IN_PROGRESS"

    def test_in_progress_request_accepted(self):
        state = _make_state(replies=[_make_reply(["REQUEST_ACCEPTED"])])
        assert compute_status(state) == "IN_PROGRESS"

    def test_action_required_identity(self):
        state = _make_state(
            replies=[_make_reply(["AUTO_ACKNOWLEDGE", "IDENTITY_REQUIRED"])]
        )
        assert compute_status(state) == "ACTION_NEEDED"

    def test_action_required_confirmation(self):
        state = _make_state(replies=[_make_reply(["CONFIRMATION_REQUIRED"])])
        assert compute_status(state) == "ACTION_NEEDED"

    def test_action_required_wrong_channel(self):
        state = _make_state(replies=[_make_reply(["WRONG_CHANNEL"])])
        assert compute_status(state) == "ACTION_NEEDED"

    def test_action_required_wrong_channel_portal(self):
        # WRONG_CHANNEL now covers former REDIRECT_TO_PORTAL cases too
        state = _make_state(replies=[_make_reply(["WRONG_CHANNEL"])])
        assert compute_status(state) == "ACTION_NEEDED"

    def test_extended(self):
        state = _make_state(replies=[_make_reply(["EXTENDED"])])
        assert compute_status(state) == "IN_PROGRESS"

    def test_completed_data_link(self):
        state = _make_state(replies=[_make_reply(["DATA_PROVIDED_LINK"])])
        assert compute_status(state) == "DONE"

    def test_completed_data_attachment(self):
        state = _make_state(replies=[_make_reply(["DATA_PROVIDED_ATTACHMENT"])])
        assert compute_status(state) == "DONE"

    def test_completed_fulfilled_deletion(self):
        state = _make_state(replies=[_make_reply(["FULFILLED_DELETION"])])
        assert compute_status(state) == "DONE"

    def test_denied_request_denied(self):
        state = _make_state(replies=[_make_reply(["REQUEST_DENIED"])])
        assert compute_status(state) == "DONE"

    def test_denied_no_data(self):
        state = _make_state(replies=[_make_reply(["NO_DATA_HELD"])])
        assert compute_status(state) == "DONE"

    def test_denied_not_applicable(self):
        state = _make_state(replies=[_make_reply(["NOT_GDPR_APPLICABLE"])])
        assert compute_status(state) == "DONE"

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
        assert compute_status(state) == "DONE"

    def test_user_replied_when_all_action_replies_sent(self):
        # WRONG_CHANNEL reply, user replied → USER_REPLIED
        state = _make_state(
            replies=[
                _make_reply(["WRONG_CHANNEL"], reply_review_status="sent"),
            ]
        )
        assert compute_status(state) == "REPLIED"

    def test_action_required_when_some_action_replies_not_sent(self):
        # Two action replies, only one replied to → still ACTION_REQUIRED
        state = _make_state(
            replies=[
                _make_reply(
                    ["IDENTITY_REQUIRED"], msg_id="msg001", reply_review_status="sent"
                ),
                _make_reply(
                    ["MORE_INFO_REQUIRED"], msg_id="msg002", reply_review_status=""
                ),
            ]
        )
        assert compute_status(state) == "ACTION_NEEDED"

    def test_action_required_when_reply_is_pending(self):
        # pending (draft ready but not sent) → ACTION_REQUIRED
        state = _make_state(
            replies=[
                _make_reply(["WRONG_CHANNEL"], reply_review_status="pending"),
            ]
        )
        assert compute_status(state) == "ACTION_NEEDED"

    def test_user_replied_ignores_non_gdpr(self):
        # Action reply replied to + NON_GDPR reply → USER_REPLIED (NON_GDPR excluded)
        state = _make_state(
            replies=[
                _make_reply(
                    ["WRONG_CHANNEL"], msg_id="msg001", reply_review_status="sent"
                ),
                _make_reply(["NON_GDPR"], msg_id="msg002", reply_review_status=""),
            ]
        )
        assert compute_status(state) == "REPLIED"

    def test_portal_submitted_triggers_replied(self):
        # portal_submitted should be treated same as "sent" for status computation
        state = _make_state(replies=[
            _make_reply(["WRONG_CHANNEL"], reply_review_status="portal_submitted"),
        ])
        assert compute_status(state) == "REPLIED"

    def test_portal_submitted_mixed_with_unsent_action(self):
        # One portal_submitted + one unsent action → still ACTION_NEEDED
        state = _make_state(replies=[
            _make_reply(["WRONG_CHANNEL"], msg_id="msg001", reply_review_status="portal_submitted"),
            _make_reply(["IDENTITY_REQUIRED"], msg_id="msg002", reply_review_status=""),
        ])
        assert compute_status(state) == "ACTION_NEEDED"


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
        assert compute_status(state) == "STALLED"

    def test_action_above_extended(self):
        state = _make_state(
            replies=[
                _make_reply(["EXTENDED"]),
                _make_reply(["IDENTITY_REQUIRED"], msg_id="msg002"),
            ]
        )
        assert compute_status(state) == "ACTION_NEEDED"

    def test_sort_key_ordering(self):
        keys = [
            status_sort_key(s) for s in ["DONE", "WAITING", "IN_PROGRESS", "OVERDUE"]
        ]
        assert keys == sorted(keys)  # ascending by urgency in sort_key

    def test_terminal_overrides_unresolved_action(self):
        """FULFILLED_DELETION (terminal) should override unresolved HUMAN_REVIEW (action)."""
        state = _make_state(
            replies=[
                _make_reply(["HUMAN_REVIEW"], msg_id="msg001"),
                _make_reply(["FULFILLED_DELETION"], msg_id="msg002"),
            ]
        )
        assert compute_status(state) == "DONE"

    def test_terminal_overrides_unresolved_wrong_channel(self):
        """DATA_PROVIDED_LINK (terminal) should override unresolved WRONG_CHANNEL."""
        state = _make_state(
            replies=[
                _make_reply(["WRONG_CHANNEL"], msg_id="msg001"),
                _make_reply(["DATA_PROVIDED_LINK"], msg_id="msg002"),
            ]
        )
        assert compute_status(state) == "DONE"

    def test_your_reply_resolves_action_required(self):
        """YOUR_REPLY after action-required reply should resolve to USER_REPLIED."""
        state = _make_state(
            replies=[
                _make_reply(
                    ["IDENTITY_REQUIRED"],
                    msg_id="msg001",
                    received_at="2026-03-20T10:00:00Z",
                ),
                _make_reply(
                    ["YOUR_REPLY"], msg_id="msg002", received_at="2026-03-21T10:00:00Z"
                ),
            ]
        )
        assert compute_status(state) == "REPLIED"

    def test_your_reply_before_action_does_not_resolve(self):
        """YOUR_REPLY before an action-required reply should not resolve it."""
        state = _make_state(
            replies=[
                _make_reply(
                    ["YOUR_REPLY"], msg_id="msg001", received_at="2026-03-19T10:00:00Z"
                ),
                _make_reply(
                    ["IDENTITY_REQUIRED"],
                    msg_id="msg002",
                    received_at="2026-03-20T10:00:00Z",
                ),
            ]
        )
        assert compute_status(state) == "ACTION_NEEDED"

    def test_dismissed_draft_resolves_action(self):
        """Dismissed drafts should resolve the action (user decided not to respond)."""
        state = _make_state(
            replies=[
                _make_reply(["WRONG_CHANNEL"], reply_review_status="dismissed"),
            ]
        )
        assert compute_status(state) == "REPLIED"

    def test_mixed_sent_and_dismissed_resolves(self):
        """Mix of sent and dismissed action replies should resolve to USER_REPLIED."""
        state = _make_state(
            replies=[
                _make_reply(
                    ["WRONG_CHANNEL"], msg_id="msg001", reply_review_status="sent"
                ),
                _make_reply(
                    ["IDENTITY_REQUIRED"],
                    msg_id="msg002",
                    reply_review_status="dismissed",
                ),
            ]
        )
        assert compute_status(state) == "REPLIED"

    def test_completed_data_provided_inline(self):
        """DATA_PROVIDED_INLINE should trigger COMPLETED status."""
        state = _make_state(replies=[_make_reply(["DATA_PROVIDED_INLINE"])])
        assert compute_status(state) == "DONE"


# ---------------------------------------------------------------------------
# Bounce superseded / exhausted tests
# ---------------------------------------------------------------------------


class TestBounceSuperseded:
    def test_bounce_superseded_by_later_reply(self):
        # SAR bounced first, then we sent to a new address and got acknowledged.
        # Status should reflect the later reply, not the old bounce.
        state = _make_state(
            replies=[
                _make_reply(
                    ["BOUNCE_PERMANENT"],
                    msg_id="msg001",
                    received_at="2026-03-10T10:00:00Z",
                ),
                _make_reply(
                    ["REQUEST_ACCEPTED"],
                    msg_id="msg002",
                    received_at="2026-03-15T10:00:00Z",
                ),
            ]
        )
        assert compute_status(state) == "IN_PROGRESS"

    def test_bounce_not_superseded_when_latest(self):
        # Only bounce reply — should still return BOUNCED.
        state = _make_state(
            replies=[
                _make_reply(["BOUNCE_PERMANENT"], msg_id="msg001"),
            ]
        )
        assert compute_status(state) == "STALLED"

    def test_two_bounces_returns_bounced(self):
        # Two bounce replies — most recent event is still a bounce → BOUNCED.
        # (ACTION_REQUIRED / ADDRESS_NOT_FOUND only fires once address_exhausted=True.)
        state = _make_state(
            replies=[
                _make_reply(
                    ["BOUNCE_PERMANENT"],
                    msg_id="msg001",
                    received_at="2026-03-10T10:00:00Z",
                ),
                _make_reply(
                    ["BOUNCE_PERMANENT"],
                    msg_id="msg002",
                    received_at="2026-03-15T10:00:00Z",
                ),
            ]
        )
        assert compute_status(state) == "STALLED"

    def test_bounce_superseded_non_gdpr_ignored(self):
        # NON_GDPR reply after bounce should not count as superseding it.
        state = _make_state(
            replies=[
                _make_reply(
                    ["BOUNCE_PERMANENT"],
                    msg_id="msg001",
                    received_at="2026-03-10T10:00:00Z",
                ),
                _make_reply(
                    ["NON_GDPR"], msg_id="msg002", received_at="2026-03-15T10:00:00Z"
                ),
            ]
        )
        assert compute_status(state) == "STALLED"


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

    def test_deadline_reset_on_confirmation_required(self):
        """CONFIRMATION_REQUIRED reply resets the 30-day GDPR deadline."""
        state = _make_state(deadline_days_from_now=5)  # only 5 days left
        original_deadline = state.deadline
        reply_ts = date.today().isoformat() + "T12:00:00Z"
        new = [_make_reply(["CONFIRMATION_REQUIRED"], received_at=reply_ts)]
        updated = update_state(state, new)
        assert updated.deadline != original_deadline
        expected = deadline_from_sent(reply_ts)
        assert updated.deadline == expected

    def test_deadline_reset_on_request_accepted(self):
        """REQUEST_ACCEPTED reply resets the 30-day GDPR deadline."""
        state = _make_state(deadline_days_from_now=10)
        original_deadline = state.deadline
        reply_ts = date.today().isoformat() + "T14:00:00Z"
        new = [_make_reply(["REQUEST_ACCEPTED"], received_at=reply_ts)]
        updated = update_state(state, new)
        assert updated.deadline != original_deadline
        assert updated.deadline == deadline_from_sent(reply_ts)

    def test_deadline_reset_on_in_progress(self):
        """IN_PROGRESS reply resets the 30-day GDPR deadline."""
        state = _make_state(deadline_days_from_now=10)
        reply_ts = date.today().isoformat() + "T14:00:00Z"
        new = [_make_reply(["IN_PROGRESS"], received_at=reply_ts)]
        updated = update_state(state, new)
        assert updated.deadline == deadline_from_sent(reply_ts)

    def test_no_deadline_reset_on_auto_acknowledge(self):
        """AUTO_ACKNOWLEDGE does NOT reset the deadline."""
        state = _make_state(deadline_days_from_now=10)
        original_deadline = state.deadline
        new = [_make_reply(["AUTO_ACKNOWLEDGE"])]
        updated = update_state(state, new)
        assert updated.deadline == original_deadline

    def test_no_deadline_reset_on_duplicate_reply(self):
        """Duplicate replies (already in state) should NOT reset deadline."""
        existing = _make_reply(["CONFIRMATION_REQUIRED"], msg_id="existing")
        state = _make_state(replies=[existing], deadline_days_from_now=5)
        original_deadline = state.deadline
        dup = _make_reply(["CONFIRMATION_REQUIRED"], msg_id="existing")
        updated = update_state(state, [dup])
        assert updated.deadline == original_deadline


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

    def test_reply_record_new_fields_roundtrip(self, tmp_path):
        path = tmp_path / "reply_state.json"
        reply = _make_reply(
            ["WRONG_CHANNEL"],
            suggested_reply="Please clarify which channel to use.",
            reply_review_status="pending",
        )
        state = _make_state(replies=[reply])
        save_state("user@gmail.com", {"example.com": state}, path=path)
        loaded = load_state("user@gmail.com", path=path)
        r = loaded["example.com"].replies[0]
        assert r.suggested_reply == "Please clarify which channel to use."
        assert r.reply_review_status == "pending"

    def test_reply_record_old_dict_loads_with_defaults(self):
        """Old reply_state.json entries without new fields load with empty defaults."""
        d = {
            "gmail_message_id": "msg001",
            "received_at": "2026-03-17T10:00:00Z",
            "from": "privacy@example.com",
            "subject": "Re: SAR",
            "snippet": "test",
            "tags": ["AUTO_ACKNOWLEDGE"],
            "extracted": {},
            "llm_used": False,
            "has_attachment": False,
            "attachment_catalog": None,
            # no suggested_reply, reply_review_status, sent_reply_body, sent_reply_at
        }
        r = ReplyRecord.from_dict(d)
        assert r.suggested_reply == ""
        assert r.reply_review_status == ""
        assert r.sent_reply_body == ""
        assert r.sent_reply_at == ""

    def test_portal_submitted_roundtrip(self, tmp_path):
        """portal_submitted reply_review_status survives save/load."""
        path = tmp_path / "reply_state.json"
        reply = _make_reply(
            ["WRONG_CHANNEL"],
            reply_review_status="portal_submitted",
            sent_reply_body="Submitted via portal",
            sent_reply_at="2026-04-18T10:00:00Z",
        )
        state = _make_state(replies=[reply])
        save_state("user@gmail.com", {"example.com": state}, path=path)
        loaded = load_state("user@gmail.com", path=path)
        r = loaded["example.com"].replies[0]
        assert r.reply_review_status == "portal_submitted"
        assert r.sent_reply_body == "Submitted via portal"

    def test_wrong_channel_extracted_fields_roundtrip(self, tmp_path):
        """wrong_channel_instructions and login_required survive save/load."""
        path = tmp_path / "reply_state.json"
        reply = _make_reply(["WRONG_CHANNEL"])
        reply.extracted["wrong_channel_instructions"] = "Submit via privacy center at https://privacy.example.com"
        reply.extracted["login_required"] = True
        state = _make_state(replies=[reply])
        save_state("user@gmail.com", {"example.com": state}, path=path)
        loaded = load_state("user@gmail.com", path=path)
        r = loaded["example.com"].replies[0]
        assert r.extracted["wrong_channel_instructions"] == "Submit via privacy center at https://privacy.example.com"
        assert r.extracted["login_required"] is True

    def test_sent_reply_fields_roundtrip(self, tmp_path):
        """sent_reply_body and sent_reply_at survive a save/load cycle."""
        path = tmp_path / "reply_state.json"
        reply = _make_reply(
            ["WRONG_CHANNEL"],
            reply_review_status="sent",
            sent_reply_body="I will re-submit via the web form.",
            sent_reply_at="2026-03-27T10:00:00Z",
        )
        state = _make_state(replies=[reply])
        save_state("user@gmail.com", {"example.com": state}, path=path)
        loaded = load_state("user@gmail.com", path=path)
        r = loaded["example.com"].replies[0]
        assert r.sent_reply_body == "I will re-submit via the web form."
        assert r.sent_reply_at == "2026-03-27T10:00:00Z"


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


# ---------------------------------------------------------------------------
# portal_verification field tests
# ---------------------------------------------------------------------------


class TestPortalVerificationField:
    def test_reply_record_round_trip_with_portal_verification(self):
        """portal_verification field survives to_dict/from_dict cycle."""
        record = ReplyRecord(
            gmail_message_id="abc123",
            received_at="2026-04-13T10:00:00Z",
            from_addr="privacy@example.com",
            subject="Re: SAR",
            snippet="Use our portal",
            tags=["WRONG_CHANNEL"],
            extracted={"portal_url": "https://example.com/privacy"},
            llm_used=False,
            has_attachment=False,
            attachment_catalog=None,
            portal_verification={
                "url": "https://example.com/privacy",
                "classification": "gdpr_portal",
                "checked_at": "2026-04-13T10:05:00Z",
                "error": None,
                "page_title": "Privacy Request Form",
            },
        )
        d = record.to_dict()
        assert d["portal_verification"]["classification"] == "gdpr_portal"

        restored = ReplyRecord.from_dict(d)
        assert restored.portal_verification["classification"] == "gdpr_portal"
        assert restored.portal_verification["url"] == "https://example.com/privacy"

    def test_reply_record_round_trip_without_portal_verification(self):
        """Backward compat: old records without portal_verification still load."""
        d = {
            "gmail_message_id": "xyz",
            "received_at": "2026-04-13T10:00:00Z",
            "from": "test@example.com",
            "subject": "Re: SAR",
            "snippet": "Hello",
            "tags": ["AUTO_ACKNOWLEDGE"],
            "extracted": {},
            "llm_used": False,
            "has_attachment": False,
        }
        record = ReplyRecord.from_dict(d)
        assert record.portal_verification is None
