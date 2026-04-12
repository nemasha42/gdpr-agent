"""Tests for user_identity parameter in letter_engine.composer."""

from unittest.mock import MagicMock


def _make_record():
    record = MagicMock()
    record.domain = "spotify.com"
    record.company_name = "Spotify"
    record.contact.email = "privacy@spotify.com"
    record.contact.privacy_email = "privacy@spotify.com"
    record.contact.dpo_email = ""
    record.contact.preferred_method = "email"
    record.contact.dsar_portal_url = ""
    record.contact.gdpr_portal_url = ""
    record.contact.postal_address.line1 = ""
    record.contact.postal_address.city = ""
    record.contact.postal_address.postcode = ""
    record.contact.postal_address.country = ""
    return record


def _make_identity():
    return {
        "user_full_name": "Alice Smith",
        "user_email": "alice@gmail.com",
        "user_address_line1": "",
        "user_address_city": "",
        "user_address_postcode": "",
        "user_address_country": "",
        "gdpr_framework": "UK GDPR",
    }


def test_compose_uses_user_identity():
    from letter_engine.composer import compose

    record = _make_record()
    letter = compose(record, user_identity=_make_identity())
    assert "Alice Smith" in letter.body
    assert "alice@gmail.com" in letter.body


def test_compose_subject_uses_user_identity():
    from letter_engine.composer import compose

    record = _make_record()
    letter = compose(record, user_identity=_make_identity())
    assert "Alice Smith" in letter.subject


def test_compose_subprocessor_uses_user_identity():
    from letter_engine.composer import compose_subprocessor_request

    record = _make_record()
    letter = compose_subprocessor_request(record, user_identity=_make_identity())
    if letter is not None:
        assert "Alice Smith" in letter.body


def test_compose_subprocessor_subject_uses_user_identity():
    from letter_engine.composer import compose_subprocessor_request

    record = _make_record()
    letter = compose_subprocessor_request(record, user_identity=_make_identity())
    if letter is not None:
        assert "Alice Smith" in letter.subject


def test_compose_subprocessor_returns_none_without_email():
    from letter_engine.composer import compose_subprocessor_request

    record = _make_record()
    record.contact.privacy_email = ""
    record.contact.dpo_email = ""
    record.contact.preferred_method = "portal"
    letter = compose_subprocessor_request(record, user_identity=_make_identity())
    assert letter is None
